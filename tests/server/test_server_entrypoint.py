"""Story 5.1 / Task 2 — python -m rider_server 진입점 (QA gap-fill).

``__main__.main()`` 는 uvicorn 을 import-string(``rider_server.main:app``)으로 기동하고
HOST/PORT/APP_ENV env 로 host·port·reload 를 결정한다. 실제 서버를 띄우지 않고
``uvicorn.run`` 을 monkeypatch 해 전달 인자만 검증한다.

``rider_server.__main__`` 임포트는 함수 안으로 미룬다 — 모듈 상단 임포트는 runpy
RuntimeWarning 을 유발할 수 있다(memory/agent-main-runpy-warning).
"""

from __future__ import annotations


def _run_main_capturing(monkeypatch):
    """``main()`` 을 실행하며 ``uvicorn.run`` 호출 인자를 캡처해 돌려준다."""
    import uvicorn

    captured: dict = {}

    def _fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", _fake_run)

    from rider_server.__main__ import main

    main()
    return captured


def test_main_runs_uvicorn_with_import_string_and_defaults(monkeypatch):
    for var in ("HOST", "PORT", "APP_ENV"):
        monkeypatch.delenv(var, raising=False)

    captured = _run_main_capturing(monkeypatch)

    # 운영 정본과 동일한 import-string 으로 기동한다(reload 동작 조건).
    assert captured["app"] == "rider_server.main:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8000
    assert isinstance(captured["port"], int)
    # 기본 APP_ENV=development → reload 활성.
    assert captured["reload"] is True


def test_main_honors_host_and_port_env(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("APP_ENV", "development")

    captured = _run_main_capturing(monkeypatch)

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["reload"] is True


def test_main_disables_reload_outside_development(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    captured = _run_main_capturing(monkeypatch)

    # 운영(비-development)에서는 reload 를 끈다.
    assert captured["reload"] is False
