"""ChannelTestService — 채널별 '전송 테스트' 오케스트레이션(실발송 게이트 해제 조건).

운영자가 실제 메시지 보내기(``tenant.sending_enabled``)를 켜기 전에, 선택한 메시지 채널로
**실제 테스트 메시지**가 정상 전송되는지 확인하는 경로다. 한 채널이라도 전송 테스트에 성공하면
그 tenant 의 ``send_test_passed_at`` 이 스탬프되고(게이트 해제 조건), 운영자는 그 뒤 실발송을
켤 수 있다(:mod:`rider_server.services.admin_entities.tenant_service` 게이트).

메신저별 전송 경로가 비대칭이라 테스트 판정도 비대칭이다:

  - **Telegram**: 서버가 Bot API 로 **동기 직접 전송**한다(:class:`CentralTelegramSender`
    재사용). 전송이 성공하면 즉시 ``PASSED``, 실패하면 즉시 ``FAILED`` — 결과가 그 자리에서
    확정된다.
  - **Kakao**: 서버는 직접 보내지 못하고 ``KAKAO_SEND`` 잡을 큐에 넣는다. 원격 PC 에이전트가
    그 잡을 가져가 KakaoTalk UI 자동화로 실제 전송하고 **비동기로** 결과(성공/실패)를 보고한다.
    그래서 테스트 버튼은 잡을 enqueue 하고 ``PENDING`` 을 돌려주며, 운영자 화면이 잡 상태를
    폴링(:meth:`check_kakao_test`)해 에이전트가 ``SUCCEEDED`` 를 보고했을 때 비로소 ``PASSED``
    로 스탬프한다(잡 결과 기반 자동 판정).

설계 불변식(서비스 레이어 선례 계승):
  - 시각은 호출부 주입(``at``) — 내부에서 ``now()`` 호출 안 함(결정적).
  - 전송 어댑터·enqueue·잡 상태 조회는 **콜백 주입 seam**(테스트는 fake 로 대체). 본 서비스는
    FastAPI/HTTP/실 큐를 직접 import 하지 않는다.
  - fail-closed: 채널이 없거나 tenant scope 밖이거나 라우팅 식별자가 비면 ``FAILED`` 로 막는다
    (오발송보다 미발송).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from rider_server.domain import Messenger, MessengerChannel, Tenant

# 전송 테스트 결과 코드(plain-string — domain enum 아님, 표현/판정 어휘).
TEST_RESULT_PASSED = "PASSED"  # 전송 확인됨 → send_test_passed_at 스탬프됨
TEST_RESULT_FAILED = "FAILED"  # 전송 실패 → 게이트 그대로 막힘
TEST_RESULT_PENDING = "PENDING"  # 카카오 잡 enqueue 됨, 에이전트 결과 대기

# 전송 테스트 잡 payload 마커 — dispatch fan-out 의 실 KAKAO_SEND 잡과 구분한다(테스트 전송은
# delivery_log/idempotency 와 무관한 1회성 검증). 에이전트는 payload 의 room_name/message 만
# 읽으므로 이 마커는 무시되고, 서버 쪽 상태 조회/감사에서만 쓰인다.
KAKAO_TEST_JOB_MARKER = "channel_send_test"

# 운영자에게 보내는 기본 테스트 메시지 본문. 실제 고객 실적 메시지와 명확히 구분되도록 표식한다.
DEFAULT_TEST_MESSAGE = "[전송 테스트] 라이더 모니터링 채널 연결 확인 메시지입니다."

# 메신저별 전송 경로 타입.
TelegramTestSend = Callable[[MessengerChannel, str], None]  # 동기 직접 전송(성공=무예외)
KakaoEnqueue = Callable[..., Awaitable[str]]  # KAKAO_SEND enqueue → job_id 반환
JobStatusReader = Callable[[str], Awaitable[str | None]]  # job_id → 상태(UPPER_SNAKE)/None


@dataclass(frozen=True)
class ChannelTestOutcome:
    """전송 테스트 1회 결과(불변).

    ``result`` 는 ``PASSED``/``FAILED``/``PENDING`` 중 하나. ``message`` 는 운영자 화면용
    한글 안내. ``job_id`` 는 카카오 테스트가 enqueue 한 잡 id(폴링 키, 텔레그램은 None).
    """

    result: str
    message: str
    job_id: str | None = None


class ChannelTestService:
    """채널 전송 테스트 실행 + 성공 시 tenant 게이트 해제 스탬프."""

    def __init__(
        self,
        *,
        get_channel: Callable[[str], Awaitable[MessengerChannel | None]],
        get_tenant: Callable[[str], Awaitable[Tenant | None]],
        mark_send_test_passed: Callable[..., Awaitable[Tenant]],
        telegram_test_send: TelegramTestSend | None,
        kakao_enqueue: KakaoEnqueue | None,
        job_status: JobStatusReader | None,
    ) -> None:
        self._get_channel = get_channel
        self._get_tenant = get_tenant
        self._mark_send_test_passed = mark_send_test_passed
        self._telegram_test_send = telegram_test_send
        self._kakao_enqueue = kakao_enqueue
        self._job_status = job_status

    async def run_test(
        self,
        channel_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        message: str = DEFAULT_TEST_MESSAGE,
    ) -> ChannelTestOutcome:
        """선택 채널로 전송 테스트를 실행한다(메신저별 분기).

        텔레그램은 동기 전송 후 즉시 PASSED/FAILED 스탬프, 카카오는 잡 enqueue 후 PENDING.
        채널이 없거나 tenant scope 밖이거나 라우팅 식별자가 비면 FAILED(fail-closed).
        """

        channel = await self._scoped_channel(channel_id, tenant_id)
        if channel is None:
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, "대상 채널을 찾을 수 없습니다"
            )

        if channel.messenger is Messenger.TELEGRAM:
            return await self._run_telegram_test(
                channel, at=at, actor_id=actor_id, source=source, message=message
            )
        if channel.messenger is Messenger.KAKAO:
            return await self._run_kakao_test(channel, message=message)
        return ChannelTestOutcome(
            TEST_RESULT_FAILED, f"지원하지 않는 메신저입니다: {channel.messenger.value}"
        )

    async def _run_telegram_test(
        self,
        channel: MessengerChannel,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None,
        message: str,
    ) -> ChannelTestOutcome:
        if self._telegram_test_send is None:
            return ChannelTestOutcome(
                TEST_RESULT_FAILED,
                "텔레그램 전송이 구성되지 않았습니다(봇 토큰 확인)",
            )
        if not (channel.telegram_chat_id or "").strip():
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, "텔레그램 채팅 ID가 비어 있습니다"
            )
        try:
            self._telegram_test_send(channel, message)
        except Exception as exc:  # noqa: BLE001 - 전송 실패는 FAILED 로 변환(운영자 안내)
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, f"텔레그램 전송 실패: {exc}"
            )
        await self._stamp_passed(
            channel.tenant_id, at=at, actor_id=actor_id, source=source
        )
        return ChannelTestOutcome(
            TEST_RESULT_PASSED, "텔레그램 전송 테스트 완료 — 실제 메시지 보내기를 켤 수 있습니다"
        )

    async def _run_kakao_test(
        self, channel: MessengerChannel, *, message: str
    ) -> ChannelTestOutcome:
        if self._kakao_enqueue is None:
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, "카카오 전송 큐가 구성되지 않았습니다"
            )
        room_name = (channel.kakao_room_name or "").strip()
        if not room_name:
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, "카카오톡 방 이름이 비어 있습니다"
            )
        job_id = await self._kakao_enqueue(
            kakao_room_name=room_name,
            message=message,
            tenant_id=channel.tenant_id,
            channel_id=channel.id,
        )
        return ChannelTestOutcome(
            TEST_RESULT_PENDING,
            "카카오 전송 테스트 요청됨 — 에이전트 전송 결과를 기다리는 중입니다",
            job_id=job_id,
        )

    async def check_kakao_test(
        self,
        job_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
    ) -> ChannelTestOutcome:
        """enqueue 된 카카오 테스트 잡의 상태를 조회해 결과를 판정한다(폴링용).

        에이전트가 ``SUCCEEDED`` 를 보고했으면 PASSED 로 스탬프, ``FAILED`` 면 FAILED,
        그 외(PENDING/CLAIMED/RUNNING/없음)면 아직 PENDING. 잡 결과 기반 자동 판정이다.
        """

        if self._job_status is None:
            return ChannelTestOutcome(
                TEST_RESULT_FAILED, "잡 상태 조회가 구성되지 않았습니다", job_id=job_id
            )
        status = await self._job_status(job_id)
        if status == "SUCCEEDED":
            await self._stamp_passed(
                tenant_id, at=at, actor_id=actor_id, source=source
            )
            return ChannelTestOutcome(
                TEST_RESULT_PASSED,
                "카카오 전송 테스트 완료 — 실제 메시지 보내기를 켤 수 있습니다",
                job_id=job_id,
            )
        if status == "FAILED":
            return ChannelTestOutcome(
                TEST_RESULT_FAILED,
                "카카오 전송 실패 — 방 이름/에이전트 상태를 확인하세요",
                job_id=job_id,
            )
        return ChannelTestOutcome(
            TEST_RESULT_PENDING,
            "카카오 전송 결과 대기 중 — 잠시 후 다시 확인하세요",
            job_id=job_id,
        )

    async def _scoped_channel(
        self, channel_id: str, tenant_id: str
    ) -> MessengerChannel | None:
        channel = await self._get_channel(channel_id)
        if channel is None or channel.tenant_id != tenant_id:
            return None
        return channel

    async def _stamp_passed(
        self,
        tenant_id: str,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None,
    ) -> None:
        await self._mark_send_test_passed(
            tenant_id,
            send_test_passed_at=at,
            at=at,
            actor_id=actor_id,
            source=source,
            reason="채널 전송 테스트 통과",
        )
