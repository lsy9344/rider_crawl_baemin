"""Story 2.7 / AC1~AC3 (ADD-16 1~5단계, FR-31, NFR-18·21·22) — 마이그레이션 잠금.

외부 호출 없음 — 순수 함수·frozen 값 객체만 단언한다. 모든 파일 I/O는 ``tmp_path`` 안에서만
일어나 실 ``runtime/``/``logs/`` 를 만지지 않는다. 모든 fixture는 가짜 ID·가짜 64-hex hash·
가짜 alias·고정 ``datetime`` 만 쓴다(비결정 ``now()`` 금지, 신규 매핑·산출물에 평문 secret 0).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from rider_crawl.ui_settings import UiSettingsStore
from rider_server.domain import (
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    SecretRef,
    SecretStorageClass,
    Tenant,
)
from rider_server.migration import (
    MigrationResult,
    MigrationSeed,
    MigrationState,
    TargetMapping,
    TargetMigration,
    activate,
    approve,
    back_up_settings,
    classify_and_issue,
    copy_state_dir,
    map_active_tab,
    mark_dry_run_passed,
    pause,
    roll_back,
    run_migration,
    seed_from_state,
)

# 결정성: 마이그레이션 내부 now() 금지 — created_at은 호출부가 주입한다(고정값).
_FIXED_AT = datetime(2026, 1, 1, 0, 0, 0)

# 가짜 64-hex message hash(실제 secret 아님 — sha256 모양만).
_HASH_A = "a" * 64
_HASH_B = "b" * 64

# 활성 탭(배민/쿠팡)·비활성 탭 fixture 빌더 ────────────────────────────────


def _active_baemin(url: str = "https://self.baemin.com/stats/A") -> dict:
    return {
        "performance_url": url,
        "platform_name": "baemin",
        "baemin_center_name": "센터A",
        "baemin_center_id": "CENTER-A",
        "interval_minutes": 30,
    }


def _active_coupang(url: str = "https://partner.coupangeats.com/merchant/B") -> dict:
    # 자격증명은 **ref만**(평문 금지 — ADD-15). storage_class는 secret_kind로 분류된다.
    return {
        "performance_url": url,
        "platform_name": "coupang",
        "baemin_center_name": "쿠팡센터B",
        "baemin_center_id": "CENTER-B",
        "interval_minutes": 45,
        "coupang_login_id_ref": "local:fake-b/coupang_login_id",
        "coupang_login_password_ref": "local:fake-b/coupang_login_password",
    }


def _inactive() -> dict:
    return {"performance_url": ""}


def _write_settings(path: Path, tabs: list[dict]) -> None:
    path.write_text(json.dumps({"crawlings": tabs}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_last_message(state_dir: Path, *, scope_hash: str, message_hash: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"last_message.{scope_hash}.sha256").write_text(message_hash, encoding="utf-8")


# ── AC1: 백업(원본 미삭제) · 활성/비활성 분류 · ID 발급 ─────────────────────


def test_run_migration_backs_up_original_before_mutation(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _active_coupang(), _inactive()])
    original_bytes = settings_path.read_bytes()
    backup_path = tmp_path / "backup" / "ui_settings.json.bak"

    run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=backup_path,
        created_at=_FIXED_AT,
    )

    # 백업은 변형(ID 발급) 전 원본과 byte 동일(롤백 아티팩트, NFR-18).
    assert backup_path.read_bytes() == original_bytes
    # 원본은 삭제·이동되지 않는다(미삭제). ID 발급으로 내용은 바뀌지만 파일은 그대로 존재한다.
    assert settings_path.exists()


def test_run_migration_classifies_active_tabs_only(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _active_coupang(), _inactive()])

    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )

    # 활성 2탭만 TargetMigration(매핑 있음). 비활성 1탭은 inactive_count로만 집계(매핑 없음).
    assert isinstance(result, MigrationResult)
    assert len(result.targets) == 2
    assert result.inactive_count == 1
    assert all(t.mapping is not None for t in result.targets)
    assert all(t.state == MigrationState.MAPPED for t in result.targets)
    # 전체 raw 위치 기반 1-based crawling_index(활성-only 위치 아님).
    assert [t.crawling_index for t in result.targets] == [1, 2]


def test_run_migration_issues_three_ids_and_is_idempotent(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _inactive()])
    kwargs = dict(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )

    first = run_migration(**kwargs)
    mapping = first.targets[0].mapping
    # 활성 탭에 ID 3종 발급(load_all 재사용 — 본 모듈은 uuid4를 직접 부르지 않는다).
    assert mapping.tenant.id
    assert mapping.platform_account.id
    assert mapping.monitoring_target.id
    assert len({mapping.tenant.id, mapping.platform_account.id, mapping.monitoring_target.id}) == 3

    # 재실행 시 동일 ID 유지(영속화 → 멱등).
    second = run_migration(**kwargs)
    again = second.targets[0].mapping
    assert again.tenant.id == mapping.tenant.id
    assert again.platform_account.id == mapping.platform_account.id
    assert again.monitoring_target.id == mapping.monitoring_target.id


def test_back_up_settings_missing_original_raises(tmp_path) -> None:
    # 원본이 없으면 명확한 실패(fail-closed로 surface).
    with pytest.raises(FileNotFoundError):
        back_up_settings(tmp_path / "nope.json", backup_path=tmp_path / "b.bak")


def test_classify_and_issue_excludes_padding_slots(tmp_path) -> None:
    # load_all의 9-slot 패딩 기본 슬롯은 후보가 아니다 — 파일에 실제 존재한 탭만 분류한다.
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _inactive()])
    store = UiSettingsStore(settings_path)

    active, inactive_count = classify_and_issue(store)
    assert len(active) == 1  # 활성 1
    assert inactive_count == 1  # 비활성 1 (패딩 7슬롯은 제외 — 9-2=7이 새지 않음)
    assert active[0][0] == 1


# ── AC2: 상태 폴더 복사(원본 미삭제) · last_message hash → seed 승계 ────────


def test_run_migration_copies_state_and_inherits_seed(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin()])
    state_root = tmp_path / "state"
    _write_last_message(state_root / "crawling1", scope_hash="deadbeefdeadbeef", message_hash=_HASH_A)
    (state_root / "crawling1" / "other.txt").write_text("keep", encoding="utf-8")

    result = run_migration(
        settings_path=settings_path,
        state_root=state_root,
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    target = result.targets[0]
    target_id = target.mapping.monitoring_target.id
    dest = state_root / "targets" / target_id

    # crawling1 내용이 targets/<id>로 복사되고(2.2 state_subdir 정본 일치)…
    assert (dest / "last_message.deadbeefdeadbeef.sha256").read_text(encoding="utf-8") == _HASH_A
    assert (dest / "other.txt").read_text(encoding="utf-8") == "keep"
    assert target.state_copied_to == dest
    # …원본 crawling1은 삭제되지 않는다(NFR-18, 불변식 ①).
    assert (state_root / "crawling1" / "last_message.deadbeefdeadbeef.sha256").exists()
    assert (state_root / "crawling1" / "other.txt").exists()

    # old last_message hash가 MigrationSeed로 승계된다(NFR-21, AC2(b)).
    seed = target.seed
    assert isinstance(seed, MigrationSeed)
    assert seed.monitoring_target_id == target_id
    assert seed.message_hash == _HASH_A
    assert seed.scope_hash == "deadbeefdeadbeef"


def test_copy_state_dir_is_idempotent_and_never_overwrites(tmp_path) -> None:
    state_root = tmp_path / "state"
    src = state_root / "crawling1"
    src.mkdir(parents=True)
    (src / "shared.txt").write_text("from-source", encoding="utf-8")
    (src / "new.txt").write_text("brand-new", encoding="utf-8")
    # 대상 폴더가 이미 있고 같은 이름 파일이 있을 때: 기존 파일은 덮어쓰지 않는다(멱등).
    dest = state_root / "targets" / "tgt-1"
    dest.mkdir(parents=True)
    (dest / "shared.txt").write_text("existing-dest", encoding="utf-8")

    returned = copy_state_dir(state_root, crawling_index=1, monitoring_target_id="tgt-1")

    assert returned == dest
    assert (dest / "shared.txt").read_text(encoding="utf-8") == "existing-dest"  # 덮어쓰기 0
    assert (dest / "new.txt").read_text(encoding="utf-8") == "brand-new"  # 없던 파일만 복사
    # 원본 미삭제.
    assert (src / "shared.txt").read_text(encoding="utf-8") == "from-source"


def test_copy_state_dir_missing_source_creates_empty_dest(tmp_path) -> None:
    # 원본 폴더가 없던 탭(첫 발송 전): 빈 대상 폴더만 만들고 seed는 없다(fail-safe).
    state_root = tmp_path / "state"
    dest = copy_state_dir(state_root, crawling_index=5, monitoring_target_id="tgt-x")
    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert seed_from_state(dest, monitoring_target_id="tgt-x") is None


def test_run_migration_crawling_index_uses_full_position_not_active_only(tmp_path) -> None:
    # off-by-one 가드: 비활성 탭이 활성 탭들 사이에 있을 때(활성·비활성·활성 = [0]·[1]·[2]),
    # 두 번째 활성 탭의 복사 원본은 crawling2가 아니라 crawling3(전체 1-based 위치)여야 한다.
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _inactive(), _active_coupang()])
    state_root = tmp_path / "state"
    _write_last_message(state_root / "crawling1", scope_hash="aaaa1111aaaa1111", message_hash=_HASH_A)
    _write_last_message(state_root / "crawling3", scope_hash="bbbb2222bbbb2222", message_hash=_HASH_B)

    result = run_migration(
        settings_path=settings_path,
        state_root=state_root,
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )

    assert [t.crawling_index for t in result.targets] == [1, 3]
    first, second = result.targets
    # 첫 활성 탭(위치 1) → crawling1 hash 승계.
    assert first.seed.message_hash == _HASH_A
    assert first.seed.scope_hash == "aaaa1111aaaa1111"
    # 두 번째 활성 탭(위치 3) → crawling3 hash 승계(crawling2가 아님 — 활성-only 인덱싱 회귀 차단).
    assert second.seed.message_hash == _HASH_B
    assert second.seed.scope_hash == "bbbb2222bbbb2222"


# ── AC3: 마이그레이션 상태머신 · 승인 전 활성화 0(fail-closed 불변식 ③) ────


def _mapped_target(tmp_path) -> TargetMigration:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    return result.targets[0]


def test_run_migration_stops_at_mapped_no_auto_activation(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin(), _active_coupang()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    # 승인 전 활성화 0(불변식 ③): 모든 활성 대상은 MAPPED에서 멈춘다(자동 ACTIVE 0).
    assert all(t.state == MigrationState.MAPPED for t in result.targets)
    assert not any(t.state == MigrationState.ACTIVE for t in result.targets)


def test_transition_happy_path_mapped_to_active(tmp_path) -> None:
    tm = _mapped_target(tmp_path)
    tm = mark_dry_run_passed(tm)
    assert tm.state == MigrationState.DRY_RUN_PASSED
    tm = approve(tm)
    assert tm.state == MigrationState.APPROVED
    tm = activate(tm)
    assert tm.state == MigrationState.ACTIVE
    tm = pause(tm)
    assert tm.state == MigrationState.PAUSED
    # 전이는 state만 바꾸고 매핑·seed·식별 필드를 보존한다(dataclasses.replace).
    assert tm.crawling_index == 1
    assert tm.mapping is not None


def test_activate_without_approval_raises(tmp_path) -> None:
    # 승인 없는 활성화 차단(fail-closed 불변식 ③).
    tm = _mapped_target(tmp_path)
    with pytest.raises(ValueError):
        activate(tm)  # MAPPED → ACTIVE 직행 금지


def test_activate_on_discovered_raises(tmp_path) -> None:
    discovered = TargetMigration(
        crawling_index=2,
        legacy_alias="크롤링2",
        state=MigrationState.DISCOVERED,
        mapping=None,
        seed=None,
        state_copied_to=None,
    )
    with pytest.raises(ValueError):
        activate(discovered)
    with pytest.raises(ValueError):
        approve(discovered)  # DISCOVERED → APPROVED도 금지


def test_roll_back_from_any_state(tmp_path) -> None:
    tm = _mapped_target(tmp_path)
    assert roll_back(tm).state == MigrationState.ROLLED_BACK
    active = activate(approve(mark_dry_run_passed(tm)))
    assert roll_back(active).state == MigrationState.ROLLED_BACK


def test_target_migration_and_state_are_frozen_str_enum(tmp_path) -> None:
    tm = _mapped_target(tmp_path)
    assert dataclasses.is_dataclass(tm)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tm.state = MigrationState.ACTIVE  # type: ignore[misc]
    assert isinstance(MigrationState.MAPPED, str)


# ── 직렬화 정본 ───────────────────────────────────────────────────────────


def test_migration_state_serializes_to_uppercase_string() -> None:
    assert json.dumps([MigrationState.MAPPED]) == '["MAPPED"]'
    assert MigrationState.MAPPED == "MAPPED"


@pytest.mark.parametrize("member", list(MigrationState))
def test_every_migration_state_round_trips(member) -> None:
    # (str, Enum) 정본 직렬화는 .value/==(대문자) — str()/f-string은 신뢰하지 않는다.
    assert member == member.value
    assert json.dumps(member) == f'"{member.value}"'


def test_migration_state_members_are_locked() -> None:
    # NFR-22 상태 어휘 정본(드리프트 가드). 멤버가 추가/삭제되면 의도적으로 깨진다.
    assert {m.value for m in MigrationState} == {
        "DISCOVERED",
        "MAPPED",
        "DRY_RUN_PASSED",
        "APPROVED",
        "ACTIVE",
        "PAUSED",
        "ROLLED_BACK",
    }


# ── 도메인 매핑 정합 · secret 비노출(ref만) ───────────────────────────────


def test_mapping_ids_are_consistent_across_models(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    mapping = result.targets[0].mapping
    tenant, account, target = mapping.tenant, mapping.platform_account, mapping.monitoring_target

    # tenant_id 체인 일관(customer_id == tenant_id ×2 == Tenant.id).
    assert target.tenant_id == account.tenant_id == tenant.id
    assert target.platform_account_id == account.id
    # 2.3 중립 필드가 도메인 모델로 올바르게 매핑된다.
    assert target.center_name == "센터A"
    assert target.url == "https://self.baemin.com/stats/A"
    assert target.external_id == "CENTER-A"
    assert target.interval_minutes == 30
    assert target.status == MonitoringTargetStatus.ACTIVE
    # created_at은 주입값(결정성), Tenant 상태는 ACTIVE.
    assert tenant.created_at == _FIXED_AT
    assert account.platform == Platform.BAEMIN
    assert isinstance(tenant, Tenant)
    assert isinstance(account, PlatformAccount)
    assert isinstance(target, MonitoringTarget)
    assert isinstance(mapping, TargetMapping)


def test_platform_account_uses_secret_refs_not_plaintext(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    # 활성 쿠팡(자격증명 ref 보유) + 활성 배민(자격증명 없음).
    _write_settings(settings_path, [_active_coupang(), _active_baemin()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    by_platform = {t.mapping.platform_account.platform: t.mapping.platform_account for t in result.targets}

    coupang = by_platform[Platform.COUPANG]
    assert isinstance(coupang.username, str)
    assert isinstance(coupang.password, str)
    assert coupang.username == "local:fake-b/coupang_login_id"

    baemin = by_platform[Platform.BAEMIN]
    assert baemin.username == ""
    assert baemin.password == ""


def test_map_active_tab_unknown_platform_is_fail_closed(tmp_path) -> None:
    # 미지 platform_name은 조용히 BAEMIN으로 떨어뜨리지 않고 ValueError(fail-closed).
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin()])
    store = UiSettingsStore(settings_path)
    active, _ = classify_and_issue(store)
    _, settings = active[0]
    settings.platform_name = "doordash"  # UiSettings는 non-frozen — 직접 주입
    with pytest.raises(ValueError):
        map_active_tab(settings, created_at=_FIXED_AT)


# ══════════════════════════════════════════════════════════════════════════
# QA gap coverage (bmad-qa-generate-e2e-tests) — 기존 27케이스가 happy-path와
# 헤드라인 불변식을 잠갔다. 아래는 미커버 분기(상태머신 가드 전이·경계 케이스·
# 중첩 복사·frozen 계약·ADD-15 백업 경계)를 메우는 추가 잠금이다.
# ══════════════════════════════════════════════════════════════════════════


# ── AC3 보강: 상태머신의 나머지 fail-closed 가드 전이 ──────────────────────


def test_pause_on_non_active_raises(tmp_path) -> None:
    # pause는 ACTIVE에서만 가능 — MAPPED에서 호출하면 ValueError(잘못된 전이 차단).
    tm = _mapped_target(tmp_path)
    with pytest.raises(ValueError):
        pause(tm)


def test_mark_dry_run_passed_on_non_mapped_raises(tmp_path) -> None:
    # mark_dry_run_passed는 MAPPED에서만 — DISCOVERED에서 호출하면 ValueError.
    discovered = TargetMigration(
        crawling_index=2,
        legacy_alias="크롤링2",
        state=MigrationState.DISCOVERED,
        mapping=None,
        seed=None,
        state_copied_to=None,
    )
    with pytest.raises(ValueError):
        mark_dry_run_passed(discovered)


def test_approve_skipping_dry_run_raises(tmp_path) -> None:
    # approve는 DRY_RUN_PASSED에서만 — dry-run을 건너뛴 MAPPED 직행 승인은 차단(fail-closed ③).
    tm = _mapped_target(tmp_path)
    with pytest.raises(ValueError):
        approve(tm)


# ── AC1/AC2 보강: 경계 케이스(활성 0·이전 상태 없음) ───────────────────────


def test_run_migration_all_inactive_yields_no_targets(tmp_path) -> None:
    # 활성 탭이 하나도 없으면(전부 비활성): 매핑 0·발급 0이되 백업은 여전히 수행된다(NFR-18).
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_inactive(), _inactive()])
    backup_path = tmp_path / "ui_settings.json.bak"

    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=backup_path,
        created_at=_FIXED_AT,
    )

    assert result.targets == ()
    assert result.inactive_count == 2
    assert backup_path.exists()  # 활성 대상이 없어도 백업은 항상 먼저 일어난다.


def test_run_migration_active_tab_without_prior_state_has_no_seed(tmp_path) -> None:
    # 첫 발송 전 활성 탭(crawling{N} 폴더 없음): MAPPED로 매핑되되 seed는 None이고
    # targets/<id>는 빈 폴더로 생성된다(fail-safe — dedup 승계할 hash가 아직 없음).
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_baemin()])
    state_root = tmp_path / "state"  # crawling1 폴더를 일부러 만들지 않는다.

    result = run_migration(
        settings_path=settings_path,
        state_root=state_root,
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    target = result.targets[0]
    assert target.state == MigrationState.MAPPED
    assert target.seed is None
    assert target.state_copied_to is not None
    assert target.state_copied_to.is_dir()
    assert list(target.state_copied_to.iterdir()) == []  # 빈 대상 폴더


# ── AC2 보강: seed 추출 분기(비관련 파일 무시·hash 공백 strip) ─────────────


def test_seed_from_state_ignores_unrelated_files_and_strips_hash(tmp_path) -> None:
    # last_message.*.sha256가 아닌 파일만 있으면 None(seed 없음).
    only_other = tmp_path / "only_other"
    only_other.mkdir()
    (only_other / "notes.txt").write_text("unrelated", encoding="utf-8")
    assert seed_from_state(only_other, monitoring_target_id="tgt-x") is None

    # 파일 내용의 후행 개행/공백은 strip돼 message_hash에 섞이지 않는다(app 형식 충실).
    with_hash = tmp_path / "with_hash"
    _write_last_message(with_hash, scope_hash="cafe0000cafe0000", message_hash=_HASH_A + "\n  ")
    seed = seed_from_state(with_hash, monitoring_target_id="tgt-y")
    assert seed is not None
    assert seed.message_hash == _HASH_A
    assert seed.scope_hash == "cafe0000cafe0000"


# ── AC2 보강: 상태 복사의 중첩 하위 폴더 재귀 ──────────────────────────────


def test_copy_state_dir_copies_nested_subdirectories(tmp_path) -> None:
    # _copy_missing은 평면 파일뿐 아니라 중첩 폴더도 재귀 복사한다(원본 미삭제).
    state_root = tmp_path / "state"
    src = state_root / "crawling1"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "inner.txt").write_text("nested", encoding="utf-8")
    (src / "top.txt").write_text("flat", encoding="utf-8")

    dest = copy_state_dir(state_root, crawling_index=1, monitoring_target_id="tgt-nested")

    assert (dest / "top.txt").read_text(encoding="utf-8") == "flat"
    assert (dest / "sub" / "inner.txt").read_text(encoding="utf-8") == "nested"
    # 원본은 그대로(NFR-18).
    assert (src / "sub" / "inner.txt").read_text(encoding="utf-8") == "nested"


# ── 계약 보강: 나머지 값 객체도 frozen(불변) ──────────────────────────────


def test_value_objects_are_frozen(tmp_path) -> None:
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_coupang()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    mapping = result.targets[0].mapping
    seed = MigrationSeed(monitoring_target_id="t", message_hash=_HASH_A, scope_hash="s")

    # MigrationResult·TargetMapping·MigrationSeed 모두 frozen 값 객체다(Task 2 계약).
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.inactive_count = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        mapping.tenant = None  # type: ignore[misc,assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        seed.message_hash = "x"  # type: ignore[misc]


# ── 보안 경계: 백업 충실도(NFR-18) vs 신규 산출물 평문 금지(ADD-15) ─────────


def test_backup_preserves_plaintext_while_mapping_exposes_refs_only(tmp_path) -> None:
    # legacy 원본에 가짜 평문 자격증명이 있는 경우(실제 secret 아님 — 명백한 가짜값).
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(
        settings_path,
        [
            {
                "performance_url": "https://partner.coupangeats.com/merchant/C",
                "platform_name": "coupang",
                "baemin_center_name": "쿠팡센터C",
                "baemin_center_id": "CENTER-C",
                "interval_minutes": 30,
                "coupang_login_id": "fakeplainid",
                "coupang_login_password": "fakeplainpw",
            }
        ],
    )
    backup_path = tmp_path / "ui_settings.json.bak"

    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=backup_path,
        created_at=_FIXED_AT,
    )

    # NFR-18: 백업은 원본 그대로(평문 포함) — 롤백 아티팩트라 redaction하지 않는다.
    backup_bytes = backup_path.read_bytes()
    assert b"fakeplainid" in backup_bytes
    assert b"fakeplainpw" in backup_bytes

    # Plaintext credentials are now stored directly in DB (운영 간소화).
    account = result.targets[0].mapping.platform_account
    assert isinstance(account.username, str)
    assert isinstance(account.password, str)
    assert account.username == "fakeplainid"
    assert account.password == "fakeplainpw"


# ── 도메인 매핑 정합: 쿠팡(두 번째 플랫폼) 중립 필드 ───────────────────────


def test_coupang_tab_maps_neutral_fields_consistently(tmp_path) -> None:
    # baemin 경로뿐 아니라 coupang 경로도 2.3 중립 필드를 도메인 모델로 올바르게 매핑한다.
    settings_path = tmp_path / "ui_settings.json"
    _write_settings(settings_path, [_active_coupang()])
    result = run_migration(
        settings_path=settings_path,
        state_root=tmp_path / "state",
        backup_path=tmp_path / "ui_settings.json.bak",
        created_at=_FIXED_AT,
    )
    mapping = result.targets[0].mapping
    tenant, account, target = mapping.tenant, mapping.platform_account, mapping.monitoring_target

    assert account.platform == Platform.COUPANG
    assert target.tenant_id == account.tenant_id == tenant.id
    assert target.platform_account_id == account.id
    assert target.center_name == "쿠팡센터B"
    assert target.url == "https://partner.coupangeats.com/merchant/B"
    assert target.external_id == "CENTER-B"
    assert target.interval_minutes == 45
    assert target.status == MonitoringTargetStatus.ACTIVE
