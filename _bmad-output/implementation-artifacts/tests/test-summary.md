# Test Automation Summary — Story 4.3 (Agent heartbeat 보고)

작성: 2026-06-13 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 4.3은 UI가 없는 **Agent heartbeat primitive**(payload 빌더 + 단발 `send_heartbeat` + 주기 `HeartbeatReporter` loop)다. 서버 수신·offline 판정·Admin은 Epic 5 소유라, 테스트는 **주입 fake transport / 실 `HttpTransport`+fake `urlopen`** 에 대한 client-side 동작 검증 형태다(4.x 표준 — epic-3-retro 108). 외부(브라우저/네트워크/Kakao/Gmail) 미호출, 가짜 값만(`agtok-fake-…`/`agent-fake-…`).

dev-story가 `tests/agent/test_heartbeat.py`에 26건을 만든 상태에서, 구현 분기 중 **테스트가 비어 있던 격차**를 찾아 자동 보강(auto-apply)했다.

## Generated / 보강 테스트

`tests/agent/test_heartbeat.py` (+5건, 기존 26 → **31건**). 기존 헬퍼(`FakeTransport`·`StoppingSleep`) 재사용, 신규 추상화 0.

| # | 테스트 | 커버한 격차 | 근거 |
|---|--------|-------------|------|
| Gap1 | `test_reporter_survives_non_transport_exception_and_records_event` | `report_once` 의 일반 예외(`except Exception`) best-effort 흡수 + `_record_error` no-log 분기. 기존엔 `TransportError` 경로만 검증 — 주입 provider/transport 가 일반 예외를 던지면 thread 가 죽지 않는지 미검증이었음 | AC2.5 |
| Gap2 | `test_reporter_recovers_to_valid_after_revoked` | `401`→revoked surfacing 후 token 재발급 성공 시 `valid` 회복 + `on_status` `[REVOKED, VALID]` 전이. 문서화된 회복 동작인데 기존 커버 0(`_set_status` revoked→valid 분기 포함) | AC2 |
| Gap3 | `test_send_heartbeat_non_list_commands_parse_to_empty` | `_result_from_response` 의 비-list `commands` → 안전 `[]` 분기. 기존엔 list/누락만 검증(malformed 서버 응답 방어 미검증) | AC1 |
| Gap4 | `test_heartbeat_url_strips_trailing_slash` | `_heartbeat_url` 의 `rstrip("/")` URL 정규화(`//v1/...` 이중 슬래시 방지). 기존 URL 테스트는 trailing slash 없는 base 만 사용 | AC1 |
| Gap5 | `test_reporter_stop_method_halts_running_loop` | 공개 `stop()` 메서드로 동작 중 루프 정지. 기존엔 stop_event 를 외부에서 직접 set 만 함 — thread-safe `stop()` 경로 미검증 | AC2 |

기존 26건(유지): payload 7키+`agent_version`, provider 반영, POST URL, interval `[30,60]` clamp(경계), 응답 파싱, 주기 N회 후 정지, 단발 `TransportError` 복원력, `401` revoke surfacing, capabilities 6종 superset, Bearer 헤더+평문 비노출, 실 `HttpTransport` 헤더 병합·op-label·E2E.

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — 30~60s 주기 POST·5필드+`agent_version` payload·interval clamp·응답 파싱 | ✅ 기존 다수 + Gap3(비-list commands)/Gap4(URL 정규화) |
| AC2 — offline/버전-drift 판정 입력 제공·best-effort 복원력·401 surfacing | ✅ 기존(주기·503·401) + Gap1(일반 예외 복원력)/Gap2(revoked→valid 회복)/Gap5(`stop()`) |
| AC3 — capabilities = 처리 가능 job type 6종 superset | ✅ 기존 2건(superset·주입 반영, "정확히 N" lock 없음) |

`heartbeat.py` 공개 표면 전부 커버: `build_heartbeat_payload`/`send_heartbeat`/`clamp_interval`/`HeartbeatReporter`(`run`·`report_once`·`stop`·`needs_registration`)/`default_metrics`/`HeartbeatResult`. 보강 후 best-effort 일반 예외·revoked→valid 회복·비-list commands·trailing-slash URL·공개 `stop()` 분기까지 커버.

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- `tests/agent/test_heartbeat.py -q` → **31 passed**
- 전체 스위트 `-q` → **1091 passed, 0 failed** (기존 1086 + 신규 5, 순수 additive·회귀 0)
- `tests/agent/test_agent_package.py tests/agent/test_registration.py -q` → **42 passed** (4.1 sync·third-party root==`{rider_crawl}`·deps-9핀 가드 + 4.2 register 무회귀 green)

## 범위/누출 검증

- 이번 QA 라운드 변경은 `tests/agent/test_heartbeat.py` 에 테스트 추가뿐. 프로덕션 코드(`heartbeat.py`/`registration.py`)·`src/rider_crawl`·`src/rider_server`·`pyproject.toml`·`__main__.py` **0줄 변경**.
- 누출 grep(봇토큰 `\d{6,}:[\w-]{30,}`/`chat_id=`/휴대폰) → 신규 테스트에 0건. `agtok-fake` 리터럴 비-테스트 src 에 0건. 에러 경로에 평문 token 진입 0(token 은 Authorization 헤더에만).
- 역방향 의존(`rider_crawl`→`rider_agent` import) 신규 0건.

## 체크리스트 결과(`checklist.md`)

- [x] API/client 테스트(heartbeat = outbound HTTPS client; fake transport/실 `HttpTransport`+fake urlopen) / E2E(해당 없음 — UI 없는 primitive, 서버는 Epic 5)
- [x] 표준 프레임워크 API(pytest, `parametrize`, `monkeypatch`, fake transport/sleep)
- [x] happy path(payload·send·주기 N회) + 임계 케이스(일반 예외 복원력·revoked→valid·401·malformed commands)
- [x] 전 테스트 통과 / 의미 있는 단언 / 명확한 설명(docstring) / 하드코딩 sleep 없음(주입 fake sleep) / 순서 독립(각 케이스 자체 fixture)
- [x] 요약 작성 · 적정 위치(`tests/agent/`) 저장 · 커버리지 명시

## Next Steps

- 4.4 startup `start_heartbeat_thread()` 배선 후 통합 경로(thread 기동·메인 run 루프) 테스트 추가.
- 실제 provider 소스(`active_jobs` 4.4 / `browser_profiles` 4.5 / `kakao_status` 4.6)가 배선되면 각 스토리에서 provider 주입 케이스로 확장.
