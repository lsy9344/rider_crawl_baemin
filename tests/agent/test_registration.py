"""Story 4.2 — 등록 클라이언트(register) + 멱등 + thin CLI wiring 검증.

외부 호출 없음: transport 는 fake(canned/에러), store 는 주입 fake codec + tmp_path.
값은 명백한 가짜값만(``agtok-fake-…``/``regcode-fake-…``/``agent-fake-…``).
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from rider_agent import __version__
from rider_agent.registration import (
    DEFAULT_SERVER_BASE_URL,
    REGISTER_PATH,
    SERVER_URL_ENV,
    HttpTransport,
    MachineInfo,
    RegistrationError,
    TransportError,
    collect_machine_info,
    register_agent,
)
from rider_agent.secure_store import (
    AGENT_TOKEN_REF,
    DpapiSecretStore,
    load_local_agent_identity,
)


def _fake_protect(plaintext: str) -> bytes:
    return bytes(b ^ 0x5A for b in plaintext.encode("utf-8"))


def _fake_unprotect(blob: bytes) -> str:
    return bytes(b ^ 0x5A for b in blob).decode("utf-8")


def _store(tmp_path) -> DpapiSecretStore:
    return DpapiSecretStore(
        tmp_path / "agent_secrets.dpapi.json",
        protect=_fake_protect,
        unprotect=_fake_unprotect,
    )


CANNED = {
    "agent_id": "agent-fake-1",
    "agent_token": "agtok-fake-issued",
    "tenant_scope": {"tenant": "t-fake", "job_types": ["baemin"]},
    "config_version": "cfg-fake-1",
}

_INFO = MachineInfo(
    machine_fingerprint="fp-fake",
    hostname="host-fake",
    os="os-fake",
    agent_version="ver-fake",
)


class FakeTransport:
    """주입 가능한 fake transport: canned 응답 또는 에러. 호출 카운터 보유(멱등 검증용)."""

    def __init__(self, *, response: dict | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, url: str, body: dict) -> dict:
        self.calls.append((url, body))
        if self.error is not None:
            raise self.error
        return dict(self.response or {})


# ──────────────────────────────────────────────────────────────────────────
# AC1 — 등록 + 4값 파싱 + secret/config 분리 저장
# ──────────────────────────────────────────────────────────────────────────


def test_register_posts_five_fields_and_splits_storage(tmp_path):
    transport = FakeTransport(response=CANNED)
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"

    identity = register_agent(
        "regcode-fake-1",
        transport=transport,
        store=store,
        identity_path=identity_path,
        machine_info=_INFO,
    )

    # POST 1회 + 본문 5필드 정확.
    assert len(transport.calls) == 1
    url, body = transport.calls[0]
    assert url.endswith(REGISTER_PATH)
    assert body == {
        "registration_code": "regcode-fake-1",
        "machine_fingerprint": "fp-fake",
        "hostname": "host-fake",
        "os": "os-fake",
        "agent_version": "ver-fake",
    }

    # token → store, 나머지 → agent_config.json.
    assert identity.agent_token == "agtok-fake-issued"
    assert store.resolve(AGENT_TOKEN_REF) == "agtok-fake-issued"
    config = json.loads(identity_path.read_text(encoding="utf-8"))
    assert config == {
        "agent_id": "agent-fake-1",
        "tenant_scope": {"tenant": "t-fake", "job_types": ["baemin"]},
        "config_version": "cfg-fake-1",
    }
    assert "agent_token" not in config
    # 평문 token 부재(config 텍스트).
    assert "agtok-fake-issued" not in identity_path.read_text(encoding="utf-8")


def test_register_missing_response_fields_raises_and_writes_nothing(tmp_path):
    transport = FakeTransport(response={"agent_id": "agent-fake-1"})  # token 등 누락
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"

    with pytest.raises(RegistrationError):
        register_agent(
            "regcode-fake-4",
            transport=transport,
            store=store,
            identity_path=identity_path,
            machine_info=_INFO,
        )
    assert identity_path.exists() is False
    assert store.resolve(AGENT_TOKEN_REF) is None


def test_collect_machine_info_defaults_and_injection():
    info = collect_machine_info()
    for value in (info.machine_fingerprint, info.hostname, info.os, info.agent_version):
        assert isinstance(value, str) and value
    assert info.agent_version == __version__

    overridden = collect_machine_info(
        hostname="h-fake",
        os_name="o-fake",
        agent_version="v-fake",
        machine_fingerprint="fp-fake",
    )
    assert (
        overridden.hostname,
        overridden.os,
        overridden.agent_version,
        overridden.machine_fingerprint,
    ) == ("h-fake", "o-fake", "v-fake", "fp-fake")


# ──────────────────────────────────────────────────────────────────────────
# AC1.2 — 멱등 + 코드 무효/이미 사용 거부(평문 누출 없음, 기존 미변경)
# ──────────────────────────────────────────────────────────────────────────


def test_register_idempotent_does_not_post_when_identity_exists(tmp_path):
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    register_agent(
        "regcode-fake-1",
        transport=FakeTransport(response=CANNED),
        store=store,
        identity_path=identity_path,
        machine_info=_INFO,
    )

    # 유효 identity 가 이미 있으므로 2차 호출은 POST 하지 않고 기존을 반환(코드 미소모).
    second = FakeTransport(response={**CANNED, "agent_token": "agtok-fake-DIFFERENT"})
    identity = register_agent(
        "regcode-fake-2",
        transport=second,
        store=store,
        identity_path=identity_path,
        machine_info=_INFO,
    )
    assert second.calls == []
    assert identity.agent_token == "agtok-fake-issued"  # 덮어쓰지 않음.


def test_register_rejected_code_raises_without_leak_and_no_overwrite(tmp_path):
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    transport = FakeTransport(
        error=TransportError("code already used", status_code=409)
    )

    with pytest.raises(RegistrationError) as excinfo:
        register_agent(
            "regcode-fake-secret-3",
            transport=transport,
            store=store,
            identity_path=identity_path,
            machine_info=_INFO,
        )

    message = str(excinfo.value)
    assert "regcode-fake-secret-3" not in message  # code 평문 미포함.
    assert "409" in message  # 상태코드(비밀 아님)는 surfacing.
    # 기존 identity 미생성/미변경.
    assert load_local_agent_identity(store=store, identity_path=identity_path) is None
    assert identity_path.exists() is False
    assert store.resolve(AGENT_TOKEN_REF) is None


# ──────────────────────────────────────────────────────────────────────────
# AC1 — __main__ 의 register thin wiring (redaction·token/code 미출력·무회귀)
# ──────────────────────────────────────────────────────────────────────────


# NOTE: rider_agent.__main__ 은 함수 내부에서 lazy import 한다. 모듈 상단에서 import 하면
# pytest collection 시점에 __main__ 이 sys.modules 에 올라가, 4.1 의 runpy 테스트
# (runpy.run_module("rider_agent", run_name="__main__"))가 RuntimeWarning 을 낸다(무회귀 유지).
def test_run_register_cli_success_prints_redacted(tmp_path, capsys):
    from rider_agent import __main__ as agent_main

    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    rc = agent_main._run_register(
        ["--code", "regcode-fake-cli-1"],
        transport=FakeTransport(response=CANNED),
        store=store,
        identity_path=identity_path,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "registered" in out
    assert "agent-fake-1" in out  # agent_id 는 비밀 아님.
    assert "agtok-fake-issued" not in out  # token 평문 미출력.
    assert "regcode-fake-cli-1" not in out  # code 평문 미출력.


def test_run_register_cli_already_registered(tmp_path, capsys):
    from rider_agent import __main__ as agent_main

    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    agent_main._run_register(
        ["--code", "regcode-fake-a"],
        transport=FakeTransport(response=CANNED),
        store=store,
        identity_path=identity_path,
    )
    capsys.readouterr()

    rc = agent_main._run_register(
        ["--code", "regcode-fake-b"],
        transport=FakeTransport(response=CANNED),
        store=store,
        identity_path=identity_path,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "already-registered" in out


def test_run_register_cli_failure_returns_1_without_leak(tmp_path, capsys):
    from rider_agent import __main__ as agent_main

    rc = agent_main._run_register(
        ["--code", "regcode-fake-cli-fail"],
        transport=FakeTransport(error=TransportError("nope", status_code=409)),
        store=_store(tmp_path),
        identity_path=tmp_path / "agent_config.json",
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "failed" in out
    assert "regcode-fake-cli-fail" not in out


def test_main_routes_register_subcommand(monkeypatch):
    from rider_agent import __main__ as agent_main

    captured: dict[str, list[str]] = {}

    def fake_run(register_argv, **_kwargs):
        captured["argv"] = register_argv
        return 7

    monkeypatch.setattr(agent_main, "_run_register", fake_run)
    assert agent_main.main(["register", "--code", "regcode-fake"]) == 7
    assert captured["argv"] == ["--code", "regcode-fake"]


def test_main_without_subcommand_prints_banner(capsys):
    from rider_agent import __main__ as agent_main

    assert agent_main.main([]) == 0
    assert "sync runtime" in capsys.readouterr().out


def test_main_ignores_stray_args_falls_back_to_banner(capsys):
    from rider_agent import __main__ as agent_main

    # runpy-under-pytest 가 argv 를 오염시켜도 'register' 가 아니면 배너로 폴백(무회귀).
    assert agent_main.main(["-q", "tests/agent/x.py"]) == 0
    assert "sync runtime" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════════════
# QA gap coverage (qa-generate-e2e-tests) — 기존 4.2 케이스가 비운 명시 요구사항 보강.
#   G1: HttpTransport(실 stdlib urllib transport) — 기존엔 전부 fake 라 미검증 (AC1)
#   G2: _register_url base_url/env override·trailing-slash (AC1)
#   G3: _identity_from_response 보안 엣지(빈 token/agent_id·coercion) (AC1·AC2)
#   G4: register_agent 빈/공백 code → RegistrationError(POST 없음) (AC1)
#   G8: CLI --code 필수 → SystemExit (Task 3)
# ══════════════════════════════════════════════════════════════════════════


class _FakeHttpResponse:
    """urllib ``urlopen`` 이 돌려주는 context-manager 응답의 최소 fake(``read()`` 만)."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._raw


