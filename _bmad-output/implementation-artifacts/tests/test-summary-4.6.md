# 테스트 자동화 요약 — Story 4.6 (KakaoSenderWorker)

**워크플로:** bmad-qa-generate-e2e-tests
**대상:** Story 4.6 — `KakaoSenderWorker`(FIFO 단일-세션 직렬 전송 + 정확한 채팅방 검증 매핑 + heartbeat `kakao_status` 소스 배선 + `KAKAO_SEND` `execute_job` 라우팅 + startup 배선)
**테스트 프레임워크:** pytest (`pyproject.toml`: `pythonpath=["src"]`, `testpaths=["tests"]`)
**실행:** `.venv/Scripts/python.exe -m pytest -q`
**날짜:** 2026-06-14

> QA 자동화 역할 — 기존 구현(상태 `review`)에 대해 **AC 대비 커버리지 갭만 발굴·보강**했다. 코드 리뷰/스토리 검증은 본 워크플로 범위 아님. **제품 코드(`src/`)는 0줄 변경**, 테스트만 추가.

## 생성/보강 테스트

이 프로젝트의 Agent 측 도메인은 순수 동기(KakaoTalk/UI/네트워크/실 시계/실 thread 장기 대기는 주입 fake)라 UI E2E 표면이 없다 → 단위/통합 레벨 pytest로 검증. 실 KakaoTalk PC 앱·`pyautogui`/`pyperclip`·실 네트워크 미호출(가짜 방명/메시지/job_id만).

신규 6건 — `tests/agent/test_kakao_sender.py` (기존 28건 → 34건):

- [x] `test_execute_fail_closed_when_no_result_within_timeout` — **`execute()` 결과 미수신 fail-closed 분기**(소비자 미기동/타임아웃 → hang/이중 성공 없이 `KAKAO_FAILURE`, 임의 전송 0, `failed`/`last_error_code` 집계). 기존엔 payload 누락 fail-closed만 검증됨. (AC4)
- [x] `test_build_execute_job_default_fallback_rejects_unknown_type` — **라우터 기본 fallback(`default_execute_job`) 분기**: KAKAO_SEND 외 미지원 type은 `UNSUPPORTED_JOB_TYPE`로 종결되고 워커로 새지 않음. 기존엔 명시 fallback만 검증됨. (AC4)
- [x] `test_start_kakao_sender_respects_explicit_enabled_override` — **`enabled` 명시 override 분기(양방향)**: caps에 KAKAO_SEND 없어도 `enabled=True`면 기동, 있어도 `enabled=False`면 미기동. 기존엔 capability 추론 경로만 검증됨. (AC4)
- [x] `test_run_agent_start_kakao_sender_but_not_capable_stays_disabled` — **`run_agent(start_kakao_sender=True)` + 비-capable caps(crawler-only 노드)**: 워커 미기동·`kakao_status="disabled"` 무회귀. 기존엔 capable+기동 / `start_kakao_sender=False`만 검증(미커버 조합). (AC4)
- [x] `test_stop_is_safe_on_never_started_worker` — 미기동 워커 `stop()` idempotency(`_thread is None` 분기, 예외 없음). (AC1.3)
- [x] `test_queue_depth_and_lag_return_to_zero_after_drain` — **드레인 후 depth/lag=0**: 처리된 항목은 대기 집합에서 빠져(시계 진행해도 lag 0) 결정적. 기존 lag 테스트는 소비자 미기동(대기 누적)만 검증. (AC3)

## 발굴한 갭 → 보강 매핑

