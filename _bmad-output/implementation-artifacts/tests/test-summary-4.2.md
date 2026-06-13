# 테스트 자동화 요약 — Story 4.2 (등록 코드 입력과 Agent 토큰 보안 저장)

- **워크플로:** `bmad-qa-generate-e2e-tests`
- **대상 기능:** `registration.register_agent` / `HttpTransport` / `secure_store.DpapiSecretStore` · `load_local_agent_identity` · `validate_agent_token` / `__main__` register CLI (P3-02, FR-12·16, NFR-7·8, ADD-6·15)
- **프레임워크:** pytest (`pyproject.toml`, `pythonpath=["src"]`) — UI 없는 순수 동기 등록 클라이언트라 서비스/단위 레벨 테스트(서버 부재 → 주입 transport/store stub)
- **실행:** `.venv/Scripts/python.exe -m pytest`
- **작성자:** Noah Lee (QA automate)
- **일자:** 2026-06-13

## 생성된 테스트

QA 커버리지 분석으로 발견한 갭을 자동 적용했습니다(`tests/agent/` additive, 기존 29건 → **52건**, +23건). 제품 코드 무변경 — 테스트 파일만 추가.

### `tests/agent/test_registration.py` (+17건)

| 테스트 | 갭 | 검증 내용 | AC |
|---|---|---|---|
| `test_http_transport_posts_json_and_returns_dict` | G1 | 실 stdlib `urllib` transport happy path — JSON body·`Content-Type`·`POST`·timeout 정합, 2xx dict 반환 | AC1 |
| `test_http_transport_http_error_surfaces_status_code` `[400·404·409·500]` | G1 | 4xx/5xx → `TransportError(status_code=...)`, 본문 미독(secret 누출 방지) | AC1 |
| `test_http_transport_url_error_becomes_transport_error_without_status` | G1 | 연결 실패(`URLError`) → `TransportError`(status 없음) | AC1 |
| `test_http_transport_non_json_response_becomes_transport_error` | G1 | 응답이 JSON 아님 → `TransportError` | AC1 |
| `test_http_transport_non_object_json_becomes_transport_error` | G1 | JSON 이지만 object 아님 → `TransportError` | AC1 |
| `test_register_agent_end_to_end_through_http_transport` | G1 | **E2E**: 실 `HttpTransport`(fake urlopen) → `register_agent` 분리 저장 + config 평문 token 부재 | AC1·AC2 |
| `test_register_url_uses_injected_base_url_and_strips_trailing_slash` | G2 | base_url 주입 시 trailing-slash 제거 + `REGISTER_PATH` 결합 | AC1 |
| `test_register_url_falls_back_to_env` | G2 | base_url 미주입 → `RIDER_AGENT_SERVER_URL` 폴백 | AC1 |
| `test_register_url_uses_default_when_unset` | G2 | base_url·env 둘 다 없음 → 기본 placeholder | AC1 |
| `test_register_blank_code_raises_without_posting` | G4 | 빈/공백 code → `RegistrationError`, POST 미발생(코드 미소모) | AC1 |
| `test_register_empty_token_in_response_raises_and_writes_nothing` | G3 | 빈 `agent_token` 응답 거부 + 미저장(빈 token 을 유효 identity 로 안 받음) | AC1·AC2 |
| `test_register_empty_agent_id_in_response_raises` | G3 | 빈 `agent_id` 응답 거부 | AC1 |
| `test_register_coerces_nondict_tenant_scope_and_nonstr_config_version` | G3 | `tenant_scope` 비-dict→`{}`, `config_version` 비-str→`str()` coerce | AC1 |
| `test_run_register_requires_code_arg` | G8 | CLI `--code` 필수(argparse) → 누락 시 `SystemExit` | Task 3 |

### `tests/agent/test_secure_store.py` (+6건)

| 테스트 | 갭 | 검증 내용 | AC |
|---|---|---|---|
| `test_dpapi_store_resolve_fail_closed_on_undecryptable_blob` | G5 | 타-머신/손상 blob(unprotect 실패) → 예외 전파 없이 `None`(fail-closed) | AC2·AC3 |
| `test_dpapi_store_corrupt_json_is_fail_closed` | G5 | store 파일 JSON 손상 → `resolve` `None`(무-크래시) | AC2 |
| `test_load_agent_config_corrupt_returns_none` | G6 | `agent_config.json` 손상 → `load_agent_config` `None` | AC1 |
| `test_load_identity_none_when_config_corrupt` | G6 | 손상 config + 유효 token 이어도 identity `None`(미등록 취급) | AC1·AC3 |
| `test_load_identity_none_when_agent_id_empty` | G6 | config `agent_id` 빈 문자열 → identity `None` | AC1·AC3 |
| `test_default_paths_separate_identity_and_secret_store` | G7 | **프로덕션 기본 경로**도 identity ≠ secret store, 같은 state dir·정확한 basename(분리 불변식) | AC2 |