# G1 — HttpTransport happy path: JSON body·헤더·메서드·타임아웃이 올바르고 2xx dict 반환.
def test_http_transport_posts_json_and_returns_dict():
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["content_type"] = request.get_header("Content-type")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeHttpResponse(json.dumps({"agent_id": "agent-fake-1"}).encode("utf-8"))

    transport = HttpTransport(urlopen=fake_urlopen, timeout_seconds=5)
    result = transport.post_json(
        "https://localhost/v1/agents/register",
        {"registration_code": "regcode-fake-http"},
    )

    assert result == {"agent_id": "agent-fake-1"}
    assert captured["method"] == "POST"
    assert captured["content_type"] == "application/json"
    assert captured["body"] == {"registration_code": "regcode-fake-http"}
    assert captured["timeout"] == 5


# G1 — 4xx/5xx 는 상태코드만 surfacing(본문은 읽지 않아 secret 누출 없음).
@pytest.mark.parametrize("status", [400, 404, 409, 500])
def test_http_transport_http_error_surfaces_status_code(status):
    def fake_urlopen(request, timeout=None):
        raise HTTPError("https://localhost/x", status, "boom", {}, None)

    transport = HttpTransport(urlopen=fake_urlopen)
    with pytest.raises(TransportError) as excinfo:
        transport.post_json("https://localhost/x", {"registration_code": "regcode-fake"})
    assert excinfo.value.status_code == status


