"""DispatchService — run_once의 전송 단계 분리(Story 3.1 / P2-01, FR-9).

책임: 메시지 텍스트를 받아 ``send_enabled`` 게이팅 후 단일 전송(기존
``messengers.dispatch_text_message`` 1회)하고 ``DispatchResult`` 를 반환한다.
기본 adapter는 ``run_once`` 의 ``_send_message`` 와 동일하며, 테스트는
``send_message`` 인자로 fake를 주입해 실제 Telegram/Kakao 호출을 끊는다.

범위 경계(반드시):
  - lock·dedup 미이관: ``RunLock``(브라우저 scope)·``send_only_on_change`` 의
    ``last_message`` 파일 dedup은 ``run_once`` 호환 경로가 계속 소유한다.
    따라서 ``dispatch`` 는 dedup을 수행하지 않고 ``skipped`` 는 항상 ``False`` 다 —
    ``DeliveryLog``/idempotency seam은 Story 3.5가 채운다. dedup scope key를
    새로 만들거나 축소하지 않는다.
  - 단일 전송만: DeliveryRule fan-out(1 대상 → N 채널)은 Story 3.4.
  - 중앙 webhook 아님: 기본 adapter는 기존 per-Agent ``dispatch_text_message``
    경로다 — Telegram 중앙 sendMessage는 Story 3.7/Epic 5.

설계 불변식:
  - 순수·결정적·의존성 0: 내부 ``datetime.now()``/``uuid4()`` 미호출(2.6 규약).
  - 단방향 import: ``rider_server`` → ``rider_crawl`` 만, 역방향 0.
  - ``message_hash`` 는 ``run_once`` 와 동일한
    ``hashlib.sha256(message.encode("utf-8")).hexdigest()``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from rider_crawl import messengers
from rider_crawl.config import AppConfig


@dataclass(frozen=True)
class DispatchResult:
    """전송 결과(불변).

    필드 집합은 ``app.RunResult``(app.py 15-21)와 동일 — 합성 결과를 ``run_once``
    와 1:1 비교(parity)하기 위함이다. ``skipped`` 는 3.1에서 항상 ``False`` 다
    (dedup 미이관 — Story 3.5의 idempotency seam이 채운다). shape parity를 위해
    필드만 둔다.
    """

    message: str
    sent: bool
    skipped: bool
    message_hash: str


class DispatchService:
    """전송 단계 — 주입 가능한 sender를 받아 단일 전송한다(순수 정적)."""

    @staticmethod
    def dispatch(
        config: AppConfig,
        message: str,
        *,
        send_message: Callable[[AppConfig, str], None] | None = None,
    ) -> DispatchResult:
        sender = send_message or _default_send_message
        # run_once(app.py 41)와 동일한 hash 계산.
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

        # 3.1은 dedup을 하지 않는다(skipped 항상 False — Story 3.5가 채움).
        if config.send_enabled:
            sender(config, message)
            return DispatchResult(message=message, sent=True, skipped=False, message_hash=message_hash)
        return DispatchResult(message=message, sent=False, skipped=False, message_hash=message_hash)


def _default_send_message(config: AppConfig, message: str) -> None:
    # run_once._send_message(app.py 137-140)와 동일 경로(messenger registry 위임).
    messengers.dispatch_text_message(config, message)
