"""Story 5.1 / AC2 — 에러 envelope·HTTP 상태코드 매핑·redaction 심화 (QA gap-fill).

기존 ``test_server_app.py`` 는 404/422(validation)/500 envelope 만 다룬다. 여기서는
Task 3 가 요구한 "HTTP 상태코드를 의미 있게 매핑(400/401/403/404/409/422/429/503)" 을
**실제 exception handler 경로**로 검증하고(기존 테스트는 ``HTTPStatus.name`` 순수
함수만 확인), 405(Method Not Allowed)·HTTPException detail redaction·500
``error_message_redacted`` 마스킹·envelope 키 snake_case 를 추가로 잠근다.

``TestClient`` in-process(외부 서비스/소켓 미사용). secret 은 fake·secret-shaped
문자열만 쓰며(실제 토큰 아님), 응답에 평문으로 남지 않음을 redact 어서션으로 확인한다.
"""

from __future__ import annotations

import re
from http import HTTPStatus

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rider_server.main import create_app
from rider_server.settings import Settings

_FAKE_SETTINGS = Settings(
    app_env="test",
    app_version="9.9.9",
    build_sha=None,
    build_time=None,
)

# 운영 엔드포인트는 GET 전용 — 다른 메서드는 405 여야 한다.
_OPERATIONAL_PATHS = ("/health", "/version", "/metrics")

# Task 3 명시 상태코드 → HTTPStatus.name(UPPER_SNAKE) 기대 매핑.
_STATUS_CODE_CASES = [
    (400, "BAD_REQUEST"),
    (401, "UNAUTHORIZED"),
    (403, "FORBIDDEN"),
    (404, "NOT_FOUND"),
    (409, "CONFLICT"),
    (422, "UNPROCESSABLE_ENTITY"),
    (429, "TOO_MANY_REQUESTS"),
    (503, "SERVICE_UNAVAILABLE"),
]


def _app_with_status_probe():
    """임의 상태코드로 ``HTTPException`` 을 던지는 probe 라우트를 단 app."""
    app = create_app(_FAKE_SETTINGS)

    @app.get("/__probe_status")
    async def _probe_status(code: int) -> dict:  # noqa: D401
        # detail 에 secret-shaped 문자열을 섞어 redaction 경로도 함께 탄다.
        raise HTTPException(status_code=code, detail="boom token=supersecret999")

    return app


def _assert_error_envelope(body: dict, *, code: str) -> None:
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert re.match(r"^[A-Z][A-Z0-9_]*$", err["code"]), err["code"]
    assert "message_redacted" in err


# ── 405 Method Not Allowed (GET 전용 운영 엔드포인트) ─────────────────────────

@pytest.mark.parametrize("path", _OPERATIONAL_PATHS)
def test_post_to_get_only_operational_endpoint_405_envelope(path):
    c = TestClient(create_app(_FAKE_SETTINGS), raise_server_exceptions=False)
    r = c.post(path)
    assert r.status_code == 405
    _assert_error_envelope(r.json(), code="METHOD_NOT_ALLOWED")


# ── HTTPException status → UPPER_SNAKE code (실 handler 경로) ─────────────────

@pytest.mark.parametrize("status_code,code", _STATUS_CODE_CASES)
def test_httpexception_status_maps_to_upper_snake_code(status_code, code):
    # 기존 테스트와 달리 실제 _http_exc_handler 를 통과시켜 검증한다.
    c = TestClient(_app_with_status_probe(), raise_server_exceptions=False)
    r = c.get("/__probe_status", params={"code": status_code})
    # 의미 있는 HTTP 상태코드가 그대로 보존된다.
    assert r.status_code == status_code
    _assert_error_envelope(r.json(), code=code)


# 참고: HTTPException(422) → UNPROCESSABLE_ENTITY 이고, FastAPI 자동 검증 실패
# (RequestValidationError) → VALIDATION_ERROR 다(다른 경로 · test_server_app.py 참조).


# ── HTTPException detail 의 secret-shaped 문자열은 redact 된다 ────────────────

def test_httpexception_detail_redacts_secret_shaped_message():
    c = TestClient(_app_with_status_probe(), raise_server_exceptions=False)
    r = c.get("/__probe_status", params={"code": 400})
    assert r.status_code == 400
    # detail 의 secret-shaped 토큰 값은 응답 어디에도 평문으로 남지 않는다.
    assert "supersecret999" not in r.text
    assert r.json()["error"]["message_redacted"] != "boom token=supersecret999"


# ── 500 envelope: 일반 메시지 노출 + 예외 본문 redact ────────────────────────

def test_unhandled_exception_500_exposes_redacted_error_body():
    app = create_app(_FAKE_SETTINGS)

    @app.get("/__boom")
    async def _boom() -> dict:
        raise ValueError("password=hunter2 leaked")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/__boom")
    assert r.status_code == 500
    err = r.json()["error"]
    # 사용자에게는 일반 메시지만, 예외 본문은 redact 된 별도 필드로 노출된다.
    assert err["message_redacted"] == "internal server error"
    assert "error_message_redacted" in err
    assert "hunter2" not in r.text


# ── envelope 키는 모두 snake_case (camelCase 변환 없음, AC2) ──────────────────

def test_error_envelope_keys_are_snake_case():
    snake = re.compile(r"^[a-z][a-z0-9_]*$")
    app = create_app(_FAKE_SETTINGS)

    @app.get("/__boom2")
    async def _boom2() -> dict:
        raise RuntimeError("x")

    c = TestClient(app, raise_server_exceptions=False)
    body = c.get("/__boom2").json()
    assert list(body) == ["error"]
    for key in body["error"]:
        assert snake.match(key), f"non-snake_case envelope key: {key!r}"


# ── HTTPStatus.name 이 매핑 표와 일치(자기검증, non-vacuous) ──────────────────

@pytest.mark.parametrize("status_code,code", _STATUS_CODE_CASES)
def test_httpstatus_name_matches_expected_mapping(status_code, code):
    assert HTTPStatus(status_code).name == code
