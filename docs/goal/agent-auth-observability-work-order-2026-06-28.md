# Agent Auth Observability Work Order

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

작성일: 2026-06-28  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
근거: 2026-06-28 코드 확인, 기존 `docs/goal` 작업지시서 패턴, Agent 인증/heartbeat 운영 검토

**Goal:** 에이전트 PC와 웹앱 서버 사이에서 로그인 만료, 2차 인증, Chrome 현재 상태, 토큰 불일치가 생겼을 때 운영자가 바로 원인을 볼 수 있게 만든다. 실행 구조는 유지하고 관측 구조만 보강한다.

**Architecture:** 서버가 에이전트 Chrome을 직접 조회하는 pull 구조로 바꾸지 않는다. 에이전트가 heartbeat, job-result, job-event를 서버로 push하는 현재 구조를 유지한다. DB의 `browser_profiles`는 배정 정보, heartbeat의 `browser_profiles`는 현재 런타임 상태로 분리해서 본다.

**Tech Stack:** Python, FastAPI, SQLAlchemy/PostgreSQL, Jinja admin templates, pytest.

---

## 실행 범위 결정

Phase 1 구현자는 Task 0, Task 1, Task 2, Task 3A, Task 4, Task 5 Option A를 진행한다.

- Task 3B는 이번 작업에서 구현하지 않는다. 아래 Future Work 섹션은 후속 설계 결정을 보존하기 위한 참고 자료다.
- Task 5는 Option A(서버 enqueue 차단)를 기본 선택으로 고정한다. Option B/C는 이번 작업에서 구현하지 않는다. 단, 조사 중 이미 `CAPTURE_DIAGNOSTIC` enqueue가 운영상 필요하다는 근거가 나오면 작업을 멈추고 별도 승인 뒤 범위를 다시 연다.
- screenshot/html/clipboard artifact 저장은 Phase 2 정책 문서가 생기기 전까지 구현하지 않는다.

---

## 결론

현재 방향은 맞다. NAT, 방화벽, 에이전트 PC의 로컬 Chrome/CDP 특성 때문에 서버가 에이전트 PC를 직접 잡아당기는 구조는 더 깨지기 쉽다.

문제의 핵심은 실행 구조가 아니라 장애 대응용 관측 구조다. 특히 아래 두 가지가 지금 가장 싸고 효과가 크다.

1. 서버가 이미 보내는 `403 agent token mismatch`를 에이전트가 명확히 처리하지 못한다.
2. 에이전트 heartbeat에 이미 들어오는 Chrome profile 상태가 관리자 화면에 보이지 않는다.

`CAPTURE_DIAGNOSTIC`은 선언만 있고 워커가 없다. 현재 확인된 Admin 수동 crawl 경로는 payload 생성 단계에서 non-crawl type을 막지만, service 경계의 명시 allowlist가 약하다. 실수로 큐에 들어간 경우 Agent는 `UNSUPPORTED_JOB_TYPE` 실패로 닫는다. screenshot/html artifact 수집은 업로드 백엔드, 보존 기간, 크기 제한, redaction 정책이 같이 필요하므로 마지막 단계로 둔다.

## 비범위

- 이 작업에서 서버가 에이전트 Chrome을 직접 호출하는 pull 구조를 만들지 않는다.
- DB `BrowserProfile`과 heartbeat `browser_profiles`를 강제로 동기화하지 않는다.
- parser 오류나 네트워크 오류를 인증 만료로 바꾸지 않는다. `UNKNOWN`은 fail-safe로 유지하고, 대신 왜 `UNKNOWN`인지 보이게 만든다.
- Phase 1에서는 screenshot/html/clipboard artifact 저장을 구현하지 않는다.

## 우선순위

| 순위 | 작업 | 비용 | 이유 |
| --- | --- | --- | --- |
| 1 | heartbeat/job_loop의 403 token mismatch를 identity mismatch로 명시 처리 | 낮음 | 서버는 이미 403을 보내고 있고, 에이전트 분기만 빠져 있다. |
| 2 | 관리자 화면에 기존 heartbeat `browser_profiles.state/cdp_port` 렌더 + DB/heartbeat 역할 라벨 분리 | 낮음 | 서버 `capacity_json`에 이미 저장되고 있어 백엔드 변경이 작고, 같은 화면을 한 번만 수정하면 된다. |
| 3A | heartbeat `browser_profiles`에 `auth_state`, `last_error_code`, `last_probe_at` 같은 최소 진단 필드 추가 | 중간 | page/URL 배선 없이도 CDP, parser, 인증 필요 힌트를 먼저 볼 수 있다. |
| Future 3B | `page_kind`, redacted URL 등 브라우저 페이지 문맥 수집 설계 | 높음 | auth probe와 profile heartbeat가 현재 분리되어 있어 새 데이터 경로가 필요하다. 이번 Phase 1에서는 구현하지 않는다. |
| 4 | 운영 문서와 auth-required 화면의 profile 용어 정리 | 낮음 | Agents 화면의 역할 라벨은 Task 2에서 처리하고, 남은 문서/보조 화면만 정리한다. |
| 5 | `CAPTURE_DIAGNOSTIC` 서버 enqueue 차단 + artifact 기반 진단 설계 문서 | 중간 | capability만 빼면 claim matching 불변식이 깨진다. 이번 작업은 Option A로 고정한다. |

---

## 현재 코드 근거

- `src/rider_server/api/agents.py`는 heartbeat token mismatch를 403으로 응답한다.
- `src/rider_server/api/jobs.py`도 job claim/complete 계열에서 token mismatch를 403으로 응답한다.
- `src/rider_agent/heartbeat.py`의 `_handle_transport_error()`는 401만 registration 필요 상태로 처리한다.
- `src/rider_agent/job_loop.py`의 claim/complete transport error 처리도 401 중심이다. claim의 403은 현재 transient 오류처럼 backoff에 묻힌다.
- `src/rider_agent/registration.py`가 `TransportError`를 정의한다. `src/rider_agent/secure_store.py`는 DPAPI/토큰 저장 책임이므로 HTTP 상태 helper를 넣지 않는다.
- `src/rider_agent/browser_profile.py`의 `browser_profiles()`는 `id`, `target_id`, `agent_id`, `cdp_port`, `state`만 보낸다.
- `BrowserProfileManager.browser_profiles()`는 내부 `ProfileAssignment` registry만 투영한다. `default_login_probe()`와 crawl/auth job 결과가 이 registry로 자동 반영되는 배선은 아직 없다.
- `src/rider_agent/auth/baemin_auth.py`의 `default_login_probe()`는 auth state 문자열만 반환하고, page kind나 현재 URL 문맥을 만들지 않는다.
- `src/rider_agent/workers/crawl_worker.py`의 기본 수집 경로는 process boundary를 통해 `run_crawl_in_subprocess(...)` 결과를 그대로 반환할 수 있다. 따라서 Task 3A 진단 기록은 `_execute_payload()` 예외 분기만이 아니라 process boundary 결과에도 반영되어야 한다. 단, timeout cleanup은 profile assignment를 release할 수 있으므로 timeout 진단은 cleanup 전에 기록하거나 별도 순서를 둬야 한다.
- `src/rider_server/services/agent_registry.py`는 heartbeat `browser_profiles`를 `capacity_json`에 저장하지만 allowlist는 제한적이다. 현재 `profile_path_ref`는 ref 값으로 허용되어 있으나, 관리자 화면의 런타임 상태 표시에는 노출하지 않는다.
- `src/rider_server/admin/dashboard_repository_postgres.py`와 `_agents.html`은 `capacity_json.browser_profiles`를 관리자 화면에 보여주지 않는다.
- DB 모델 `src/rider_server/db/models/agent.py`의 `BrowserProfile`은 `agents.id`/`monitoring_targets.id`에 FK를 가진 배정 관계 테이블이다(런타임 상태가 아니라 배정은 비교적 정적).
- `src/rider_agent/auth/baemin_auth.py`의 `classify_baemin_auth_state()`는 정상 snapshot을 얻으면 `ACTIVE`, `BrowserActionRequiredError`(auth action 필요)는 `AUTH_REQUIRED`, 그 외 parser/연결류 예외는 인증 문제로 단정하지 않고 `UNKNOWN`으로 둔다. 이 fail-safe는 유지한다.
- `DEFAULT_CAPABILITIES`에는 `CAPTURE_DIAGNOSTIC`이 포함되어 있지만 실제 워커는 없다. 동시에 `tests/server/test_job_vocab.py`는 `set(JOB_TYPES) == set(DEFAULT_CAPABILITIES)`를 강제한다.
- `src/rider_agent/jobs.py`는 존재하지 않는다. Agent capability 정본은 `src/rider_agent/heartbeat.py`의 `DEFAULT_CAPABILITIES`다.
- `UNSUPPORTED_JOB_TYPE` 결과는 `src/rider_agent/job_loop.py`의 `default_execute_job()`가 `make_failure_result(ERROR_UNSUPPORTED_JOB_TYPE, ...)`로 만든다. `worker_composition.compose_execute_job()`가 crawl→auth→kakao 워커를 체인하고, 어디에도 매칭되지 않은 job type은 이 `default_execute_job` fallback으로 떨어진다. `crawl_worker.py`의 `make_unsupported(job)`도 결국 이 `default_execute_job`을 호출한다. `UNSUPPORTED_JOB_TYPE` 자체는 현재 retryable failure category가 아니라 보통 terminal `FAILED`로 끝난다. 따라서 Task 5의 위험은 retry 폭주라기보다 미구현 artifact job이 운영 큐에 애매한 실패로 남는 것이다. Option B handler는 이 fallback 체인 안, default fallback 직전에 삽입해야 한다.
- `src/rider_server/services/agent_registry.py`의 sanitizer(`_sanitize_mapping`/`_sanitize_value`)는 현재 `cdp_port` 전용 타입/범위(`1..65535`)를 검증하지 않는다. `cdp_port` 위치에서도 음수·`0`·`999999` 같은 int뿐 아니라 bool/float 같은 non-string scalar가 저장될 수 있다. 따라서 범위와 타입 검증은 "유지"가 아니라 신규 추가 대상이다.
- `src/rider_agent/job_loop.py`에는 `_send_events_with_retry()`가 없다. job event 보고는 현재 `_emit_started()`에서 best-effort로 처리하고, 실패해도 job 실행을 막지 않는다.
- `tests/server/test_admin_dashboard.py`는 Jinja template을 `admin_routes.templates.env.get_template(...).render(...)` 패턴으로 직접 렌더한다. `render_agents_fragment()` helper는 없다.

