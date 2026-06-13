# 테스트 자동화 요약 — Story 3.1 (run_once 3-분리)

- **워크플로:** bmad-qa-generate-e2e-tests
- **대상 스토리:** `_bmad-output/implementation-artifacts/3-1-run-once를-수집-렌더-전송-서비스로-분리.md` (status: review)
- **테스트 프레임워크:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
- **실행 환경:** `.venv/Scripts/python.exe -m pytest` (WSL `python3` 미설치)
- **날짜:** 2026-06-13
- **성격:** 순수 additive — **테스트 전용**. 제품 코드(`src/rider_crawl/`·`src/rider_server/services/*.py`)·`pyproject.toml` **0줄 변경**.

> 본 서비스들은 순수·동기 도메인 서비스이고 **런타임 미배선**(UI는 계속 `run_once` 호출)이라 HTTP API/UI E2E가 존재하지 않는다. 따라서 E2E의 대응물은 **서비스 경계 통합 테스트**(crawl→render→dispatch 합성이 `run_once`와 동일한 결과를 내는 parity)다.

## 생성/보강된 테스트

파일: `tests/server/test_run_once_split.py` (기존 13 케이스 → **22 케이스**, +9)

### 신규 추가(갭 채움)

| # | 테스트 | 메운 갭 | AC |
|---|---|---|---|
| 1 | `test_crawl_default_adapter_delegates_to_platform_registry` | **기본 adapter 미커버** — 미주입 시 `platforms.crawl_snapshot(config, platform_name=...)` 위임(parity의 근거) | AC1.2 |
| 2 | `test_dispatch_default_adapter_delegates_to_messenger_registry` | 미주입+send_enabled 시 `messengers.dispatch_text_message` 위임 | AC1.2 |
| 3 | `test_dispatch_default_adapter_not_called_in_dry_run` | dry-run 게이팅이 기본 adapter보다 우선(미전송) | AC1.2 |
| 4 | `test_dispatch_message_hash_is_sha256_of_message[False/True]` | `message_hash == sha256(message)` 직접 단언(전송/dry-run 양쪽) | AC1 |
| 5 | `test_render_failure_does_not_reach_dispatch` | FR-7 독립 실패를 render→dispatch 경계로 확장 | AC3 |
| 6 | `test_dispatch_sender_failure_propagates` | sender 실패가 전파(성공 결과 날조 안 함) | AC3/FR-7 |
| 7 | `test_split_parity_source_label_falls_back_to_crawl_name` | parity의 **미커버 분기** — center 빈 값 → `crawl_name` fallback | AC1 |
| 8 | `test_services_reexport_is_additive` | 3 서비스+`DispatchResult` 재노출 + 2.6 `SubscriptionGate` 심볼 보존 | Task 5 |

### 기존(보존, 일부 강화)

- 독립 호출·주입 fake: crawl/render/dispatch (6)
- `DispatchResult` frozen 불변 (1)
- AC3 crawl 예외 전파·다음 단계 미진입 (2)
- run_once parity 4조합(배민/쿠팡 × dry-run/실발송) — `skipped` parity 단언 **추가** (4)
- 단방향 import 가드: `rider_crawl`가 `rider_server` 미import (1)

## 커버리지

- **AC1**(독립 호출·주입 가능·parity): 커버 — fake 경로 + **기본 adapter 위임 경로**(신규) + hash + parity 양 분기.
- **AC2**(호환 경로·단방향): 커버 — `run_once`/`rider_crawl` 0줄 변경, ast 기반 단방향 import 가드, 재노출 additive 가드(신규).
- **AC3**(FR-7 독립 실패): 커버 — crawl/render/dispatch **세 단계 모두** 예외 전파·다음 단계 미진입.
- 서비스 3개 / 공개 메서드 3개: 100% 호출 커버. 기본 adapter(`_default_crawl_snapshot`/`_default_send_message`) 커버 **신규 확보**.

## 발견한 핵심 갭 (Auto-applied)

분리 전 테스트는 **주입 fake 경로만** 잠갔다 — 정작 "run_once와 동일성"을 떠받치는 **기본(미주입) adapter 위임 코드**(`_default_crawl_snapshot`/`_default_send_message`)가 한 줄도 실행되지 않았다. 사용자 지시(Auto-apply)대로 이 갭을 우선 메웠고(#1~3), 부수적으로 hash 직접 단언(#4)·FR-7의 render/dispatch 단계 확장(#5·6)·parity fallback 분기(#7)·재노출 additive 가드(#8)를 추가했다.

## 검증 결과

- 신규 파일 단독: **22 passed** (`tests/server/test_run_once_split.py`)
- 전체 스위트: **808 passed, 0 failed** (Story 기준선 799 대비 +9 = 신규 테스트만큼만 증가, 순수 additive·무회귀)
- 범위: `git diff -w --stat`에 `src/rider_crawl/`·`pyproject.toml` 변경 0줄
- 누출: 신규 테스트 평문 secret/`chat_id`/휴대폰 0건, fake sender는 메모리 리스트 수집(실제 전송 0)

## 다음 단계

- 외부 호출 없는 단위/통합 테스트라 CI에 그대로 편입 가능.
- Story 3.2~3.5에서 Snapshot fail-closed·Message hash·fan-out·DeliveryLog/idempotency가 같은 서비스 경계에 additive로 붙을 때, 본 parity/독립실패 테스트가 회귀 그물로 계속 작동.
</content>
