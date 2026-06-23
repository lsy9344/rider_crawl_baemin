from pathlib import Path

from fastapi.testclient import TestClient


def test_dev_admin_ui_requires_explicit_local_bypass_flag() -> None:
    source = Path("scripts/dev_admin_ui.py").read_text(encoding="utf-8")

    assert "DEV_ADMIN_AUTH_BYPASS" in source
    assert "APP_ENV" in source
    assert "production" in source
    assert "127.0.0.1" in source


def test_dev_admin_ui_local_auth_smoke_seeds_click_to_agent_queue(monkeypatch) -> None:
    from scripts import dev_admin_ui

    monkeypatch.setenv("DEV_AUTH_SMOKE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@127.0.0.1:1/rider")
    app = dev_admin_ui.build_dev_app()
    assert app.state.db_engine is None
    client = TestClient(app, raise_server_exceptions=False)

    registered = client.post(
        "/v1/agents/register",
        json={
            "registration_code": "LOCAL-AUTH-AGENT",
            "machine_fingerprint": "local-machine",
            "hostname": "LOCAL-PC",
            "os": "Windows",
            "agent_version": "0.1.0",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["agent_token"]

    started = client.post(
        "/admin/targets/local-auth-target/auth-start?tenant=local-tenant",
        data={"confirm_action": "confirmed"},
        headers={"Origin": "http://testserver"},
    )
    assert started.status_code == 200
    assert "인증 시작됨" in started.text

    # crawl-coupang-auth-separation Task 5: 이메일 2FA 정보가 완비된 쿠팡 계정의 '인증 시작'은
    # 전용 인증 job(AUTH_COUPANG_2FA)을 만든다(OPEN_AUTH_BROWSER 가 아니라). dev smoke seed 는
    # verification_email_* 를 채우므로 자동복구 경로로 라우팅된다.
    claimed = client.post(
        "/v1/jobs/claim",
        json={
            "agent_id": registered.json()["agent_id"],
            "capabilities": ["AUTH_COUPANG_2FA"],
            "max_jobs": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert claimed.status_code == 200
    jobs = claimed.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["type"] == "AUTH_COUPANG_2FA"
    assert jobs[0]["payload"]["platform"] == "coupang"
    assert jobs[0]["payload"]["recovery_mode"] == "coupang_auto_email_2fa"