---

## Task 0 - 기준선 확인

- [ ] 작업 전 git 상태를 확인한다.

```powershell
git status --short
```

Expected: 작업자가 만든 변경 외에 의도하지 않은 변경이 없어야 한다. 기존 변경이 있으면 되돌리지 말고 이 문서에 영향 여부만 판단한다.

- [ ] 관련 테스트 파일을 먼저 확인한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py tests/agent/test_job_loop.py tests/agent/test_browser_profile.py tests/agent/test_crawl_worker.py tests/server/test_admin_dashboard.py tests/server/test_agents_api.py tests/server/test_admin_actions.py tests/server/test_jobs_api.py tests/server/test_job_vocab.py -q
```

Expected: 현재 main 기준으로 통과해야 한다. 기존 실패가 있으면 실패 테스트명과 원인을 기록하고, 이번 작업 검증 범위를 분리한다.

---

## Task 1 - 403 identity mismatch를 에이전트에서 명시 처리

### 목적

토큰 불일치나 agent identity mismatch가 403으로 왔을 때 transient 네트워크 오류처럼 재시도하지 않는다. 운영자와 로그에 "이 에이전트는 재등록/토큰 확인이 필요하다"가 바로 보이게 한다.

### 변경 파일

- `src/rider_agent/heartbeat.py`
- `src/rider_agent/job_loop.py`
- `tests/agent/test_heartbeat.py`
- `tests/agent/test_job_loop.py`

### 구현 단계

- [ ] 공유 helper를 새로 만들지 말고 기존 401 분기를 확장한다.

이유:

- `secure_store.py`는 DPAPI/토큰 저장 책임만 가진다.
- HTTP 실패는 `registration.TransportError.status_code`로 이미 `heartbeat.py`와 `job_loop.py`에서 직접 처리한다.
- 따라서 가장 작은 변경은 기존 `status_code == 401` 분기 옆에 403 처리를 추가하는 것이다.

주의:

- helper가 꼭 필요해지면 `TransportError`가 정의된 `registration.py` 쪽을 검토한다.
- 이번 작업에서는 helper보다 각 call site의 event code 차이를 분명히 두는 것이 더 중요하다.

- [ ] `heartbeat.py`에서 401과 403을 같은 큰 흐름으로 처리하되, 로그/이벤트 코드는 다르게 둔다.

예상 정책:

| status | token_status | needs_registration | event code |
| --- | --- | --- | --- |
| 401 | `TOKEN_STATUS_REVOKED` | `True` | `AGENT_HEARTBEAT_REVOKED` |
| 403 | `TOKEN_STATUS_REVOKED` | `True` | `AGENT_HEARTBEAT_IDENTITY_REJECTED` |

- [ ] `job_loop.py`의 job claim에서 403을 backoff로 보내지 않는다.

수정 대상 흐름:

- `run_once()`의 claim `TransportError` 처리
- `_handle_transport_error()`
- `_complete_with_retry()`의 complete/reporting `TransportError` 처리
- `_emit_started()` event reporting 경로는 확인만 한다. 현재 `_send_events_with_retry()`는 없고, event 보고는 best-effort라 실패해도 job 실행/complete를 막지 않는다.

예상 정책:

| status | 처리 | event code |
| --- | --- | --- |
| 401 | 기존 revoked 처리 유지 | `AGENT_JOB_REVOKED` |
| 403 | revoked와 같은 등록 필요 상태로 전환, identity mismatch 이벤트/로그 사용 | `AGENT_JOB_IDENTITY_REJECTED` |
| 409/410 | 기존 lease lost 처리 유지 | `AGENT_JOB_LEASE_LOST` |
| 그 외 | 기존 transient/backoff 처리 유지 | 기존 call site code |

주의:

- 403을 `AGENT_JOB_REVOKED`로 뭉개지 않는다. 운영자가 "토큰 자체가 invalid/revoked"와 "토큰이 다른 agent identity로 해석됨"을 구분할 수 있어야 한다.
- `ERROR_JOB_IDENTITY_REJECTED = "AGENT_JOB_IDENTITY_REJECTED"` 같은 새 상수를 `job_loop.py`에 추가한다.

event reporting 주의:

- 이번 Task 1의 필수 범위는 heartbeat, claim, complete의 403 identity mismatch 처리다.
- `_emit_started()`에서 `TransportError(401/403)` 처리를 추가하더라도 best-effort 원칙을 유지한다. event 전송 실패가 job 실행이나 complete 보고를 막으면 안 된다.
- 서버 event API는 현재 body `agent_id` 비교가 없어 claim/complete와 같은 403 mismatch 경로가 아니다. 403 event 테스트를 억지로 만들지 않는다.

- [ ] heartbeat 403 테스트를 추가한다.

테스트 의도:

```python
def test_reporter_surfaces_identity_mismatch_on_403_without_crash_or_spin():
    stop = threading.Event()
    sleep = StoppingSleep(stop, stop_after=2)
    transport = FakeTransport(
        error=TransportError("agent heartbeat HTTP error", status_code=403)
    )

    reporter = HeartbeatReporter(
        ..., transport=transport, sleep=sleep, stop_event=stop, ...
    )
    reporter.run()

    assert len(transport.calls) == 2
    assert sleep.intervals
    assert reporter.needs_registration is True
    assert reporter.token_status == TOKEN_STATUS_REVOKED
    assert reporter.last_error_event["code"] == "AGENT_HEARTBEAT_IDENTITY_REJECTED"
```

Expected: 403이 generic error가 아니라 registration 필요 상태로 남는다.

- [ ] job claim 403 테스트를 추가한다.

테스트 의도:

```python
def test_runner_surfaces_identity_mismatch_on_403_claim_without_backoff_spin():
    transport = FakeJobTransport(
        claim_error=TransportError("claim failed", status_code=403)
    )

    runner = _runner(transport=transport, ...)
    runner.run()

    assert runner.needs_registration is True
    assert runner.token_status == TOKEN_STATUS_REVOKED
    assert runner.last_error_event["code"] == "AGENT_JOB_IDENTITY_REJECTED"
