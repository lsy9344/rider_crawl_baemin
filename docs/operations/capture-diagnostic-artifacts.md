# CAPTURE_DIAGNOSTIC Artifacts (Phase 2 Design)

작성일: 2026-06-28
근거: agent-auth-observability-work-order-2026-06-28 Task 5

## 현재 상태(Phase 1)

- `CAPTURE_DIAGNOSTIC` 은 `JOB_TYPES`(`rider_server/queue/states.py`)와
  `DEFAULT_CAPABILITIES`(`rider_agent/heartbeat.py`)에 **남아 있다**(vocabulary/capability 불변).
  `tests/server/test_job_vocab.py` 의 `set(JOB_TYPES) == set(DEFAULT_CAPABILITIES)` 불변식과
  `tests/agent/test_autostart.py` 의 `handleable_job_types` 단언을 유지하기 위해 제거하지 않는다.
- 실제 artifact 캡처 워커는 **없다**. screenshot/html/clipboard 저장을 구현하지 않는다.
- Phase 1(Option A)에서 서버 enqueue 를 차단했다: `AdminActionService.test_crawl(...)` 은
  `CRAWL_BAEMIN`/`CRAWL_COUPANG` 만 허용하는 명시 allowlist(`_MANUAL_CRAWL_JOB_TYPES`)로
  `CAPTURE_DIAGNOSTIC` 을 fail-closed 거부한다(payload 생성/enqueue 전). scheduler 는
  `CAPTURE_DIAGNOSTIC` 을 enqueue 하지 않는다.

허용되는 문자열 위치(정상):

- `rider_server/queue/states.py` 의 vocabulary
- `rider_agent/heartbeat.py` 의 capability
- `tests/server/test_job_vocab.py` 의 mirror 테스트
- `rider_server/admin/routes.py` 의 표시 라벨(`"CAPTURE_DIAGNOSTIC": "진단 캡처"`)

금지되는 생성 위치:

- scheduler 가 `queue_backend.enqueue(job_type="CAPTURE_DIAGNOSTIC", ...)` 를 호출하는 경로
- `AdminActionService` 가 manual action 으로 `CAPTURE_DIAGNOSTIC` 을 enqueue 하는 경로
- Admin action route 가 form/API 입력만으로 `CAPTURE_DIAGNOSTIC` enqueue 를 여는 경로

## Phase 2 에서 결정해야 할 것

아래가 정해지기 전에는 screenshot/html 캡처 워커를 구현하지 않는다.

- **artifact 저장소**: local blob / S3 호환 storage / DB 외부 object storage 중 하나
- **다운로드 방식**: signed URL 또는 관리자 권한 기반 다운로드
- **보존 기간**: artifact 자동 삭제까지의 기간
- **최대 크기**: artifact 1건/총량 상한
- **허용 artifact type**: 예) screenshot(png), 정제된 html 일부
- **금지 artifact type**: 예) cookie/localStorage/token/원시 네트워크 로그
- **HTML redaction 정책**: 저장 전 secret/PII 제거 규칙
- **screenshot redaction 정책**: 민감 영역 마스킹 규칙
- **`JobEvent.artifact_refs` schema**: artifact 참조 구조(원시 바이트 비저장, ref/메타만)
- **artifact 삭제 배치**: 보존 기간 경과분 정리 작업

## Phase 2 구현 전제

아래가 정해지기 전에는 캡처 워커를 만들지 않는다.

- 저장소 위치
- 접근 권한
- 보존 기간
- 개인정보/토큰 redaction 규칙
- 운영자가 실제로 볼 화면

## 보류된 대안(이번 작업 범위 아님)

- **Option B** — Agent 가 `UNSUPPORTED_JOB_TYPE` 대신 명시적 `DIAGNOSTIC_NOT_CONFIGURED`
  실패로 닫기. 서버가 어쩔 수 없이 job 을 만들 수 있어야 한다는 제품/운영 결정이 내려진
  경우에만 별도 작업으로 연다. 새 Agent status `skipped` 를 만들지 않는다(complete API 가
  422). 결과는 기존 complete 계약 안에서 `status="failed"`,
  `error_code="DIAGNOSTIC_NOT_CONFIGURED"` 형태여야 한다.
- **Option C** — capability 와 job type 을 분리. claim matching 정책과
  `tests/server/test_job_vocab.py` 불변식을 함께 재설계해야 하므로 이번 기본 선택이 아니다.