### 기존 테스트 (변경 없음 — 회귀 보존)

기존 29건(AC1 등록·4값 파싱·분리 저장·멱등·코드거부, AC2 DPAPI seam·평문 비노출·repr/log redaction·store 분리·atomic, AC3 token 게이트 4상태) 및 4.1 가드 14건(third-party root·sync·단방향 import·deps 핀)은 모두 그대로 통과.

## 커버리지

- **`HttpTransport`(실 urllib 경로):** 기존 0건 → **happy(2xx) + 4xx/5xx(400·404·409·500) + URLError + 비-JSON + 비-object + E2E** 커버. 이전엔 모든 등록 테스트가 fake transport 라 프로덕션 transport 가 완전 미검증이었음(최대 갭).
- **`_register_url`:** base_url 주입 / env 폴백 / 기본값 — 3경로 커버.
- **`_identity_from_response` 보안 엣지:** 빈 token / 빈 agent_id / 비-dict tenant_scope / 비-str config_version — 커버.
- **`DpapiSecretStore` fail-closed:** 손상 blob(타-머신) / 손상 JSON → `None` — "재등록 필요" surfacing 경로 커버(AC3 게이트의 입력 조건).
- **identity 로드 robustness:** 손상 config / 빈 agent_id → `None`(미등록, 무-크래시).
- **분리 불변식:** 기존엔 주입 `tmp_path` 경로로만 검증 → **프로덕션 기본 경로**(`default_identity_path()` ≠ `default_secret_store_path()`)까지 잠금.
- **AC 매핑:** AC1(등록·4값·멱등·URL·입력검증·실 transport) · AC2(DPAPI seam·평문 비노출·분리·fail-closed) · AC3(token 게이트·미등록/만료/revoke surfacing) — **전부 커버**.

## 검증 결과

- **전체 스위트:** `1060 passed` (기준선 `1037` → +23 신규, **회귀 0**, 경고 0)
- **대상 디렉터리:** `tests/agent/` `66 passed` (4.1 가드 14 + 4.2 기존 29 + 신규 23)
- **4.1 가드 단독:** `tests/agent/test_agent_package.py` `14 passed` — third-party root=={rider_crawl}·sync·단방향·deps 핀 모두 green(신규는 테스트만 추가라 `src/rider_agent/*.py` glob 가드 불변)
- **범위:** `src/rider_crawl/`·`src/rider_server/`·`pyproject.toml`·제품 코드 **0줄 변경** — `tests/agent/` 두 파일만 additive
- **누출 스캔:** 신규 테스트 가짜값만(`agtok-fake-*`/`regcode-fake-*`/`agent-fake-*`), 실 봇 토큰 정규식 `[0-9]{6,}:[A-Za-z0-9_-]{30,}`·`chat_id=digits` **0건**
- **외부 호출:** 실 네트워크/실 DPAPI(비-skip 경로)/Telegram/Kakao/Gmail/브라우저 **미호출** — transport·codec·경로 전부 주입 fake + `tmp_path`

## 체크리스트 (`checklist.md`)

- [x] API/서비스 테스트 생성(등록 클라이언트·transport — 상태코드 200/400/404/409/500·에러 케이스·응답 구조 검증)
- [x] E2E 테스트 생성(UI 없음 — CLI thin wiring 은 커버; `register_agent` E2E 1건)
- [x] 표준 프레임워크 API(pytest·monkeypatch·parametrize·capsys) 사용
- [x] happy path 커버
- [x] 핵심 에러/경계 케이스 커버(HTTP 4xx/5xx·URLError·fail-closed·손상 입력·빈 token)
- [x] 전체 테스트 통과(1060)
- [x] 의미 기반 단언(분리 불변식·status_code·평문 부재·coercion)
- [x] 명확한 테스트 설명(갭 라벨 G1~G8 주석)
- [x] 하드코딩 sleep/wait 없음
- [x] 테스트 간 독립(각자 `tmp_path`·주입 fake, 순서 의존 0)
- [x] 요약 작성·커버리지 지표 포함

## 다음 단계

- CI에서 스위트 실행(기준선 1060). 실 DPAPI round-trip(`test_dpapi_real_round_trip_on_windows`)은 Windows 러너에서만 비-skip 실행.
- Story 4.3(heartbeat)·4.4(job claim) 구현 시 `validate_agent_token()` 게이트와 `HttpTransport` seam 위에 additive로 테스트 확장(4.4가 이 게이트를 claim 루프에 배선).
- 리뷰 시 Dev Agent Record 테스트 수치는 **재측정값 1개**(1060)로 정정(qa-e2e가 dev 노트 1037 이후 +23 append — memory/stale-test-count-a2).
