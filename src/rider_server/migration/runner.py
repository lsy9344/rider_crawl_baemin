"""기존 탭 설정의 안전한 마이그레이션 오케스트레이션(Story 2.7 / ADD-16, FR-31).

기존 ``runtime/state/ui_settings.json`` 의 **활성 탭**을 ID 기반 도메인 모델로 옮기는
**순수·결정적 절차**다. ADD-16 1~5단계를 실제로 수행한다:

  1. 원본 ``ui_settings.json`` 백업(어떤 변형보다 먼저, 원자적 복사, 원본 미삭제 — NFR-18).
  2. ``crawlings`` 에서 ``performance_url`` 이 채워진 **활성 탭만** target 후보로 분류(비활성은
     보존하되 발급·복사·매핑·활성화 대상에서 제외 — 불변식 ②).
  3. 활성 탭에 ``tenant_id``/``platform_account_id``/``monitoring_target_id`` 발급 —
     ``UiSettingsStore.load_all`` (Story 2.1) **재사용**(``uuid4`` 직접 호출 금지, 반-재발명).
  4. ``runtime/state/crawling{N}`` → ``runtime/state/targets/<monitoring_target_id>`` 상태
     **복사**(원본 미삭제, 멱등 — NFR-18, 불변식 ①).
  5. old ``last_message.<scope>.sha256`` hash → ``MigrationSeed`` 로 승계해 Epic 3/5
     DeliveryLog/idempotency seed 정본을 표현(NFR-21, 불변식 ④).

6~8단계(실제 dry-run 렌더 비교·DeliveryRule 활성화·발송)는 **상태 어휘**(``MigrationState``)
로만 표현하고 부작용을 일으키지 않는다 — 실제 비교는 Story 3.8, 활성화/발송은 Story 3.4 +
Epic 5 소유. **승인 전 활성화 0**(불변식 ③)이 fail-closed 최상위 원칙이다: ``run_migration``
은 활성 대상을 ``MAPPED`` 에서 멈추고, ``APPROVED``/``ACTIVE`` 전이는 운영자 입력을 표현하는
명시 전이 함수(``approve``/``activate``) 호출로만 가능하다.

**순수·결정적·의존성 0(Epic 2 제약).** FastAPI/SQLAlchemy/async 의존이 없다. 비결정값
(``created_at``·백업 경로·상태 루트)은 호출부가 주입하고(테스트 결정성), 파일 I/O는 주입된
경로로만 일어난다(``tmp_path`` 격리). ``rider_crawl`` 과 ``rider_server.domain`` 은 **import해
소비만** 하고 한 줄도 바꾸지 않는다(NFR-20 회귀 0).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from rider_crawl.ui_settings import UiSettings, UiSettingsStore
from rider_server.domain import (
    BaeminAuthState,
    CustomerLifecycleState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Tenant,
)


class MigrationState(str, Enum):
    """마이그레이션 진행 상태머신(NFR-22). ADD-16 단계별 진행을 표현한다.

    ``(str, Enum)`` + **멤버 이름 == 값(대문자)** — Story 2.5 enum 컨벤션 계승(``StrEnum`` 은
    3.11+ 라 ``>=3.10`` 호환 위해 ``(str, Enum)``). 마이그레이션-전용 어휘이므로 도메인 enum
    (``domain/states.py``, Story 2.5 ``==`` 잠금)이 아니라 ``migration/`` 모듈에 둔다(Story 2.6
    이 ``DispatchJobStatus`` 를 ``services/`` 에 둔 선례와 동형).

    매핑(NFR-22 ↔ ADD-16): ``DISCOVERED`` = 분류됨(비활성 탭의 보존 상태이기도 함),
    ``MAPPED`` = 도메인 모델 매핑 완료(``run_migration`` 기본 정지점 — 승인 전 활성화 0),
    ``DRY_RUN_PASSED`` = dry-run 통과 표현(실제 비교는 Story 3.8), ``APPROVED`` = 운영자 승인,
    ``ACTIVE`` = 활성화 표현(실제 enable/발송은 Story 3.4 + Epic 5), ``PAUSED`` = 일시 중지,
    ``ROLLED_BACK`` = 롤백.
    """

    DISCOVERED = "DISCOVERED"
    MAPPED = "MAPPED"
    DRY_RUN_PASSED = "DRY_RUN_PASSED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class TargetMapping:
    """활성 탭 1개를 Story 2.5 도메인 모델로 묶은 결과(도메인 **소비**, 무변경)."""

    tenant: Tenant
    platform_account: PlatformAccount
    monitoring_target: MonitoringTarget


@dataclass(frozen=True)
class MigrationSeed:
    """old ``last_message`` hash 승계 정본(AC2(b), NFR-21).

    Epic 3/5 DeliveryLog dedup이 소비할 **부분 seed**다 — dedup key의 나머지 필드
    (channel_id·collected_at·template_version)는 Epic 3 snapshot/template 도입 후 채운다.
    평문 secret 없음 — hash·id만 담는다(ADD-15).
    """

    monitoring_target_id: str
    message_hash: str
    scope_hash: str


@dataclass(frozen=True)
class TargetMigration:
    """대상 1개의 마이그레이션 진행(frozen 값 객체, AC3).

    활성 탭은 ``state=MAPPED`` + ``mapping``/``seed`` 를 담고, 비활성 탭은 ``DISCOVERED`` ·
    ``mapping=None`` (보존·미활성화)으로 표현할 수 있다. 상태 전이는 ``dataclasses.replace`` 로
    ``state`` 만 바꾸는 순수 함수(``approve``/``activate`` 등)로만 일어난다.
    """

    crawling_index: int
    legacy_alias: str
    state: MigrationState
    mapping: TargetMapping | None
    seed: MigrationSeed | None
    state_copied_to: Path | None


@dataclass(frozen=True)
class MigrationResult:
    """전체 마이그레이션 결과(불변).

    ``backup_path`` = 원본 백업 위치(롤백 아티팩트), ``targets`` = 활성 대상별 진행(``MAPPED``),
    ``inactive_count`` = 보존된 비활성 탭 수(발급·복사·매핑 0 — 불변식 ②). 비활성 탭은 ID·매핑을
    만들지 않으므로 ``targets`` 에 넣지 않고 수만 집계한다(개념상 ``DISCOVERED`` 로 보존).
    """

    backup_path: Path
    targets: tuple[TargetMigration, ...]
    inactive_count: int


# ── Task 3: 백업(원본 미삭제) ─────────────────────────────────────────────


def back_up_settings(settings_path: Path, *, backup_path: Path) -> Path:
    """원본 ``ui_settings.json`` 을 ``backup_path`` 로 **원자적·byte 충실** 복사한다(NFR-18).

    어떤 변형(ID 발급/secret 흡수)보다 **먼저** 호출돼야 하는 롤백 아티팩트다. 원본은
    **읽기만** 하고 삭제·이동하지 않는다(불변식 ①). ``backup_path`` 는 호출부가 주입한다
    (내부 ``datetime.now()`` 금지 — 결정성). 원본이 없으면 ``FileNotFoundError`` (마이그레이션할
    것이 없음 — fail-closed로 호출부에 surface).

    **백업 충실도 vs ADD-15:** 원본이 legacy 평문 secret을 담고 있어도 백업은 **원본 그대로**
    (redaction 없음) 둔다 — ADD-15의 "평문 금지"는 마이그레이션이 **생성하는 신규 ID-모델
    산출물**에 적용되지 충실한 롤백 백업에는 적용되지 않는다(NFR-18 우선).
    """

    data = settings_path.read_bytes()  # 없으면 FileNotFoundError — 명확한 실패
    _atomic_write_bytes(backup_path, data)
    return backup_path


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    # ``ui_settings._atomic_write_text`` 와 동일 정신(temp→fsync→os.replace)이되 byte 충실
    # 복사라 text 모드 newline 변환을 피한다(롤백 아티팩트는 원본과 byte 동일해야 함). temp는
    # cross-device 비원자화를 피하려 반드시 ``path.parent`` 에 만든다.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        with tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


# ── Task 4: 분류 + ID 발급 재사용 ────────────────────────────────────────


def classify_and_issue(
    store: UiSettingsStore, *, max_tabs: int = 9
) -> tuple[list[tuple[int, UiSettings]], int]:
    """활성 탭에 ID를 발급(``load_all`` 재사용)하고 활성/비활성으로 분류한다.

    ``store.load_all`` 이 **활성 탭에만** 안정 ``uuid4`` ID를 발급·영속화한다(Story 2.1 —
    본 모듈은 ``uuid4`` /발급 로직을 재구현하지 않는다). 그 뒤 파일의 **실제 탭 수**(raw
    ``crawlings`` 길이)로 잘라 ``load_all`` 의 9-slot 패딩 기본 슬롯을 후보에서 제외한다(AC2).

    반환: ``([(crawling_index, settings), ...], inactive_count)``. ``crawling_index`` 는 **전체
    raw 리스트의 1-based 위치**(활성-only 위치가 **아님**)다 — legacy 상태 폴더명
    ``crawling{index}`` 와 정합하려면 비활성 탭이 사이에 있어도 전체 위치를 써야 한다(off-by-one
    회귀 시 빈 ``targets/<id>`` 로 dedup 미승계가 조용히 생긴다).
    """

    raw_count = _raw_tab_count(store.path)
    all_settings = store.load_all(max_tabs=max_tabs)

    active: list[tuple[int, UiSettings]] = []
    inactive_count = 0
    # enumerate 전체 리스트(start=1) → 활성만 골라낸다. 활성 판정은 ``ui.active_crawling_settings`` /
    # ``ui_settings.load_all`` (244)의 ``performance_url.strip()`` 정본을 인라인 복제한다(ui는
    # tkinter 무거운 모듈·순환 회피로 import하지 않는다 — load_all이 같은 규율을 쓴다).
    for crawling_index, settings in enumerate(all_settings[:raw_count], start=1):
        if settings.performance_url.strip():
            active.append((crawling_index, settings))
        else:
            inactive_count += 1
    return active, inactive_count


def _raw_tab_count(path: Path) -> int:
    # ``load_all`` 의 items 산정과 동일 규칙으로 파일의 실제 탭 수를 센다(패딩 제외 정합).
    if not path.exists():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("crawlings"), list):
        return sum(1 for item in raw["crawlings"] if isinstance(item, dict))
    if isinstance(raw, dict):
        return 1
    return 0


# ── Task 5: 도메인 매핑 ──────────────────────────────────────────────────

# platform_name(소문자 registry 키) → Platform(대문자 도메인 enum). 별개 레이어 명시 변환
# (states.py 62-71). ``load_all`` 이 platform_name을 {"baemin","coupang"}로 정규화하므로 미지
# 값은 실제로 도달하지 않지만, 도달 시 fail-closed로 ``ValueError`` (조용한 오분류 방지).
_PLATFORM_BY_NAME: dict[str, Platform] = {
    "baemin": Platform.BAEMIN,
    "coupang": Platform.COUPANG,
}


def map_active_tab(settings: UiSettings, *, created_at: datetime) -> TargetMapping:
    """발급된 ID-보유 활성 ``UiSettings`` 를 Story 2.5 도메인 모델로 매핑한다.

    Story 2.5가 2.7에 위임한 wiring(``monitoring_target.py`` 9-10)이다. 도메인은 **소비만** 하고
    무변경이다. ``created_at`` 은 호출부 주입(결정성). 자격증명은 **평문이 아니라 ``SecretRef``**
    로만 가리킨다(ADD-15) — 미설정 탭은 ``NOT_STORED``.
    """

    tenant = Tenant(
        id=settings.customer_id,
        name=settings.customer_name or settings.legacy_alias,
        status=CustomerLifecycleState.ACTIVE,
        created_at=created_at,
    )
    platform_account = PlatformAccount(
        id=settings.platform_account_id,
        tenant_id=settings.customer_id,
        platform=_platform_from_name(settings.platform_name),
        label=settings.legacy_alias,
        username=settings.coupang_login_id or settings.coupang_login_id_ref or "",
        password=settings.coupang_login_password or settings.coupang_login_password_ref or "",
        verification_email_address=settings.verification_email_address,
        verification_email_app_password=settings.verification_email_app_password,
        verification_email_subject_keyword=settings.verification_email_subject_keyword,
        verification_email_sender_keyword=settings.verification_email_sender_keyword,
        auth_state=BaeminAuthState.UNKNOWN,
    )
    monitoring_target = MonitoringTarget(
        id=settings.monitoring_target_id,
        tenant_id=settings.customer_id,
        platform_account_id=settings.platform_account_id,
        name=settings.display_name or settings.center_name,
        center_name=settings.center_name,
        external_id=settings.target_external_id,
        url=settings.primary_url,
        interval_minutes=settings.interval_minutes,
        status=MonitoringTargetStatus.ACTIVE,
    )
    return TargetMapping(
        tenant=tenant,
        platform_account=platform_account,
        monitoring_target=monitoring_target,
    )


def _platform_from_name(platform_name: str) -> Platform:
    try:
        return _PLATFORM_BY_NAME[platform_name.strip().casefold()]
    except KeyError as exc:  # fail-closed — 조용히 BAEMIN으로 떨어뜨리지 않는다
        raise ValueError(f"unknown platform_name for migration: {platform_name!r}") from exc




# ── Task 6: 상태 폴더 복사(원본 미삭제) + seed 추출 ──────────────────────


def copy_state_dir(
    state_root: Path, *, crawling_index: int, monitoring_target_id: str
) -> Path:
    """``state_root/crawling{N}`` → ``state_root/targets/<id>`` 로 내용을 **복사**한다(NFR-18).

    원본 ``crawling{N}`` 은 **읽기만** 하고 삭제·이동하지 않는다(불변식 ①). 대상 폴더가 이미
    있으면 **없는 파일만** 복사해 원본을 덮어쓰지 않는다(멱등). 원본 폴더가 없으면(상태 기록이
    아직 없던 탭) 빈 대상 폴더만 만들고 no-op(seed 없음 — fail-safe). 대상 경로는 Story 2.2
    ``_state_subdir_for`` (``targets/<id>``)·``config.state_dir`` 정본과 일치한다.
    """

    src = state_root / f"crawling{crawling_index}"
    dest = state_root / "targets" / monitoring_target_id
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        _copy_missing(src, dest)
    return dest


def _copy_missing(src: Path, dest: Path) -> None:
    # 원본 읽기 전용, 기존 대상 파일 덮어쓰기 0(멱등). last_message.*.sha256는 평면 파일이지만
    # 중첩 폴더도 안전하게 다룬다.
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        target = dest / entry.name
        if entry.is_dir():
            _copy_missing(entry, target)
        elif entry.is_file() and not target.exists():
            target.write_bytes(entry.read_bytes())


def seed_from_state(state_dir: Path, *, monitoring_target_id: str) -> MigrationSeed | None:
    """복사된 ``targets/<id>`` 에서 ``last_message.<scope>.sha256`` → ``MigrationSeed`` 추출.

    ``message_hash`` = 파일 내용, ``scope_hash`` = 파일명 토큰(``app._last_message_hash_path``
    63-65 형식). 파일이 없으면 ``None`` (첫 발송 전 탭 — seed 없음). 현재 단일 scope 운영이라
    첫 파일을 대표 seed로 쓴다(여러 scope는 Epic 3/5에서 확장).
    """

    if not state_dir.is_dir():
        return None
    prefix, suffix = "last_message.", ".sha256"
    for entry in sorted(state_dir.iterdir()):
        name = entry.name
        if entry.is_file() and name.startswith(prefix) and name.endswith(suffix):
            scope_hash = name[len(prefix) : -len(suffix)]
            message_hash = entry.read_text(encoding="utf-8").strip()
            return MigrationSeed(
                monitoring_target_id=monitoring_target_id,
                message_hash=message_hash,
                scope_hash=scope_hash,
            )
    return None


# ── Task 7: 오케스트레이션 + 상태 전이(승인 전 활성화 0) ─────────────────


def run_migration(
    *,
    settings_path: Path,
    state_root: Path,
    backup_path: Path,
    created_at: datetime,
    max_tabs: int = 9,
) -> MigrationResult:
    """ADD-16 1~5단계를 결정론적으로 실행한다(백업→분류·발급→매핑+상태복사+seed).

    순서: **백업(먼저) → 분류·ID 발급 → 각 활성 탭 매핑 + 상태 복사 + seed**. 활성 대상은
    ``TargetMigration(state=MAPPED, …)`` 로 멈춘다 — **자동으로 ``APPROVED``/``ACTIVE`` 로
    진행하지 않고 활성 DeliveryRule·발송을 만들지 않는다**(불변식 ③). ``APPROVED``/``ACTIVE`` 는
    ``approve``/``activate`` 명시 호출(운영자 입력 표현)로만 가능하다. 비활성 탭은 보존·미발급으로
    ``inactive_count`` 에만 집계한다(불변식 ②). 모든 비결정값(``created_at``/``backup_path``/
    ``state_root``)은 인자 주입(테스트 결정성).
    """

    backup = back_up_settings(settings_path, backup_path=backup_path)
    # secret store는 settings_path 옆 분리 파일(기본). settings_path가 tmp_path면 store도 격리.
    store = UiSettingsStore(settings_path)
    active, inactive_count = classify_and_issue(store, max_tabs=max_tabs)

    targets: list[TargetMigration] = []
    for crawling_index, settings in active:
        mapping = map_active_tab(settings, created_at=created_at)
        target_id = settings.monitoring_target_id
        copied_to = copy_state_dir(
            state_root, crawling_index=crawling_index, monitoring_target_id=target_id
        )
        seed = seed_from_state(copied_to, monitoring_target_id=target_id)
        targets.append(
            TargetMigration(
                crawling_index=crawling_index,
                legacy_alias=settings.legacy_alias,
                state=MigrationState.MAPPED,
                mapping=mapping,
                seed=seed,
                state_copied_to=copied_to,
            )
        )
    return MigrationResult(
        backup_path=backup,
        targets=tuple(targets),
        inactive_count=inactive_count,
    )


def _transition(
    target: TargetMigration, *, expected: MigrationState, to: MigrationState
) -> TargetMigration:
    # 선행 상태를 검증하고 위반 시 ``ValueError`` (fail-closed — 승인 없는 활성화 등 차단).
    # ``dataclasses.replace`` 로 state만 전이하고 매핑·seed 등 나머지는 보존한다.
    if target.state != expected:
        raise ValueError(
            f"invalid transition to {to.value}: requires {expected.value}, got {target.state.value}"
        )
    return replace(target, state=to)


def mark_dry_run_passed(target: TargetMigration) -> TargetMigration:
    """``MAPPED`` → ``DRY_RUN_PASSED``. 상태만 바꾼다 — 실제 old/new 렌더 비교는 Story 3.8."""
    return _transition(target, expected=MigrationState.MAPPED, to=MigrationState.DRY_RUN_PASSED)


def approve(target: TargetMigration) -> TargetMigration:
    """``DRY_RUN_PASSED`` → ``APPROVED`` (운영자 승인 표현)."""
    return _transition(target, expected=MigrationState.DRY_RUN_PASSED, to=MigrationState.APPROVED)


def activate(target: TargetMigration) -> TargetMigration:
    """``APPROVED`` → ``ACTIVE``. 상태만 바꾼다 — 실제 DeliveryRule enable/발송은 Story 3.4 +
    Epic 5. ``APPROVED`` 가 아닌 상태(예: ``MAPPED``/``DISCOVERED``)에서 호출하면 승인 없는
    활성화를 막기 위해 ``ValueError`` (fail-closed 불변식 ③)."""
    return _transition(target, expected=MigrationState.APPROVED, to=MigrationState.ACTIVE)


def pause(target: TargetMigration) -> TargetMigration:
    """``ACTIVE`` → ``PAUSED``."""
    return _transition(target, expected=MigrationState.ACTIVE, to=MigrationState.PAUSED)


def roll_back(target: TargetMigration) -> TargetMigration:
    """임의 상태 → ``ROLLED_BACK`` (백업 기반 복구 표현 — 선행 상태 제약 없음)."""
    return replace(target, state=MigrationState.ROLLED_BACK)
