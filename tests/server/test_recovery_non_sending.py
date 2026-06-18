"""Story 5.8 / AC3 — 복구·신규 환경 non-sending 게이트(NFR-9·25, always-run, 무 DB).

(1) 순수 ``effective_send_enabled``: send_enabled·sending_enabled 둘 다 True 일 때만 True
    (복구 기본 OFF → 차단·fail-closed).
(2) ``Settings.sending_enabled`` 기본 OFF·env truthy 파싱·``create_app`` app.state 반영.

fake 값만. 평면 ``tests/server/`` 컨벤션.
"""

from __future__ import annotations

import pytest

from rider_server.main import create_app
from rider_server.services.recovery import effective_send_enabled
from rider_server.settings import Settings

_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)


# ══════════════════════════════════════════════════════════════════════════
# (1) 순수 게이트 — AND compose
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "send_enabled,sending_enabled,expected",
    [
        (True, True, True),      # 둘 다 켜짐 → 실전송
        (True, False, False),    # 환경 non-sending(복구 기본) → 차단(send_enabled 무시)
        (False, True, False),    # 채널/대상 게이트 off → 차단
        (False, False, False),   # 둘 다 off → 차단
    ],
)
def test_effective_send_enabled_is_and_of_both(send_enabled, sending_enabled, expected) -> None:
    assert effective_send_enabled(send_enabled=send_enabled, sending_enabled=sending_enabled) is expected


# ══════════════════════════════════════════════════════════════════════════
# (2) Settings 기본 OFF·env 파싱·app.state 반영
# ══════════════════════════════════════════════════════════════════════════

def test_sending_enabled_defaults_off() -> None:
    # 복구/신규 환경 fail-closed — env 미설정 시 기본 OFF.
    assert Settings.from_env({}).sending_enabled is False


def test_sending_enabled_env_truthy_parsing() -> None:
    assert Settings.from_env({"RIDER_SENDING_ENABLED": "1"}).sending_enabled is True
    assert Settings.from_env({"RIDER_SENDING_ENABLED": "true"}).sending_enabled is True
    assert Settings.from_env({"RIDER_SENDING_ENABLED": "on"}).sending_enabled is True
    assert Settings.from_env({"RIDER_SENDING_ENABLED": "0"}).sending_enabled is False
    assert Settings.from_env({"RIDER_SENDING_ENABLED": "false"}).sending_enabled is False
    assert Settings.from_env({"RIDER_SENDING_ENABLED": ""}).sending_enabled is False


def test_create_app_default_is_non_sending() -> None:
    # 명시적 활성화 전까지 실전송 차단 — app.state.sending_enabled 기본 False.
    app = create_app(_FAKE_SETTINGS)
    assert app.state.sending_enabled is False


def test_create_app_reflects_enabled_setting() -> None:
    enabled = Settings(app_env="t", app_version="9", build_sha=None, build_time=None, sending_enabled=True)
    assert create_app(enabled).state.sending_enabled is True


# ══════════════════════════════════════════════════════════════════════════
# (3) Admin 보안 설정 env 파싱(IP allowlist·MFA 토글) — 신규 deps 0(stdlib)
# ══════════════════════════════════════════════════════════════════════════

def test_admin_ip_allowlist_parses_comma_list() -> None:
    s = Settings.from_env({"RIDER_ADMIN_IP_ALLOWLIST": "10.0.0.0/8, 203.0.113.5 ,"})
    assert s.admin_ip_allowlist == ("10.0.0.0/8", "203.0.113.5")  # 빈 항목 제거·trim
    assert Settings.from_env({}).admin_ip_allowlist == ()  # 미설정 → 추가 제한 없음


def test_admin_mfa_required_defaults_true() -> None:
    assert Settings.from_env({}).admin_mfa_required is True
    assert Settings.from_env({"RIDER_ADMIN_MFA_REQUIRED": "false"}).admin_mfa_required is False


def test_admin_public_access_defaults_off_and_parses_truthy() -> None:
    assert Settings.from_env({}).admin_public_access is False
    assert Settings.from_env({"RIDER_ADMIN_PUBLIC_ACCESS": "1"}).admin_public_access is True
    assert Settings.from_env({"RIDER_ADMIN_PUBLIC_ACCESS": "false"}).admin_public_access is False
