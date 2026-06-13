# Test Automation Summary — Story 3.5 (DeliveryLog + idempotency)

**Workflow:** `bmad-qa-generate-e2e-tests` · **Date:** 2026-06-13 · **Story:** 3.5 (P2-05, FR-10, ADD-5)
**Framework:** pytest 9.0.3 (Python 3.11, `pyproject.toml` `pythonpath=["src"]`) — used existing project framework.

> Saved as `test-summary-3-5.md` (not `test-summary.md`) to avoid overwriting Story 1.1's QA summary in the same directory.

## Scope

Story 3.5 is a pure-additive backend story (no HTTP API, no UI): `DeliveryLog` (frozen dataclass),
`DeliveryStatus` enum, and `IdempotentDeliveryService` (`build_dedup_key` = 5-field dedup key,
`deliver_once` = insert-then-send + `DeliveryLog`). "E2E" here = end-to-end through the **service
composition** (`Snapshot`→`Message`→`DeliveryRule`→`plan()`→`deliver_once()`), since there is no
network/UI surface. QA role: generate tests + auto-apply discovered gaps only — no code review.

## Existing coverage (dev-authored, baseline)

- `tests/server/test_idempotency.py` — 14 unit tests on the `deliver_once` primitive via a synthetic
  `_job()` (dedup-key 5-dim/determinism, retry-blocked, insert-then-send order, crash-after-send,
  send-exception propagation, false-block prevention, duplicate_blocked audit, frozen/contract, re-export, non-exposure).
- `tests/server/test_domain_models.py` — 2 lock tests (`DeliveryLog` 11th model field-set/defaults, `DeliveryStatus` exactly-2-members).

## Gaps discovered & auto-applied

New file: **`tests/server/test_idempotency_e2e.py`** (5 tests, purely additive). The existing tests
never exercised the real `plan()`→`deliver_once()` composition (only a hand-built job) and missed
several boundary semantics:

| # | Gap | New test | AC |
|---|---|---|---|
| A | No end-to-end fan-out: real `Snapshot`/`Message`/`DeliveryRule`→`plan()`→`deliver_once()`, dedup-key provenance, idempotent re-run (crash/retry blocks all, 0 re-sends) | `test_fanout_plan_then_deliver_once_is_idempotent_across_reruns` | AC1, AC3 |
| A′ | fan-out × idempotency: disabled rule (soft-delete) is skipped while the active channel still sends | `test_fanout_disabled_rule_is_not_dispatched_and_active_channel_still_sends` | AC3 + 3.4 |
| B | **No-release** after `send` failure → retry is `DUPLICATE_BLOCKED` (fail-closed; release = 3.6 boundary). Existing test only checked propagation | `test_send_failure_does_not_release_key_so_retry_is_blocked` | AC2 |
| C | `collected_at` normalization edges: tz-aware determinism + sub-second (1µs) distinctness (no truncation merging two snapshots) | `test_dedup_key_collected_at_normalization_tz_and_subsecond` | AC1 |
| D | Sequential "content changed / new collection time → must send again" through a shared reserve seam | `test_changed_content_or_new_collection_time_is_not_blocked_after_send` | AC3 |

All new tests inject `reserve`/`send`/`log_id_for`/`sent_at`/`collected_at` seams and compose real
domain models **only in the test** (the `dispatch_all`↔`deliver_once` runtime wiring stays Epic 5 —
no product code changed).

## Generated Tests

### E2E / integration tests
- [x] `tests/server/test_idempotency_e2e.py` — 5 tests (gaps A, A′, B, C, D above)

## Coverage

- Acceptance Criteria touched by new tests: AC1, AC2, AC3 (end-to-end provenance + boundary semantics).
- Service surface: `IdempotentDeliveryService.build_dedup_key` + `deliver_once` exercised through the
  real `DispatchFanoutService.plan` fan-out (previously only via synthetic jobs).
- Tests: **887 passed** (baseline 882 at HEAD `3d767cc` + 5 new). 0 regressions.

## Validation (./checklist.md)

- [x] E2E tests generated · standard framework (pytest) · happy path + critical errors (send-failure, crash-after-send)
- [x] All generated tests pass (5/5; full suite 887) · clear descriptions · no sleeps · independent (each builds its own seam)
- [x] Summary created · tests in `tests/server/` · coverage metrics included
- [x] Additive only: `git status` shows only the new test file; no product/`rider_crawl` change; secret-leak grep clean

## Next Steps

- Epic 5 wires `deliver_once` into `dispatch_all`'s `send` callback + real DB `uq_delivery_logs_dedup_key`
  UNIQUE; these E2E tests already pin the expected composition behavior at the seam level.
- Story 3.6 adds failure classification/retry/release — the no-release test (B) documents the current
  3.5 boundary that 3.6 will extend.