```

Expected: 403이 transient backoff로 숨지 않는다.

- [ ] claim 403도 401처럼 backoff 카운터를 올리지 않는지 확인한다.

테스트 의도:

- 기존 `test_claim_401_is_not_backed_off()` 옆에 403 케이스를 추가한다.
- 주입 `StoppingSleep`의 대기 시간이 `DEFAULT_SHORT_POLL_INTERVAL_SECONDS + jitter`로 유지되는지 확인한다.
- 403에서 `_consecutive_claim_failures`가 증가해 지수 backoff로 가지 않도록 검증한다. 가능하면 private 값 직접 확인보다 sleep interval로 검증한다.

- [ ] job complete 403 테스트를 추가한다.

테스트 의도:

- claimed job 실행은 끝났지만 complete가 403을 받는 경우를 만든다.
- `needs_registration`과 `token_status`가 revoked로 남는다.
- `last_error_event["code"] == "AGENT_JOB_IDENTITY_REJECTED"`를 확인한다.
- 409/410 lease lost 처리는 기존 테스트와 동작을 그대로 유지한다.
- 403 complete는 401 complete와 같은 revoked 계열이다. `COMPLETE_REPORT_REVOKED`로 끝나며 local complete outbox record는 `sent`나 `discarded`로 표시하지 않는다. 재등록 전 replay가 다시 403을 받으면 새 claim으로 진행하지 않는 것이 정상이다.
- complete outbox가 켜진 테스트에서는 403 complete 후 pending record가 남고, 다음 replay가 새 claim보다 먼저 시도되는지 확인한다.

- [ ] event reporting 경로는 회귀 확인만 한다.

Expected:

- `_emit_started()`는 여전히 best-effort다.
- event 전송 실패 때문에 job 실행이나 complete 보고가 중단되지 않는다.
- event 401/403을 별도로 처리했다면 error code와 로그만 바뀌고 job 결과 흐름은 바뀌지 않는다.
- 서버 event API는 현재 event body의 `agent_id` mismatch를 검사하는 claim/complete와 같은 403 경로가 아니다. event 403 단위 테스트를 만들려면 fake transport 수준에서 best-effort 동작만 확인한다.

### 검증

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py tests/agent/test_job_loop.py -q
```

Expected: 신규 403 테스트와 기존 401 테스트가 모두 통과한다.

---

## Task 2 - 관리자 Agents 화면에 기존 Chrome runtime 상태 표시

### 목적

이미 heartbeat로 들어와 `capacity_json.browser_profiles`에 저장되는 정보를 운영자 화면에 보이게 한다. 이 단계에서는 에이전트 payload를 늘리지 않는다.

DB `BrowserProfile` 기반 정보와 heartbeat `browser_profiles` 기반 정보는 같은 화면에서 보이더라도 의미가 다르다. `_agents.html`에서 heartbeat 기반 열을 추가할 때 이 역할 구분 라벨도 같이 처리한다.

### 변경 파일

- `src/rider_server/admin/dashboard_service.py`
- `src/rider_server/admin/dashboard_repository_postgres.py`
- `src/rider_server/admin/templates/_agents.html`
- `tests/server/test_admin_dashboard.py`

### 구현 단계

- [ ] `dashboard_service.py`에 화면용 row 모델을 추가한다.

```python
@dataclass(frozen=True)
class AgentBrowserProfileRow:
    profile_id: str
    target_id: str | None
    state: str | None
    cdp_port: int | None
    source: str = "heartbeat"
```

- [ ] `AgentHealthFacts`와 `AgentRow`에 `browser_profiles: tuple[AgentBrowserProfileRow, ...] = ()` 필드를 추가한다.

주의:

- 기본값 없는 필드로 추가하면 기존 `tests/server/test_admin_dashboard.py`의 여러 `AgentHealthFacts(...)` 생성자가 불필요하게 깨진다.
- `AgentBrowserProfileRow`도 `test_readmodel_dtos_have_no_secret_shaped_fields()`의 DTO 목록에 포함한다. 필드명에 `token`, `secret`, `password`, `otp`, `_ref` 같은 값이 들어가면 안 된다.

- [ ] `dashboard_repository_postgres.py`에서 `Agent.capacity_json["browser_profiles"]`를 읽어 안전한 값만 넘긴다.

필터 규칙:

- `id`, `target_id`, `state`는 문자열일 때만 사용한다.
- 문자열은 화면 표시용 길이 제한을 둔다.
- `cdp_port`는 정수이고 `1..65535`일 때만 사용한다.
- `cdp_port`가 float, bool, 문자열이면 버린다.
- control character가 들어간 문자열은 버린다.
- `profile_path`, `password`, cookie, token 같은 값은 읽지도 않고 넘기지도 않는다.
- `profile_path_ref`는 서버 저장 allowlist에 남아 있어도 이 화면용 row에는 넣지 않는다.

주의:

- 현재 `agent_registry.py`의 서버 저장 sanitizer에는 `cdp_port` 범위 검증이 없다. 이 화면용 repository helper에서만 범위를 보면 raw `capacity_json`에는 여전히 잘못된 포트가 남는다. 서버 저장 단계의 범위 검증은 Task 3A에서 함께 신규 추가한다.

- [ ] sanitizer는 `dashboard_repository_postgres.py`에 작고 명시적인 helper로 둔다.

이유:

- raw `capacity_json`은 repository가 DB row에서 읽는다.
- `DashboardService.agent_row(...)`는 이미 정제된 `AgentHealthFacts`만 받아 화면 row로 바꾸는 계층으로 유지한다.

예상 helper:

```python
def _browser_profile_rows(value: object) -> tuple[AgentBrowserProfileRow, ...]:
    ...
```

규칙:

- `capacity_json["browser_profiles"]`가 list가 아니면 빈 tuple을 반환한다.
- 각 item이 dict가 아니면 건너뛴다.
- `profile_id`는 `id`가 안전한 문자열일 때만 만든다. `id`가 없으면 해당 row는 버린다.
- helper는 DB ORM row나 raw capacity dict를 template까지 넘기지 않는다.

- [ ] `_agents.html`에 "Chrome 현재 상태" 열을 추가한다.

표시 예:

```text
READY / target baemin-store-1 / CDP 9222
AUTH_REQUIRED / target coupang-center-2 / CDP 9223
```

표시 원칙:

- 이 열은 `heartbeat runtime`으로 라벨링한다.
- DB 기반 배정 정보가 같은 화면에 있거나 이후 추가되면 `배정 정보`로 라벨링한다.
- 값이 없으면 `-`로 표시한다.
- 상태 이름은 그대로 보여주되, 색상 badge가 이미 있는 패턴이면 기존 badge 스타일을 재사용한다.
- column 추가 후 빈 목록 row의 `colspan`도 실제 열 수와 맞춘다.

- [ ] dashboard service 단위 테스트를 추가한다.

테스트 의도:

```python
def test_agent_row_maps_heartbeat_browser_profiles():
    facts = AgentHealthFacts(
        ...,
        browser_profiles=(
            AgentBrowserProfileRow(
                profile_id="profile-1",
                target_id="target-1",
                state="READY",
                cdp_port=9222,
            ),
        ),
    )

    row = agent_row(facts, now=...)

    assert row.browser_profiles[0].state == "READY"
    assert row.browser_profiles[0].cdp_port == 9222
```

unsafe capacity mapping 테스트 의도:

- raw `capacity_json["browser_profiles"]`에 `profile_path`, `profile_path_ref`, `password`, `token`, `current_url`, 잘못된 `cdp_port`를 넣는다.
- repository helper 또는 `PostgresDashboardRepository.agent_health(...)` 결과에 안전한 `id/state/target_id/cdp_port`만 남는지 확인한다.
- unsafe 문자열이 `AgentBrowserProfileRow` 어느 필드에도 들어가지 않는지 확인한다.

- [ ] template 렌더 테스트를 추가한다.

먼저 `tests/server/test_admin_dashboard.py`의 기존 렌더 패턴을 확인한다.

```powershell
rg "templates.env.get_template|_agents.html" tests/server/test_admin_dashboard.py src/rider_server/admin/routes.py
```

Expected: 별도 `render_agents_fragment()` helper가 아니라 `admin_routes.templates.env.get_template("_agents.html").render(...)` 패턴을 사용한다.

주의: 현재 `test_admin_dashboard.py`에는 같은 패턴이 `_auth_required.html`, `_targets.html`에는 있지만 `_agents.html`을 직접 렌더하는 선례는 없다. 따라서 이 테스트는 기존 선례를 복제하는 것이 아니라 `_agents.html`의 첫 직접 렌더 진입점을 새로 추가하는 것이다. 동일 패턴(`get_template("_agents.html").render(agents=[...])`)을 따르되, `_auth_required.html`/`_targets.html` 호출을 참고 형태로만 본다.

테스트 의도:

