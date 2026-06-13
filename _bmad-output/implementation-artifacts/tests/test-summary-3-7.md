# Test Automation Summary — Story 3.7 (Telegram 중앙 전송 도입)

- **워크플로:** bmad-qa-generate-e2e-tests
- **대상 스토리:** `3-7-telegram-중앙-전송-도입.md` (Status: review)
- **실행일:** 2026-06-13
- **테스트 프레임워크:** pytest (`pyproject.toml`, `pythonpath=["src"]`) — 프로젝트 기존 프레임워크 재사용
- **실행 명령:** `.venv/Scripts/python.exe -m pytest -q` (운영 venv — WSL `python3`는 pytest 미설치)
- **UI E2E:** 해당 없음 — 본 기능은 백엔드 **outbound send-only 어댑터**(UI/브라우저 경로 없음). API/서비스 레이어 테스트로 대체.

## 결과

| 항목 | 수치 |
| --- | --- |
| clean 기준선(HEAD `bf6603f`, 3.6 종료, 3.7 파일 추가 전 — dev 측정) | 923 passed |
| 3.7 dev 구현 포함(untracked 파일 존재 상태, 본인 재측정) | **943 passed** |
| QA 보강 후 전체(본인 재측정) | **949 passed** |
| 신규 추가(이번 QA 워크플로) | **+6** |
| 회귀 | **0** |
| 대상 파일(`tests/server/test_telegram_central_dispatch.py`) | **26 passed** (기존 20 + 신규 6) |

> 주의(A2′): 직접 재측정한 943은 **clean 기준선이 아니라** 3.7 dev의 20개 신규 테스트(untracked)가 이미 포함된 수치다. clean 3.6-end 기준선은 923(dev 측정). 본 QA 정본 = **949 passed**.

## 갭 분석 → 보강한 테스트 (auto-apply)

스토리는 이미 구현(review)되어 20케이스가 존재했다. 아래는 AC/Task에 매핑되지만 **테스트로 고정되지 않았던 seam**을 보강한 것이다.

| # | 신규 테스트 | 메운 갭 | AC / Task |
| --- | --- | --- | --- |
| 1 | `test_as_send_callback_drives_dispatch_all_fanout_send_only` | `as_send_callback()`이 어떤 테스트에서도 호출되지 않았고, 한 대상 → N 채널 fan-out → 중앙 send 경로가 실제 `DispatchFanoutService.dispatch_all` seam을 통해 검증된 적 없음 | AC1, Task 1(3번째 체크박스) |
| 2 | `test_dispatch_all_isolates_central_send_failure_fail_closed` | 중앙 sender의 fail-closed(미등록 채널)가 `dispatch_all` **채널 격리** 안에서 contain되는 경로 미검증 | AC1, AC5 |
| 3 | `test_is_ambiguous_send_failure_classifies_by_exception_type` | 헬퍼가 `send_telegram_text` 래핑을 통해서만 간접 검증됨 — `not isinstance(TelegramSendError)`(raw `OSError`/`ValueError`) 분기와 기본 `ambiguous=False` 분기 미고정 | AC2.5, Task 3 |
| 4 | `test_find_collisions_groups_three_members_and_preserves_input_order` | docstring이 "2개 이상·입력 순서 보존·결정적"을 약속하나 기존 테스트는 **정확히 2개·단일 그룹**만 커버 — 3멤버 그룹·다중 그룹 입력순서 미고정 | AC3, Task 4 |
| 5 | `test_send_forwards_injected_timeout_seconds` | `timeout_seconds` 필드가 `urlopen`으로 전달되나 주입값 전파가 미검증 | AC4, Task 1 |
| 6 | `test_send_default_timeout_seconds_is_forwarded` | 동상 — 기본값(10) 전파 경계 고정 | AC4, Task 1 |

## 기존 커버리지(20케이스) 요약

- **AC1(중앙 send-only):** `sendMessage` 1회·올바른 payload, thread_id 생략, **AST import-엣지** 기반 send-only 가드(`get_telegram_updates`/`TelegramUpdatePoller` 미import), resolve_token seam, unknown/non-telegram 채널 fail-closed.
- **AC2(scope + 채널별 DeliveryLog):** `TelegramRoute.from_channel` 도출(thread_id None/값/빈문자), 3.6 `attempt_delivery` compose 성공→SENT / 실패→`TELEGRAM_FAILURE`.
- **AC2.5(ambiguous 미재전송):** ambiguous 실패 release 안 함 → 2라운드 reserve 충돌→`DUPLICATE_BLOCKED`(send 0); definite 실패 release/재시도.
- **AC3(충돌 검출):** 활성 2채널 충돌, 비활성/Kakao/다른 조합 제외, `None`↔`""` 동일 키, 예외 메시지 redact.
- **AC4(단일 시도·결정성·비노출):** `retry_attempts=1`(이중 재시도 없음), 결정성, 재노출·frozen.

## 품질 게이트(checklist)

- [x] API/서비스 테스트 생성 (UI E2E는 해당 없음 — 백엔드 어댑터)
- [x] 표준 프레임워크 API(pytest)·명확한 테스트명/주석
- [x] happy path + critical error 케이스(fail-closed·ambiguous·definite·채널 격리)
- [x] 전체 통과 — **949 passed**
- [x] hardcoded sleep/wait **0** (fake `urlopen`·in-memory seam)
- [x] 테스트 독립성(케이스마다 fresh fixture, 순서 의존 없음)
- [x] 평문 secret/식별자 **0** (가짜 token `FAKE-TELEGRAM-TOKEN`·가짜 chat_id `-100*`만; digits-id/전화/이메일 grep 0건)
- [x] 의존성 단방향 유지(`grep -rn "import rider_server" src/rider_crawl/` = 0건)
- [x] 순수 additive — 제품 코드 무변경(QA 보강은 untracked 테스트 파일에만; `git diff -w` 제품코드 0줄)

## 다음 단계

- 코드 리뷰(`bmad-code-review`)로 스토리 검증 — 본 워크플로는 테스트 생성만 수행.
- Dev Agent Record의 테스트 수치(943 passed = 923 + dev 20)는 이번 QA 보강(+6)으로 stale: 리뷰 시점 단일 정본 = **949 passed**(clean 923 + dev 20 + QA 6). 리뷰에서 정정 권장(A2′ — memory/stale-test-count-a2).
- ambiguous 미재전송·토픽 충돌의 **런타임 enforcement**·실제 `DeliveryLog` 영속·인바운드 webhook은 Epic 5/3.8 — 본 스토리 범위 밖(정의·seam만).
</content>
</invoke>
