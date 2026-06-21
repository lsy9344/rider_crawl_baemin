"""로컬 secret store seam + 분류 매핑 단위 테스트 (Story 2.4 / P1-06, NFR-8).

순수 객체/파일 I/O 단위 테스트라 외부 서비스 호출이 없고, tmp_path만 쓴다(실 runtime/·
secrets.local.json 미변형). 값은 명백한 가짜값(tok-fake/pw-fake/id-fake)만 쓴다(A1 게이트).
"""

from rider_crawl import secret_store
from rider_crawl.secret_store import (
    SECRET_STORAGE_AGENT_LOCAL,
    SECRET_STORAGE_CENTRAL,
    SECRET_STORAGE_NOT_STORED,
    LocalFileSecretStore,
    classify_secret_storage,
)
from rider_crawl.ui_settings import _SECRET_FIELDS


# ── AC4: secret 저장 위치 3분류 매핑 ──


def test_classification_maps_secret_kinds_to_three_buckets():
    # 텔레그램 봇 토큰=중앙, 쿠팡 password·login-id=Agent-local,
    # 인증 이메일 주소/앱 비밀번호=Agent-local, OTP=비저장.
    assert classify_secret_storage("telegram_bot_token") == SECRET_STORAGE_CENTRAL
    assert classify_secret_storage("coupang_login_password") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("coupang_login_id") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("verification_email_address") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("verification_email_app_password") == SECRET_STORAGE_AGENT_LOCAL
    assert classify_secret_storage("otp") == SECRET_STORAGE_NOT_STORED


def test_classification_values_are_exactly_three_distinct_strings():
    # enum이 아니라 단순 문자열 3종(2.5가 정식 enum으로 승격). 값이 정확히 3분류여야 한다.
    assert {
        SECRET_STORAGE_CENTRAL,
        SECRET_STORAGE_AGENT_LOCAL,
        SECRET_STORAGE_NOT_STORED,
    } == {"central", "agent_local", "not_stored"}


# ── AC6/7: LocalFileSecretStore put/resolve seam ──


def test_put_resolve_round_trip(tmp_path):
    store = LocalFileSecretStore(tmp_path / "store.json")
    ref = store.put("tok-fake")
    assert store.resolve(ref) == "tok-fake"


def test_put_with_explicit_ref_is_stable_and_deterministic(tmp_path):
    # 같은 (값, ref)로 두 번 put해도 같은 ref가 유지된다(재로드/재정렬 안정 — dedup/diff 안정).
    store = LocalFileSecretStore(tmp_path / "store.json")
    ref1 = store.put("tok-fake", ref="local:mt-1:telegram_bot_token")
    ref2 = store.put("tok-fake", ref="local:mt-1:telegram_bot_token")
    assert ref1 == ref2 == "local:mt-1:telegram_bot_token"
    assert store.resolve(ref1) == "tok-fake"


def test_put_without_ref_is_content_deterministic(tmp_path):
    # ref를 주지 않으면 내용 기반 결정적 핸들 — 같은 입력 같은 ref.
    store = LocalFileSecretStore(tmp_path / "store.json")
    assert store.put("pw-fake") == store.put("pw-fake")


def test_resolve_missing_ref_returns_none_fail_closed(tmp_path):
    # 없는 ref/빈 ref → None(fail-closed). 호출부가 빈 평문으로 안전 처리한다.
    store = LocalFileSecretStore(tmp_path / "store.json")
    assert store.resolve("local:nope:telegram_bot_token") is None
    assert store.resolve("") is None


def test_store_file_is_separate_from_settings_file(tmp_path):
    # store 파일은 ui_settings.json과 **다른 경로**여야 한다(설정 파일에 평문 잔존 0의 전제).
    settings_path = tmp_path / "ui_settings.json"
    store = LocalFileSecretStore(tmp_path / "secrets.local.json")
    store.put("tok-fake", ref="local:mt-1:telegram_bot_token")

    assert store.path != settings_path
    assert store.path.exists()
    assert settings_path.exists() is False  # store는 설정 파일을 건드리지 않는다