```python
def test_agents_fragment_renders_browser_profile_state():
    html = admin_routes.templates.env.get_template("_agents.html").render(
        agents=[
            AgentRow(
                ...,
                browser_profiles=(
                    AgentBrowserProfileRow(
                        profile_id="profile-1",
                        target_id="target-1",
                        state="AUTH_REQUIRED",
                        cdp_port=9222,
                    ),
                ),
            )
        ]
    )

    assert "AUTH_REQUIRED" in html
    assert "9222" in html
    assert "heartbeat runtime" in html
```

negative 테스트 의도:

- template 단위 negative 테스트만으로 unsafe raw capacity 정제를 검증하지 않는다.
- unsafe 값은 `AgentRow`에 들어가기 전 repository helper 또는 `PostgresDashboardRepository.agent_health(...)` 결과에서 막는다.
- `_agents.html` 테스트는 이미 정제된 `AgentBrowserProfileRow`를 렌더하는지만 검증한다. raw `capacity_json`에 있는 `profile_path`, `profile_path_ref`, token, raw URL을 template 테스트 입력에 직접 섞지 않는다.
- 필요하면 repository 테스트에서 unsafe raw capacity를 넣고, 정제 결과와 최종 template HTML 모두에 unsafe 문자열이 없음을 같은 테스트 안에서 확인한다.

### 검증

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py -q
```

Expected: Agents 화면에 기존 heartbeat Chrome 상태가 보이고, unsafe 값은 row 모델과 template 어디에도 렌더되지 않는다.

---

## Task 3A - heartbeat browser_profiles 최소 진단 필드 추가

### 목적

운영자가 `UNKNOWN`, 인증 필요, parser 오류, CDP 연결 문제를 최소한의 텍스트 힌트로 구분할 수 있게 한다. 이 단계에서는 page kind와 URL을 수집하지 않는다.

현재 구조에서 `BrowserProfileManager.browser_profiles()`는 내부 `ProfileAssignment` registry만 투영한다. `default_login_probe()`와 crawl/auth job 결과가 이 registry로 자동 반영되는 배선은 없다. 따라서 Task 3A는 "필드 몇 개 추가"가 아니라 "기존 profile registry에 안전한 진단값을 기록하는 작은 공개 API와 호출 지점 추가"로 진행한다.

중요: 기본 `CrawlWorker` 경로는 process boundary를 타면 child process 결과 `JobResult`를 그대로 반환할 수 있다. 그러므로 진단 기록은 `_execute_payload()`의 개별 예외 분기만으로 끝내면 안 된다. 단, timeout 경로는 cleanup이 profile assignment를 release할 수 있으므로, timeout 진단은 반드시 release 전에 기록한다.

범위 결정: 3A는 **이미 registry에 있는 profile row만 보강**한다. profile 생성 전에 끝나는 실패를 표시하기 위해 heartbeat placeholder row를 새로 만들지 않는다. 따라서 `PAYLOAD_EXPIRED`, 최초 profile 준비 전 `PROFILE_UNAVAILABLE` 같은 실패는 job result에는 남지만, 기존 assignment가 없으면 heartbeat `browser_profiles[]`에는 새 row가 생기지 않는다. profile 없는 실패까지 운영 화면에 별도 row로 보여주는 작업은 Future Work 또는 별도 작업지시서에서 설계한다.

### 변경 파일

- `src/rider_agent/browser_profile.py`
- `src/rider_agent/workers/crawl_worker.py`
- `src/rider_agent/worker_composition.py`
- `src/rider_server/services/agent_registry.py`
- `src/rider_server/admin/dashboard_service.py`
- `src/rider_server/admin/dashboard_repository_postgres.py`
- `src/rider_server/admin/templates/_agents.html`
- `tests/agent/test_browser_profile.py`
- `tests/agent/test_crawl_worker.py`
- `tests/agent/test_job_loop.py` 또는 `tests/agent/test_baemin_auth.py` 중 실제 wrapper 검증 위치
- `tests/agent/test_coupang_gmail_2fa.py` 또는 `tests/agent/test_job_loop.py` 중 `AUTH_COUPANG_2FA` wrapper 검증 위치
- `tests/server/test_agents_api.py`
- `tests/server/test_admin_dashboard.py`

### 3A 실행 순서

3A는 한 번에 구현하지 말고 아래 순서로 나눈다.

1. Agent profile registry 계약: `ProfileAssignment`, `BrowserProfileManager.record_profile_diagnostic(...)`, `browser_profiles()` projection, agent 단위 테스트를 먼저 완성한다.
2. Agent 진단 기록 배선: `CrawlWorker` 최종 `JobResult` 기록, timeout release 전 기록, auth wrapper 기록을 구현한다. 이 단계에서 서버/API/UI는 아직 건드리지 않는다.
3. 서버 저장/화면 표시: `agent_registry.py` sanitizer, dashboard repository/service row, `_agents.html` 표시, server/admin 테스트를 구현한다.

각 단계는 해당 테스트를 먼저 추가하고 통과시킨 뒤 다음 단계로 넘어간다. 3A 진행 중 Future Work 3B 필드(`page_kind`, `current_url_redacted`)를 미리 추가하지 않는다.

### 3A 필드 계약

heartbeat `browser_profiles[]`에 아래 optional 필드를 추가한다.

| field | 예 | 설명 |
| --- | --- | --- |
| `auth_state` | `ACTIVE`, `AUTH_REQUIRED`, `CENTER_MISMATCH`, `UNKNOWN` | 현재 profile에 마지막으로 관측된 인증 상태 요약 |
| `last_probe_at` | `2026-06-28T10:20:30Z` | 이 값을 확인한 시각 |
| `last_error_code` | `CDP_UNREACHABLE`, `PARSER_MISSING_DATA`, `AUTH_REQUIRED` | 실패나 `UNKNOWN`일 때 원인 힌트 |

3A에서는 아래 필드를 추가하지 않는다.

- `page_kind`
- `current_url_redacted`
- raw current URL
- HTML/screenshot/clipboard/cookie/localStorage/token

### 구현 단계

- [ ] `ProfileAssignment`에 optional 진단 필드를 추가한다.

예상 필드:

```python
auth_state: str | None = None
last_error_code: str | None = None
last_probe_at: str | None = None
```

주의:

- raw profile path나 secret 값은 계속 `ProfileAssignment`의 heartbeat projection에 넣지 않는다.
- `last_probe_at`은 ISO-8601 UTC 문자열(`2026-06-28T10:20:30Z` 형태)로 고정한다. timestamp 숫자와 문자열 표현을 섞지 않는다.

- [ ] `BrowserProfileManager`에 공개 갱신 메서드를 추가한다.

예상 형태:

```python
def record_profile_diagnostic(
    self,
    tenant_id: str,
    target_id: str,
    *,
    auth_state: str | None = None,
    last_error_code: str | None = None,
    last_probe_at: str | None = None,
) -> None:
    ...
```

규칙:

- registry에 없는 profile은 새로 만들지 않고 조용히 반환한다.
- 이 규칙 때문에 profile 생성 전 실패는 heartbeat row로 새로 보이지 않는다. 해당 실패는 job result/error_code에서만 확인한다.
- private `_set_state_obj()`를 외부 모듈에서 직접 호출하지 않는다.
- `ProfileAssignment`는 `@dataclass(frozen=True)`다. 직접 속성 대입을 하지 말고 기존 `_set_state_obj()`처럼 `dataclasses.replace(assignment, auth_state=..., last_error_code=..., last_probe_at=...)`로 새 인스턴스를 만들어 registry에 다시 넣는다.
- 문자열은 agent 쪽에서도 길이 상한을 두거나, 최소한 서버 sanitizer가 자르도록 한다.

- [ ] `BrowserProfileManager.browser_profiles()`가 3A optional 필드를 포함한다.

기존 필드 `id`, `target_id`, `agent_id`, `cdp_port`, `state`는 유지한다.
값이 `None`인 optional 필드는 생략해도 된다.

- [ ] `BrowserProfileManager.record_profile_diagnostic()` 단위 테스트를 추가한다.

테스트 의도:

- registry에 row가 없으면 조용히 no-op 한다.
- `ProfileAssignment`가 frozen dataclass이므로 직접 속성 대입이 아니라 `dataclasses.replace(...)`로 새 assignment를 저장한다.
- `browser_profiles()` projection에 optional 진단 필드가 포함된다.
- raw `profile_dir`, secret, URL, HTML, screenshot, clipboard는 projection에 들어가지 않는다.

- [ ] `CrawlWorker`가 최종 `JobResult`를 기준으로 알려진 실패/상태를 profile 진단값으로 기록한다.

권장 형태:

```python
def _record_crawl_diagnostic_from_result(
    self,
    payload: CrawlJobPayload,
    result: JobResult,
) -> None:
    ...
