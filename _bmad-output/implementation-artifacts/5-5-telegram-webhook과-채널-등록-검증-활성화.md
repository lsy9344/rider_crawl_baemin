---
baseline_commit: 402610ccd6720b094418042556f9d2dc39378f4e
---

# Story 5.5: Telegram webhook과 채널 등록·검증·활성화

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want Telegram을 secret header 검증 webhook + `/register <code>`로 등록하고, 채널을 테스트 메시지 확인(검증) 후에만 활성화하고 싶다,
so that getUpdates polling 경합 없이 채널을 등록하고, 검증되지 않은 채널로 실서비스 전송이 나가지 않게 한다.

## Acceptance Criteria

> 정본 enum/컬럼 이름은 이미 구현된 코드를 따른다. 에픽 AC의 "등록/검증/활성" 표현은 코드값 `PENDING`(등록)·`VERIFIED`(검증)·`ACTIVE`(활성)에 대응한다. "topic_id"의 DB 컬럼명은 `thread_id`다(Telegram 와이어 필드는 `message_thread_id`).

**AC1 — secret header 검증 webhook + `/register <code>` (P4-06, FR-29, ADD-11)**
- **Given** Telegram 채널을 등록해야 할 때
- **When** secret header 검증 webhook과 `/register <code>` 명령 처리를 구현하면
- **Then** Telegram이 보낸 `X-Telegram-Bot-Api-Secret-Token` 헤더가 설정된 secret과 **상수시간 비교로 일치할 때만** 요청을 수락하고, 미검증(헤더 누락/불일치) 요청은 거부된다(에러 envelope, `getUpdates` polling 없이 동작).
- **And** `/register <code>` 수신 시 해당 코드로 식별되는 `messenger_channels` 행에 `telegram_chat_id`와 optional `thread_id`(= Telegram `message_thread_id`)가 **자동 저장**되고, 같은 chat에서 재등록(중복 update)해도 깨지지 않는다(idempotent).
- **And** webhook/등록 경로는 `getUpdates`/`TelegramUpdatePoller`를 **import하지 않는다**(AST import-edge 가드로 강제). 봇 토큰·webhook secret은 로그/예외/응답 본문/DB 평문에 노출되지 않는다(NFR-5·8).

**AC2 — 검증 전 운영 전송 차단(register→verify→activate 게이트, FR-29)**
- **Given** 채널을 활성화하기 전일 때
- **When** 등록(PENDING)·검증(VERIFIED)·활성(ACTIVE) 절차를 거치면
- **Then** Telegram 채널은 `chat_id`+`thread_id`가 확인되고 테스트 메시지가 성공한 뒤에만, Kakao 채팅방은 **고유 방명(또는 동등 식별) 정책**을 통과한 뒤에만 `state=ACTIVE`로 전이되어 전송 대상이 된다.
- **And** `state != ACTIVE`(PENDING/VERIFIED/INACTIVE) 채널에 연결된 DeliveryRule은 **실제 운영 전송에 쓰이지 않는다**(운영 전송 게이트 = `state == ACTIVE` 채널만). 이 게이트는 DB 없이 검증 가능한 순수 정책으로 존재한다.
- **And** 활성화 시 활성 Telegram 채널 간 `(chat_id, thread_id)` 중복이 없음을 강제하고(기존 `assert_unique_telegram_topics` 재사용), 활성 Kakao 채널 간 `kakao_room_name` 중복이면 활성화를 차단한다.

**AC3 — 채널 상태 기록과 영속(ADD-7 messenger_channels)**
- **Given** 등록·검증 결과를 추적해야 할 때
- **When** 채널 상태를 전이하면
- **Then** `messenger_channels.state`가 `PENDING`→`VERIFIED`→`ACTIVE`(+ soft-delete `INACTIVE`)로 구분되어 기록되고, 상태 전이는 **service 레이어에서만** 수행된다(라우트/DB 직접 컬럼 변경 금지).
- **And** 등록 코드 매핑과 활성 `(chat_id, thread_id)` 유니크를 위한 DB 마이그레이션(0004, additive)이 적용되며 `upgrade`/`downgrade`가 round-trip한다. DB 테이블 수는 **14개 유지**(신규 테이블 금지, 컬럼/제약만 additive).

## Tasks / Subtasks

- [x] **Task 1 — settings에 webhook secret/bot-token 소스 추가 (AC1)**
  - [x] 1.1 `src/rider_server/settings.py`의 `Settings`에 webhook secret·bot-token 소스 env를 **additive**로 추가한다(예: `telegram_webhook_secret_ref`, `telegram_bot_token_ref`). 기존 4-필드 positional 호환을 위해 default 값을 가진 **마지막 필드**로 두고, `from_env`에서 `env.get(...) or None`로 정규화한다. 평문 secret을 설정 객체에 싣지 않는다(`*_ref` 핸들/주입 seam만; `database_url` 추가 패턴 따름) [Source: src/rider_server/settings.py].
  - [x] 1.2 webhook secret/bot-token을 실제로 해석하는 주입 seam(예: `app.state.resolve_telegram_secret` / `resolve_token` 콜백)을 `create_app`에 둔다. 테스트가 fake secret을 주입할 수 있어야 한다(기존 `app.state.resolve_agent_id` seam 선례) [Source: src/rider_server/main.py:86-88].
