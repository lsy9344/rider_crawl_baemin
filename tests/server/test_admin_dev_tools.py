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

    claimed = client.post(
        "/v1/jobs/claim",
        json={
            "agent_id": registered.json()["agent_id"],
            "capabilities": ["OPEN_AUTH_BROWSER"],
            "max_jobs": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert claimed.status_code == 200
    jobs = claimed.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["type"] == "OPEN_AUTH_BROWSER"
    assert jobs[0]["payload"]["platform"] == "coupang"