```

호출 위치:

- `CrawlWorker.execute()`에서 supported crawl job의 최종 `JobResult`가 만들어진 직후를 기본 위치로 삼는다.
- process boundary 결과와 `_execute_payload()` 결과는 모두 이 helper를 통과해야 한다.
- timeout 결과는 예외다. `_run_with_timeout()`의 cleanup이 `release(payload.tenant_id, payload.target_id)`를 호출해 assignment를 없앨 수 있으므로, timeout diagnostic은 timeout 전용 helper에서 release 전에 기록한다.
- unsupported job은 기록하지 않는다.
- `execute()` 안의 여러 early return 때문에 한 분기만 고치면 빠진다. 지원 job 경로를 작은 내부 helper로 빼거나, 최종 `result`를 한 곳에서 받은 뒤 `_record_crawl_diagnostic_from_result(...)`를 호출하는 구조로 정리한다. timeout처럼 cleanup 순서가 중요한 분기는 이 공통 구조 밖에서 먼저 기록해도 된다.

예상 매핑:

| 상황 | auth_state | last_error_code | heartbeat 기록 조건 |
| --- | --- | --- | --- |
| crawl success | `ACTIVE` | 없음 | 기존 assignment가 있음 |
| `BrowserActionRequiredError` | `AUTH_REQUIRED` | `AUTH_REQUIRED` | 기존 assignment가 있음 |
| auth probe가 `AUTH_REQUIRED` 반환 | `AUTH_REQUIRED` | `AUTH_REQUIRED` | 기존 assignment가 있음 |
| auth probe가 `UNKNOWN` 반환 | `UNKNOWN` | `AUTH_PROBE_UNKNOWN` | 기존 assignment가 있음 |
| `CdpUnavailableError` 또는 `CDP_UNREACHABLE` 분류 | `UNKNOWN` | `CDP_UNREACHABLE` | 기존 assignment가 있음 |
| parser missing data | `UNKNOWN` | `PARSER_MISSING_DATA` | 기존 assignment가 있음 |
| crawl timeout | `UNKNOWN` | `CRAWL_TIMEOUT` | 기존 assignment가 있음 |
| center mismatch | `CENTER_MISMATCH` | `TARGET_VALIDATION_FAILURE` | 기존 assignment가 있음 |
| profile unavailable | `UNKNOWN` | `PROFILE_UNAVAILABLE` | 기존 assignment가 있을 때만 |
| payload expired | `UNKNOWN` | `PAYLOAD_EXPIRED` | 기존 assignment가 있을 때만 |

주의:

- parser/CDP 오류를 `AUTH_REQUIRED`로 바꾸지 않는다.
- job result의 기존 `error_code`와 `result_json.auth_state` 계약은 바꾸지 않는다.
- 진단 기록 실패가 job 결과를 바꾸면 안 된다.
- 진단 기록은 assignment 객체를 호출자에게 반환받아서 하지 않는다. `CrawlWorker`가 이미 들고 있는 `self._profile_manager`에 `payload.tenant_id`, `payload.target_id`를 키로 `record_profile_diagnostic(...)`을 호출한다.
- `last_probe_at`은 기록 시점의 UTC ISO 문자열이다. 테스트에서는 `now` 주입 또는 고정 helper로 결정적으로 검증한다.
- timeout 테스트는 cleanup이 assignment를 release하더라도 release 전에 `CRAWL_TIMEOUT` 진단을 기록하는지 검증한다. timeout 시점에 기존 assignment가 아예 없으면 `record_profile_diagnostic(...)`은 기존 no-op 규칙대로 조용히 반환한다.

- [ ] auth worker 경로도 profile manager가 있을 때만 결과를 기록한다.

`worker_composition.py`의 auth wrapper는 `_with_profile_assignment()`로 profile을 확보한다. `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA` 실행 결과에 `result_json.auth_state`, `error_code`가 있으면 같은 manager에 `record_profile_diagnostic()`을 호출한다.

범위 결정:

- 3A는 `crawl_profile_manager`가 있는 조합에서만 auth diagnostic을 기록한다.
- 현재 기본 `BrowserProfileManager` 생성은 crawl worker 시작 조건(`start_crawl_worker`와 crawl capability)에 묶여 있다. auth-only Agent 조합까지 heartbeat profile diagnostic을 남기려면 manager 생성 조건을 넓히는 별도 결정이 필요하다.
- 이번 작업에서 manager 생성 조건을 넓히지 않는다면, auth-only 조합은 기존처럼 profile row 없이 job result만 남는 것이 정상이다.

권장 형태:

```python
def _record_auth_diagnostic_from_result(
    manager: object,
    job: object,
    result: object,
    *,
    now: Callable[[], float] | Callable[[], datetime],
) -> None:
    ...
```

호출 위치:

- `baemin_auth.build_auth_execute_job(...)`가 반환한 router를 감싼 wrapper의 반환 직전.
- `coupang_gmail_2fa.build_coupang_auth_execute_job(...)`가 반환한 router를 감싼 wrapper의 반환 직전.
- wrapper는 `JobResult`를 검사만 하고 원본 result를 그대로 반환한다.
- 최소 1개 테스트는 `compose_execute_job(...)` 레벨에서 수행해 실제 auth wrapper 배선까지 검증한다. 개별 auth 함수 단위 테스트만으로는 `crawl_profile_manager.record_profile_diagnostic(...)` 호출 위치가 빠져도 잡기 어렵다.
- `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA` 중 최소 2개 경로를 compose 레벨에서 검증한다. 가능하면 `AUTH_COUPANG_2FA`도 포함해 `result_json.auth_state` 정규화 뒤 기록되는지 확인한다.

주의:

- `_with_profile_assignment()`는 assignment를 반환하지 않고 `cdp_url`, `browser_user_data_dir`만 job payload에 주입한다. 반환 시그니처를 바꾸지 않는다.
- auth wrapper는 `crawl_profile_manager`에 job payload의 `tenant_id`, `target_id`를 키로 `record_profile_diagnostic(...)`을 호출한다.
- `baemin_auth.py`나 `coupang_gmail_2fa.py` 내부에 `BrowserProfileManager` import를 넣지 않는다.
- `rider_agent`는 계속 `rider_server`를 import하지 않는다.
- secret, OTP, 이메일 주소, 앱 비밀번호는 진단값에 넣지 않는다.
- `AUTH_COUPANG_2FA`는 내부 recovery 결과를 wrapper 바깥으로 정규화한 뒤 `result_json.auth_state`와 `result_json.auth_recovery_state`를 만든다. 진단 기록은 최종 `JobResult.result_json.auth_state`를 기준으로 한다.

- [ ] 서버 allowlist를 3A 필드만 확장한다.

`src/rider_server/services/agent_registry.py`의 `_BROWSER_PROFILE_KEYS`에 아래 값을 추가한다.

```python
{
    "auth_state",
    "last_probe_at",
    "last_error_code",
}
```

- [ ] 서버 저장 sanitizer는 3A 필드의 타입/길이도 검증한다.

권장 규칙:

- `auth_state`, `last_error_code`, `last_probe_at`은 문자열일 때만 저장한다.
- 빈 문자열, control character 포함 문자열, 과도하게 긴 문자열은 저장하지 않는다.
- unknown enum 값을 새 상태로 해석하지 않는다. 그대로 표시할 값이면 길이 제한을 통과한 문자열로만 보존하고, 정책 판단에는 쓰지 않는다.

- [ ] `cdp_port` 정수 범위 검증을 서버 저장 sanitizer에 신규 추가한다.

현재 `agent_registry.py`의 `_sanitize_value()`는 int를 그대로 통과시켜 `cdp_port`에 음수·`0`·`999999`도 저장된다. "유지"가 아니라 이번에 추가하는 작업이다.

- `cdp_port`는 정수이고 `1..65535`일 때만 저장한다.
- `bool`은 `int` 서브타입이므로 `cdp_port`로 받지 않는다(`isinstance(value, bool)`을 먼저 걸러낸다).
- float, 문자열, 범위 밖 정수는 저장하지 않는다.
- 현재 sanitizer는 문자열 외 scalar를 그대로 둘 수 있으므로, `cdp_port` 위치에서는 bool/float도 명시적으로 버린다.
- 이 검증은 `browser_profiles[]` item 단위로 적용되도록 `_BROWSER_PROFILE_KEYS` 경로의 sanitize 지점에 둔다. 전역 `_sanitize_value()`의 모든 int를 막지 않는다(다른 capacity 값에 영향 주지 않게 한다).

- [ ] 서버 sanitizer 테스트를 확장한다.

Expected:

- 새 안전 필드는 저장된다.
- `current_url`, `current_url_redacted`, `page_kind`, `html`, `screenshot`, `clipboard`, `cookies`, `token`, `password`, `profile_path`는 3A에서는 저장되지 않는다.
- `profile_path_ref`는 기존 allowlist에 남아도, 관리자 화면의 런타임 상태 표시에는 렌더하지 않는다.
- `cdp_port`가 `0`, 음수, `65535` 초과, `bool`, float, 문자열이면 저장되지 않는다. 유효 범위 정수만 저장된다.
- 잘못된 타입의 `auth_state`, `last_error_code`, `last_probe_at`, `cdp_port`는 저장되거나 렌더되지 않는다.
- 기존 `tests/server/test_agents_api.py::test_heartbeat_strips_sensitive_payload_fields_before_storage`를 확장하거나 같은 파일에 인접 테스트를 추가해 `heartbeat_capacity(...)` 공통 sanitizer 결과를 고정한다. InMemory와 Postgres registry가 같은 함수를 쓰므로 이 계층을 직접 잠그는 것이 핵심이다.

- [ ] 관리자 화면 Chrome 상태 열에 3A 필드를 추가 표시한다.

표시 예:

```text
AUTH_REQUIRED / AUTH_REQUIRED / checked 10:20
UNKNOWN / CDP_UNREACHABLE / checked 10:21
CENTER_MISMATCH / TARGET_VALIDATION_FAILURE / checked 10:22
```

`AgentBrowserProfileRow`에는 Task 2 필드에 아래 optional 필드를 추가한다.

```python
auth_state: str | None = None
last_error_code: str | None = None
last_probe_at: str | None = None
```

### 3A 검증

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_browser_profile.py tests/agent/test_crawl_worker.py tests/agent/test_job_loop.py tests/agent/test_baemin_auth.py tests/agent/test_coupang_gmail_2fa.py tests/server/test_agents_api.py tests/server/test_admin_dashboard.py -q
```

