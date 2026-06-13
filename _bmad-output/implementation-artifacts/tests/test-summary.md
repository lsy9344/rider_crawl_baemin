# Test Automation Summary — Story 4.9 (쿠팡 Gmail 2FA 메일함 분리·lock)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 4.9는 UI가 없는 **쿠팡 Gmail 2FA primitive**(`src/rider_agent/auth/coupang_gmail_2fa.py`)
다 — mailbox 별 token 분리 helper + `MailboxLockRegistry` + 실패 분류기 + bounded 복구
orchestrator. 실제 `CRAWL_COUPANG` 수집 워커·서버 OAuth onboarding·`gmail_reauth_required_count`
알림은 Epic 5 소유라, 테스트는 **주입 fake `recover`/`store`(fake codec)/`now`/`sleep`/
`recover_session`/`fetch_code`** 에 대한 결정적 검증 형태다(4.x 표준). 외부(실 Gmail/실 DPAPI/
실 쿠팡 화면/실 시계) 미호출, 가짜 값만(`mailbox-fake-…`/`otp-fake-…`/`…-fake-token`).

dev-story가 `tests/agent/test_coupang_gmail_2fa.py`에 30건을 만든 상태에서, AC 대비 **테스트가
비어 있던 격차**를 찾아 자동 보강(auto-apply)했다. **프로덕션 소스 0줄 변경**(QA 워크플로는
테스트만 생성).

## Generated / 보강 테스트

`tests/agent/test_coupang_gmail_2fa.py` (+8건, 기존 30 → **38건**). 기존 헬퍼(`_store` fake
codec·`_recover`·`_FakeConfig`) 재사용, 신규 추상화 0.

| # | 테스트 | 커버한 격차 | AC |
|---|--------|-------------|----|
| G1 | `test_secret_storage_policy_gmail_token_agent_local_otp_not_stored` | `classify_secret_storage`: `gmail_oauth_token`→`agent_local`, `otp`→`not_stored` 저장 분류 정합(docstring 주장만 있고 테스트 0) | AC1.2 |
| G2 | `test_reauth_predicate_that_raises_fails_closed_to_transient` | 주입 `is_reauth` predicate 가 **던져도** GMAIL_REAUTH 오분류 0 → transient(bounded) fail-closed(predicate 가 분류 오도 불가) | AC3·AC4 |
| G3 | `test_orchestrator_releases_lock_on_failure_path` | 실패/예외 경로에서도 mailbox lock **항상 해제**(`finally`) → 다음 복구 hang 0. 기존엔 성공 경로 해제만 증명 | AC2.6 |
| G4 | `test_build_coupang_recover_default_recover_session_consumes_reuse` | 기본 `recover_session` 이 reuse `recover_coupang_session_with_email_2fa` **그 객체**(OTP/컷오프/query 필터/코드 파싱 위임·재구현 0 계약) | AC2.5 |
| G5 | `test_success_result_surfaces_ref_only_no_plaintext_mailbox` | **성공 경로** 누출 가드 — 평문 mailbox(이메일) 0, 해시 ref 만. 기존 누출 가드는 실패 경로만 | AC3/NFR-5 |
| G6 | `test_default_registry_serializes_same_mailbox_across_calls` | `locks` 미주입 시 전역 `_DEFAULT_LOCKS` 공유 → 같은 mailbox 호출 간 직렬화(모듈 주석이 경고하는 동작) | AC2 |
| G7 | `test_success_metrics_surface_attempt_count_on_nth_attempt` | 재시도 후 성공도 `metrics["attempts"]` 운영 표면화(bounded 카운터 관측) | AC4·AC10 |
| G9 | `test_classify_recovered_false_takes_precedence_over_reauth` | 분류기 우선순위: `recovered=False`(사람 조치) > `is_reauth`(순수 함수 계약) | AC3 |

기존 30건(유지): token 분리 7(round-trip·ref-only·두 mailbox 다른 ref·교차 resolve 0·미저장
None·opaque ref·🚨 회귀 트랩 per-mailbox token 경로 분기·fetch_code 위임), lock 4(같은 객체·
예외 해제·같은 mailbox 직렬 max-active-1·다른 mailbox 병렬), 분류기 8(True/False/reauth/transient/
모호 fail-closed/secret 미노출/쿠팡≠배민/rider_server enum 부재), orchestrator 7(성공·False 즉시
멈춤·reauth 즉시 멈춤·transient bounded·N회째 성공·단일 transient 사유·상한 유한), 누출 가드 2,
import-safety·단방향·sync 2.

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — token mailbox 분리·서버 ref 만·고객 간 비공유 | ✅ 기존 7 + G1(저장 분류 정합)/G5(성공 경로 ref-only) |
| AC2 — 같은 mailbox lock 직렬·다른 병렬·요청시각/필터 검색(reuse 위임)·결정적 해제 | ✅ 기존 4 + G3(실패 경로 해제)/G4(reuse 위임)/G6(기본 등록부 직렬화) |
| AC3 — 민감값 0 노출·CAPTCHA→USER_ACTION_REQUIRED·reauth→GMAIL_REAUTH_REQUIRED | ✅ 기존 10 + G2(predicate fail-closed)/G5(성공 누출)/G9(분류 우선순위) |
| AC4 — bounded·반복 인증 요청 0·탭 중지 정책 유지·운영 상태 표면화 | ✅ 기존 7 + G2/G7(성공 attempts) |

