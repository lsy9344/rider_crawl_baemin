# Test Automation Summary — Story 5.5 (Telegram webhook + 채널 등록·검증·활성화)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest (`.venv/Scripts/python.exe -m pytest`, `pythonpath=["src"]`) · 모드: 발견 갭 자동 적용

> 본 파일은 5.5 스냅샷이며, 동일 내용이 `test-summary.md`(QA 런 정본)에도 반영됐다. 제품 코드 무변경 — 테스트만 추가.

## 결과 요약

| 구분 | dev-exit | post-QA(현재) | 증감 |
| --- | --- | --- | --- |
| 전체 스위트 passed | 1615 | **1635** | **+20** |
| 전체 스위트 skipped | 24 | 24 | 0 |
| Story 5.5 always-run | 39 | **59** | **+20** |
| Story 5.5 PG-gated(skip) | 3 | 3 | 0 |

전체 회귀: `1635 passed, 24 skipped` — 신규 갭 테스트 20개, **회귀 0**. 이 수치가 review 정본(dev-exit 39 는 QA 추가로 stale — memory/stale-test-count-a2).

## 발견·채운 갭

기존 39 always-run + 3 PG-gated 는 happy-path·핵심 거부를 잘 잠갔으나, **에러 처리 분기·fail-closed 분기·양성 비대칭·문서화된 경계 동작·CI 에서 skip 되는 순수 의미**에 갭이 있었다.

### `tests/server/test_telegram_webhook.py` (+4 · AC1)

- `test_parse_register_prefers_message_over_channel_post` — `message or channel_post` 우선순위(둘 다 있을 때 message 채택).
- `test_webhook_malformed_body_is_ignored_with_ok_true` — **라우트 `except ValueError` 분기 전체 미검증**이었음. 비-JSON 본문 → `200 {"ok":true}` 흡수, 등록 안 됨.
- `test_webhook_schema_mismatch_body_is_ignored_with_ok_true` — 유효 JSON·스키마 불일치(chat.id 비-int) → pydantic `ValidationError`(ValueError 하위) 흡수 → 200(거부 401 아님).
- `test_webhook_registers_without_thread_id_keeps_thread_none` — thread 없는 등록의 HTTP 경계 `thread_id=None` 정규화.

### `tests/server/test_channel_lifecycle.py` (+14 · AC2·AC3)

- `test_soft_delete_allowed_from_non_active_states` — `PENDING→INACTIVE`·`VERIFIED→INACTIVE` 허용 전이.
- `test_self_and_unknown_transitions_are_denied` — self-전이·역행(`ACTIVE→VERIFIED/PENDING`) 거부.
- `test_operational_delivery_rules_excludes_dangling_channel_id` — **문서화된 dangling rule 처리**(맵에 없는 channel_id → KeyError 없이 게이트 제외).
- `test_operational_channels_empty_when_none_active` — 활성 0건 → `[]`.
- `test_find_kakao_collisions_normalizes_surrounding_whitespace` — 방명 `strip` 정규화 충돌(`"동일방"` ↔ `"  동일방  "`).
- `test_register_known_code_empty_chat_id_is_fail_closed` — **fail-closed 분기**(코드 식별·chat_id 빈값 → `registered=False`, channel non-None, 라우팅 미기록).
- `test_register_updates_routing_when_chat_id_changes` — 같은 코드·다른 chat 재등록 → 라우팅 갱신 + `PENDING` 재설정(멱등 no-op 아님).
- `test_register_reactivates_inactive_channel_to_pending` — **`INACTIVE→PENDING` reactivate**(`already_routed` 의 `state != INACTIVE` 절).
- `test_activate_allows_unique_kakao_room` — Kakao **양성 활성 경로**(기존엔 거부만 — 비대칭 해소).
- `test_activate_requires_verified_state_at_service_level` — activate() 에서 `PENDING` 활성 거부(검증 건너뛰기 차단).
- `test_deactivate_allowed_from_pending_without_activation` — `PENDING` 에서 soft-delete.
- `test_verify_and_deactivate_on_missing_channel_raise_not_found` — `verify`/`deactivate` 채널 부재 fail-closed(기존엔 activate 만).
- `test_postgres_repo_to_domain_coerces_row_without_db` — **PG-gated 가 가린 순수 converter** `_to_domain` always-run 추출(enum 강제·id 문자열화).
- `test_postgres_repo_to_domain_maps_kakao_row` — 〃 Kakao 행 변환.