Expected: 최소 진단 필드는 안전하게 저장/렌더되고, 금지 필드는 저장되지 않는다.

---

## Future Work 3B - page kind와 redacted URL 진단 경로 설계

### 목적

운영자가 로그인 화면, 2FA 화면, 대시보드, 오류 페이지를 더 정확히 구분할 수 있게 한다.

이 작업은 비용이 높다. 현재 `default_login_probe()`는 auth state 문자열만 반환하고 page/URL 문맥을 갖지 않는다. `BrowserProfileManager` heartbeat registry도 probe 결과를 자동으로 받지 않는다. 따라서 3B는 3A 이후 별도 작업으로 진행한다.

Phase 1 구현자는 3B를 구현하지 않는다. 이 섹션은 후속 작업을 위한 설계 결정 목록이다. 3B 구현을 진행하려면 아래 결정이 완료된 뒤 별도 작업지시서나 명시 승인으로 범위를 다시 연다.

### 설계 검토 파일 후보

- `src/rider_agent/browser_profile.py`
- `src/rider_agent/auth/baemin_auth.py`
- `src/rider_agent/auth/coupang_gmail_2fa.py`
- `src/rider_agent/worker_composition.py`
- `src/rider_agent/reuse.py` 또는 page context를 만들 수 있는 실제 seam (`src/rider_crawl/`에는 `reuse.py`가 없다)
- `src/rider_server/services/agent_registry.py`
- `src/rider_server/admin/dashboard_service.py`
- `src/rider_server/admin/dashboard_repository_postgres.py`
- `src/rider_server/admin/templates/_agents.html`
- 관련 tests

### 3B 필드 계약

heartbeat `browser_profiles[]`에 아래 optional 필드를 추가한다.

| field | 예 | 설명 |
| --- | --- | --- |
| `page_kind` | `LOGIN`, `TWO_FACTOR`, `DASHBOARD`, `ERROR`, `UNKNOWN` | 현재 페이지의 큰 종류 |
| `current_url_redacted` | `https://store.example.com/login` | query, fragment, secret 제거 URL |

### 구현 전 결정

- [ ] page context를 어디에서 얻을지 결정한다.

가능한 선택지:

- `crawl_snapshot` 또는 platform probe가 page kind와 URL을 담은 구조적 결과를 반환한다.
- page context를 담은 새 예외 타입을 만들고, auth/crawl worker가 이를 잡아 profile manager에 기록한다.
- 별도 read-only browser probe 함수를 만들고, heartbeat 직전 또는 job 직후에만 호출한다.

결정 기준:

- heartbeat 주기마다 브라우저를 새로 조회해 부하나 락 경합을 만들지 않는다.
- job 실행 결과를 바꾸지 않고 관측값만 추가한다.
- page context 수집 실패는 `UNKNOWN` 진단으로 남기되, crawler 성공/실패 판단을 바꾸지 않는다.

- [ ] URL redaction helper 위치를 정한다.

예상 함수:

```python
def redact_current_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
```

규칙:

- raw URL query와 fragment를 보내지 않는다.
- path segment에도 주문번호, tenant/account id, session-like 값이 들어갈 수 있다. 3B 설계에서는 path 전체 허용, 특정 segment 마스킹, path 제거 중 하나를 명시적으로 고른다.
- `current_url_redacted`는 길이 제한을 둔다. 과도하게 긴 path는 저장하지 않거나 잘라낸다.
- HTML, screenshot, clipboard, cookie, localStorage, token은 보내지 않는다.
- `profile_path` 같은 로컬 파일 경로는 보내지 않는다.
- 문자열 필드는 길이 제한을 둔다.
- 알 수 없는 enum 값은 서버에서 `UNKNOWN` 또는 빈 값으로 낮춘다.

- [ ] 서버 allowlist를 3B 필드까지 확장할 구현 위치를 설계한다.

`src/rider_server/services/agent_registry.py`의 `_BROWSER_PROFILE_KEYS`에 아래 값을 추가한다.

```python
{
    "page_kind",
    "current_url_redacted",
}
```

- [ ] 관리자 화면 Chrome 상태 열에 3B 필드를 추가 표시할 UI 규칙을 설계한다.

표시 예:

```text
AUTH_REQUIRED / LOGIN / https://example.com/login / checked 10:20
UNKNOWN / ERROR / CDP_UNREACHABLE / checked 10:21
```

### 3B 검증

3B를 실제 구현하는 별도 작업에서만 아래 검증을 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_browser_profile.py tests/server/test_agents_api.py tests/server/test_admin_dashboard.py -q
```

Expected: page kind와 redacted URL은 안전하게 저장/렌더되고, raw URL과 금지 필드는 저장되지 않는다.

---

## Task 4 - DB profile과 heartbeat profile의 역할 분리

### 목적

같은 이름의 `browser_profiles`가 두 의미로 쓰여 생기는 혼란을 줄인다.

Agents 화면의 heartbeat runtime 라벨은 Task 2에서 함께 처리한다. 이 Task는 auth-required 화면과 운영 문서에서 같은 용어 혼란이 남지 않게 정리한다.

### 변경 파일

- `src/rider_server/admin/templates/_auth_required.html`
- `docs/operations/queue-backlog-handling-policy.md`
- 필요하면 새 문서 `docs/operations/agent-browser-profile-observability.md`

### 역할 정의

| 대상 | 의미 | 수명 | 정본 |
| --- | --- | --- | --- |
| DB `BrowserProfile` table | 어떤 target이 어떤 agent/profile/port에 배정됐는지 (agent/target FK 관계 테이블) | 비교적 정적 | 배정 정보 정본 |
| heartbeat `capacity_json.browser_profiles` | 지금 에이전트 PC에서 Chrome/CDP가 어떤 상태인지 | 휘발성 | 런타임 상태 정본 |

### 구현 단계

- [ ] Task 2에서 Agents 화면의 heartbeat 기반 목록이 `현재 Chrome 상태` 또는 `heartbeat runtime`으로 라벨링됐는지 확인한다.
- [ ] auth-required 화면의 DB 기반 profile 값은 `배정 정보` 또는 `DB 배정 정보`로 라벨링한다.
- [ ] `_auth_required.html`에 DB 배정 profile 표시 열을 추가한다.

표시 원칙:

- `AuthRequiredRow.profile_id`는 DB `BrowserProfile` 기반 배정 정보다.
- 열 제목은 `DB 배정 정보` 또는 `배정 profile`처럼 heartbeat runtime과 섞이지 않는 이름을 쓴다.
- `profile_id`가 있으면 안전하게 렌더하고, 없으면 `-`로 표시한다.
- 컬럼을 추가하면 빈 목록 row의 `colspan="3"`은 실제 열 수에 맞춰 `colspan="4"`로 수정한다.

- [ ] 운영 문서에 "두 값을 강제 동기화하지 않는다"를 명시한다.
- [ ] auth-required 화면에서 DB 배정값과 heartbeat 현재값이 다를 때 혼란이 없도록 문구를 조정한다.
- [ ] 기존 `tests/server/test_admin_dashboard.py::test_auth_required_fragment_lists_only_tenant_rows`의 `assert ">p1<" not in body` 기대는 새 정책에 맞게 수정한다.
- [ ] `_auth_required.html` 직접 렌더 테스트를 추가해 `DB 배정 정보`, `p1`, `-`, `colspan="4"`를 고정한다.

테스트 의도:

- `profile_id="p1"`인 `AuthRequiredRow`가 `DB 배정 정보` 라벨 아래 렌더된다.
- `profile_id=None`이면 `-` 또는 muted empty 표시가 나온다.
- 기존 인증 시작/브라우저 열기/상태 재확인 버튼 동작은 유지된다.

### 검증

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py -q
```

