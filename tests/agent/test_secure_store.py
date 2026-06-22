"""Story 4.2 — Agent-local DPAPI secure store + identity 영속 + token 게이트 검증.

외부 호출 없음(실 DPAPI 는 Windows skipif 단일 테스트로만, 그 외엔 주입 fake codec + tmp_path).
값은 명백한 가짜값만(``agtok-fake-…``/``regcode-fake-…``/``agent-fake-…``) — 실 토큰/코드 금지.
"""

from __future__ import annotations

import json
import sys

import pytest

from rider_agent.secure_store import (
    AGENT_TOKEN_REF,
    TOKEN_STATUS_MISSING,
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_VALID,
    AgentIdentity,
    DpapiSecretStore,
    load_local_agent_identity,
    save_agent_identity,
    validate_agent_token,
)
from rider_crawl.redaction import REDACTED, redact
from rider_crawl.secret_store import SecretStore


# ── 주입 fake codec: 비-Windows 에서도 결정적 round-trip. XOR 라 store 파일엔 평문이 남지 않는다.
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


# ──────────────────────────────────────────────────────────────────────────
# AC2 — DPAPI 백엔드가 SecretStore Protocol 을 만족(재발명 아님) + 평문 비노출
# ──────────────────────────────────────────────────────────────────────────


def test_dpapi_store_put_resolve_round_trip(tmp_path):
    store = _store(tmp_path)
    ref = store.put("agtok-fake-roundtrip")
    assert store.resolve(ref) == "agtok-fake-roundtrip"
    # store 파일엔 평문이 아니라 인코딩 blob 만 — 평문 token 부재.
    assert "agtok-fake-roundtrip" not in store.path.read_text(encoding="utf-8")


def test_dpapi_store_satisfies_secret_store_protocol(tmp_path):
    # rider_crawl 의 SecretStore seam 을 그대로 구현(새 인터페이스 아님). 같은 put/resolve 계약.
    store = _store(tmp_path)

    def use(seam: SecretStore) -> str | None:
        return seam.resolve(seam.put("agtok-fake-proto", ref=AGENT_TOKEN_REF))

    assert use(store) == "agtok-fake-proto"


def test_dpapi_store_resolve_missing_is_fail_closed(tmp_path):
    store = _store(tmp_path)
    assert store.resolve("local:nope") is None
    assert store.resolve("") is None


def test_dpapi_store_idempotent_unchanged_value_no_rewrite(tmp_path):
    store = _store(tmp_path)
    store.put("agtok-fake-x", ref="r1")
    before = store.path.read_bytes()
    store.put("agtok-fake-x", ref="r1")
    assert store.path.read_bytes() == before


def test_dpapi_store_same_ref_new_value_updates(tmp_path):
    store = _store(tmp_path)
    store.put("agtok-fake-old", ref="r1")
    store.put("agtok-fake-new", ref="r1")
    assert store.resolve("r1") == "agtok-fake-new"
    # 재오픈(디스크 정본)도 새 값.
    reopened = DpapiSecretStore(
        store.path, protect=_fake_protect, unprotect=_fake_unprotect
    )
    assert reopened.resolve("r1") == "agtok-fake-new"


def test_token_never_appears_plaintext_in_config_or_store(tmp_path):
    # AC2 핵심 불변식: 등록 후 agent_config.json 텍스트와 store 파일 텍스트 어디에도 평문 token 없음.
    fake_token = "agtok-fake-separation-invariant"
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    identity = AgentIdentity(
        agent_id="agent-fake-7",
        agent_token=fake_token,
        tenant_scope={"tenant": "t-fake", "job_types": ["baemin"]},
        config_version="cfg-fake-3",
    )

    save_agent_identity(identity, store=store, identity_path=identity_path)

    config_text = identity_path.read_text(encoding="utf-8")
    store_text = store.path.read_text(encoding="utf-8")
    # 물리 분리: 두 파일은 다른 경로.
    assert store.path != identity_path
    # 평문 token 부재(양쪽).
    assert fake_token not in config_text
    assert fake_token not in store_text
    # config 엔 비밀 아님 값만(token 키 자체가 없음).
    config = json.loads(config_text)
    assert config == {
        "agent_id": "agent-fake-7",
        "tenant_scope": {"tenant": "t-fake", "job_types": ["baemin"]},
        "config_version": "cfg-fake-3",
    }
    assert "agent_token" not in config
    # round-trip: token 은 store 에서만 복원.
    loaded = load_local_agent_identity(store=store, identity_path=identity_path)
    assert loaded is not None
    assert loaded.agent_token == fake_token
    assert loaded.agent_id == "agent-fake-7"
    assert loaded.tenant_scope == {"tenant": "t-fake", "job_types": ["baemin"]}
    # atomic write — .tmp 잔여물 0.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_store_file_is_separate_from_config_file(tmp_path):
    store = _store(tmp_path)
    identity_path = tmp_path / "agent_config.json"
    identity = AgentIdentity(
        agent_id="agent-fake-1",
        agent_token="agtok-fake-sep",
        tenant_scope={},
        config_version="v",
    )
    save_agent_identity(identity, store=store, identity_path=identity_path)

    assert store.path != identity_path
    assert store.path.exists()
    assert identity_path.exists()