- [x] **Task 2 — Telegram webhook 라우터 + secret header 검증 + `/register` 파싱 (AC1)**
  - [x] 2.1 `src/rider_server/api/telegram_webhook.py`에 `APIRouter`를 만들고 inbound `POST` webhook 엔드포인트를 추가한다. 경로는 외부 인바운드 단일 진입점으로 의도적으로 정한다(권장: `/v1/telegram/webhook`; 운영 엔드포인트 `/health`·`/version`·`/metrics`만 root-level 금지 대상이라 `/v1/` 리소스 경로는 허용된다) [Source: tests/server/test_server_app.py:95-102; architecture.md#API-Boundaries].
  - [x] 2.2 `X-Telegram-Bot-Api-Secret-Token` 헤더를 `secrets.compare_digest`로 **상수시간 비교**한다. 누락/불일치면 `HTTPException`(401 또는 403)을 raise해 전역 envelope로 거부한다(secret 값을 로그/응답에 넣지 않음). 검증 로직은 DB 없이 테스트 가능한 순수 함수로 분리한다 [Source: src/rider_server/main.py:118-151; src/rider_server/api/jobs.py:50-73].
  - [x] 2.3 Telegram Update 페이로드를 Pydantic v2(snake_case, camelCase alias 금지) 또는 안전 파서로 받아 `/register <code>` 명령을 인식한다. `message.text`/`channel_post`에서 `chat.id`(→`telegram_chat_id`)와 optional `message_thread_id`(→`thread_id`)를 추출한다. 명령이 아니거나 코드 누락이면 그냥 `200 {"ok": true}`로 무시(에러 아님)한다 [Source: src/rider_server/api/jobs.py:79-101,124-215].
  - [x] 2.4 라우터를 `src/rider_server/api/__init__.py`에 재노출하고 `create_app`에서 `app.include_router(...)`로 등록한다 [Source: src/rider_server/main.py:154; src/rider_server/api/__init__.py].
  - [x] 2.5 webhook 모듈이 `rider_crawl.sender.get_telegram_updates`/`TelegramUpdatePoller`/`telegram_commands` poller를 **import하지 않음**을 AST import-edge 가드로 강제한다(Story 3.7의 send-only 가드 미러; raw-text 검사 금지) [Source: memory negative-guard-tests-use-ast; 3-7 story Debug Log].
- [x] **Task 3 — 채널 등록/검증/활성 lifecycle service (AC2·AC3)**
  - [x] 3.1 채널 lifecycle service(예: `src/rider_server/services/channel_registration.py`)에 상태 전이를 둔다 — `register(chat_id, thread_id, code)`(→`PENDING`, 라우팅 id 저장), `verify(channel_id)`(`PENDING`→`VERIFIED`, 테스트 메시지 성공 확인 후), `activate(channel_id)`(`VERIFIED`→`ACTIVE`), `deactivate`(→`INACTIVE` soft-delete). 전이는 허용 set만 통과시키는 순수 검증을 가진다(잘못된 전이는 명확한 예외). 직접 DB 컬럼 변경 금지 — 전이는 이 service에서만 [Source: architecture.md#State-Management; src/rider_server/queue/states.py assert_transition 선례].
  - [x] 3.2 `MessengerChannelState`는 정본 4멤버(`PENDING`/`VERIFIED`/`ACTIVE`/`INACTIVE`)를 **그대로 사용**한다. 멤버를 추가/제거하지 않는다(count-lock: `tests/server/test_domain_states.py:147` `_names(MessengerChannelState) == {...}` 및 다른 파일의 잠금) [Source: src/rider_server/domain/states.py:105-111; memory enum-member-count-locks].
  - [x] 3.3 `activate` 시: Telegram이면 활성 채널 집합에 대해 `assert_unique_telegram_topics`(기존 함수 재사용)로 `(chat_id, thread_id)` 충돌을 강제한다. Kakao면 활성 채널 간 `kakao_room_name` 고유성(또는 동등 식별 정책)을 강제하고 중복이면 활성화를 차단한다 [Source: src/rider_server/services/telegram_central_dispatch.py:238-260].
  - [x] 3.4 DB 영속은 주입된 `async_sessionmaker[AsyncSession]`를 받는 repository로 처리한다(`PostgresQueueBackend.__init__`/`postgres_queue.py`의 `async with self._session_factory() as session: ... await session.commit()` 패턴, `_as_uuid` id 강제). `create_app`에 session-factory seam이 아직 없으므로 `app.state`에 추가한다(테스트는 in-memory fake repo 주입) [Source: src/rider_server/queue/postgres_queue.py; src/rider_server/db/base.py:54-60].
- [x] **Task 4 — 운영 전송 게이트: 검증 전 채널 차단 (AC2)**
  - [x] 4.1 운영 전송 대상 선별을 `state == ACTIVE` 채널로 제한하는 순수 게이트 함수를 둔다(예: `operational_channels(channels)` / `is_operational(channel)`). DeliveryRule fan-out 경로(`dispatch_fanout_service`)·중앙 Telegram 전송이 이 게이트를 거치게 한다 [Source: src/rider_server/services/telegram_central_dispatch.py:214-236; src/rider_server/services/dispatch_fanout_service.py].
  - [x] 4.2 PENDING/VERIFIED/INACTIVE 채널은 운영 전송에서 제외됨을 DB 없이 검증한다(순수 정책 단위 테스트). 기존 `find_telegram_topic_collisions`가 이미 `state==ACTIVE`만 보는 것과 일관 유지 [Source: src/rider_server/services/telegram_central_dispatch.py:218-230].
- [x] **Task 5 — 테스트 메시지 발송으로 검증(PENDING→VERIFIED) (AC2)**
  - [x] 5.1 검증용 테스트 메시지 발송 경로를 둔다 — 기존 `CentralTelegramSender`(send-only 어댑터)를 **재사용**한다(재구현 금지). 토큰은 `resolve_token` seam으로 주입하고 절대 로그/응답에 넣지 않는다 [Source: src/rider_server/services/telegram_central_dispatch.py:150-204].
  - [x] 5.2 `CentralTelegramSender`/`send_telegram_text`는 **동기(urllib)** 다. async webhook/route에서 직접 호출해 이벤트 루프를 블로킹하지 않는다 — `run_in_executor` 등 executor 경계로 감싸거나 동기 영역에서 호출한다(async-boundary 가드가 `src/rider_server/**`를 rglob으로 검사) [Source: architecture.md#Loading/Async-State; tests/server/test_server_async_boundary.py].
  - [x] 5.3 테스트 메시지 성공 시 검증 결과 기록(가능하면 `DeliveryLog` 생성, operations-security-test-contract "Messenger test → One test message succeeds and creates DeliveryLog")을 남긴다. 단, 실제 `DeliveryLog` 영속은 dedup/idempotency 경계(`idempotency.py`)와 충돌하지 않게 한다 [Source: src/rider_server/services/idempotency.py].
- [x] **Task 6 — DB 마이그레이션 0004 + ORM 동기화 (AC3)**
  - [x] 6.1 `src/rider_server/db/models/messaging.py`의 `MessengerChannel`에 **등록 코드 매핑 컬럼**(예: `registration_code: str | None`, nullable, 라우팅/운영용 — secret 아님)과 필요 시 `created_at`/`verified_at`/`activated_at`(`ts(nullable=True)`) 타임스탬프를 additive로 추가한다. 신규 테이블은 만들지 않는다(14표 유지). 결정사항은 아래 "열린 질문" 참조 [Source: src/rider_server/db/models/messaging.py:20-29; src/rider_server/db/models/_columns.py].
  - [x] 6.2 활성 채널 `(telegram_chat_id, thread_id)` 유니크를 DB로 강제한다. Postgres 부분 유니크 인덱스 `WHERE state = 'ACTIVE'`가 정책(활성만 유일)과 일치한다 — Alembic `op.create_index(..., postgresql_where=...)` 사용. 전역 유니크는 PENDING/INACTIVE 중복 등록을 막아 부작용이 클 수 있으니 부분 유니크를 권장한다 [Source: src/rider_server/services/telegram_central_dispatch.py:218-230; src/rider_server/db/base.py NAMING_CONVENTION].
  - [x] 6.3 `migrations/versions/0004_<name>.py`를 추가한다. `revision="0004_<name>"`, `down_revision="0003_monitoring_targets_scheduling"`. `upgrade()`는 `op.add_column(...)`+`op.create_index(...)`(부분 유니크), `downgrade()`는 정확한 round-trip. 0001/0002/0003은 수정 금지 [Source: migrations/versions/0003_monitoring_targets_scheduling.py:24-26].
  - [x] 6.4 `tests/server/test_db_schema.py` 가드 갱신: `test_single_migration_head_with_initial_base`의 단일 head를 `0003`→`0004`로 이동(선형 체인 0001→0002→0003→0004), `down_revision` 단언 추가. `test_metadata_has_exactly_14_contract_tables`는 **14 유지**. additive 컬럼이 `test_migration_renders_every_model_column`·`test_each_table_has_required_fields`(superset)와 일관됨을 확인 [Source: tests/server/test_db_schema.py:95-98,390,401-411].
- [x] **Task 7 — (ADD-12) telegram-dispatcher 배포 placeholder 채우기 (선택, AC와 무관한 운영 산출물)**
  - [x] 7.1 `deploy/docker-compose.yml`의 `# telegram-dispatcher: # Story 5.5` placeholder를 실제 서비스 정의로 바꾸고 `deploy/env/`에 webhook secret/public URL env를 분리 추가한다(`deploy/Dockerfile.server` 재사용). secret 평문 커밋 금지(`*.env`는 예시/플레이스홀더만) [Source: deploy/docker-compose.yml; architecture.md#Infrastructure-&-Deployment].
- [x] **Task 8 — 테스트 (AC1·AC2·AC3, 4-tier)**
  - [x] 8.1 always-run 순수 정책(DB 불필요): secret-header 검증(누락/불일치 거부, 일치 수락), `/register <code>` 파싱(chat_id/thread_id 추출, 비명령 무시), 상태 전이 허용/거부, 운영 게이트(`ACTIVE`만), Telegram 충돌·Kakao 고유 방명, `thread_id` None↔"" 정규화.
  - [x] 8.2 always-run 오케스트레이션(in-memory fake repo + 주입 now): webhook→register(PENDING)→verify(VERIFIED, 테스트 메시지 fake 성공)→activate(ACTIVE) 전체 흐름; 동일 chat 재등록 idempotent; 활성 `(chat_id, thread_id)` 중복 활성화 거부.
  - [x] 8.3 reuse/boundary 가드: webhook 모듈 `getUpdates`/poller import 없음(AST), 단방향 import(`rider_server`→`rider_crawl`만; `rider_agent` import 금지; 역방향 금지), `assert_unique_telegram_topics`/`CentralTelegramSender` 재사용(재구현 아님), async-boundary(블로킹 sync 직접 호출 없음).
  - [x] 8.4 PG-gated negative(`tests/negative/`, `@pytest.mark.skipif`로 `TEST_DATABASE_URL` 없으면 skip): 실제 부분 유니크 `uq_/ix_` IntegrityError(중복 활성 `(chat_id, thread_id)`); 유효 UUID·부모 행(tenant/messenger_channels) seed 후 검증; upgrade/downgrade round-trip. **SQLite로 Postgres 유니크 의미 대체 금지**.
  - [x] 8.5 테스트 파일 컨벤션: 상단 docstring `"""Story 5.5 / ACx …"""`, `from __future__ import annotations`, fake fixture만(`"FAKE-TELEGRAM-TOKEN"`, `chat_id="-100test"` 등 — 실제 토큰/전화/이메일/chat_id 금지), `tests/server/` flat(`__init__.py` 없음). async 테스트는 `httpx.AsyncClient + ASGITransport`(+`asyncio.run`) 또는 `TestClient`(pytest-asyncio 미사용) [Source: tests/server/test_server_async_e2e.py].

## Dev Notes

### 핵심 정본(검증 완료) — 절대 틀리면 안 되는 이름

- **채널 상태 enum**: `MessengerChannelState(str, Enum)` = `PENDING` / `VERIFIED` / `ACTIVE` / `INACTIVE` (정확히 4멤버, count-lock). 라이프사이클: 등록=`PENDING`, 검증=`VERIFIED`, 활성=`ACTIVE`, soft-delete=`INACTIVE`. **`REGISTERED` 멤버는 없다** — 등록 진입 상태는 `PENDING`이다 [Source: src/rider_server/domain/states.py:105-111].
- **`messenger_channels` 현재 컬럼**: `id`(UUID PK), `tenant_id`(FK tenants), `messenger`(String=`Messenger` 값 TELEGRAM/KAKAO), `telegram_chat_id`(String nullable, **라우팅 id·secret 아님**), `thread_id`(String nullable, **라우팅 id·secret 아님**), `kakao_room_name`(String nullable), `state`(String). **topic 컬럼명은 `thread_id`**(≠ `topic_id`/`message_thread_id`). secret 컬럼·타임스탬프·유니크 제약은 **아직 없음** [Source: src/rider_server/db/models/messaging.py:20-29].
- **마이그레이션 head = `0003_monitoring_targets_scheduling`** → 다음은 `0004_*`, `down_revision="0003_monitoring_targets_scheduling"`. 0001=초기 14표, 0002=jobs lease, 0003=monitoring_targets 스케줄링. **테이블 수는 14 고정**(SecretRef는 모델이나 테이블 아님; `test_metadata_has_exactly_14_contract_tables`) [Source: migrations/versions/; tests/server/test_db_schema.py:95-98].
- **job type vocab은 6개 plain-string 상수**(`CRAWL_BAEMIN`/`CRAWL_COUPANG`/`AUTH_CHECK`/`OPEN_AUTH_BROWSER`/`KAKAO_SEND`/`CAPTURE_DIAGNOSTIC`). **`DISPATCH_TELEGRAM` job type은 없다** — Telegram은 **중앙 send-only**(Agent job 아님). 구표기 `CRAWL`/`RENDER`/`DISPATCH_TELEGRAM` 사용 금지 [Source: src/rider_server/queue/states.py:22-29; memory agent-job-type-vocab].

### 재사용 자산(재구현 금지 — compose/import만)

- `src/rider_server/services/telegram_central_dispatch.py` (Story 3.7, contract-final):
  - `TelegramRoute`(frozen): `chat_id`, `thread_id`; `from_channel(channel)`(비Telegram/빈 chat_id면 `ValueError` fail-closed). **전송 scope = (chat_id, thread_id)** — webhook 라우팅에도 동일 개념 사용.
  - `CentralTelegramSender`: `channels`, `resolve_token`, `urlopen` 주입; `.send(job, text)`, `.as_send_callback()`. **send-only**(getUpdates/poller 호출 없음). 테스트 메시지 발송에 재사용.
  - `find_telegram_topic_collisions(channels)`·`assert_unique_telegram_topics(channels)`(`TelegramTopicCollisionError`, 메시지 `redact()` 통과) — **`state==ACTIVE` Telegram 채널만** 대상. 활성화 게이트로 재사용. `thread_id` None↔"" 정규화 내장.
  - `is_ambiguous_send_failure(exc)`: 모호 실패는 dedup key release 금지(oversend 회피) — 3.7이 helper만 두고 런타임 배선은 Epic 5로 미룸(연계 시 release 결정에 연결).
  - 재노출: `from rider_server.services import CentralTelegramSender, TelegramRoute, assert_unique_telegram_topics, find_telegram_topic_collisions, TelegramTopicCollisionError` [Source: src/rider_server/services/__init__.py].
- `rider_crawl.sender.send_telegram_text(...)`(legacy 전송, `CentralTelegramSender` 내부에서 재사용). **`rider_crawl`에 `setWebhook` helper는 없다** — webhook 등록(setWebhook + secret_token)이 필요하면 이 스토리가 운영 단계/별도 헬퍼로 추가. `get_telegram_updates`/`telegram_commands`(poller)는 **확장하지 말고 우회**한다 [Source: src/rider_crawl/sender.py].
- 에러 envelope: `rider_crawl.redaction.redacted_error_event` + `main.py` 전역 핸들러. **라우트에서 `{"error":...}` 직접 만들지 말고** `HTTPException(status, detail)`을 raise하면 전역 핸들러가 `{"error":{"code":"<UPPER_SNAKE>","message_redacted":"..."}}`로 변환(코드=`HTTPStatus(status).name`). 422는 `VALIDATION_ERROR`, 미처리는 `INTERNAL_ERROR`. 검증 실패 메시지에 입력값(secret 가능) 금지 [Source: src/rider_server/main.py:57-69,118-151].
- `redact(text, *, mask_operational_ids=False)`: 자유 텍스트의 chat_id/thread_id는 **기본 마스킹 안 함**. 진단 문자열은 `chat_id=<v> thread_id=<v>` key=value로 만들어 `redact(..., mask_operational_ids=True)`로 마스킹(3.7 충돌 진단 선례). **봇 토큰·webhook secret은 절대 로그/예외/응답에 넣지 않음**(어떤 경우에도) [Source: src/rider_crawl/redaction.py:130; memory redact-skips-operational-ids].

### FastAPI 라우팅·설정 패턴(복붙용)

- 라우터: `APIRouter(prefix="/v1/<plural>", tags=[...])` → `api/__init__.py` 재노출 → `create_app`에서 `app.include_router(...)`. 응답은 plain `dict` 반환(또는 Pydantic), 시각은 ISO 8601 UTC `...Z`(`_iso_utc` idiom) [Source: src/rider_server/api/jobs.py:44,107-114; src/rider_server/main.py:154].
- 인증/헤더 seam 선례: `resolve_agent`(Depends)가 `request.headers`에서 토큰을 읽고 불일치 시 `HTTPException(401)`, 토큰을 echo하지 않음 — webhook secret-header 검증을 같은 모양으로 [Source: src/rider_server/api/jobs.py:50-73].
- backend/의존성 주입: 현재 `Depends(get_session)` 없음. `request.app.state.<x>`로 읽는다(`app.state.queue_backend`/`resolve_agent_id` 선례). DB 접근 repository는 생성자에 `async_sessionmaker` 주입(`postgres_queue.py` 템플릿). session-factory seam을 `app.state`에 신설 [Source: src/rider_server/main.py:86-88; src/rider_server/queue/postgres_queue.py].
- 라우트 경로 가드: `test_registered_routes_have_no_v1_operational_paths`는 `/v1/health|version|metrics`만 금지하고 `/v1/jobs/*`는 허용. webhook을 `/v1/telegram/webhook`에 두는 것은 가드와 충돌하지 않는다 [Source: tests/server/test_server_app.py:95-102].
- settings: stdlib `os.environ` frozen dataclass + `from_env`(빈 문자열→None). **`pydantic-settings` 도입 금지**(9-dep lock). 신규 env는 default 가진 마지막 필드로 additive [Source: src/rider_server/settings.py].

### 가드레일(위반 시 CI 실패)

- **9-dep lock**: `[project].dependencies` 정확히 9개 유지. webhook은 신규 third-party 의존성 불필요(FastAPI/SQLAlchemy/asyncpg/httpx는 이미 `[project.optional-dependencies]`의 server/dev 그룹). 필요해도 main deps에 절대 추가 금지 [Source: memory server-deps-go-in-optional-group].
- **enum count-lock**: `MessengerChannelState`(4)·`CustomerLifecycleState`(11)·`SubscriptionStatus`(4)·`FailureCategory`(7) 멤버 추가/삭제 금지. 새 vocab은 plain-string 모듈 상수로. 잠금이 **여러 파일**에 흩어져 있으니 변경 전 repo 전체 grep [Source: memory enum-member-count-locks; tests/server/test_domain_states.py].
- **단방향 import**: `rider_server`는 `rider_crawl`만 import. `rider_agent` import 금지, `rider_crawl`/`rider_agent`는 `rider_server` import 금지(AST 가드) [Source: project-context.md].
- **상태 전이는 service 레이어에서만**(라우트/DB 직접 컬럼 변경 금지) [Source: architecture.md#State-Management].
- **async-boundary**: async 함수에서 `time.sleep`/`subprocess.*`/blocking sync 직접 호출 금지(가드가 `src/rider_server/**` rglob). 동기 `CentralTelegramSender` 호출은 executor 경계로 [Source: tests/server/test_server_async_boundary.py].
- **secret 위생**: 봇 토큰·webhook secret = secret → `*_ref`/주입 seam, 평문 DB/로그/응답 금지. `telegram_chat_id`/`thread_id` = 라우팅 id(secret 아님, `*_ref`화 금지)지만 로그/예외 breadcrumb엔 `redact(..., mask_operational_ids=True)` [Source: project-context.md; data-api-contract].

### 개발 환경·프로세스(매 스토리 반복되는 함정)

- pytest는 **`.venv/Scripts/python.exe -m pytest`** 로 실행(WSL `python3` 미설치; `pythonpath=["src"]`, editable 설치 없음 — 한글 경로 `.pth`가 cp949 UnicodeDecodeError 유발) [Source: memory dev-env-quirks].
- 신규 파일은 `\n`으로 작성(CRLF 재변환이 content-compare 멱등 깨뜨림), diff는 `git diff -w`로 확인 [Source: memory crlf-roundtrip-idempotency].
- 커밋 컨벤션 `feat(story-5.5): …`, baseline 커밋 `402610c`(5.4).
- **테스트 카운트는 review 시 재측정**: dev가 적은 수치는 qa-generate-e2e가 케이스 추가하며 stale해진다. dev-exit vs post-QA를 구분해 정본 한 숫자를 review에서 기록 [Source: memory stale-test-count-a2].
- **PG-gated 파일이 순수 helper를 가린다**: secret-header 검증·상태 전이·게이트·충돌 같은 fail-closed/scope 의미는 always-run 단위 테스트로 별도 추출(CI에서 PG skip돼도 실행되게) [Source: memory pg-gated-files-hide-pure-helpers].

### 이번 스토리가 책임지는 "3.7/5.1~5.4가 미룬 것"

- 3.7이 명시적으로 5.5로 위임: 인바운드 webhook(`api/telegram_webhook.py`), secret header 검증, `/register <code>`(chat_id+optional message_thread_id 자동 저장), **DB UNIQUE(chat_id+topic)**, 채널 등록/검증/활성 라이프사이클, (선택) async dispatcher·실제 DeliveryLog 영속 [Source: src/rider_server/services/telegram_central_dispatch.py:13-19; 3-7 story line 129].
- 5.1/5.3/5.4 각각 "Telegram webhook/`/register`(5.5)"를 out-of-scope로 명시 [Source: 5-1/5-3/5-4 story files].
- 본 스토리 **이후**(혼동 금지): Admin 대시보드(5.6)·수동 운영 액션/구독 상태 전이(5.7)·MFA/audit(5.8)·`telegram_send_error_rate` 등 7지표(5.9)·부하 smoke(5.10)·Admin CRUD UI(5.11). 등록/검증을 트리거하는 **Admin UI 버튼**은 5.6/5.7 소유 — 본 스토리는 API/service/마이그레이션 표면까지.

### Project Structure Notes

- 신규: `src/rider_server/api/telegram_webhook.py`(라우터), `src/rider_server/services/channel_registration.py`(또는 유사 — lifecycle/게이트 service), `migrations/versions/0004_<name>.py`, `tests/server/test_telegram_webhook.py`·`tests/server/test_channel_lifecycle.py`(또는 유사), `tests/negative/test_messenger_channel_unique.py`(PG-gated).
- 수정: `src/rider_server/settings.py`(env additive), `src/rider_server/main.py`(include_router + state seam), `src/rider_server/api/__init__.py`(재노출), `src/rider_server/db/models/messaging.py`(컬럼 additive), `tests/server/test_db_schema.py`(head 0003→0004), (선택) `deploy/docker-compose.yml`·`deploy/env/`.
- **변경 금지**: `telegram_central_dispatch.py`(contract-final, compose만), 0001/0002/0003 마이그레이션, 기존 enum 멤버, `rider_crawl`/`rider_agent` 패키지(역/교차 import 금지).
- async dispatcher 디렉터리(`src/rider_server/dispatch/telegram_dispatcher.py`)는 architecture 트리에 예고돼 있으나, 3.7이 "반쯤 빈 패키지 선점"을 피해 미생성. 본 스토리에서 만들지(아키텍처 정합) 동기 어댑터를 `services/`에 유지할지 **의도적으로 결정**한다. AC가 outbound 디스패치 배선을 요구하지 않으므로 최소 범위(테스트 메시지 발송만)로 둬도 무방.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.5 (lines 1002-1022)] — 스토리 정의·AC, ADD-11(139)/ADD-12(140)/FR-24(64)/FR-29(72)
- [Source: _bmad-output/planning-artifacts/architecture.md] — API Naming/Format(259-298), 에러 envelope, Telegram webhook secret header(263, #API-Boundaries), State Management(322), Async(336-338), Infra/Deploy(207-211)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md] — messenger_channels Required fields(30), Admin API register/verify/test(88), Customer lifecycle(104-106)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md] — P4-06(75), P5 Telegram/Kakao 등록(84-85)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md] — Telegram token Secrets Manager(7), redaction(15-19), Messenger test → DeliveryLog(49), Kakao unique room risk(77)
- [Source: src/rider_server/domain/states.py:74-111] — Messenger·MessengerChannelState
- [Source: src/rider_server/db/models/messaging.py:20-76] — MessengerChannel/DeliveryRule/DeliveryLog ORM
- [Source: src/rider_server/services/telegram_central_dispatch.py] — 재사용 send-only 어댑터·충돌 정책
- [Source: src/rider_server/main.py / api/jobs.py / settings.py] — 앱 팩토리·라우터·에러 envelope·settings 패턴
- [Source: tests/server/test_db_schema.py / test_server_app.py / test_server_async_boundary.py] — 갱신할 가드
- [Source: _bmad-output/project-context.md] — 프로젝트 고유 규칙(단방향 import·secret 정책·텔레그램 단일 큐·redaction)

### 열린 질문(구현 시 결정 — 막지 말고 가장 안전한 선택)

1. **등록 코드(`<code>`) 출처/저장**: 기존 도메인에 `setup_code`/`registration_code` 필드가 **없음**(tenant/subscription에도 없음). 권장: `messenger_channels.registration_code`(nullable, 운영자가 PENDING 채널 사전 생성 시 1회용 코드 부여 → 고객이 `/register <code>` → webhook이 chat_id/thread_id 채움). 단, `CustomerLifecycleState.MESSENGER_VERIFY_PENDING`/`TEST_RUNNING`(테넌트 수준 라이프사이클)과의 연계는 5.7 소유 — 본 스토리는 채널 수준만. 더 단순한 대안이 있으면 그것으로 하되, **신규 테이블 추가는 금지**(14표 lock).
2. **활성 유니크 범위**: `(telegram_chat_id, thread_id)` 유니크를 **부분 유니크(`WHERE state='ACTIVE'`)** 로 권장(순수 정책 `find_telegram_topic_collisions`가 ACTIVE만 보는 것과 정합; PENDING/INACTIVE 중복 등록 허용). 전역 유니크로 하면 재등록/soft-delete 시 충돌 위험.
3. **테스트 메시지 검증(PENDING→VERIFIED) 트리거 주체**: 본 스토리는 API/service 표면(테스트 메시지 발송 + 검증 전이)까지. 운영자가 누르는 Admin UI 버튼/화면은 5.6/5.7. 자동 vs 수동 확인 경계가 모호하면 service 함수로 노출만 하고 UI는 후속 스토리에 위임.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8) — BMAD dev-story workflow

### Debug Log References

- 전체 회귀: `.venv/Scripts/python.exe -m pytest -q` → dev-exit **1615 passed, 24 skipped**; review 재측정(QA 갭 테스트 +20) **1635 passed, 24 skipped, 0 failures**(정본).
- 본 스토리 신규 테스트(always-run 59 + PG-gated 3): `pytest tests/server/test_telegram_webhook.py tests/server/test_channel_lifecycle.py tests/negative/test_messenger_channel_unique.py` → 59 passed, 3 skipped(Postgres 부재). dev-exit 의 39 always-run 은 QA 추가로 stale(memory/stale-test-count-a2) — review 에서 59 로 정정.
- 오프라인 마이그레이션 렌더 확인: 0004 가 `registration_code` 컬럼 + `uq_messenger_channels_active_telegram_topic` 부분 유니크(`WHERE state = 'ACTIVE'`)를 Postgres dialect SQL 로 생성함을 검증.

### Completion Notes List

**구현 요약(AC 매핑)**

- **AC1** — `api/telegram_webhook.py`: `POST /v1/telegram/webhook`. `verify_webhook_secret`(순수, `secrets.compare_digest` 상수시간, fail-closed)를 **본문 파싱 이전에** 적용 → 헤더 누락/불일치는 페이로드 처리 없이 401 전역 envelope. `/register <code>` 파서는 Pydantic v2 snake_case(`message`/`channel_post`, `chat.id`→chat_id, `message_thread_id`→thread_id, `@Bot` 접미 처리), 비명령/코드 누락/깨진 JSON 은 `200 {"ok": true}` 무시. 멱등 저장은 service 가 담당.
- **AC2** — `services/channel_registration.py`: register→verify→activate→deactivate 전이(허용표 + fail-closed). 운영 전송 게이트 `is_operational`/`operational_channels`/`operational_delivery_rules`(순수, `state == ACTIVE` 만; fan-out plan 앞단 합성으로 미검증/소프트삭제 채널 차단). 활성화 시 Telegram 은 3.7 `assert_unique_telegram_topics` **재사용**(동일 객체 identity 테스트), Kakao 는 `assert_unique_kakao_rooms`(신규, 방명 값 미노출). 테스트 메시지는 `CentralTelegramSender` 재사용 + `asyncio.to_thread` executor 경계(동기 urllib 비블로킹).
- **AC3** — `MessengerChannelState` 정본 4멤버 그대로(count-lock 무변경). 상태 전이는 service 레이어에만. 0004 마이그레이션(additive): `registration_code`(nullable) + 부분 유니크 인덱스. round-trip downgrade. 테이블 수 14 유지(신규 테이블 0).

**열린 질문 결정**

1. 등록 코드: `messenger_channels.registration_code`(nullable, secret 아님) 채택 — 운영자 pre-provision 1회용 코드. 신규 테이블 없음(14표 lock 준수).
2. 활성 유니크 범위: **부분 유니크 `WHERE state='ACTIVE'`** 채택(순수 정책 `find_telegram_topic_collisions`가 ACTIVE 만 보는 것과 정합; PENDING/INACTIVE 중복 허용해 재등록/soft-delete 충돌 회피).
3. 테스트 메시지 트리거 주체: 본 스토리는 **service 표면**(`verify(channel_id, send_test=...)`)까지. verify/activate 를 트리거하는 Admin UI/route 는 5.6/5.7 소유 — 라우트로 노출하지 않고 service 메서드로만 둠.

**의도적 범위 결정**

- async dispatcher 디렉터리(`dispatch/telegram_dispatcher.py`) **미생성** — 동기 send-only 어댑터를 `services/`에 유지(반쯤 빈 패키지 선점 회피, 3.7 선례). AC 가 outbound 디스패치 배선을 요구하지 않으므로 최소 범위(테스트 메시지 발송만).
- 타임스탬프 컬럼(`verified_at`/`activated_at`/`created_at`) **미추가**(Task 6.1 "필요 시") — AC3 의 상태 기록은 기존 `state` 컬럼으로 충족, 마이그레이션 표면 최소화·drift 위험 감소.
- `DeliveryLog` 영속 **미생성**(Task 5.3 "가능하면") — dedup/idempotency 경계 충돌 회피(`idempotency.py`). 검증은 상태 전이로 기록.
- Task 7(ADD-12, 선택): webhook 은 별도 dispatcher 프로세스가 아니라 backend-api 인바운드 라우트로 서빙하기로 결정 → `deploy/env/telegram-webhook.env`(`*_ref`/공개 URL 플레이스홀더만, 평문 secret 0) 신설 + docker-compose `backend-api` env_file 에 연결. 별도 서비스 정의는 만들지 않음(phantom 프로세스 회피).

**가드/재사용 준수**

- 단방향 import 가드(신규 3개 모듈 `rider_agent` import 0, third-party 허용집합 내) — AST 테스트 추가.
- webhook send-only 가드(getUpdates/`TelegramUpdatePoller`/`telegram_commands` import 0) — AST 테스트 추가(3.7 미러).
- async-boundary 가드(기존 `test_server_async_boundary.py` rglob 이 신규 모듈 자동 커버; `asyncio.to_thread` 는 비금지) — 0 위반.
- enum count-lock(`MessengerChannelState` 4), 9-dep lock, 14표 lock 무회귀.

**테스트 카운트(review 재측정 정본)**: 신규 62개(always-run 59 + PG-gated 3, `tests/server/test_telegram_webhook.py` 20 · `tests/server/test_channel_lifecycle.py` 37 · `tests/negative/test_messenger_channel_unique.py` 5[always-run 2 + PG-gated 3]). 전체 스위트 1635 passed / 24 skipped / 0 failures. (dev-exit 의 42개[39+3]·1615 은 QA 갭 테스트 +20 이전 수치라 stale — memory/stale-test-count-a2 에 따라 review 에서 정정.)

### File List

**신규(production)**
- `src/rider_server/api/telegram_webhook.py` — webhook 라우터·secret 검증·`/register` 파서
- `src/rider_server/services/channel_registration.py` — lifecycle service·전이표·운영 게이트·Kakao 고유성·in-memory repo
- `src/rider_server/services/channel_repository_postgres.py` — PostgreSQL `ChannelRepository`
- `migrations/versions/0004_messenger_channel_registration.py` — additive 컬럼 + 부분 유니크 인덱스

**신규(test)**
- `tests/server/test_telegram_webhook.py`
- `tests/server/test_channel_lifecycle.py`
- `tests/negative/test_messenger_channel_unique.py` (PG-gated)

**수정**
- `src/rider_server/settings.py` — `telegram_webhook_secret_ref`/`telegram_bot_token_ref` additive
- `src/rider_server/main.py` — `channel_repository`·`resolve_telegram_secret` seam + `telegram_webhook_router` include
- `src/rider_server/api/__init__.py` — `telegram_webhook_router` 재노출
- `src/rider_server/services/__init__.py` — 채널 등록/게이트/Kakao 심볼 재노출(additive)
- `src/rider_server/db/models/messaging.py` — `MessengerChannel.registration_code` 컬럼 additive
- `tests/server/test_db_schema.py` — 단일 head 0003→0004 + down_revision 단언 갱신(14표 유지)
- `deploy/docker-compose.yml` — backend-api 에 webhook env_file 연결(telegram-dispatcher placeholder 정리)
- `deploy/env/telegram-webhook.env` — webhook secret(`*_ref`)·공개 URL 플레이스홀더(신규)

### Change Log

| Date       | Version | Description                                                                                   | Author |
| ---------- | ------- | --------------------------------------------------------------------------------------------- | ------ |
| 2026-06-14 | 0.1     | Story 5.5 구현 — Telegram secret-header webhook + `/register`, 채널 register/verify/activate lifecycle, 운영 전송 게이트, Kakao 방명 고유성, 0004 마이그레이션(additive 컬럼 + 부분 유니크). 전체 1615 passed/24 skipped. | Amelia (claude-opus-4-8) |
| 2026-06-14 | 0.2     | Senior Developer Review (AI) — 3 AC·8 Task 전수 검증, 전체 1635 passed/24 skipped/0 failures. CRITICAL/HIGH 0. MEDIUM 1(stale 테스트 카운트 39/1615 → 정본 59/1635 정정). LOW 3(기록만). Status review→done. | dltnduf4318 (review) |

## Senior Developer Review (AI)

**Reviewer:** dltnduf4318 · **Date:** 2026-06-14 · **Outcome:** ✅ Approve (status review → done)

**Scope:** 3 AC / 8 Task 전수 검증, git diff vs File List 대조, 전체 회귀 `1635 passed, 24 skipped, 0 failures`(`.venv/Scripts/python.exe -m pytest -q`).

### AC 검증 결과 (전부 IMPLEMENTED)

- **AC1 (secret-header webhook + `/register`)** — `api/telegram_webhook.py`: `verify_webhook_secret` 가 `secrets.compare_digest` 상수시간 비교 + 양측 누락 fail-closed, **본문 파싱 이전**에 적용(미검증 요청은 페이로드 처리 0). `parse_register_command` 가 `message`/`channel_post`·`@Bot` 접미·chat_id/thread_id 추출·비명령 무시 처리. AST 가드(`test_webhook_module_is_send_only_no_getupdates_or_poller`)로 `getUpdates`/`TelegramUpdatePoller`/`telegram_commands` import 0 강제. webhook/등록 경로는 봇 토큰을 **해석조차 하지 않고**(secret 만 사용), HTTPException detail 에 입력값 echo 없음 → secret/토큰 비노출 확인.
- **AC2 (register→verify→activate 게이트)** — `services/channel_registration.py`: `is_operational`/`operational_channels`/`operational_delivery_rules` 가 `state == ACTIVE` 만 통과시키는 순수 정책(DB 0). `activate` 가 Telegram 은 3.7 `assert_unique_telegram_topics` **동일 객체 재사용**(`test_telegram_collision_function_is_reused_not_reimplemented` 가 identity 잠금), Kakao 는 `assert_unique_kakao_rooms`(방명 값 미노출)로 활성 충돌을 fail-closed 차단. `verify` 가 동기 `CentralTelegramSender.send`(urllib)를 `asyncio.to_thread` executor 경계로 감싸 async 루프 비블로킹.
- **AC3 (상태 기록·영속)** — `MessengerChannelState` 정본 4멤버 무변경(count-lock 통과). 상태 전이는 `ChannelRegistrationService` 에만(라우트는 `service.register` 위임, repository 는 영속만). 0004 마이그레이션 additive(`registration_code` nullable + `WHERE state='ACTIVE'` 부분 유니크), `test_metadata_has_exactly_14_contract_tables`·offline upgrade/downgrade round-trip 통과 → 14표 유지.

### Git vs File List
File List 의 신규 4(prod)·3(test) + 수정 8 파일이 `git status` 와 정확히 일치(불일치·미문서 변경 0).

### Findings

- **[MEDIUM][FIXED] 테스트 카운트 stale.** Dev Agent Record 의 dev-exit 수치(always-run 39 / 전체 1615)가 QA 갭 테스트 +20 이전 값. review 재측정 정본(always-run 59 + PG-gated 3 = 62 신규 / 전체 1635)으로 Debug Log·Completion Notes 를 정정함(memory/stale-test-count-a2). 코드 변경 없음.
- **[LOW][기록만] `registration_code` DB 유니크 부재.** `get_by_registration_code` 가 `.first()` 라 같은 코드가 둘 이상이면 임의 1행으로 라우팅. 코드는 운영자 1회용 pre-provision 값이고 webhook 은 secret 게이트 뒤라 위험 낮음 — 신규 제약은 14표/additive lock·재등록 충돌 회피 정책과 트레이드오프라 의도적 미추가. 운영 절차로 코드 유일성 보장 권장.
- **[LOW][기록만] 부분 유니크 인덱스가 migration-only.** `uq_messenger_channels_active_telegram_topic` 는 ORM `__table_args__` 가 아닌 0004 에만 선언(model↔migration drift). `WHERE` 술어 부분 인덱스는 선언적 표현이 번거롭고, 본 repo 는 Alembic 이 스키마 정본이라 허용 가능. `registration_code` **컬럼**은 model+migration 양쪽 동기화돼 `test_migration_renders_every_model_column` 통과.
- **[LOW][기록만] `_normalize_thread_id` 재구현.** `channel_registration` 이 3.7 `telegram_central_dispatch._normalize_thread_id`(private)와 동형 3줄 헬퍼를 재작성. private 심볼 교차 import 회피 vs DRY 트레이드오프 — 동형 의미가 주석으로 명시돼 있어 수용. 또한 register 는 채널 messenger 종류를 검사하지 않으므로(Kakao 채널에 코드 오할당 시 telegram_chat_id 기록 가능), 운영자 pre-provision 시 Telegram 채널에만 코드 부여 가정 유지 필요.

### 결론
CRITICAL/HIGH 0, 모든 AC·Task 가 코드·테스트로 실증됨. 운영 전송 게이트는 AC 문구("DB 없이 검증 가능한 순수 정책으로 존재")대로 순수 정책으로 구현됐고, 현재 production 에 `plan`/`dispatch_all`/`CentralTelegramSender` 의 런타임 호출부가 없어(후속 Epic 5 배선) 우회 경로도 없음을 grep 으로 확인. MEDIUM 1건 정정 완료, LOW 3건은 의도적 결정으로 기록만. **Approve → done.**