Expected: 화면에 두 역할이 분명히 표시된다.

---

## Task 5 - CAPTURE_DIAGNOSTIC 미완 계약 정리

### 목적

워커가 없는 `CAPTURE_DIAGNOSTIC`이 실수로 큐에 들어가 애매한 `UNSUPPORTED_JOB_TYPE` terminal failure로 남지 않게 한다. 이번 Phase 1에서는 서버 enqueue 차단(Option A)을 구현한다. 현재 `JOB_TYPES`와 `DEFAULT_CAPABILITIES`가 같은 집합이어야 한다는 테스트 불변식이 있으므로, capability는 제거하지 않는다.

정본 위치: `JOB_TYPES` vocabulary 정본은 `src/rider_server/queue/states.py`다(`JOB_TYPE_CAPTURE_DIAGNOSTIC` 포함). Agent capability 정본은 `src/rider_agent/heartbeat.py`의 `DEFAULT_CAPABILITIES`다. `tests/server/test_job_vocab.py`는 두 집합의 동일성만 단언하지 검색 대상 파일이 아니다. 아래 단계에서 vocabulary를 확인할 때는 `states.py`를 본다.

screenshot/html artifact 수집은 별도 Phase 2로 설계한다.

### 변경 파일

- `src/rider_server/services/admin_action_service.py`
- 관련 server tests
- capability mirror 확인이 필요하면 `src/rider_agent/heartbeat.py`, `tests/agent/test_heartbeat.py`
- `tests/server/test_job_vocab.py`
- 새 문서 `docs/operations/capture-diagnostic-artifacts.md`

### Phase 1 구현 단계

- [ ] `CAPTURE_DIAGNOSTIC`이 실제 워커에 연결되어 있는지 다시 확인한다.

```powershell
rg "CAPTURE_DIAGNOSTIC|capture_diagnostic|SUPPORTED_JOB|UNSUPPORTED_JOB_TYPE" src tests
```

Expected:

- `src/rider_agent/jobs.py`는 없음을 확인한다.
- `DEFAULT_CAPABILITIES`는 `src/rider_agent/heartbeat.py`에 있음을 확인한다.
- `tests/server/test_job_vocab.py`의 `set(JOB_TYPES) == set(DEFAULT_CAPABILITIES)` 불변식을 확인한다.
- capability 불변식은 한 곳이 아니다. `tests/agent/test_autostart.py::test_handleable_job_types_equals_capability_set`도 `handleable_job_types(DEFAULT_CAPABILITIES) == tuple(DEFAULT_CAPABILITIES)`를 단언한다. 이번 Option A 구현은 capability를 제거하지 않으므로 이 단언은 유지된다. Task 5 검증 셋에 `tests/agent/test_autostart.py`를 포함한다.
- 현재 `scheduler`와 Admin routes가 `CAPTURE_DIAGNOSTIC`을 직접 enqueue하지 않는지 확인한다.
- `AdminActionService.test_crawl(...)`처럼 service method가 외부 인자로 `job_type`을 받는 경로는 별도로 확인한다. 현재 코드는 `job_type in JOB_TYPES` 검사를 통과한 뒤 payload 생성 과정의 `_platform_for_crawl_job()`에서 non-crawl type을 막아 enqueue 전 실패한다. 이 암묵적 방어에 기대지 말고, service 경계에서 수동 crawl 액션은 `CRAWL_BAEMIN`/`CRAWL_COUPANG`만 허용해야 한다.
- Admin queue label이나 vocabulary 상수에 `CAPTURE_DIAGNOSTIC` 문자열이 있는 것은 정상이다. 문자열 존재 자체를 실패로 보지 않는다.
- 실제 artifact 캡처 워커가 없음을 확인한 뒤 아래 Option A를 구현한다. Option B/C는 이번 작업에서 구현하지 않는다.

### Phase 1 정책

#### Option A - 서버 enqueue 차단

이번 작업에서 구현할 선택이다. Admin route는 현재 form 값을 `CRAWL_BAEMIN`/`CRAWL_COUPANG`으로만 변환하지만, `AdminActionService.test_crawl(...)`는 service-level 인자로 `job_type`을 받는다. 현재 `CAPTURE_DIAGNOSTIC`은 payload 생성 과정에서 enqueue 전 실패하지만, 그 방어는 수동 crawl 정책이 드러나는 위치가 아니다. Option A는 service/API/scheduler 어디에서도 artifact 정책 전 `CAPTURE_DIAGNOSTIC`이 생성되지 않도록 명시 fail-closed 가드와 회귀 테스트를 두는 작업이다.

`CAPTURE_DIAGNOSTIC`은 vocabulary와 capability에는 남겨 두되, Phase 2 artifact 정책이 생기기 전까지 Admin/API/scheduler가 이 job을 만들지 못하게 한다.

장점:

- `JOB_TYPES == DEFAULT_CAPABILITIES` 불변식을 건드리지 않는다.
- claim matching 모델을 깨지 않는다.
- 미구현 job이 큐에 들어가는 운영 사고를 막는다.

구현 지시:

- [ ] `CAPTURE_DIAGNOSTIC`을 enqueue할 수 있는 서버 action/API/service 경로를 확인한다. 현재 확인된 약한 경계는 `AdminActionService.test_crawl(...)`이다. 이 메서드는 `job_type in JOB_TYPES` 검사를 먼저 통과시킨 뒤 payload 생성 과정에서 non-crawl type이 막힌다. 아래 단계는 이 암묵적 방어를 service 경계의 명시 allowlist로 바꾸는 작업이다. 다른 enqueue 경로(scheduler, 다른 API)가 더 있는지도 같이 확인한다.
- [ ] `AdminActionService.test_crawl(...)`는 `JOB_TYPES` 전체가 아니라 `CRAWL_BAEMIN`/`CRAWL_COUPANG`만 허용하도록 좁힌다.
- [ ] `tests/server/test_admin_actions.py`에 `svc.test_crawl(..., job_type="CAPTURE_DIAGNOSTIC", ...)`가 명시 allowlist 오류로 거부되고 queue에 job을 만들지 않는 테스트를 추가한다.
- [ ] 위 테스트는 기존 암묵적 `_platform_for_crawl_job()` 실패만으로도 통과하지 않게 만든다. 예를 들어 에러 메시지/코드를 `unsupported manual crawl job type` 같은 새 service-level guard로 고정하거나, `_test_crawl_payload()`가 호출되지 않았음을 spy로 확인한다.
- [ ] 회귀 방지 테스트는 scheduler/admin action 계층이 `CAPTURE_DIAGNOSTIC`을 만들지 않는다는 것을 고정한다. 단순 grep으로 `CAPTURE_DIAGNOSTIC` 문자열 전체를 금지하지 않는다.
- [ ] 허용되는 문자열 위치와 금지되는 생성 위치를 분리한다.

허용:

- `src/rider_server/queue/states.py`의 vocabulary
- `src/rider_agent/heartbeat.py`의 capability
- `tests/server/test_job_vocab.py`의 mirror 테스트
- `src/rider_server/admin/routes.py`의 표시 라벨

금지:

- scheduler가 `queue_backend.enqueue(job_type="CAPTURE_DIAGNOSTIC", ...)`를 호출하는 경로
- `AdminActionService`가 manual action으로 `CAPTURE_DIAGNOSTIC`을 enqueue하는 경로
- Admin action route가 form/API 입력만으로 `CAPTURE_DIAGNOSTIC` enqueue를 열어주는 경로

