"""Telegram 인바운드 webhook 라우터 — Story 5.5 (AC1, P4-06·FR-29·ADD-11).

3.7 이 5.5 로 위임한 **인바운드 단일 진입점**: secret header 검증 webhook + ``/register <code>``.
Agent별 ``getUpdates`` polling(``TelegramUpdatePoller``/``get_telegram_updates``)을 만들지 않고
(send-only 중앙 정책 — polling 경합 회피), Telegram 이 push 하는 update 를 단일 ``POST`` 로 받는다.

보안(NFR-5·8):
  - ``X-Telegram-Bot-Api-Secret-Token`` 헤더를 ``secrets.compare_digest`` 로 **상수시간 비교**해
    설정 secret 과 일치할 때만 수락한다. **본문 파싱 이전에** 검증하므로 미검증 요청은 페이로드를
    파싱조차 하지 않고 거부된다(헤더 누락/불일치 → 401 전역 envelope). 검증 로직은 DB 없이
    테스트 가능한 순수 함수(:func:`verify_webhook_secret`)로 분리한다.
  - secret 값·봇 토큰은 로그/응답/예외에 절대 넣지 않는다(detail 은 입력값 echo 없음).

라우팅 경로는 ``/v1/telegram/webhook`` — 운영 엔드포인트(``/health``·``/version``·``/metrics``)만
root-level 금지 대상이라 ``/v1/`` 리소스 경로는 허용된다(``test_registered_routes_have_no_v1
_operational_paths`` 와 무충돌). 상태 전이/DB 쓰기는 service(``ChannelRegistrationService``)에만
위임한다(라우트에서 직접 컬럼 변경 금지).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..services.channel_registration import ChannelRegistrationService

#: Telegram 이 webhook 호출 시 싣는 secret 헤더 이름(``setWebhook(secret_token=...)`` 와 1:1).
WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

#: ``/register`` 명령 토큰(그룹에서는 ``/register@BotName`` 형태로 올 수 있다).
_REGISTER_COMMAND = "/register"

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])


# ── secret 검증(순수·상수시간) ────────────────────────────────────────────────────


def verify_webhook_secret(provided: str | None, expected: str | None) -> bool:
    """webhook secret 헤더를 상수시간 비교한다(fail-closed).

    설정 secret(``expected``)이 없거나 헤더(``provided``)가 없으면 **거부**한다(미설정 환경에서
    임의 요청 수락 금지). 둘 다 있으면 ``secrets.compare_digest`` 로 timing-safe 비교한다.
    secret 값은 반환/로그하지 않는다.
    """

    if not expected or not provided:
        return False
    return secrets.compare_digest(provided, expected)


# ── Telegram Update 페이로드(Pydantic v2, snake_case·camelCase alias 금지) ──────────


class _TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # 그룹/채널 chat_id 는 음수 정수(예: -100…). 라우팅 식별자(secret 아님).
    id: int


class _TelegramInboundMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str | None = None
    chat: _TelegramChat | None = None
    # Telegram 와이어 필드는 snake_case ``message_thread_id`` → DB 컬럼 ``thread_id``.
    message_thread_id: int | None = None


class TelegramUpdate(BaseModel):
    """Telegram Update — 명령은 ``message`` 또는 ``channel_post`` 에 담겨 온다(나머지 필드 무시)."""

    model_config = ConfigDict(extra="ignore")
    message: _TelegramInboundMessage | None = None
    channel_post: _TelegramInboundMessage | None = None


@dataclass(frozen=True)
class RegisterCommand:
    """파싱된 ``/register <code>`` 명령(불변). chat_id/thread_id 는 라우팅 식별자(secret 아님)."""

    code: str
    chat_id: str
    thread_id: str | None


def parse_register_command(update: TelegramUpdate) -> RegisterCommand | None:
    """update 에서 ``/register <code>`` 를 인식해 ``RegisterCommand`` 를 만든다(순수).

    명령이 아니거나(``/register`` 아님), 코드가 없거나, chat 정보가 없으면 ``None`` 을 반환한다
    (호출부는 ``200 {"ok": true}`` 로 무시 — 에러 아님). 그룹의 ``/register@BotName code`` 형태도
    인식한다(``@`` 뒤 봇 이름 제거). ``message`` 우선, 없으면 ``channel_post``.
    """

    inbound = update.message or update.channel_post
    if inbound is None or inbound.text is None or inbound.chat is None:
        return None

    parts = inbound.text.split()
    if not parts:
        return None
    command = parts[0].split("@", 1)[0]  # /register@BotName → /register
    if command != _REGISTER_COMMAND or len(parts) < 2:
        return None

    code = parts[1].strip()
    if not code:
        return None

    thread_id = (
        None if inbound.message_thread_id is None else str(inbound.message_thread_id)
    )
    return RegisterCommand(code=code, chat_id=str(inbound.chat.id), thread_id=thread_id)


# ── 라우트(인바운드 단일 진입점) ──────────────────────────────────────────────────


@router.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    """``POST /v1/telegram/webhook`` — secret 검증 후 ``/register`` 를 처리한다.

    secret 검증을 **본문 파싱보다 먼저** 수행한다(미검증 요청은 페이로드 처리 안 함). 검증 통과 후
    ``/register <code>`` 면 service.register 로 chat_id/thread_id 를 멱등 저장하고, 명령이 아니거나
    페이로드가 깨졌으면 ``200 {"ok": true}`` 로 무시한다(Telegram 재전송 폭주 방지).
    """

    expected = request.app.state.resolve_telegram_secret()
    provided = request.headers.get(WEBHOOK_SECRET_HEADER)
    if not verify_webhook_secret(provided, expected):
        # secret 값/입력 echo 없이 거부(전역 envelope → {"error":{"code":"UNAUTHORIZED",...}}).
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid telegram webhook secret"
        )

    try:
        update = TelegramUpdate.model_validate(await request.json())
    except ValueError:
        # 비-JSON/스키마 불일치 update 는 명령이 아닌 것으로 보고 무시(에러 아님).
        return {"ok": True}

    command = parse_register_command(update)
    if command is None:
        return {"ok": True}

    service = ChannelRegistrationService(request.app.state.channel_repository)
    await service.register(
        code=command.code, chat_id=command.chat_id, thread_id=command.thread_id
    )
    return {"ok": True}