`coupang_gmail_2fa.py` 공개 표면 전부 커버: `mailbox_token_ref`/`store_mailbox_token`/
`resolve_mailbox_token`/`mailbox_token_path`/`MailboxLockRegistry`(`lock_for`·`acquire`)/
`classify_coupang_2fa_outcome`/`recover_coupang_mailbox`/`build_coupang_recover`.

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- `tests/agent/test_coupang_gmail_2fa.py -q` → **38 passed**
- 전체 스위트 `-q` → **1316 passed, 0 failed** (보강 전 1308 + 신규 8, 순수 additive·회귀 0)
- 4.1 가드 green: sync·단방향(`rider_server` 0)·third-party root==`rider_crawl`·deps 9핀·
  reuse seam import-safe(`googleapiclient` 미로드). enum/"정확히 N개" lock 0.

## 범위/누출 검증

- 이번 QA 라운드 변경은 `tests/agent/test_coupang_gmail_2fa.py` 에 테스트 추가뿐. 프로덕션
  코드(`coupang_gmail_2fa.py`)·`src/rider_crawl`·`src/rider_server`·`pyproject.toml`·`job_loop.py`·
  `secure_store.py`·`baemin_auth.py`·`reuse.py` **0줄 변경**(`git status`: 신규 모듈/테스트는 `??`,
  src 트리에 `M` 없음).
- 누출 가드: 신규 테스트는 가짜 식별자만(`mailbox-fake-…`/`otp-fake-…`/`…-fake-token`). G2/G5
  는 OTP/token/refresh/평문 mailbox 가 result_json·metrics·error_message_redacted·log 에 0건임을
  단언(성공·실패 경로 양쪽).
- 역방향 의존(`rider_crawl`→`rider_agent`) 신규 0건. `coupang_gmail_2fa.py` async 0·`rider_server`
  import 0(G 테스트는 기존 AST 가드 유지).

## 체크리스트 결과(`checklist.md`)

- [x] API/primitive 테스트 생성(쿠팡 2FA 복구 primitive — 주입 fake `recover`/`store`/`now`/`sleep`) / E2E(해당 없음 — UI 없는 라이브러리 primitive, 워커/서버는 Epic 5)
- [x] 표준 프레임워크 API(pytest, threading, `pytest.raises`, fake codec/sleep)
- [x] happy path(token round-trip·복구 성공·병렬) + 임계 케이스(CAPTCHA→USER_ACTION_REQUIRED·reauth→GMAIL_REAUTH·transient bounded·predicate fail-closed·누출 가드)
- [x] 전 테스트 통과(38/38, 전체 1316) / 의미 있는 단언 / 명확한 docstring / 순서 독립(각 케이스 자체 fixture/registry)
- [x] 요약 작성 · 적정 위치(`tests/agent/`) 저장 · 커버리지 명시

## Next Steps

- reauth predicate 실 binding(어떤 예외가 Gmail 재승인인지)·실 OAuth token 파일 생성/갱신 위치는
  **운영/Epic 5 소유** — 본 primitive 는 주입 seam(`is_reauth`/`recover`/`fetch_code`)만 제공.
  미래 `CRAWL_COUPANG` 워커(`crawl_worker.py`, 미존재)가 primitive 를 소비할 때 통합 경로 테스트 추가.
- `gmail_reauth_required_count` 알림·서버 측 mailbox lock 관측은 Epic 5 배선 후 검증.
- CI 비-Windows import-safety 케이스가 실 DPAPI/Gmail 미로드를 계속 보장(회귀 가드).

## 비고

- `time.sleep(0.02)` 사용 2건(G6 + 기존 직렬화 테스트)은 "겹칠 기회"를 만드는 **동시성 오버랩
  창**으로, polling 대기가 아니라 기존 파일 패턴을 따른 결정적 단언용이다. orchestrator backoff 는
  주입 `sleep`(`lambda s: None`)이라 실 대기 0 — "하드코딩 대기 금지"의 취지(flaky polling)에 위배 아님.