- [ ] 새 enqueue 경로가 이미 생겨 있거나 이번 작업에서 추가해야 한다면, 501/400 또는 admin-disabled reason으로 fail-closed 처리한다.
- [ ] `DEFAULT_CAPABILITIES`에서는 `CAPTURE_DIAGNOSTIC`을 제거하지 않는다.

#### Option B - 이번 작업에서 구현하지 않음: Agent 명시 not-configured 실패 결과

서버가 어쩔 수 없이 job을 만들 수 있어야 한다는 제품/운영 결정이 내려진 경우에만 별도 작업으로 연다. 그때는 Agent가 `UNSUPPORTED_JOB_TYPE` 대신 명시적 `DIAGNOSTIC_NOT_CONFIGURED` 실패 결과로 한 번에 닫게 한다.

중요:

- 새 Agent status `skipped`를 만들지 않는다. 서버 `map_agent_status()`는 현재 `success`/`failed`만 받으므로 `skipped`를 보내면 complete API가 422를 반환한다.
- Option B의 결과는 `status="failed"`, `error_code="DIAGNOSTIC_NOT_CONFIGURED"`, `result_json={"reason": "diagnostic_not_configured"}` 같은 형태로 서버의 기존 complete 계약 안에 있어야 한다.

장점:

- job type/capability 불변식을 유지한다.
- 큐에 들어간 job이 애매한 unsupported terminal failure로 남지 않고, 명시적인 not-configured 실패로 닫힌다.

후속 작업 지시 초안:

- [ ] `CAPTURE_DIAGNOSTIC` 전용 handler를 추가한다.

handler 삽입 위치:

- 미지원 job은 `worker_composition.compose_execute_job()`가 만든 crawl→auth→kakao fallback 체인을 통과한 뒤 `job_loop.default_execute_job()`로 떨어져 `UNSUPPORTED_JOB_TYPE`이 된다.
- 따라서 `CAPTURE_DIAGNOSTIC` 분기는 이 체인 안, `default_execute_job` fallback 직전에 둔다. `default_execute_job` 자체나 `crawl_worker.make_unsupported`를 고치지 않는다(그건 진짜 미지원 type 경로다).
- crawl/auth/kakao 워커의 type 매칭 분기와 같은 층에서 `job.type == "CAPTURE_DIAGNOSTIC"`이면 전용 handler로 보낸다.

- [ ] handler는 artifact를 만들지 않는다.
- [ ] result에는 고정 error_code와 reason만 남긴다.
- [ ] secret, URL, HTML, screenshot, clipboard는 남기지 않는다.
- [ ] `tests/server/test_jobs_api.py` 또는 기존 complete 계약 테스트에서 새 status가 아니라 기존 `failed` status로 완료되는지 확인한다.

#### Option C - 이번 작업에서 구현하지 않음: capability/job type 분리

이번 작업의 기본 선택이 아니다. 이 선택을 하려면 claim matching 정책과 `tests/server/test_job_vocab.py` 불변식을 함께 재설계해야 한다.

조건:

- 서버가 "알려진 job type"과 "기본 Agent capability"를 분리해서 다룰 수 있어야 한다.
- capability가 없는 job을 어떤 Agent에도 배정하지 않는 정책이 있어야 한다.
- 기존 테스트 주석과 claim matching 기대를 함께 바꿔야 한다.

- [ ] 후속 작업에서 이 선택을 승인받은 경우에만 capability 테스트를 수정한다.

Expected:

- 이번 Option A 구현 후 `CAPTURE_DIAGNOSTIC`은 `JOB_TYPES`와 `DEFAULT_CAPABILITIES`에 남는다.
- 미구현 artifact 캡처 job은 서버 enqueue 단계에서 차단된다.
- `UNSUPPORTED_JOB_TYPE`이 미구현 artifact 캡처의 운영 신호로 남지 않는다.

### Phase 2 설계 문서에 포함할 내용

`docs/operations/capture-diagnostic-artifacts.md`에 아래 결정을 적는다.

- artifact 저장소: local blob, S3 호환 storage, 또는 DB 외부 object storage 중 하나
- signed URL 또는 관리자 권한 기반 다운로드 방식
- 보존 기간
- 최대 크기
- 허용 artifact type
- 금지 artifact type
- HTML redaction 정책
- screenshot redaction 정책
- `JobEvent.artifact_refs` schema
- artifact 삭제 배치

### Phase 2 구현 전제

아래가 정해지기 전에는 screenshot/html 캡처 워커를 구현하지 않는다.

- 저장소 위치
- 접근 권한
- 보존 기간
- 개인정보/토큰 redaction 규칙
- 운영자가 실제로 볼 화면

### 검증

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py tests/agent/test_job_loop.py tests/agent/test_autostart.py tests/server/test_admin_actions.py tests/server/test_jobs_api.py tests/server/test_job_vocab.py -q
```

Expected: Option A 정책이 테스트로 고정된다. `JOB_TYPES == DEFAULT_CAPABILITIES` 불변식은 유지되고, `test_autostart.py`의 `handleable_job_types` 단언도 통과하며, 미구현 artifact 캡처 job이 서버 운영 경로에서 enqueue되지 않는다.

---

## 통합 검증

모든 task 완료 후 아래를 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/agent/test_heartbeat.py tests/agent/test_job_loop.py tests/agent/test_browser_profile.py tests/agent/test_crawl_worker.py tests/agent/test_baemin_auth.py tests/agent/test_coupang_gmail_2fa.py tests/server/test_agents_api.py tests/server/test_admin_dashboard.py tests/server/test_admin_actions.py tests/server/test_jobs_api.py tests/server/test_job_vocab.py -q
```

Expected: 관련 테스트가 모두 통과한다.

가능하면 전체 테스트도 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: 전체 테스트가 통과한다. 오래 걸리거나 외부 의존성 때문에 실패하면 실패한 테스트명과 이번 변경과의 관련성을 기록한다.

---

## 완료 기준

- [ ] 403 token mismatch가 heartbeat와 job loop에서 재등록/identity mismatch 상태로 명확히 표시된다.
- [ ] 403 claim 오류가 transient backoff에 묻히지 않는다.
- [ ] Agents 관리자 화면에서 heartbeat 기반 Chrome 상태와 CDP port를 볼 수 있다.
- [ ] 3A 완료 시 `UNKNOWN` 상태일 때 최소한 `auth_state`, `last_error_code`, `last_probe_at` 중 원인 파악에 필요한 값이 보인다.
- [ ] Future Work 3B는 구현하지 않았고, Phase 1 코드/테스트에 `page_kind`, `current_url_redacted`, raw URL 저장이 추가되지 않았다.
- [ ] DB profile은 배정 정보, heartbeat profile은 현재 상태라는 라벨이 화면/문서에 반영된다.
- [ ] 실제 artifact 워커가 없는 `CAPTURE_DIAGNOSTIC`은 서버 enqueue 단계에서 차단되어 미구현 artifact job이 `UNSUPPORTED_JOB_TYPE`으로 설명 없이 남지 않는다.
- [ ] screenshot/html artifact 구현은 보안/저장소/보존 정책 문서가 생긴 뒤 별도 작업으로 진행한다.

---

## 구현자가 주의할 점

- `UNKNOWN`을 줄이려고 parser/연결 예외를 `AUTH_REQUIRED`로 바꾸지 않는다.
- heartbeat payload에는 비밀값이 절대 들어가면 안 된다.
- `CrawlWorker` 진단 기록은 process boundary 결과도 포함해야 한다. `_execute_payload()` 안쪽 예외 분기만 수정하고 끝내지 않는다.
- job event 보고는 현재 `_emit_started()` best-effort 경로다. 없는 `_send_events_with_retry()` helper를 새 전제로 삼지 않는다.
- Task 3A에서는 URL을 보내지 않는다. Future Work 3B에서 URL을 보내야 하면 query와 fragment를 제거한 redacted URL만 허용한다.
- 화면에 보여주는 값은 반드시 서버에서 allowlist와 길이 제한을 통과한 값이어야 한다.
- DB `BrowserProfile`과 heartbeat `browser_profiles`를 자동 동기화하는 배치를 만들지 않는다.
- `CAPTURE_DIAGNOSTIC` 문자열 자체는 vocabulary/label에 남아도 된다. 금지할 것은 artifact 정책 전의 enqueue 경로다.
- `DEFAULT_CAPABILITIES`와 `JOB_TYPES`를 분리하려면 claim matching 정책과 테스트 불변식도 함께 재설계한다. 한쪽만 바꾸지 않는다.
- 이 계획은 관측 구조 보강이 목적이다. crawler 실행 로직을 함께 고치지 않는다.
