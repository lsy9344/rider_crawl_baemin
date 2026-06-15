"""Story 5.1 / AC1·AC2·AC3 — FastAPI 스캐폴딩·운영 엔드포인트·API 규약 잠금.

``TestClient`` 로 in-process 검증한다(외부 서비스/소켓 미사용). fixture 는 fake 값만
쓰고(실제 토큰/전화/이메일/chat_id 형태 금지 — A1), secret-shaped 입력이 응답에 평문으로
남지 않음을 redact 어서션으로 확인한다.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from rider_server.main import create_app
from rider_server.settings import Settings

# snake_case 키(camelCase 변환 없음) 단언용 — 소문자/숫자/언더스코어만 허용.
_SNAKE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
# ISO 8601 UTC(...Z, epoch 정수 혼용 금지) 단언용.
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# fake settings — 평문 secret 아님(버전/빌드 메타만).
_FAKE_SETTINGS = Settings(
    app_env="test",
    app_version="9.9.9",
    build_sha="abc1234",
    build_time="2026-06-14T00:00:00Z",
)


def _client(settings: Settings = _FAKE_SETTINGS) -> TestClient:
    # raise_server_exceptions=False → 500 envelope 를 응답으로 받아 단언할 수 있다.
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _assert_snake_case_keys(body: dict) -> None:
    for key in body:
        assert _SNAKE_KEY.match(key), f"non-snake_case key: {key!r}"


# ── AC1 — 운영 엔드포인트 동작 ────────────────────────────────────────────

def test_health_returns_ok_200():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_snake_case_and_fields():
    r = _client().get("/version")
    assert r.status_code == 200
    body = r.json()
    _assert_snake_case_keys(body)
    assert body["app_version"] == "9.9.9"
    # 주입된 build 메타가 노출되고 build_time 은 ISO 8601 UTC 포맷이다.
    assert body["build_sha"] == "abc1234"
    assert _ISO_UTC.match(body["build_time"])


def test_version_omits_unset_build_meta():
    # build_sha/build_time 미설정 시 키 자체를 노출하지 않는다.
    r = _client(Settings("prod", "1.2.3", None, None)).get("/version")
    body = r.json()
    assert body == {"app_version": "1.2.3"}


def test_metrics_minimal_extensible_shape():
    r = _client().get("/metrics")
    assert r.status_code == 200
    body = r.json()
    _assert_snake_case_keys(body)
    assert body["app_version"] == "9.9.9"
    # uptime 은 duration(숫자) — timestamp 가 아니므로 epoch 금지 규칙과 무관.
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0
    # server_time 은 ISO 8601 UTC 시각이며 실제 파싱 가능해야 한다.
    assert _ISO_UTC.match(body["server_time"])
    datetime.strptime(body["server_time"], "%Y-%m-%dT%H:%M:%SZ")


def test_favicon_request_is_quiet_no_content():
    r = _client().get("/favicon.ico")
    assert r.status_code == 204
    assert r.content == b""


# ── AC2 — 운영 엔드포인트는 root-level(/v1/ 금지) ─────────────────────────

def test_operational_endpoints_are_root_level_not_v1():
    c = _client()
    # 운영 엔드포인트는 root-level 로 존재.
    for p in ("/health", "/version", "/metrics"):
        assert c.get(p).status_code == 200
    # /v1/ 접두는 리소스 엔드포인트(5.3+) 전용 — 운영 엔드포인트에 적용하지 않는다.
    assert c.get("/v1/health").status_code == 404


def test_registered_routes_have_no_v1_operational_paths():
    app = create_app(_FAKE_SETTINGS)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/health", "/version", "/metrics"} <= paths
    # 운영 엔드포인트(health/version/metrics)는 root-level — /v1/ 아래에 두지 않는다.
    # 리소스 엔드포인트 /v1/jobs/* 는 Story 5.3+ 가 추가하므로 그것까지 금지하지 않는다.
    for name in ("health", "version", "metrics"):
        assert f"/v1/{name}" not in paths


# ── AC3 — async 핸들러 ────────────────────────────────────────────────────

def test_operational_handlers_are_async():
    app = create_app(_FAKE_SETTINGS)
    by_path = {
        r.path: r.endpoint
        for r in app.routes
        if getattr(r, "path", None) in {"/health", "/version", "/metrics"}
    }
    assert set(by_path) == {"/health", "/version", "/metrics"}
    for path, endpoint in by_path.items():
        assert inspect.iscoroutinefunction(endpoint), f"{path} handler must be async"


# ── AC2 — 에러 envelope(UPPER_SNAKE code · message_redacted) ──────────────

def _assert_error_envelope(body: dict, *, code: str) -> None:
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert re.match(r"^[A-Z][A-Z0-9_]*$", err["code"]), err["code"]
    assert "message_redacted" in err


def test_not_found_404_envelope():
    r = _client().get("/this-route-does-not-exist")
    assert r.status_code == 404
    _assert_error_envelope(r.json(), code="NOT_FOUND")


def test_validation_error_422_envelope_redacts_input():
    app = create_app(_FAKE_SETTINGS)

    @app.get("/__probe_validate")
    async def _probe_validate(count: int) -> dict:  # int 필수 → 비정수면 422
        return {"count": count}

    c = TestClient(app, raise_server_exceptions=False)
    # secret-shaped 비정수 입력 → RequestValidationError(422).
    r = c.get("/__probe_validate", params={"count": "password=hunter2"})
    assert r.status_code == 422
    _assert_error_envelope(r.json(), code="VALIDATION_ERROR")
    # 입력 값(secret 형)은 응답에 평문으로 남지 않는다.
    assert "hunter2" not in r.text


def test_unhandled_exception_500_envelope_redacts_secret():
    app = create_app(_FAKE_SETTINGS)

    @app.get("/__probe_boom")
    async def _probe_boom() -> dict:
        # 예외 메시지에 secret-shaped 문자열 — redact 로 마스킹되어야 한다.
        raise ValueError("token=supersecretvalue123")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/__probe_boom")
    assert r.status_code == 500
    body = r.json()
    _assert_error_envelope(body, code="INTERNAL_ERROR")
    assert body["error"]["message_redacted"] == "internal server error"
    # 예외 본문 secret 은 응답 어디에도 평문으로 남지 않는다.
    assert "supersecretvalue123" not in r.text


@pytest.mark.parametrize("status_code,code", [(404, "NOT_FOUND")])
def test_http_status_maps_to_upper_snake_code(status_code, code):
    # HTTPException status → HTTPStatus.name(UPPER_SNAKE) 매핑을 직접 확인.
    from http import HTTPStatus

    assert HTTPStatus(status_code).name == code
