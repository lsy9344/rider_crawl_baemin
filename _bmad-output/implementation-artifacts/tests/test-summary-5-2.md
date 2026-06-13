# Test Automation Summary — Story 5.2 (PostgreSQL 14 테이블 스키마 + Alembic 마이그레이션)

작성: 2026-06-14 · 워크플로: `bmad-qa-generate-e2e-tests` · 역할: QA 자동화(테스트 생성 전용, 코드/스토리 검증 아님) · 프레임워크: pytest

## 컨텍스트

스토리 5.2 는 5.1 FastAPI 런타임 위에 처음 올라가는 **PostgreSQL/SQLAlchemy/Alembic 영속
레이어**다 — `db/base.py`(async engine/session·`Base`·naming_convention) · 14개 ORM 모델
(`db/models/`) · Alembic async 스캐폴드 + 초기 마이그레이션(`migrations/`). API 라우트·실제
CRUD 흐름·Pydantic 스키마는 5.2 범위 밖이라 **HTTP/E2E UI 테스트 대상이 아니다**. 검증 대상은
**DB 스키마 계약과 마이그레이션 fidelity**다.

dev-story 가 `tests/server/test_db_schema.py` 에 39건((a)metadata + (b)offline SQL +
(c)Postgres-gated)을 만든 상태에서, AC 대비 **커버리지 격차**를 찾아 DB 없이 동작하는 테스트
26건을 자동 보강(auto-apply)했다. **프로덕션 코드·마이그레이션 0줄 변경**(테스트만 생성, 새 의존성 0).

## 발견한 커버리지 격차 (모두 auto-apply)

| # | 격차 | 보강 | AC |
|---|------|------|----|
| G1 | PK/FK **결정적 제약 이름**(naming_convention)이 `uq_…` 외엔 미검증 — autogenerate drift 0 의 토대인데 pk_/fk_ 이름이 비잠금 | `test_pk_constraint_names_follow_naming_convention`, `test_fk_constraint_names_follow_naming_convention` | AC2 |
| G2 | FK 가 **올바른 부모 `id`** 를 가리키는지, audit_logs **무FK(다형 참조)** 설계가 미검증(필드 존재만 확인) | `test_foreign_keys_point_to_correct_parents` (11), `test_tables_without_fk_have_none` (3) | AC2 |
| G3 | FK 컬럼이 `<entity>_id` + UUID 타입인지 미검증 | `test_fk_columns_are_entity_id_uuid` | AC2 |
| G4 | 테이블 복수 snake_case / 컬럼 snake_case 규약 미검증 | `test_all_table_names_are_plural_snake_case`, `test_all_column_names_are_snake_case` | AC2 |
| G5 | JSON 컬럼(quotas·normalized_json·capacity_json·diff_redacted)의 이식 JSON(JSONB) 타입 per-column 미검증(offline SQL 의 `JSONB` 1회 존재만 확인) | `test_json_columns_use_portable_json_type` (4) | AC2 |
| G6 | `dedup_key` **NOT NULL** 미잠금 — Postgres 는 NULL 중복을 허용하므로 NOT NULL 없이는 유니크가 재시도를 못 막을 수 있음 | `test_dedup_key_is_not_nullable` | AC3 |
| G7 | **모델↔마이그레이션 drift**: `center_name` 은 모델·마이그레이션엔 있으나 REQUIRED_FIELDS 에 없어, 마이그레이션에서 빠져도 기존 (a)가 못 잡음. "autogenerate drift 0" 은 실DB 후속으로 미뤄짐 | `test_migration_renders_every_model_column` (전 모델 컬럼 전수 대조) | AC1 |
| G8 | 리비전 그래프 결정성(단일 head·initial base=None) 미검증 | `test_single_migration_head_with_initial_base` | AC1/AC3 |

## Generated 테스트 (신규 26건 — 모두 `tests/server/test_db_schema.py` 에 추가)

### (d) FK 관계 무결성·제약 네이밍 (metadata, DB 불필요)
- [x] `test_foreign_keys_point_to_correct_parents` (11 params) — 각 `<entity>_id` → 부모 테이블·`id` 정합
- [x] `test_tables_without_fk_have_none` (3 params) — tenants·agents·audit_logs 무FK 설계 잠금
- [x] `test_fk_columns_are_entity_id_uuid` — FK 컬럼명 `_id` + UUID 타입
- [x] `test_pk_constraint_names_follow_naming_convention` — 전 테이블 `pk_<table>`
- [x] `test_fk_constraint_names_follow_naming_convention` — `fk_<table>_<col>_<reftable>`

### (e) 테이블/컬럼 네이밍 규약 (metadata, DB 불필요)
- [x] `test_all_table_names_are_plural_snake_case`
- [x] `test_all_column_names_are_snake_case`

