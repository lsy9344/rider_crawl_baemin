"""Story 5.1 / AC3 — 실제 asyncio 이벤트 루프 위 ASGI e2e (QA gap-fill).

기존 ``test_server_app.py`` 의 async 검증은 ``inspect.iscoroutinefunction`` 으로
핸들러가 ``async def`` 임을 정적으로 확인한다. 여기서는 ``httpx.AsyncClient`` +
``ASGITransport`` 로 **실제 async 이벤트 루프** 위에서 앱을 in-process 호출해
운영 엔드포인트가 async 런타임에서 end-to-end 로 동작함을 검증한다.

``pytest-asyncio`` 미도입(5.1 dep 동결)이라 ``asyncio.run`` 으로 코루틴을 구동한다.
외부 소켓/서비스는 쓰지 않는다(ASGITransport in-process).
"""

from __future__ import annotations

import asyncio

import httpx

from rider_server.main import create_app
from rider_server.settings import Settings

_FAKE_SETTINGS = Settings(
    app_env="test",
    app_version="9.9.9",
    build_sha=None,
    build_time=None,
)


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(_FAKE_SETTINGS))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


def test_health_ok_under_real_event_loop():
    r = asyncio.run(_get("/health"))
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_under_real_event_loop():
    r = asyncio.run(_get("/version"))
    assert r.status_code == 200
    assert r.json()["app_version"] == "9.9.9"


def test_error_envelope_under_real_event_loop():
    # 에러 경로(전역 exception handler)도 async 런타임에서 envelope 를 유지한다.
    r = asyncio.run(_get("/nope-does-not-exist"))
    assert r.status_code == 404
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "NOT_FOUND"