def test_agent_identity_repr_redacts_token():
    fake_token = "agtok-fake-repr"
    identity = AgentIdentity(
        agent_id="agent-fake-1",
        agent_token=fake_token,
        tenant_scope={"tenant": "t-fake"},
        config_version="v-fake-1",
    )
    text = repr(identity)
    assert fake_token not in text
    assert REDACTED in text
    assert "agent-fake-1" in text  # 비밀 아님 식별자는 보존.


def test_token_is_redacted_in_log_text():
    # 로그/배너 출력은 redact 를 통과해 token 평문이 남지 않는다(1건 단언).
    fake_token = "agtok-fake-please-redact"
    masked = redact(f"connecting with agent_token={fake_token}")
    assert fake_token not in masked
    assert REDACTED in masked


# ──────────────────────────────────────────────────────────────────────────
# AC1/AC3 — identity 로드(미등록 판정)
# ──────────────────────────────────────────────────────────────────────────


def test_load_identity_none_when_token_missing(tmp_path):
    # config 만 있고 store 에 token 없음 → None(미등록).
    identity_path = tmp_path / "agent_config.json"
    identity_path.write_text(
        json.dumps(
            {"agent_id": "agent-fake-1", "tenant_scope": {}, "config_version": "v"}
        ),
        encoding="utf-8",
    )
    assert load_local_agent_identity(store=_store(tmp_path), identity_path=identity_path) is None


def test_load_identity_none_when_config_missing(tmp_path):
    assert (
        load_local_agent_identity(
            store=_store(tmp_path), identity_path=tmp_path / "nope.json"
        )
        is None
    )


# ──────────────────────────────────────────────────────────────────────────
# AC3 — token 유효성 게이트 primitive (NFR-7, FR-16)
# ──────────────────────────────────────────────────────────────────────────


def test_validate_token_missing_when_no_identity():
    result = validate_agent_token(None)
    assert result.status == TOKEN_STATUS_MISSING
    assert result.can_receive_jobs is False
    assert result.needs_registration is True


def test_validate_token_missing_when_blank_token():
    identity = AgentIdentity(agent_id="agent-fake-1", agent_token="   ")
    result = validate_agent_token(identity)
    assert result.status == TOKEN_STATUS_MISSING
    assert result.can_receive_jobs is False


def test_validate_token_valid_on_local_presence():
    identity = AgentIdentity(agent_id="agent-fake-1", agent_token="agtok-fake-1")
    result = validate_agent_token(identity)
    assert result.status == TOKEN_STATUS_VALID
    assert result.can_receive_jobs is True
    assert result.needs_registration is False


def test_validate_token_revoked_when_server_check_false():
    # stub transport 가 401/revoked → 게이트가 미수신으로 떨어지고 재등록 필요로 surfacing.
    identity = AgentIdentity(agent_id="agent-fake-1", agent_token="agtok-fake-1")
    result = validate_agent_token(identity, server_check=lambda _identity: False)
    assert result.status == TOKEN_STATUS_REVOKED
    assert result.can_receive_jobs is False
    assert result.needs_registration is True


def test_validate_token_valid_when_server_check_true():
    identity = AgentIdentity(agent_id="agent-fake-1", agent_token="agtok-fake-1")
    result = validate_agent_token(identity, server_check=lambda _identity: True)
    assert result.can_receive_jobs is True


# ──────────────────────────────────────────────────────────────────────────
# AC2 — 실제 DPAPI round-trip(Windows 전용). 비-Windows 는 skip.
# ──────────────────────────────────────────────────────────────────────────


def test_default_dpapi_codec_is_windows_gated_at_call_time(tmp_path, monkeypatch):
    # import-safety 근거: 기본 codec(실제 DPAPI)은 함수 내부 lazy + Windows-gated 라, 비-Windows
    # 로 강제하면 import 가 아니라 **호출 시점**에 명확한 RuntimeError 를 낸다. 즉 crypt32 미탑재
    # 환경에서도 `import rider_agent.secure_store` 자체는 항상 안전하다.
    monkeypatch.setattr(sys, "platform", "linux")
    store = DpapiSecretStore(tmp_path / "real.dpapi.json")  # 기본(실제) codec
    with pytest.raises(RuntimeError):
        store.put("agtok-fake-gated")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI는 Windows 전용")