### (f) JSON→JSONB 이식 타입 (metadata, DB 불필요)
- [x] `test_json_columns_use_portable_json_type` (4 params)

### (g) dedup NOT NULL (metadata, DB 불필요)
- [x] `test_dedup_key_is_not_nullable`

### (h) 모델↔마이그레이션 drift·리비전 그래프 (Alembic offline SQL, DB 불필요)
- [x] `test_migration_renders_every_model_column` — 모든 모델 컬럼이 offline CREATE TABLE 에 렌더됨
- [x] `test_single_migration_head_with_initial_base` — 단일 head, 0001 base down_revision None

기존 39건 유지: (a) 14표·required fields·UUID PK·`_at` tz-aware·native-enum 0·평문 secret 0·`*_ref` 존재·dedup 유니크·account_id 보존, (b) offline 14 CREATE/DROP·JSONB·uq, (c) Postgres-gated 온라인.

## Coverage

| Acceptance Criterion | 커버 |
|---|---|
| AC1 — 빈 DB → 14 테이블 재현 + required fields | ✅ 14 CREATE TABLE(offline) + 14 컬럼셋 + **전 모델 컬럼 drift 가드** + 단일 head/initial base. 실DB 재현은 (c) Postgres-gated. |
| AC2 — DB 네이밍 정본 | ✅ 복수 snake_case 테이블 · snake_case 컬럼 · UUID PK `id` · **FK `<entity>_id`→부모 검증** · **pk_/fk_/uq_ 결정적 이름** · `_at` tz-aware · native ENUM 0 · secret `*_ref` 만(평문 0) · JSON→JSONB. |
| AC3 — dedup 유니크 | ✅ `uq_delivery_logs_dedup_key`(단일 컬럼) + **`dedup_key` NOT NULL** + offline round-trip(14 DROP). 실DB IntegrityError 차단·round-trip 은 (c) Postgres-gated. |

## Validation Results (단일 정본)

운영 venv `.venv/Scripts/python.exe -m pytest`:

- `tests/server/test_db_schema.py` → **65 passed, 1 skipped** (보강 전 39 → +26)
- 전체 스위트 `-q` → **1428 passed, 1 skipped** (보강 전 baseline 1402 + 신규 26, 순수 additive·회귀 0)
- 1 skipped = (c) Postgres-gated 온라인(현 WSL/venv 에 Postgres 부재 — `TEST_DATABASE_URL` 미설정)
- 가드 green(전체 스위트 통과로 확인): 9-dep lock(`test_pyproject_dependencies_unchanged_pins`)·단방향 import·rider_server async 경계.

## 범위/누출 검증

- 이번 QA 라운드 변경은 `tests/server/test_db_schema.py` 1개 파일뿐. 프로덕션 코드(`src/rider_server/db/**`)·마이그레이션(`migrations/**`)·`pyproject.toml` **0줄 변경**.
- 새 의존성 0: 신규 테스트는 기존 `sqlalchemy`/`alembic`(server extra)·stdlib `re` 만 사용 — 9-dep lock 불변.
- fixture 누출 가드: 모든 신규 테스트는 DB 연결 없이 `Base.metadata`/offline SQL 만 검사(실 토큰/전화/이메일/chat_id 0).

## 체크리스트 결과(`checklist.md`)

- [x] API/스키마 테스트 생성(DB 스키마·마이그레이션 계약) / E2E(UI 없음 — DB 레이어, 실DB e2e 는 (c) Postgres-gated 가 담당)
- [x] 표준 프레임워크 API(pytest, SQLAlchemy `inspect`/metadata, Alembic `command`/`ScriptDirectory`)
- [x] happy path(14표 재현·정상 제약) + 임계 케이스(drift 누락·잘못된 FK·평문 secret·native ENUM·NULL dedup·다형 무FK)
- [x] 전 테스트 통과(65/65 run, 전체 1428) / 의미 있는 단언(offenders 리스트로 실패 진단) / 명확한 docstring / 순서 독립(각 테스트 자체 metadata·offline SQL)
- [x] no hardcoded waits(동기 메타데이터/offline 렌더, sleep 0) / 요약 작성·적정 위치(`tests/server/`)·커버리지·수치 명시

## Next Steps

- Postgres 가용 환경(CI/로컬)에서 `TEST_DATABASE_URL` 설정 후 (c) 게이트 온라인 테스트 실행 → AC1·AC3 실DB literal fidelity 확정.
- 실DB `alembic revision --autogenerate` drift-0 실측(현재 DB-less drift 가드 + 결정적 제약 이름으로 1차 보장).
- 5.3+ repository/CRUD·job claim(`FOR UPDATE SKIP LOCKED`) 도입 시 트랜잭션·동시성 계약 테스트 추가.