| 갭 | 미검증 경로 | AC | 보강 테스트 |
|---|---|---|---|
| 결과 미수신 fail-closed | `kakao_sender.py:322-327`(`execute` "no result") | AC4 | `test_execute_fail_closed_when_no_result_within_timeout` |
| 라우터 기본 fallback | `:396·409`(`fallback=default_execute_job`) | AC4 | `test_build_execute_job_default_fallback_rejects_unknown_type` |
| `enabled` override(양방향) | `:436-440`(`enabled if not None`) | AC4 | `test_start_kakao_sender_respects_explicit_enabled_override` |
| run_agent 비활성 노드 | `job_loop.py:808-830`(start_kakao_sender=True·비-capable) | AC4 | `test_run_agent_start_kakao_sender_but_not_capable_stays_disabled` |
| 미기동 stop idempotency | `:303-305`(`_thread is None`) | AC1.3 | `test_stop_is_safe_on_never_started_worker` |
| 드레인 후 depth/lag 0 | `:287-289·240-242`(처리 시 `pending_ts.pop(0)`) | AC3 | `test_queue_depth_and_lag_return_to_zero_after_drain` |

## 커버리지

- **AC1**(FIFO 단일-세션 직렬 + 병렬 입력 금지 + 주입 primitive): enqueue==처리 순서 + 동시 enqueue 중첩 0(`max_active==1`) + stop 즉시 깨움 + start idempotent + **미기동 stop idempotency** 검증.
- **AC2**(정확한 방 검증 reuse + 실패 매핑): `KakaoUnsafeSelectionError`→`kakao_ambiguous_room`(전송 0)·`KakaoSendError(ambiguous=True)`→`kakao_unconfirmed`(재시도 0)·그 외→`kakao_failure`·성공→`make_success_result` + best-effort(예외가 thread 안 죽임) + `error_code` 값 정합 검증.
- **AC3**(전송량·queue lag 보고 + 자동 다른-방 복구 금지): 주입 `now`로 lag 결정적(빈 큐 0·대기 시 lag>0·**드레인 후 0**) + `sent`/`failed`/`depth`/`last_error_code` 집계 + 기본 경로 `send_kakao_text`(≠`dispatch_text_message`) + 실패 시 다른 방 재전송 0 검증.
- **AC4**(heartbeat 배선 + `KAKAO_SEND` 라우팅 + startup + 비노출): `build_agent_components`/`run_agent` provider 배선(미배선=`disabled` 무회귀) + KAKAO_SEND→워커·그 외→**기본/명시 fallback** + **payload 누락 & 결과 미수신 fail-closed** + startup capability/**`enabled` override**/**비활성 노드** 기동·미기동 + 종료 시 `stop()`+join + raw 방명/메시지/token 0건 검증.
- **누출 가드**: result/`kakao_status`/heartbeat payload/로그에 raw 방명·메시지·token 0(예외 본문에 방명이 들어 있어도) 단언.

## 검증 결과

- `pytest tests/agent/test_kakao_sender.py -q` → **34 passed** (28 + 6).
- 전체 스위트 `pytest -q` → **1208 passed** (보강 전 1202 → 신규 6건 반영, **0 회귀**).
- **4.1 AST 가드 green**: `tests/agent/test_agent_package.py` 통과 — third-party root==`rider_crawl`, sync(async 0), 단방향(`rider_server` import 0), deps 정확히 9핀, reuse `is` identity. `rglob`이 신규 `workers/` 하위까지 자동 검사.
- **`FailureCategory` 7-멤버 lock 무회귀**: `tests/server/test_domain_states.py` 통과 — 새 카테고리 0(`kakao_ambiguous_room`은 하위 사유, 카테고리 아님).
- 누출 가드: 신규 테스트에 실 토큰/`chat_id`/한국 휴대폰/이메일/OTP 0건(가짜값 `room-fake-…`/`msg-fake-…`/`agtok-fake-…`만).
- 스코프: 제품 코드(`src/`) 0줄 변경 — 테스트 파일만 추가(`test_kakao_sender.py`는 untracked 신규라 `git diff`에 안 보임; 추적된 `src/` 변경은 dev-story 단계의 `job_loop.py` additive뿐).

## 다음 단계

- CI에서 신규 케이스 포함 실행.
- Epic 5(서버 측 `KAKAO_SEND` job 생성·queue·`kakao_status` 수신/저장·`messenger_channels.kakao_room_name` 등록 검증·Admin kakao lag runbook) 구현 시, 서버 측 수신/저장 + queue lag>120s 알림 round-trip E2E 추가.
