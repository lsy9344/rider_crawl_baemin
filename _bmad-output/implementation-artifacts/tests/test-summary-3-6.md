# 테스트 자동화 요약 — Story 3.6 (수집 실패와 전송 실패 분리, 재시도·실패 상태 관리)

- **워크플로:** bmad-qa-generate-e2e-tests
- **대상 기능:** `DeliveryFailurePolicy`(실패 분류·error_code별 backoff 재시도 결정·parser 반복 실패 경고·`attempt_delivery` 실패-인지 전송) + `FailureCategory`/`DeliveryStatus`
- **테스트 프레임워크:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`) — 신규 프레임워크 도입 없음
- **성격:** 순수 additive — **소스 0줄 변경**, 신규 테스트 파일 1개에만 케이스 추가
- **날짜:** 2026-06-13
- **출력 위치 메모:** 스킬 기본 경로 `tests/test-summary.md`에는 이미 Story 1.1 요약이 있어 덮어쓰지 않고 본 스토리 전용 파일로 저장함.

> 이 스토리에는 HTTP API/UI 표면이 없다(순수 동기 도메인·서비스 라이브러리). 따라서 "API 테스트" = 정책 메서드 계약 테스트, "E2E 테스트" = `attempt_delivery` 다채널 전송 라운드트립 흐름으로 해석했다.

## 발견·자동 적용한 커버리지 공백 (gap → 추가 케이스)

기존 24케이스는 AC1~AC8을 폭넓게 덮고 있었으나, 구현(`delivery_failure_policy.py`)을 분기·경계·기본값 단위로 대조해 **미커버 7건**을 찾아 모두 테스트로 메웠다.

| # | 공백 | 구현 위치 | 추가 테스트 |
|---|------|-----------|-------------|
| 1 | `channel_failure_category` **fail-closed `ValueError`** 미단언(기존 테스트명은 `..._fails_closed`이나 실제론 happy 경로 2개만 검증) | `delivery_failure_policy.py:126` | `test_channel_failure_category_fails_closed_on_unknown_messenger` |
| 2 | `backoff_delay_seconds` **`attempt <= 0` 지수 클램프**(음수 지수·0초·예외 없음) | line 144 `max(0, attempt-1)` | `test_backoff_clamps_exponent_for_nonpositive_attempt` |
| 3 | `decide` 일시-실패 분기가 `TELEGRAM`만 검증 — `KAKAO`/`CRAWL` 미커버 | lines 176-189 | `test_decide_retryable_matrix_is_uniform_across_all_transient_categories` (parametrize ×3) |
| 4 | `decide` 재시도 소진 경계 `attempt == max-1` vs `== max` 미고정 | line 177 `<` / `>=` | `test_decide_retry_exhaustion_boundary_is_strict` (max=2) |
| 5 | `attempt_delivery`가 커스텀 `base/factor/cap`를 `decide→backoff`로 전달하는 plumbing 미검증 | lines 271-278 | `test_attempt_delivery_threads_custom_backoff_params_into_decision` |
| 6 | `parser_warning` **기본 threshold(3)** 경로(인자 생략) 미커버 | line 210 기본값 | `test_parser_warning_uses_default_threshold_when_omitted` |
| 7 | 이미 `SENT`인 채널이 지금은 실패 상태여도 재처리 시 `DUPLICATE_BLOCKED`(send/classify/release 0) — 멱등성 견고성 E2E | reserve 충돌 short-circuit | `test_already_sent_channel_stays_duplicate_even_if_it_would_now_fail` |

> 모두 기존 fixture(`_Seam`/`_attempt`/`_classify_to`) 재사용, 평문 secret 0, 외부 호출 0(in-memory fake), 순서 독립.

## 생성 테스트

### 계약/정책 테스트 (API 상당)
- [x] `tests/server/test_delivery_failure_policy.py` — gap #1~#6 (분류·재시도 결정·backoff·경고 정책 분기/경계/기본값/plumbing)

### E2E 테스트 (전송 흐름 라운드트립)
- [x] `tests/server/test_delivery_failure_policy.py` — gap #7 (`attempt_delivery` 멱등 재처리 — 이미 성공한 채널 재전송·재분류 없음)

## 커버리지

- 정책 메서드: `is_retryable`·`channel_failure_category`(+fail-closed)·`backoff_delay_seconds`(경계 포함)·`decide`(3 일시 카테고리·경계)·`parser_warning`(기본값 포함)·`attempt_delivery`(happy/dup/retry/held/exhausted/plumbing/멱등) = **전 분기 커버**
- `FailureCategory` 7멤버 / `DeliveryStatus` 5멤버 정본 = 잠금됨(기존)
- `delivery_failure_policy.py` 파일 단위 라인/분기 = 추가 후 잔여 미커버 분기 없음(수동 대조 — coverage 플러그인 미설치라 환경 변경 없이 수동 분석)

## 실행 결과

```
# 대상 파일
.venv/Scripts/python.exe -m pytest tests/server/test_delivery_failure_policy.py -q
→ 33 passed   (기존 24 + 신규 9)

# 전체 스위트 (회귀)
.venv/Scripts/python.exe -m pytest -q
→ 923 passed in 4.93s   (기준선 914 + 신규 9, 회귀 0)
```

- **신규 9 케이스 내역:** gap #1·#2·#4·#5·#6·#7 각 1 + gap #3 parametrize 3 = 9
- 실행 환경: `.venv/Scripts/python.exe`(WSL `python3` 미설치 — dev-env 규칙)

## 범위·안전 검증

- `git diff -w` 기준 **소스 0줄 변경** — `delivery_failure_policy.py`(294행) 무변경, 신규 케이스는 untracked 테스트 파일에만 append
- A1′ secret 게이트: 추가 케이스 평문 secret 0(봇토큰/`chat_id`숫자/한국휴대폰/이메일)
- 의존성 단방향: `src/rider_crawl/` → `rider_server` 역import 0

## 검증 체크리스트 (checklist.md)

- [x] API(정책 계약) 테스트 생성 / [x] E2E(전송 흐름) 테스트 생성
- [x] 표준 pytest API 사용 / [x] happy path / [x] critical error 케이스(fail-closed·소진·멱등)
- [x] 전 케이스 통과(33, 전체 923) / [x] 명확한 설명(gap# 매핑 docstring)
- [x] sleep·hardcoded wait 없음(backoff는 계산값) / [x] 케이스 독립(케이스별 `_Seam`)
- [x] 요약 생성 / [x] `tests/server/`에 저장 / [x] 커버리지 지표 포함

## Next Steps

- CI에서 전체 스위트 실행(별도 framework 도입 불필요)
- Epic 5 영속·async wiring 시점에 `reserve`/`send`/`release`/`classify` **실제 어댑터** 통합테스트 추가(현재는 in-memory seam) — 본 스토리는 정의·결정만, 런타임 미배선
