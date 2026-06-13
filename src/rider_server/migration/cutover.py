"""신규 경로 dry-run 비교와 승인 후 활성화(Story 3.8 / FR-3, NFR-22·24·25, ADD-16 6~8단계).

Story 2.7이 만든 ``MigrationState`` 상태머신(``DISCOVERED→MAPPED→DRY_RUN_PASSED→APPROVED→
ACTIVE→PAUSED→ROLLED_BACK``)과 전이 순수 함수(``mark_dry_run_passed``/``approve``/
``activate``/``roll_back``)·``MigrationSeed``(old ``last_message`` hash 승계)에 **compose**해,
2.7이 본 스토리에 명시 위임한 6~8단계 실체를 채운다:

  6. **실발송 없는 dry-run 실행**(``run_dry_run``/``DryRunResult``): 3.1 ``CrawlService.crawl``
     → 3.3 ``MessageRenderService.render_message`` 만 compose해 신규 ``Message`` 를 만든다 —
     ``send``/sender 인자가 0이고 ``DispatchService``/``DispatchFanoutService``/
     ``CentralTelegramSender`` 를 호출하지 않아 **실발송 경로가 코드에 존재하지 않는다**
     (구조적 no-send, operations-security-test E2E dry-run). ``DryRunResult.sent`` 는 항상 False.
  7. **기준선 비교**(``compare_to_baseline``/``DryRunComparison``): 2.7 ``MigrationSeed.message_hash``
     (old ``last_message`` hash)와 신규 ``Message.text_hash`` 를 **hash 대 hash** 로 비교한다.
     seed 없음(첫 발송 전 탭) → 차이로 취급(``matches=False``, fail-closed).
  8a. **차이 시 자동 활성화 차단 승인 게이트**(``approve_after_review``/``CutoverApprovalError``):
     차이가 있는데 운영자가 확인·승인하지 않으면 ``APPROVED`` 전이를 막는다(2.7 ``approve`` wrapping).
  8b. **cutover 동시전송 가드**(``assert_no_dual_active_send``/``activate_cutover``/``DualSendError``):
     같은 대상에 legacy 경로와 신규 ``DeliveryRule`` 이 동시에 실제 전송 가능하면 거부한다(NFR-24).
  8c. **rollback dedup 보존**(``roll_back_cutover``/``RollbackResult``): 신규 ``DeliveryRule`` 을
     ``enabled=False`` soft-delete + legacy 복구 표현 + 입력 ``DeliveryLog`` 를 **변경·삭제 없이
     보존**해 재전송이 중복을 만들지 않게 한다(NFR-25).

**순수·결정적·동기·의존성 0(2.5/2.6/2.7/3.1~3.7 토대 제약 계승).** FastAPI/SQLAlchemy/async
의존이 0이고, 내부에서 ``datetime.now()``/``uuid4()``/``random``/``time.sleep``/파일 I/O를
직접 하지 않는다 — ``message_id``/``snapshot_id``/``now`` 는 호출부 주입(3.3 규약), crawler는
``crawl_snapshot`` 콜백 주입(3.1 규약, fake로 테스트), 기준선은 ``MigrationSeed`` 주입(2.7
산출물), cutover 가능 상태는 불리언 인자 주입. 같은 입력 → 같은 결과.

**런타임 미배선(범위 경계).** ``rider_crawl``·2.7 ``runner.py`` 본문·3.1~3.7 ``services/`` 본문·
도메인 모델·enum은 **import해 compose만** 하고 한 줄도 바꾸지 않는다(NFR-20). ``assert_no_dual_active_send``/
``activate_cutover``/``roll_back_cutover`` 는 **순수 정책/표현 함수**(런타임 enforcement·DB
트랜잭션·실제 legacy 프로세스 종료 아님) — 실제 영속·async·kill switch·canary·Admin UI·
legacy 폴러 물리 종료는 Epic 5/P6가 같은 ``migration/`` 디렉터리에 additive로 덧붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Sequence

from rider_crawl.config import AppConfig
from rider_crawl.models import CrawlSnapshotResult
from rider_crawl.redaction import redact
from rider_server.domain import DeliveryLog, DeliveryRule, Message
from rider_server.migration.runner import (
    MigrationSeed,
    TargetMigration,
    activate,
    approve,
    roll_back,
)
from rider_server.services import CrawlService, MessageRenderService

# 재사용 경계(FR-2): 수집=``CrawlService.crawl``(3.1)·렌더=``MessageRenderService.render_message``
# (3.3) 재사용 — 재구현 금지. dry-run은 수집·렌더까지만이고 ``send``/dispatch를 호출하지 않는다
# (실발송 0 — operations-security-test E2E dry-run). ``SnapshotNormalizer``(3.2) fail-closed는
# 렌더러가 이미 ``MissingPerformanceDataError`` 로 보장하므로 별도 정규화 wiring을 두지 않는다.


# ── Task 1: dry-run 실행기(실발송 없음 — 구조적 no-send) ──────────────────────


@dataclass(frozen=True)
class DryRunResult:
    """dry-run 1회 산출(migration-레이어 값 객체, 2.7 ``TargetMigration``/``MigrationSeed`` 선례).

    **불변식: ``sent`` 는 항상 False** — dry-run은 실발송하지 않는다(생성 시 default만 쓰고
    True로 만들지 않는다). 신규 메시지 hash는 ``message.text_hash`` 로 도달한다(중복 보관 안 함).
    """

    target_id: str
    message: Message
    sent: bool = False


def run_dry_run(
    config: AppConfig,
    *,
    crawl_snapshot: Callable[[AppConfig], CrawlSnapshotResult],
    target_id: str,
    message_id: str,
    snapshot_id: str,
    source_label: str = "",
    now: datetime | None = None,
) -> DryRunResult:
    """실발송 없이 수집·렌더만 compose해 신규 ``Message`` 를 만든다(AC1·AC4).

    (1) ``CrawlService.crawl``(3.1 — fake ``crawl_snapshot`` 주입, 외부 브라우저 미호출),
    (2) ``MessageRenderService.render_message``(3.3 — 렌더 재구현 금지·``text_hash`` 안정),
    (3) ``DryRunResult(sent=False)``. **``send``/sender 인자가 없고** dispatch 모듈을
    호출하지 않아 실발송 경로가 코드에 존재하지 않는다(구조적 no-send). crawl/render 예외는
    삼키지 않고 전파한다(fail-closed — 잘못된 메시지로 이어지지 않음). ``now=None`` 이면 3.3
    렌더러의 기존 ``now()`` 동작을 보존한다(본 함수가 새 비결정성을 도입하지 않음).
    """

    snapshot = CrawlService.crawl(config, crawl_snapshot=crawl_snapshot)
    message = MessageRenderService.render_message(
        snapshot,
        message_id=message_id,
        snapshot_id=snapshot_id,
        source_label=source_label,
        now=now,
    )
    return DryRunResult(target_id=target_id, message=message)


# ── Task 2: 기준선 비교(old seed hash vs new text_hash) ───────────────────────


@dataclass(frozen=True)
class DryRunComparison:
    """dry-run 신규 메시지와 기준선(old ``last_message`` hash)의 hash 비교 결과."""

    target_id: str
    matches: bool
    baseline_hash: str | None
    new_hash: str
    preview_redacted: str


def compare_to_baseline(
    result: DryRunResult, *, seed: MigrationSeed | None
) -> DryRunComparison:
    """2.7 ``MigrationSeed.message_hash`` 와 신규 ``text_hash`` 를 **hash 대 hash** 로 비교(AC2).

    ``matches = (seed is not None and seed.message_hash == result.message.text_hash)`` —
    ``seed=None``(첫 발송 전 탭)이면 ``matches=False``(차이로 취급, fail-closed: 자동 활성화
    금지). old 평문 메시지는 보관되지 않으므로(hash만 승계) 재수집·재렌더가 아니라 hash
    동일성 비교다. ``preview_redacted`` 는 이미 3.3에서 redact 통과·길이 cap된
    ``text_redacted_preview`` 를 재사용한다(원문 영속 최소화·NFR-5). 내부 ``now()`` 미호출(결정적).
    """

    new_hash = result.message.text_hash
    baseline_hash = seed.message_hash if seed is not None else None
    matches = baseline_hash is not None and baseline_hash == new_hash
    return DryRunComparison(
        target_id=result.target_id,
        matches=matches,
        baseline_hash=baseline_hash,
        new_hash=new_hash,
        preview_redacted=result.message.text_redacted_preview,
    )


# ── Task 3: 승인 게이트(차이 시 자동 활성화 차단) — 2.7 ``approve`` wrapping ──


class CutoverApprovalError(ValueError):
    """메시지 차이가 있는데 운영자 확인·승인 없이 ``APPROVED`` 로 진행하려 할 때(fail-closed).

    2.7 ``runner._transition`` 의 ``ValueError`` 계승(``ValueError`` 하위) — 승인 게이트 위반을
    호출부가 일관되게 잡을 수 있게 한다.
    """


def approve_after_review(
    target: TargetMigration,
    comparison: DryRunComparison,
    *,
    operator_acknowledged_diff: bool,
) -> TargetMigration:
    """차이를 운영자가 확인·승인한 대상만 2.7 ``approve`` 로 ``APPROVED`` 전이시킨다(AC2).

    ``comparison.matches`` 가 False인데 ``operator_acknowledged_diff`` 가 False면
    ``CutoverApprovalError``(차이를 운영자가 확인·승인하지 않음 → ``APPROVED`` 차단). ``matches=True``
    이거나 운영자가 차이를 명시 승인(``operator_acknowledged_diff=True``)한 경우에만 2.7
    ``approve(target)``(``DRY_RUN_PASSED``→``APPROVED``)에 compose한다 — target이
    ``DRY_RUN_PASSED`` 가 아니면 2.7 ``_transition`` 이 ``ValueError`` 로 막는다(게이트 약화 0).
    **승인 전이 재구현 0.** 예외 breadcrumb은 ``redact()`` 통과(평문 비노출 — hash는 이미 sha256).
    """

    if not comparison.matches and not operator_acknowledged_diff:
        raise CutoverApprovalError(
            redact(
                f"승인 차단: target={comparison.target_id} 신규 메시지가 기준선과 다른데"
                " 운영자가 차이를 확인·승인하지 않았다(operator_acknowledged_diff=False)."
            )
        )
    return approve(target)


# ── Task 4: cutover 동시전송 가드(NFR-24) — old/new 동시 실전송 방지 ──────────


class DualSendError(ValueError):
    """같은 대상에 legacy 런타임 경로와 신규 ``DeliveryRule`` 이 동시에 실제 전송 가능할 때(NFR-24).

    둘 다 켜진 채 cutover하면 고객이 같은 실적을 두 번 받는다(중복 발송 disaster) → fail-closed
    로 surface. 2.7 전이 ``ValueError`` 계승.
    """


def assert_no_dual_active_send(
    *, target_id: str, legacy_path_active: bool, new_rule_enabled: bool
) -> None:
    """legacy·신규가 둘 다 전송 가능하면 ``DualSendError``(순수 불리언 가드, AC3·NFR-24).

    실제 sender를 들고 있지 않고 "전송 가능 상태"만 검사한다 — 실제 legacy 프로세스 종료·
    런타임 스위치는 Epic 5. 예외 breadcrumb에 ``target_id`` 만(평문 secret 0 — ``redact()`` 통과).
    """

    if legacy_path_active and new_rule_enabled:
        raise DualSendError(
            redact(
                f"동시전송 차단: target={target_id} legacy 경로와 신규 DeliveryRule이 동시에"
                " 전송 가능하다 — 둘 중 하나는 반드시 꺼져 있어야 한다(NFR-24)."
            )
        )


@dataclass(frozen=True)
class CutoverResult:
    """cutover 활성화 산출 — 신규 rule을 enable하고 legacy는 꺼진 상태(``legacy_path_active=False``)."""

    target: TargetMigration
    enabled_rules: tuple[DeliveryRule, ...]
    legacy_path_active: bool


def activate_cutover(
    target: TargetMigration,
    rules: Sequence[DeliveryRule],
    *,
    legacy_path_active: bool,
) -> CutoverResult:
    """legacy가 꺼진 상태에서만 2.7 ``activate`` 로 ``ACTIVE`` 전이 + 신규 rule을 enable(AC3).

    (1) ``assert_no_dual_active_send``(legacy가 켜진 채 신규 enable 시도 → ``DualSendError`` —
    legacy를 먼저 꺼야 함), (2) ``activate(target)``(2.7 — ``APPROVED``→``ACTIVE``; ``APPROVED``
    가 아니면 2.7 ``_transition`` 이 ``ValueError`` 로 승인 없는 활성화 차단), (3) 신규 rules를
    ``replace(rule, enabled=True)`` 로 enable, (4) ``CutoverResult(legacy_path_active=False)``.
    **활성화 전이 재구현 0**(2.7 ``activate`` 위임). target_id는 breadcrumb용으로 rules/target
    에서 도출한다(혼합 시 일관성 검증은 호출부/Epic 5 — 본 함수는 단순 가드).
    """

    assert_no_dual_active_send(
        target_id=_target_id_for(target, rules),
        legacy_path_active=legacy_path_active,
        new_rule_enabled=True,
    )
    activated = activate(target)
    enabled_rules = tuple(replace(rule, enabled=True) for rule in rules)
    return CutoverResult(
        target=activated,
        enabled_rules=enabled_rules,
        legacy_path_active=False,
    )


# ── Task 5: rollback(dedup 보존, NFR-25) — 신규 비활성화 + legacy 복구 + 로그 보존 ──


@dataclass(frozen=True)
class RollbackResult:
    """rollback 산출 — 신규 rule ``enabled=False``, legacy 복구, dedup 로그 보존(NFR-25)."""

    target: TargetMigration
    disabled_rules: tuple[DeliveryRule, ...]
    preserved_logs: tuple[DeliveryLog, ...]
    legacy_path_restored: bool


def roll_back_cutover(
    target: TargetMigration,
    rules: Sequence[DeliveryRule],
    *,
    delivery_logs: Iterable[DeliveryLog],
) -> RollbackResult:
    """신규 ``DeliveryRule`` 비활성화 + legacy 복구 표현 + dedup 로그 보존(AC3·NFR-25).

    (a) ``replace(rule, enabled=False)`` 로 신규 rule soft-delete(물리 삭제 금지, 2.5 패턴),
    (b) 입력 ``delivery_logs`` 를 **변경·삭제 없이 그대로 보존**(``preserved_logs`` — dedup 기록
    유지라 rollback 후 재전송이 중복을 만들지 않는다. 로그를 지우면 dedup seed가 사라져 rollback이
    중복 발송을 유발하는 disaster가 된다), (c) 2.7 ``roll_back(target)``(임의 상태→``ROLLED_BACK``)
    에 compose, (d) ``RollbackResult(legacy_path_restored=True)``. **로그를 만들거나 지우지 않는다**
    (보존만) — 실제 DeliveryLog 영속·DB·legacy 런타임 복구 스위치는 Epic 5.
    """

    disabled = tuple(replace(rule, enabled=False) for rule in rules)
    preserved = tuple(delivery_logs)
    rolled = roll_back(target)
    return RollbackResult(
        target=rolled,
        disabled_rules=disabled,
        preserved_logs=preserved,
        legacy_path_restored=True,
    )


def _target_id_for(target: TargetMigration, rules: Sequence[DeliveryRule]) -> str:
    # breadcrumb용 식별자 도출(가드 예외 메시지). rules가 있으면 그 target_id, 없으면
    # 매핑된 monitoring_target.id, 그것도 없으면 legacy_alias. 평문 secret 아님(id/alias만).
    if rules:
        return rules[0].target_id
    mapping = target.mapping
    if mapping is not None:
        return mapping.monitoring_target.id
    return target.legacy_alias