def test_dpapi_real_round_trip_on_windows(tmp_path):
    # 기본 codec(실제 crypt32). tmp_path 라 실 OS state 에 영속 쓰기 없음.
    store = DpapiSecretStore(tmp_path / "real.dpapi.json")
    ref = store.put("agtok-fake-windows-roundtrip")
    assert store.resolve(ref) == "agtok-fake-windows-roundtrip"
    text = store.path.read_text(encoding="utf-8")
    assert "agtok-fake-windows-roundtrip" not in text  # 암호화 blob, 평문 없음.


# ══════════════════════════════════════════════════════════════════════════
# QA gap coverage (qa-generate-e2e-tests) — 기존 4.2 케이스가 비운 명시 요구사항 보강.
#   G5: DpapiSecretStore fail-closed(손상/타-머신 blob·손상 JSON → None) (AC2·AC3)
#   G6: load_agent_config/load_local_agent_identity 손상 config·빈 agent_id → None (AC1·AC3)
#   G7: 프로덕션 기본 경로의 분리 불변식(identity ≠ secret store, 같은 state dir) (AC2)
# ══════════════════════════════════════════════════════════════════════════


# G5 — 다른 머신/손상 blob: unprotect 가 실패하면 예외 전파 없이 fail-closed(None).
def test_dpapi_store_resolve_fail_closed_on_undecryptable_blob(tmp_path):
    path = tmp_path / "agent_secrets.dpapi.json"
    DpapiSecretStore(path, protect=_fake_protect, unprotect=_fake_unprotect).put(
        "agtok-fake-x", ref="r1"
    )

    def _raising_unprotect(_blob: bytes) -> str:
        raise ValueError("cannot decrypt blob from another machine")

    broken = DpapiSecretStore(path, protect=_fake_protect, unprotect=_raising_unprotect)
    assert broken.resolve("r1") is None  # 재등록 필요로 떨어진다(크래시 아님).


# G5 — store 파일 JSON 이 손상돼도 resolve 는 None(크래시 아님).
def test_dpapi_store_corrupt_json_is_fail_closed(tmp_path):
    path = tmp_path / "agent_secrets.dpapi.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    store = DpapiSecretStore(path, protect=_fake_protect, unprotect=_fake_unprotect)
    assert store.resolve("r1") is None


# G6 — agent_config.json 이 손상되면 load_agent_config → None.
def test_load_agent_config_corrupt_returns_none(tmp_path):
    from rider_agent.secure_store import load_agent_config

    path = tmp_path / "agent_config.json"
    path.write_text("{ broken json", encoding="utf-8")
    assert load_agent_config(path) is None


# G6 — 손상 config + 유효 token 이어도 identity 는 None(미등록 취급, 무-크래시).
def test_load_identity_none_when_config_corrupt(tmp_path):
    path = tmp_path / "agent_config.json"
    path.write_text("{ broken json", encoding="utf-8")
    store = _store(tmp_path)
    store.put("agtok-fake-x", ref=AGENT_TOKEN_REF)
    assert load_local_agent_identity(store=store, identity_path=path) is None


# G6 — config 의 agent_id 가 빈 문자열이면 identity 는 None.
def test_load_identity_none_when_agent_id_empty(tmp_path):
    path = tmp_path / "agent_config.json"
    path.write_text(
        json.dumps({"agent_id": "", "tenant_scope": {}, "config_version": "v"}),
        encoding="utf-8",
    )
    store = _store(tmp_path)
    store.put("agtok-fake-x", ref=AGENT_TOKEN_REF)
    assert load_local_agent_identity(store=store, identity_path=path) is None


# G7 — 프로덕션 기본 경로도 identity ≠ secret store(핵심 분리 불변식). app_state_root 격리.
def test_default_paths_separate_identity_and_secret_store(tmp_path, monkeypatch):
    import rider_agent.secure_store as ss
    from rider_agent.secure_store import (
        IDENTITY_FILENAME,
        SECRET_STORE_FILENAME,
        default_agent_state_dir,
        default_identity_path,
        default_secret_store_path,
    )

    monkeypatch.setattr(ss, "app_state_root", lambda: tmp_path)

    state_dir = default_agent_state_dir()
    identity = default_identity_path()
    secret = default_secret_store_path()

    assert identity != secret  # 분리 불변식(프로덕션 기본 경로).
    assert identity.name == IDENTITY_FILENAME
    assert secret.name == SECRET_STORE_FILENAME
    assert identity.parent == state_dir == secret.parent
    assert state_dir == tmp_path / "runtime" / "state" / "agent"