# G1 — 연결 실패(URLError)는 상태코드 없는 TransportError.
def test_http_transport_url_error_becomes_transport_error_without_status():
    def fake_urlopen(request, timeout=None):
        raise URLError("connection refused")

    transport = HttpTransport(urlopen=fake_urlopen)
    with pytest.raises(TransportError) as excinfo:
        transport.post_json("https://localhost/x", {})
    assert excinfo.value.status_code is None


# G1 — 응답이 JSON 이 아니면 TransportError.
def test_http_transport_non_json_response_becomes_transport_error():
    def fake_urlopen(request, timeout=None):
        return _FakeHttpResponse(b"<html>not json</html>")

    transport = HttpTransport(urlopen=fake_urlopen)
    with pytest.raises(TransportError):
        transport.post_json("https://localhost/x", {})


# G1 — JSON 이지만 object(dict) 가 아니면 TransportError.
def test_http_transport_non_object_json_becomes_transport_error():
    def fake_urlopen(request, timeout=None):
        return _FakeHttpResponse(b"[1, 2, 3]")

    transport = HttpTransport(urlopen=fake_urlopen)
    with pytest.raises(TransportError):
        transport.post_json("https://localhost/x", {})


# G1 — E2E: 실 HttpTransport(fake urlopen) 를 통해 register_agent 가 분리 저장까지 수행.
def test_register_agent_end_to_end_through_http_transport(tmp_path):
    def fake_urlopen(request, timeout=None):
        return _FakeHttpResponse(json.dumps(CANNED).encode("utf-8"))

    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    identity = register_agent(
        "regcode-fake-e2e",
        transport=HttpTransport(urlopen=fake_urlopen),
        store=store,
        identity_path=identity_path,
        machine_info=_INFO,
        base_url="https://example.test",
    )

    assert identity.agent_id == "agent-fake-1"
    assert store.resolve(AGENT_TOKEN_REF) == "agtok-fake-issued"
    # token 평문이 config 텍스트에 없음(E2E 경로에서도 분리 불변식 성립).
    assert "agtok-fake-issued" not in identity_path.read_text(encoding="utf-8")