def test_put_survives_reopen_atomic_no_temp_leftover(tmp_path):
    # 여러 ref를 별도 인스턴스로 써도 보존되고(atomic write로 손상 없음), .tmp 잔여물이 없다.
    path = tmp_path / "store.json"
    LocalFileSecretStore(path).put("tok-fake", ref="r1")
    LocalFileSecretStore(path).put("pw-fake", ref="r2")

    reopened = LocalFileSecretStore(path)
    assert reopened.resolve("r1") == "tok-fake"
    assert reopened.resolve("r2") == "pw-fake"
    leftovers = [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_put_idempotent_does_not_rewrite_when_value_unchanged(tmp_path):
    # 같은 값 재put은 store 파일을 다시 쓰지 않는다(불필요한 churn 방지).
    path = tmp_path / "store.json"
    store = LocalFileSecretStore(path)
    store.put("tok-fake", ref="r1")
    before = path.read_bytes()

    store.put("tok-fake", ref="r1")

    assert path.read_bytes() == before


# ── QA gap: store-layer 회전(같은 ref, 값 변경) + OTP 비저장 잠금 ──


def test_put_with_reused_ref_updates_stored_value(tmp_path):
    # GAP(AC6): 멱등(값 동일)은 기존 테스트가 잠갔지만, **같은 ref에 다른 값**을 put하면 store가
    # 새 값으로 갱신해야 한다(반쪽 마이그레이션 재이관·secret 회전의 store 레이어 보장).
    path = tmp_path / "store.json"
    store = LocalFileSecretStore(path)
    store.put("pw-old", ref="r1")

    store.put("pw-new", ref="r1")

    assert store.resolve("r1") == "pw-new"
    # 재오픈(디스크 정본)도 새 값이어야 한다.
    assert LocalFileSecretStore(path).resolve("r1") == "pw-new"


def test_default_secret_store_uses_windows_protected_store_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store.sys, "platform", "win32")
    monkeypatch.delenv("RIDER_CRAWL_SECRET_STORE", raising=False)

    store = secret_store.default_secret_store(tmp_path / "secrets.local.json")

    assert store.__class__.__name__ == "WindowsDpapiSecretStore"


def test_file_secret_store_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store.sys, "platform", "win32")

    monkeypatch.delenv("RIDER_CRAWL_SECRET_STORE", raising=False)
    default_store = secret_store.default_secret_store(tmp_path / "default.json")
    assert not isinstance(default_store, LocalFileSecretStore)

    monkeypatch.setenv("RIDER_CRAWL_SECRET_STORE", "local_file")
    opted_in = secret_store.default_secret_store(tmp_path / "file.json")
    assert isinstance(opted_in, LocalFileSecretStore)


def test_local_file_secret_store_restricts_file_permissions(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    calls = []

    def _fake_restrict(written_path):
        calls.append(written_path)

    monkeypatch.setattr(secret_store, "_restrict_file_to_current_user", _fake_restrict)

    LocalFileSecretStore(path).put("pw-fake", ref="r1")

    assert calls == [path]


def test_otp_is_not_stored_and_excluded_from_store_handled_fields():
    # GAP(AC2/Task1): OTP/2FA 코드는 **비저장** 분류이고, store가 영속하는 secret 필드 집합
    # (_SECRET_FIELDS)에 포함되지 않아야 한다(store는 token/password/login-id만 다룬다).
    assert classify_secret_storage("otp") == SECRET_STORAGE_NOT_STORED
    assert "otp" not in _SECRET_FIELDS
    assert set(_SECRET_FIELDS) == {
        "telegram_bot_token",
        "coupang_login_password",
        "coupang_login_id",
        "verification_email_address",
        "verification_email_app_password",
    }