### `tests/negative/test_messenger_channel_unique.py` (+2 · AC3, always-run)

- `test_0004_offline_upgrade_renders_partial_unique_on_active_only` — **CI 에서 skip 되던 `WHERE state='ACTIVE'` 부분 술어**(설계 핵심)를 offline SQL 렌더로 always-run 잠금(전역 유니크 아님 보장).
- `test_0004_offline_downgrade_round_trip_drops_index_and_column` — 0004→0003 round-trip(인덱스+컬럼 제거) offline 잠금.

> memory/`pg-gated-files-hide-pure-helpers` 준수: PG 부재 CI 에서 skip 되는 fail-closed/scope 의미(부분 유니크 술어·ORM→domain 변환)를 always-run 단위로 별도 추출.

## Coverage

| 표면 | 상태 |
| --- | --- |
| webhook 라우트 분기(거부/등록/무시/에러흡수) | ✅ 전부 |
| 채널 lifecycle 전이(register/verify/activate/deactivate) | ✅ 양·음성 |
| 운영 전송 게이트(ACTIVE-only·dangling) | ✅ |
| Telegram/Kakao 활성 고유성(거부+양성) | ✅ |
| 0004 부분 유니크 IntegrityError | ⏳ PG-gated(실 Postgres 시 실행) |
| 0004 부분 유니크 술어·round-trip(렌더) | ✅ always-run |

- **AC1**: secret 상수시간 검증, `/register` 파싱, 라우트(401 거부·등록·idempotent·**깨진/스키마불일치 본문 흡수**·기본 seam fail-closed) — 분기 커버 완료.
- **AC2**: 운영 게이트(`ACTIVE`만·dangling 제외·순서 보존), Telegram 충돌 재사용, Kakao 방명 고유성(**거부+양성**·방명 비노출), verify 실패 시 PENDING 유지 — 커버 완료.
- **AC3**: 전이 허용표(lifecycle·soft-delete·거부·self), register 멱등/갱신/reactivate, 0004(컬럼·**부분 유니크 술어**·round-trip), ORM↔domain 순수 변환 — 커버 완료.

## 검증 체크리스트

- [x] API 테스트 생성(webhook 라우트·서비스 오케스트레이션)
- [x] UI E2E — 해당 없음(Story 5.5 는 API/service/마이그레이션 표면만; 등록 UI 는 5.6/5.7 소유)
- [x] 표준 프레임워크(pytest) · happy path · 핵심 에러 케이스
- [x] 전 테스트 통과 — `1635 passed, 24 skipped`
- [x] 하드코딩 sleep/대기 없음(`asyncio.run`) · 테스트 독립성(각자 repo/app)
- [x] fake fixture 만(`FAKE-WEBHOOK-SECRET`/`-100fake` 등 — 실제 토큰/전화/이메일/chat_id 없음)
- [x] 요약·커버리지 메트릭 포함

실행:

```bash
.venv/Scripts/python.exe -m pytest tests/server/test_telegram_webhook.py \
  tests/server/test_channel_lifecycle.py tests/negative/test_messenger_channel_unique.py -q
# → 59 passed, 3 skipped  (PG-gated 는 TEST_DATABASE_URL 설정 시 실행)
```

## Next Steps

- CI always-run 회귀. 실 Postgres(`TEST_DATABASE_URL`) 연동 시 PG-gated 3건 + 부분 유니크 IntegrityError 자동 검증.
- review 단계에서 정본 카운트(**1635 passed / 24 skipped**)로 story Dev Agent Record dev-exit 수치(39) 갱신(memory/stale-test-count-a2).
