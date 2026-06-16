"""tenant 별 텔레그램 게이트/토큰/secret 해석 단위 테스트(0012).

DB 없이 fake provider 로 ``_default_telegram_sender``(발송 직전 tenant 게이트),
``_default_resolve_telegram_token``(tenant 토큰 우선·env 폴백), ``_default_resolve_telegram_secret``
(tenant secret 집합 ∪ env)을 검증한다. 게이트는 fail-closed.
"""

from __future__ import annotations

import pytest

from rider_server.domain import MessengerChannel
from rider_server.domain.states import Messenger, MessengerChannelState
from rider_server.main import (
    _default_resolve_telegram_secret,
    _default_resolve_telegram_token,
    _default_telegram_sender,
)
from rider_server.services.tenant_telegram_config import TenantTelegramSettings
from rider_server.settings import Settings


class _FakeProvider:
    """async provider fake — tenant_id→설정 매핑. list_active_webhook_secrets 도 제공."""

    def __init__(self, by_tenant: dict[str, TenantTelegramSettings]):
        self._by_tenant = by_tenant

    async def get(self, tenant_id: str):
        return self._by_tenant.get(tenant_id)

    async def list_active_webhook_secrets(self):
        return [s.telegram_webhook_secret for s in self._by_tenant.values() if s.telegram_webhook_secret]


def _settings(**kw) -> Settings:
    base = dict(app_env="test", app_version="0", build_sha=None, build_time=None)
    base.update(kw)
    return Settings(**base)


def _channel(tenant_id: str) -> MessengerChannel:
    return MessengerChannel(
        id="ch-1",
        tenant_id=tenant_id,
        messenger=Messenger.TELEGRAM,
        telegram_chat_id="-1001",
        thread_id=None,
        kakao_room_name=None,
        state=MessengerChannelState.ACTIVE,
    )


def _cfg(tenant_id: str, *, token="", secret="", sending=False) -> TenantTelegramSettings:
    return TenantTelegramSettings(
        tenant_id=tenant_id,
        telegram_bot_token=token,
        telegram_webhook_secret=secret,
        sending_enabled=sending,
    )


def test_token_resolver_prefers_tenant_token_over_env() -> None:
    provider = _FakeProvider({"t1": _cfg("t1", token="TENANT_TOKEN")})
    resolve = _default_resolve_telegram_token(
        _settings(telegram_bot_token_ref="env:X"), provider
    )
    assert resolve(_channel("t1")) == "TENANT_TOKEN"


def test_token_resolver_falls_back_to_env_when_tenant_empty(monkeypatch) -> None:
    monkeypatch.setenv("X", "ENV_TOKEN")
    provider = _FakeProvider({"t1": _cfg("t1", token="")})
    resolve = _default_resolve_telegram_token(
        _settings(telegram_bot_token_ref="env:X"), provider
    )
    assert resolve(_channel("t1")) == "ENV_TOKEN"


def test_token_resolver_fail_closed_when_no_tenant_and_no_env() -> None:
    provider = _FakeProvider({})
    resolve = _default_resolve_telegram_token(_settings(), provider)
    with pytest.raises(RuntimeError):
        resolve(_channel("t1"))


def test_sender_blocks_send_when_tenant_gate_off() -> None:
    provider = _FakeProvider({"t1": _cfg("t1", token="TK", sending=False)})
    sender = _default_telegram_sender(_settings(sending_enabled=False), provider)
    assert sender is not None
    with pytest.raises(RuntimeError, match="fail-closed"):
        sender(_channel("t1"), object(), "hello")


def test_sender_allows_when_tenant_gate_on(monkeypatch) -> None:
    sent: dict = {}

    # CentralTelegramSender.send 를 가로채 실제 HTTP 없이 게이트 통과만 검증.
    def fake_send(self, job, text):
        sent["text"] = text

    monkeypatch.setattr(
        "rider_server.main.CentralTelegramSender.send", fake_send, raising=True
    )
    provider = _FakeProvider({"t1": _cfg("t1", token="TK", sending=True)})
    sender = _default_telegram_sender(_settings(sending_enabled=False), provider)
    sender(_channel("t1"), object(), "hello")
    assert sent["text"] == "hello"


def test_secret_resolver_unions_tenant_and_env(monkeypatch) -> None:
    monkeypatch.setenv("WS", "ENV_SECRET")
    provider = _FakeProvider(
        {
            "t1": _cfg("t1", secret="TENANT_A"),
            "t2": _cfg("t2", secret="TENANT_B"),
        }
    )
    resolve = _default_resolve_telegram_secret(
        _settings(telegram_webhook_secret_ref="env:WS"), provider
    )
    secrets_set = resolve()
    assert "ENV_SECRET" in secrets_set
    assert "TENANT_A" in secrets_set
    assert "TENANT_B" in secrets_set


def test_secret_resolver_empty_when_nothing_configured() -> None:
    provider = _FakeProvider({})
    resolve = _default_resolve_telegram_secret(_settings(), provider)
    assert resolve() == []