class _UrlCapturingTransport:
    """URL 만 캡처하고 canned 응답을 주는 transport(URL 빌드 검증용)."""

    def __init__(self) -> None:
        self.url: str | None = None

    def post_json(self, url: str, body: dict) -> dict:
        self.url = url
        return dict(CANNED)


# G2 — base_url 주입 시 trailing-slash 를 떼고 REGISTER_PATH 를 붙인다.
def test_register_url_uses_injected_base_url_and_strips_trailing_slash(tmp_path):
    transport = _UrlCapturingTransport()
    register_agent(
        "regcode-fake-url",
        transport=transport,
        store=_store(tmp_path),
        identity_path=tmp_path / "agent_config.json",
        machine_info=_INFO,
        base_url="https://srv.test/",
    )
    assert transport.url == "https://srv.test" + REGISTER_PATH


# G2 — base_url 미주입 시 환경변수(RIDER_AGENT_SERVER_URL)로 폴백.
def test_register_url_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv(SERVER_URL_ENV, "https://env.test")
    transport = _UrlCapturingTransport()
    register_agent(
        "regcode-fake-env",
        transport=transport,
        store=_store(tmp_path),
        identity_path=tmp_path / "agent_config.json",
        machine_info=_INFO,
    )
    assert transport.url == "https://env.test" + REGISTER_PATH


# G2 — base_url·env 둘 다 없으면 기본 placeholder 로.
def test_register_url_uses_default_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(SERVER_URL_ENV, raising=False)
    transport = _UrlCapturingTransport()
    register_agent(
        "regcode-fake-default",
        transport=transport,
        store=_store(tmp_path),
        identity_path=tmp_path / "agent_config.json",
        machine_info=_INFO,
    )
    assert transport.url == DEFAULT_SERVER_BASE_URL + REGISTER_PATH


# G4 — 빈/공백 code 는 POST 전에 RegistrationError(코드 소모 없음).
def test_register_blank_code_raises_without_posting(tmp_path):
    transport = FakeTransport(response=CANNED)
    with pytest.raises(RegistrationError):
        register_agent(
            "   ",
            transport=transport,
            store=_store(tmp_path),
            identity_path=tmp_path / "agent_config.json",
            machine_info=_INFO,
        )
    assert transport.calls == []


# G3 — 응답 token 이 빈 문자열이면 거부(빈 token 을 유효 identity 로 받아들이지 않음).
def test_register_empty_token_in_response_raises_and_writes_nothing(tmp_path):
    transport = FakeTransport(response={**CANNED, "agent_token": ""})
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    with pytest.raises(RegistrationError):
        register_agent(
            "regcode-fake-empty-token",
            transport=transport,
            store=store,
            identity_path=identity_path,
            machine_info=_INFO,
        )
    assert identity_path.exists() is False
    assert store.resolve(AGENT_TOKEN_REF) is None


# G3 — 응답 agent_id 가 빈 문자열이면 거부.
def test_register_empty_agent_id_in_response_raises(tmp_path):
    transport = FakeTransport(response={**CANNED, "agent_id": ""})
    with pytest.raises(RegistrationError):
        register_agent(
            "regcode-fake-empty-id",
            transport=transport,
            store=_store(tmp_path),
            identity_path=tmp_path / "agent_config.json",
            machine_info=_INFO,
        )


# G3 — tenant_scope 가 dict 아님 → {} 로, config_version 이 비-문자열 → str() 로 coerce.
def test_register_coerces_nondict_tenant_scope_and_nonstr_config_version(tmp_path):
    transport = FakeTransport(
        response={**CANNED, "tenant_scope": "not-a-dict", "config_version": 7}
    )
    identity = register_agent(
        "regcode-fake-coerce",
        transport=transport,
        store=_store(tmp_path),
        identity_path=tmp_path / "agent_config.json",
        machine_info=_INFO,
    )
    assert identity.tenant_scope == {}
    assert identity.config_version == "7"


# G8 — CLI 는 --code 가 필수다(argparse) → 누락 시 SystemExit.
def test_run_register_requires_code_arg(tmp_path):
    from rider_agent import __main__ as agent_main

    with pytest.raises(SystemExit):
        agent_main._run_register(
            [],
            transport=FakeTransport(response=CANNED),
            store=_store(tmp_path),
            identity_path=tmp_path / "agent_config.json",
        )
