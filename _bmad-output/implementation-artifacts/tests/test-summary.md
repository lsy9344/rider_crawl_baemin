# Test Automation Summary — Story 5.8 (Audit log와 Admin 접근 보안)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 본 파일은 QA 런 정본이며 5.8 스냅샷이다. **제품 코드 무변경 — 테스트만 추가.**

## 결과 요약

| 구분 | dev-exit | post-QA(현재) | 증감 |
| --- | --- | --- | --- |
| 전체 스위트 passed | 1804 | **1815** | **+11** |
| 전체 스위트 skipped | 41 | 41 | 0 |
| 5.8 보강 3파일 always-run | 34 | **45** | **+11** |
| 5.8 PG-gated(skip) | 5 | 5 | 0 |

전체 회귀: `1815 passed, 41 skipped` — 신규 갭 테스트 11개, **회귀 0**. 이 수치가 review 정본(dev-exit 1804 는 QA 추가로 stale — memory/stale-test-count-a2). 모든 신규 테스트는 **always-run**(무 DB)이라 CI PG skip 환경에서도 실행된다.

## 발견·채운 갭

기존 always-run 은 핵심 불변식(4역할 rank·IP allowlist·MFA·revoke→401·non-sending AND·redaction)을 잘 잠갔으나, **(a) 읽기 경로 `enforce_session` 의 deny 분기(401/403, write-free)가 happy-path 만 커버, (b) 미인증 POST 가 audit 를 증폭하지 않는 anti-flooding 불변식 미검증, (c) `_audit_values` 순수 매핑(actor UUID/sentinel·7필드 passthrough)·`PostgresAgentTokenRepository.is_revoked` fail-closed 가 PG-gated 파일에만 가려짐, (d) token rotate 라우트·채널 rotate happy-path 라우트 커버리지 부재**에 갭이 있었다(memory/pg-gated-files-hide-pure-helpers).

### `tests/server/test_admin_security.py` (+5) — AC2 접근 제어

- `test_enforce_session_get_no_principal_is_401_and_write_free` — 읽기 전용 GET 의 fail-closed(principal 미해결→401) + **write-free**(audit 0). 기존엔 `test_viewer_can_read_dashboard` happy 만.
- `test_enforce_session_get_ip_not_allowed_is_403_and_write_free` — GET 경로 IP allowlist 거부(403) — `require_role` 와 별개인 `enforce_session` 의 IP deny 분기(무 audit).
- `test_no_principal_post_does_not_amplify_audit` — **anti-flooding 불변식**: 미인증 POST 는 401 이되 DENIED audit 0(`_audit_denied` 의도적 설계). 기존 `test_no_principal_is_401` 은 401 만.
- `test_allowlist_set_without_source_header_is_failclosed_403` — source 미상(XFF 없음→client host 가 IP 아님) + allowlist → `source_ip`/`ip_allowed` fail-closed 403 + DENIED audit.
- `test_async_resolve_admin_principal_seam_supported` — `resolve_principal` 의 `inspect.isawaitable` 분기(async principal seam). 기존엔 sync lambda 만.

### `tests/server/test_audit_log_schema.py` (+2) — AC1 audit 매핑

- `test_audit_values_maps_seven_fields_with_uuid_actor` — PG repo 순수 함수 `_audit_values` 의 actor/target UUID 파싱 + `source`/`reason`/`result` 7필드 passthrough(PG-gated 파일만 간접 사용해 CI skip 시 가려짐).
- `test_audit_values_preserves_unauthenticated_actor_sentinel` — 미인증 sentinel(UUID 아님) → `actor_id` 컬럼 NULL + `diff_redacted.actor` 보존(미인증도 추적).

### `tests/server/test_agent_token_revoke.py` (+4) — AC3 token revoke/rotate

- `test_route_secret_admin_can_rotate_agent_token` — `POST /admin/agents/{id}/token/rotate` 라우트(200 + rotate 시각 + AGENT_TOKEN_ROTATE audit). 기존 라우트 커버리지 0(revoke 만).
- `test_route_operator_cannot_rotate_agent_token` — rotate 도 SECRET_ADMIN↑ 게이트 — OPERATOR 403 + DENIED audit + rotate 미반영.
- `test_route_channel_token_rotate_accepts_ref` — `POST /admin/channels/{id}/token/rotate` happy(유효 `*_ref`→200 + audit ref 보존). 기존엔 평문 거부(400)만.
- `test_pg_is_revoked_failclosed_on_non_uuid_agent_id` — `PostgresAgentTokenRepository.is_revoked` 의 non-UUID fail-closed(→True) 분기(DB 접근 전 반환 → 무-DB always-run 추출).

## 설계 관찰(결함 아님 — 코드 리뷰 메모)

- **non-sending 소비처 부재**: `recovery.effective_send_enabled` + `Settings.sending_enabled` + `app.state.sending_enabled` 는 순수 helper/flag 로 테스트되지만 **dispatch 경로 소비처가 아직 없다**(settings/recovery/main 외 참조 0). 실제 전송 차단 통합은 Epic 5 reconcile 로 이연 → "실제 send 차단" end-to-end 단언은 만들지 않음(소비처 부재 시 가짜 green 위험). memory/story-5-8-audit-on-deny-anti-flooding 기록.
- **라우트 실 `now()`**: 라우트는 주입 불가한 실시간 `now()` 라 시각 단언 없이 인가 성공/거부/HTML 만 검증(memory/admin-routes-wallclock-severity).
- **HELD Dispatch 영속(열린 질문 #1)**: PG `get_held_dispatch→None` 보수적 미노출 — 순수 게이트 의미는 always-run 으로 잠금(5.7 선례 유지).

## 커버리지(5.8 AC별)

- **AC1 audit 완성**: 7필드 스키마·서비스 채움·DENIED·redaction(기존) + `_audit_values` 순수 매핑(actor UUID/sentinel·passthrough) 보강.
- **AC2 접근 제어**: 4역할 rank·IP·MFA·라우트 게이트·audit-on-deny(기존) + enforce_session deny(401/403 write-free)·anti-flooding·source 미상·async seam 보강.
- **AC3 token·복구성**: revoke/rotate service·외부 ref 회전·revoke→401·non-sending(기존) + rotate 라우트·채널 rotate happy·PG is_revoked fail-closed 보강.

## lock 무회귀

14표 lock·head 0005·9-dep·enum count-lock(`AdminRole`4·`AuditResult`3·기존 11/4/3/7)·단방향 import(`security/` `rider_agent` 0) 전부 유지 — 신규 컬럼/테이블/마이그레이션/enum/deps 0.

## 검증

- 3개 보강 파일: `pytest tests/server/test_admin_security.py test_audit_log_schema.py test_agent_token_revoke.py` → **45 passed**
- 전체 스위트: **1815 passed / 41 skipped** ✅ · 회귀 0

## Next Steps

- non-sending 게이트의 실제 dispatch 소비처가 Epic 5 reconcile 에서 배선되면 "복구 환경 실전송 0" end-to-end 테스트 추가.
- **PG-gated 5건**(audit 7필드 영속·`token_revoked_at` UPDATE·0005 round-trip·cross-tenant 누출 0)은 실 PostgreSQL(`TEST_DATABASE_URL`) CI 잡에서만 실행 — 현 WSL/venv skip.
- 정본 테스트 카운트(1815)는 review 단계에서 재측정해 Dev Agent Record 와 일치시킨다.
