---
baseline_commit: 3d767cc
---

# Story 3.5: DeliveryLog와 idempotency — crash-after-send에도 중복 차단

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 3.4가 만든 채널별 fan-out 단위(`DispatchJob`)가 실제로 전송될 때, **5필드 dedup key**(`monitoring_target_id + messenger_channel_id + snapshot_collected_at + template_version + message_hash`)로 **idempotency**를 강제하고 그 결과를 **`DeliveryLog`**(계약 테이블 `delivery_logs` backing record)에 기록하되, **성공 전송 전에 dedup key 유니크 제약을 먼저 확보하는 insert-then-send**(또는 동등) 패턴으로 **재시도·전송 직후 크래시(crash-after-send)에도 같은 메시지가 두 번 발송되지 않게** 하고 싶다. 단, 이 모든 것은 **순수 additive·런타임 미배선**(2.5/3.1/3.2/3.3/3.4 토대 제약 계승)으로, `src/rider_crawl/`(특히 `app.py` 의 `send_only_on_change`/`_message_scope_key`/`_is_duplicate`/`_write_last_hash` run_once 호환 dedup)·3.1~3.4 기존 코드·`DeliveryRule`/`MessengerChannel`/`Message`/`DispatchJob` 값 객체는 **한 줄도 바꾸지 않고**(재사용만), 신규 도메인 레코드 **`DeliveryLog`**(11번째 도메인 모델) + 신규 enum **`DeliveryStatus`** + 신규 서비스 **`IdempotentDeliveryService`**(`build_dedup_key` = 5필드 → 안정적 dedup key, `deliver_once` = 주입된 reserve/sender seam으로 insert-then-send + DeliveryLog 생성)만 추가한다,
so that 동일 대상·Snapshot·채널·템플릿·메시지 조합의 성공 전송은 재시도돼도 다시 보내지지 않고(FR-10), 다른 고객·다른 대상·다른 채널의 전송은 **오차단되지 않으며**(AC3), 중복으로 막힌 전송은 `DeliveryLog` 에 `duplicate_blocked` 결과로 남아 관측 가능하고(NFR-15), 이 dedup/insert-then-send seam 위에 **3.6 채널별 실패 상태 분류·재시도**(error_code·`AUTH_REQUIRED`·backoff)·**3.7 Telegram 중앙 전송**·**Epic 5 영속(`delivery_logs` 테이블·`uq_delivery_logs_dedup_key` UNIQUE·async wiring·런타임 교체)·Epic 4 Kakao 실전송**이 additive로 빌드된다(P2-05, FR-10, ADD-5, NFR-1~4·15).

> **이 스토리의 성격 — "한 `DispatchJob`(3.4) → dedup key 확보 후 전송 → `DeliveryLog` 기록, 그것만."** 3.1이 `run_once` 를 세 서비스로 **구조 분리**(`DispatchService.dispatch` = 단일 전송, `skipped` 항상 False, dedup 미이관 — docstring 12·41행이 "`DeliveryLog`/idempotency seam은 Story 3.5가 채운다"고 **명시 위임**), 3.2가 수집을 `Snapshot`(+`collected_at`)으로, 3.3이 렌더를 `Message`(+`template_version`/`text_hash`)로, 3.4가 단일 Message를 채널별 **`DispatchJob`**(`target_id`·`channel_id`·`message_id`·`messenger`·`template_version`·`message_hash` 보존)으로 fan-out했다. 본 스토리는 그 `DispatchJob` 의 **실제 전송 경계에 dedup 유니크 제약(insert-then-send)을 끼우고** 결과를 `DeliveryLog` 로 남긴다. 본 스토리는 **채널별 실패 error_code 분류·재시도·`AUTH_REQUIRED`·backoff·circuit breaker=3.6, Telegram 중앙 sendMessage/webhook=3.7, dry-run 비교·승인 활성화=3.8, `delivery_logs`/`jobs` 테이블·SQLAlchemy ORM/Alembic·Pydantic·`uq_delivery_logs_dedup_key` DB UNIQUE·async wiring·런타임 교체·tenant 템플릿(`template_id`)·migration seed→DeliveryLog 실제 적재=Epic 5, Kakao 실제 PC 자동화 전송=Epic 4** 를 끌어오지 않는다. [Source: epics.md Epic 3(511-513)·Story 3.5(600-621)·Story 3.6~3.8(623-693), implementation-contract.md P2-05(51)·flow(18), data-api-contract.md(16·34·146-156), architecture.md(142-143·313-314·357·428), src/rider_server/services/dispatch_service.py(8-16·41), src/rider_server/services/dispatch_fanout_service.py(9-10)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 `domain/delivery_log.py`(`DeliveryLog` frozen dataclass) + `domain/states.py` 에 `DeliveryStatus` enum **additive 추가** + `domain/__init__.py` 재노출 갱신(11번째 모델 + enum) + 신규 `services/idempotency.py`(`IdempotentDeliveryService`·`build_dedup_key`·`deliver_once`) + `services/__init__.py` 재노출 additive + 신규 테스트 `tests/server/test_idempotency.py` + 기존 도메인 lock 테스트 `tests/server/test_domain_models.py` **additive 갱신**(11모델·DeliveryStatus 잠금 — 3.2/3.3이 9·10번째 모델로 lock 갱신한 선례와 동형). 아래는 **다른 스토리/에픽 소유 — 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `app.py`(`run_once`·`send_only_on_change`·`_is_duplicate`·`_write_last_hash`·`_message_scope_key` — 마지막 메시지 해시 dedup)·`message.py`·`messengers/`·`redaction.py` 등 어떤 파일도 수정하지 않는다. **import해서 재사용만** 한다(`redact` 만 사용). **이유 1(의존성 방향 — 절대 규칙):** `rider_server → rider_crawl` import만 허용, 역방향 금지(project-context.md 64, architecture.md 482). **이유 2(legacy dedup 소유권):** `app.py:43-65·98-118` 의 파일 기반 `last_message` dedup은 **run_once 호환 경로가 계속 소유**한다 — 본 스토리는 그 경로를 신규 `DeliveryLog`/dedup key로 갈아끼우지 **않는다**(신규 경로 정의만, 런타임 교체는 Epic 5). 두 dedup은 **공존**하며 `scope key 비축소` 원칙(3.4 AC3이 `DispatchJob` 의 `target_id`+`channel_id` 둘 다 보존으로 이미 만족)을 5필드 key가 그대로 이어받는다. [Source: project-context.md(64·92), src/rider_crawl/app.py(43-65·98-118), src/rider_server/services/dispatch_service.py(8-13)]
> - **`DispatchJob`/`FanoutOutcome`/`DispatchFanoutService`(3.4) 무변경.** 본 스토리는 `DispatchJob` 을 **import·소비**만 한다(dedup key 4필드 = `target_id`·`channel_id`·`template_version`·`message_hash` 를 그대로 읽음). `DispatchJob` 에 필드를 더하거나 `dispatch_all` 본문을 바꾸지 않는다. fan-out(채널별 격리 루프)은 3.4 소유 — 본 스토리는 **단일 job의 idempotent 전송 primitive(`deliver_once`)** 만 제공하고, 그 primitive를 `dispatch_all` 의 `send` 콜백에 조립하는 것은 Epic 5 wiring이다. [Source: src/rider_server/services/dispatch_fanout_service.py(48-65·127-152)]
> - **`DeliveryRule`(2.5)·`MessengerChannel`(2.5)·`Message`(3.3)·`Snapshot`(3.2) dataclass 무변경.** import·재사용만. `Snapshot.collected_at`(3.2, datetime)는 dedup key의 `snapshot_collected_at` 차원이라 **호출부가 `deliver_once` 에 인자로 주입**한다(`DispatchJob` 은 `collected_at` 을 직접 보유하지 않음 — `message_id`→`Message.snapshot_id`→`Snapshot.collected_at` 조인은 Epic 5 영속 레이어; 본 서비스는 조인하지 않고 주입받는다). [Source: src/rider_server/domain/snapshot.py(24), src/rider_server/domain/message.py(25), src/rider_server/services/dispatch_fanout_service.py(62)]
> - **채널별 실패 error_code 분류·재시도·`AUTH_REQUIRED`·`telegram_failure`/`kakao_failure` 카테고리·backoff·circuit breaker** → **3.6**(P2-06, FR-11·26). 본 스토리의 `DeliveryLog.error_code` 는 **항상 `None`**(3.5는 분류하지 않음); `deliver_once` 는 `send` 예외를 **분류하지 않고 호출부로 전파**한다(try/except로 삼키지 않음 — 채널 격리 contain은 3.4 `dispatch_all`, 실패 분류·재시도·release는 3.6). `DeliveryStatus` 에 실패 카테고리(`*_FAILURE`/`AUTH_REQUIRED`)를 추가하지 않는다(3.6 선점 금지 — 본 스토리는 dedup 결과 어휘 `SENT`/`DUPLICATE_BLOCKED` 만 정의). [Source: epics.md Story 3.6(623-643), architecture.md(323-328)]
> - **Telegram 중앙 sendMessage/webhook** → **3.7/Epic 5**. `deliver_once` 의 `send` 는 **주입된 콜백**일 뿐 — 중앙/per-Agent 경로 선택은 호출부(3.7/Epic 5) 책임. [Source: architecture.md(433-434), src/rider_server/services/dispatch_fanout_service.py(138-140)]
> - **`delivery_logs`/`jobs` 테이블·`uq_delivery_logs_dedup_key` DB UNIQUE·SQLAlchemy ORM/Alembic·Pydantic·async wiring·런타임 교체·migration seed 실제 적재(`MigrationSeed`→`DeliveryLog`)** → **Epic 5**. 본 스토리는 **순수 dataclass 값 객체 + 순수 동기 서비스 + 테스트만**, **런타임 미배선**이다(2.5/2.6/3.1~3.4 동일). dedup의 "유니크 제약"은 본 스토리에선 **주입된 `reserve` 콜백 seam**(in-memory fake로 테스트)으로 표현하고, 실제 DB UNIQUE 인덱스는 Epic 5가 같은 dedup key 위에 건다. [Source: architecture.md(173·357·428·493·524-526), data-api-contract.md(34), src/rider_server/migration/runner.py(86-97)]
>
> **순수·결정적·의존성 0(2.5/2.6/3.1/3.2/3.3/3.4 토대 제약 계승).** `DeliveryLog`·`DeliveryStatus`·`IdempotentDeliveryService` 는 FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬이다. **내부에서 `datetime.now()`/`uuid4()` 를 호출하지 않는다** — `DeliveryLog.id`(log id)·`sent_at`·`collected_at` 은 **호출부 주입**(`log_id_for` 콜백·`sent_at` 인자·`collected_at` 인자; 2.5 `Tenant.created_at`·3.2 `Snapshot.collected_at`·3.3 `Message.id`·3.4 `job_id_for` 주입 선례). [Source: project-context.md(35), src/rider_server/services/snapshot_normalizer.py(55-65), src/rider_server/services/dispatch_fanout_service.py(24-25·103)]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** dedup key는 `target_id`·`channel_id`(불투명 FK)·`collected_at`·`template_version`·`message_hash`(이미 sha256 hex) 로만 구성 — chat_id 숫자·room_name 원문·봇 토큰·비밀번호·OTP를 담지 않는다. `DeliveryLog` 어떤 필드에도 평문 secret을 두지 않는다(`error_code` 는 3.6의 enum 코드, 본 스토리는 `None`). 예외/로그 메시지는 redaction 통과(`deliver_once` 는 secret을 만들지 않지만, 본 스토리가 새로 만드는 어떤 로그/메시지도 `redact()` 를 통과해야 한다). 테스트 fixture·예외 메시지에 실제 봇 토큰/비밀번호/OTP/`chat_id` 숫자/전화/이메일 원문을 넣지 않는다(가짜 id·sha256 형태 hash만). [Source: project-context.md(81), architecture.md(330·343), src/rider_crawl/redaction.py(130)]

## Acceptance Criteria

**AC1 — 5필드 dedup key + 성공 전송 idempotency: 같은 key 재시도는 재전송 안 함 (P2-05, FR-10, ADD-5)**

1. **Given** 3.4가 만든 `DispatchJob`(`target_id`·`channel_id`·`message_id`·`template_version`·`message_hash` 보유)과 그 Snapshot의 `collected_at` 이 있을 때 **When** `IdempotentDeliveryService.build_dedup_key(target_id=..., channel_id=..., collected_at=..., template_version=..., message_hash=...)`(또는 `DispatchJob` + `collected_at` 입력)으로 dedup key를 만들면(P2-05, ADD-5, data-api-contract 146-156) **Then** dedup key는 **정확히 5차원**(`monitoring_target_id`(=`target_id`) + `messenger_channel_id`(=`channel_id`) + `snapshot_collected_at`(=`collected_at`) + `template_version` + `message_hash`)으로 구성되고, **동일 입력 → 동일 key(결정적·안정적)**, `collected_at` 은 안정적 직렬화(`.isoformat()`)로 정규화되며, 어느 한 차원이라도 다르면 key가 달라진다. [Source: data-api-contract.md(146-156), epics.md AC(608-611), src/rider_server/services/dispatch_fanout_service.py(60-65)]
2. **And** `IdempotentDeliveryService.deliver_once(job, *, collected_at, reserve, send, log_id_for, sent_at)` 로 전송하면, 처음 전송은 `reserve(dedup_key)` 로 **성공 전송 전에 유니크 제약을 먼저 확보**(insert-then-send)한 뒤 `send(job)` 을 호출하고 `DeliveryLog(status=SENT, dedup_key=..., sent_at=주입, error_code=None)` 를 반환한다. **같은 `DispatchJob`(=같은 dedup key)이 재시도돼도**, 이미 성공 확보된 key면 `reserve` 가 False를 반환하고 `deliver_once` 는 **`send` 를 호출하지 않은 채** `DeliveryLog(status=DUPLICATE_BLOCKED, sent_at=None)` 를 반환한다(동일 idempotency key의 성공 전송은 재전송 안 함). [Source: epics.md AC(608-611), architecture.md(313-314·357), data-api-contract.md(34)]

**AC2 — crash-after-send 안전: insert-then-send + at-least-once (ADD-5)**

3. **Given** crash-after-send(전송 직후 상태 기록 전 크래시)를 가정해야 할 때 **When** 전송을 처리하면 **Then** **성공 전송 전에 dedup key 유니크 제약을 먼저 확보하는 insert-then-send(또는 동등) 패턴**을 사용한다 — `reserve(dedup_key)` 가 `send` 보다 **먼저** 일어나고, reserve 성공으로 key가 확보된 뒤에만 `send` 가 호출된다. 따라서 전송 직후 크래시로 SENT 기록을 못 남겨도, **재시도 시 같은 key의 `reserve` 가 충돌(False)** 하여 `DUPLICATE_BLOCKED` 로 처리되고 **재전송되지 않는다**. [Source: epics.md AC(613-616), architecture.md(142-143·313-314)]
4. **And** **at-least-once 의미론을 가정하고 exactly-once를 가정하지 않는다** — 본 서비스는 messenger의 단일 전달을 보장하지 않으며(중앙 Telegram/Kakao Agent의 재시도는 3.6/3.7/Epic 4 소유), 제공하는 보장은 "**성공 확보된 dedup key는 재전송하지 않는다**"(중복 차단)와 "유니크 제약은 **성공 전송 전에** 확보된다"이다. `reserve` 충돌 = "이미 (전송됐거나 전송 중인) key" 로 fail-closed 처리(오발송보다 미발송, architecture 43·329). `send` 가 예외를 던지면 `deliver_once` 는 **분류·재시도·release 없이 호출부로 전파**한다(실패 운영 정책 = 3.6). [Source: architecture.md(43·142-143·329·346), epics.md Story 3.6(623-643)]

**AC3 — 오차단 방지 + duplicate_blocked 기록 (FR-10, NFR-15)**

5. **Given** 중복 방지 키가 다른 전송을 오차단하면 안 될 때 **When** **다른 고객·다른 대상(`target_id`)·다른 채널(`channel_id`)·다른 Snapshot(`collected_at`)·다른 템플릿(`template_version`)·다른 메시지(`message_hash`)** 중 **하나라도 다른** 전송이 들어오면 **Then** 그 전송들은 **서로 다른 dedup key**를 가져 `reserve` 가 각각 성공하고 **잘못 차단되지 않는다**(독립 전송). 특히 같은 Message·같은 `target_id` 라도 **`channel_id` 가 다르면 dedup key가 다르다**(3.4 AC3 scope 비축소 — 한 채널의 중복 판단이 다른 채널을 막지 않음). [Source: epics.md AC(618-620), project-context.md(92), data-api-contract.md(146-156)]
6. **And** **중복으로 막힌 전송은 `DeliveryLog` 에 별도 결과(`DeliveryStatus.DUPLICATE_BLOCKED`)로 기록**된다(관측 가능, NFR-15) — `DUPLICATE_BLOCKED` `DeliveryLog` 는 `status=DUPLICATE_BLOCKED`·`dedup_key`(추적용 동일 key)·`sent_at=None`·`error_code=None` 을 가지며, **유니크 제약은 성공(`SENT`) 레코드에만 적용**된다(architecture 173: "delivery_logs 성공 레코드에 DB 유니크 제약"). 즉 `DUPLICATE_BLOCKED` 레코드는 reserve를 다시 시도하지 않는 audit 기록이라 유니크 제약과 충돌하지 않는다. [Source: epics.md AC(621), architecture.md(173·357·359), data-api-contract.md(34)]

**AC4 — DeliveryLog 도메인 모델·DeliveryStatus enum 계약 일치 (ADD-7·ADD-9, FR-30)**

7. **And** `DeliveryLog` 는 `data-api-contract` 의 `delivery_logs` 테이블 필수 필드와 일치하는 frozen dataclass다: **`id`, `message_id`(→Message FK), `channel_id`(→MessengerChannel FK), `status`(`DeliveryStatus`), `dedup_key`(str), `error_code`(`str | None`, 본 스토리 항상 None — 3.6 소유), `sent_at`(`datetime | None`)**. `DeliveryStatus` enum은 `(str, Enum)` + 멤버명==대문자값 정본(2.5/3.2 enum 패턴 계승)으로 **`SENT`·`DUPLICATE_BLOCKED` 2멤버**만 정의한다(실패 카테고리는 3.6/Epic 5 — 2.5 `SubscriptionStatus`·3.2 `SnapshotQualityState` 가 "값 정의 vs 로직 경계"로 미래 어휘를 최소만 둔 선례와 동형). `DeliveryLog` 는 **11번째 도메인 모델**로 `domain/__init__.py` `__all__` 와 `tests/server/test_domain_models.py` lock에 additive로 등재된다. [Source: data-api-contract.md(16·34), src/rider_server/domain/states.py(74-81·123-136), src/rider_server/domain/__init__.py(34-60), tests/server/test_domain_models.py(251-297)]

**AC5 — 순수 additive·무회귀·단방향·비노출 (FR-2, NFR-20, 토대 제약)**

8. **And** `src/rider_crawl/`·`pyproject.toml`·3.1 `dispatch_service.py`·3.2 `snapshot_normalizer.py`·3.3 `message_render_service.py`·3.4 `dispatch_fanout_service.py`·기존 도메인 모델(`delivery_rule.py`/`messenger_channel.py`/`message.py`/`snapshot.py`) **0줄 변경**(`git diff -w --stat`)으로 기존 회귀 그물(`tests/server/test_run_once_split.py`·`test_dispatch_fanout.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`test_domain_models.py`(lock additive 갱신분 제외)·`tests/test_app.py`)이 **전부 그대로 통과**하고, 본 스토리는 신규 idempotency/DeliveryLog 케이스만큼만 테스트 수가 증가한다(순수 additive). 의존성은 **단방향**(`rider_server → rider_crawl` 만, 역방향 0 — ast 가드 `test_rider_crawl_never_imports_rider_server` 통과)이고, 신규 코드·테스트에 평문 secret 0건이다. [Source: project-context.md(58·64·82), 3-4 스토리(52), epic-2-retro-2026-06-13.md(114-115)]

## Tasks / Subtasks

- [x] **Task 1 — `DeliveryStatus` enum 추가: `domain/states.py` (AC: 4, 6)** — `SnapshotQualityState`(3.2) 패턴과 동형 `(str, Enum)` + 멤버명==대문자값:
  - [x] **`class DeliveryStatus(str, Enum)`** 멤버 **정확히 2개**: `SENT = "SENT"`, `DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED"`. docstring으로 "값 정의 vs 로직 경계"를 명시(`SubscriptionStatus`/`SnapshotQualityState` 선례) — **실패 카테고리(`telegram_failure`/`kakao_failure`/`AUTH_REQUIRED`)·재시도/보류 상태는 3.6/Epic 5 소유라 본 스토리는 dedup 결과 어휘만 정의**. `DUPLICATE_BLOCKED` 는 architecture 325의 운영 카테고리·359의 `DUPLICATE_BLOCKED` 와 정합(대문자 정본). [Source: src/rider_server/domain/states.py(123-136), architecture.md(324-325·359), epics.md AC(621)]
- [x] **Task 2 — `DeliveryLog` 도메인 모델 추가: `domain/delivery_log.py` (AC: 4, 6)** — 순수 frozen dataclass(2.5/3.2/3.3 도메인 모델 패턴, `rider_crawl` import 0):
  - [x] **`@dataclass(frozen=True) class DeliveryLog`** 필드(dataclass 순서 — 필수 먼저, default는 끝): `id: str`(주입), `message_id: str`(→Message FK), `channel_id: str`(→MessengerChannel FK), `status: DeliveryStatus`, `dedup_key: str`, `error_code: str | None = None`(3.6 소유 — 본 스토리 항상 None), `sent_at: datetime | None = None`(SENT만 값, DUPLICATE_BLOCKED은 None). 모듈 docstring으로 `delivery_logs` 계약 매핑·위임처(error_code 분류=3.6, DB UNIQUE/ORM/async=Epic 5)·`rider_crawl` 미import(순수 레코드, 변환은 services)·dedup key 5차원(target_id·channel_id 는 본 레코드에 직접 없고 `dedup_key` 안에 합성됨; message_id·channel_id 는 계약 컬럼) 의도를 2~4줄로 남긴다(`message.py`/`snapshot.py` docstring 형식 계승). [Source: data-api-contract.md(16·34), src/rider_server/domain/message.py(1-30), src/rider_server/domain/snapshot.py(1-35)]
- [x] **Task 3 — 도메인 재노출 갱신: `domain/__init__.py` + lock 테스트 `tests/server/test_domain_models.py` (AC: 4)** — additive(3.2/3.3 9·10번째 모델 등재 선례와 동형):
  - [x] `domain/__init__.py`: `from .delivery_log import DeliveryLog` import 추가, `from .states import (... DeliveryStatus ...)` 추가, `__all__` 에 `"DeliveryLog"`(11번째 모델, `"Message"` 뒤) + `"DeliveryStatus"`(상태머신 enum 그룹) additive 추가. 기존 10모델·enum 보존(무삭제). docstring에 "Story 3.5 — DeliveryLog 전송 결과 레코드(11번째) + DeliveryStatus" 1줄 보강. [Source: src/rider_server/domain/__init__.py(34-60)]
  - [x] `tests/server/test_domain_models.py`: `test_package_all_reexports_eight_models_and_all_enums` 의 `expected` set과 `model_names` set에 `"DeliveryLog"` additive, `expected` 에 `"DeliveryStatus"` additive(3.2/3.3이 `Snapshot`/`Message`·`SnapshotQualityState` 를 같은 자리에 추가한 패턴 그대로). **이 lock 갱신 외 기존 단언은 무변경**. 가능하면 `DeliveryLog`/`DeliveryStatus` 의 필드·frozen·기본값(`error_code=None`/`sent_at=None`)을 잠그는 단언 1~2개를 같은 파일에 additive로 추가(계약 정본 고정). [Source: tests/server/test_domain_models.py(251-297)]
- [x] **Task 4 — `build_dedup_key` + `deliver_once`: 신규 `services/idempotency.py` (AC: 1, 2, 3, 6)** — 순수·결정적 staticmethod, architecture 428이 명시한 정본 파일명(`services/idempotency.py # dedup key + insert-then-send`):
  - [x] **`class IdempotentDeliveryService`** (services 클래스+staticmethod 패턴 — `DispatchService`/`SnapshotNormalizer`/`MessageRenderService`/`DispatchFanoutService` 와 동거). import는 단방향만: `from rider_server.domain import DeliveryLog, DeliveryStatus`, `from rider_server.services.dispatch_fanout_service import DispatchJob`(같은 레이어 소비), `from rider_crawl.redaction import redact`(필요 시), 표준 `hashlib`/`datetime`/`typing`. 역방향 import 0. [Source: architecture.md(426-428), src/rider_server/services/snapshot_normalizer.py(46-65)]
  - [x] **`@staticmethod def build_dedup_key(*, target_id: str, channel_id: str, collected_at: datetime, template_version: str, message_hash: str) -> str`**: 5차원을 **안정적·결정적**으로 합성한다. 권장: `collected_at.isoformat()` 로 시각 정규화 후, 식별자에 미포함인 구분자(예: `"|"` — opaque id/hex/iso8601/template_version 에 없음)로 join한 canonical 문자열을 dedup key로 둔다(`app.py:_message_scope_key` 의 `"\n".join` 스타일 계승). **5필드 전부 포함** 필수(축소 금지). DB 컬럼 길이를 위해 sha256-wrapping할지는 Epic 5 영속 레이어 결정으로 두되, **논리 key는 5필드 전량을 결정한다**(같은 입력→같은 key, 한 필드라도 다르면 다른 key). 내부 `now()` 미호출(시각은 `collected_at` 인자). [Source: data-api-contract.md(146-156), src/rider_crawl/app.py(98-118), src/rider_server/services/dispatch_service.py(63)]
  - [x] **`@staticmethod def deliver_once(job: DispatchJob, *, collected_at: datetime, reserve: Callable[[str], bool], send: Callable[[DispatchJob], None], log_id_for: Callable[[DispatchJob], str], sent_at: datetime) -> DeliveryLog`**: (1) `key = build_dedup_key(target_id=job.target_id, channel_id=job.channel_id, collected_at=collected_at, template_version=job.template_version, message_hash=job.message_hash)`, (2) **insert-then-send**: `if not reserve(key): return DeliveryLog(id=log_id_for(job), message_id=job.message_id, channel_id=job.channel_id, status=DeliveryStatus.DUPLICATE_BLOCKED, dedup_key=key, error_code=None, sent_at=None)` — reserve 충돌이면 **`send` 미호출**(중복 차단, AC1.2/AC3), (3) reserve 성공이면 `send(job)` 호출(유니크 제약 확보 **후** 전송 — crash-after-send 안전, AC2), (4) `return DeliveryLog(..., status=DeliveryStatus.SENT, dedup_key=key, error_code=None, sent_at=sent_at)`. **`send` 예외는 try/except로 삼키지 않고 전파**(분류·재시도·release=3.6 — 본 스토리는 dedup 가드만). 내부 `uuid4()`/`now()` 미호출(`id`/`sent_at`/`collected_at` 주입). [Source: epics.md AC(608-621), architecture.md(313-314·329·346), src/rider_server/services/dispatch_fanout_service.py(127-152)]
  - [x] **`reserve` 콜백 계약(docstring):** `reserve(dedup_key) -> bool` 은 **성공 전송 전 유니크 제약 확보(INSERT) seam** — 새로 확보=`True`(전송 진행), 이미 확보됨(=SENT 레코드 존재)=`False`(중복 차단). 실제 DB `uq_delivery_logs_dedup_key` UNIQUE 인덱스는 Epic 5가 같은 key 위에 건다(본 스토리는 in-memory fake로 테스트). 유니크 제약은 **성공 레코드에만** 적용됨을 명시(architecture 173). [Source: architecture.md(173·357·428·493), data-api-contract.md(34)]
- [x] **Task 5 — 서비스 재노출 갱신: `services/__init__.py` (AC: 1)** — additive only:
  - [x] `from .idempotency import IdempotentDeliveryService` import 추가, `__all__` 에 `"IdempotentDeliveryService"` additive(기존 3.1~3.4 심볼 무삭제). docstring에 "Story 3.5(P2-05, FR-10, ADD-5)가 `IdempotentDeliveryService`(`build_dedup_key`=5필드 dedup key, `deliver_once`=insert-then-send + `DeliveryLog` 생성)를 additive로 추가 — architecture 428 `idempotency.py` 정본" 1단락 보강. `DeliveryLog`/`DeliveryStatus` 는 domain 소속이라 services에서 재노출하지 않는다(domain에서 노출). [Source: src/rider_server/services/__init__.py(1-53)]
- [x] **Task 6 — 테스트 추가: 신규 `tests/server/test_idempotency.py` (AC: 1~8)** — 외부 호출 없음(fake/in-memory), 가짜 값만. 평면 `tests/server/`(`__init__.py` 미추가 — 기존 컨벤션). `test_dispatch_fanout.py`/`test_domain_models.py` 의 fixture 패턴 재사용(가짜 `DispatchJob`·`Message`·sha256 형태 hash):
  - [x] **(AC1 — dedup key 5차원·결정성):** `build_dedup_key(...)` 가 5필드 모두를 반영(각 필드를 하나씩 바꾸면 key가 달라짐 — 5개 distinct 단언), 같은 입력 두 번 호출 시 동일 key(결정적), `collected_at` 이 안정적으로 직렬화됨(같은 datetime → 같은 key). [Source: data-api-contract.md(146-156)]
  - [x] **(AC1.2 — 성공 후 재시도 차단):** in-memory reserve(예: `seen: set[str]`; `reserve = lambda k: k not in seen and (seen.add(k) or True)` 동등)로 `deliver_once` 를 같은 `DispatchJob` 으로 **두 번** 호출 → 1회차 `status=SENT`(+`send` 1회 호출·`sent_at` 주입값), 2회차 `status=DUPLICATE_BLOCKED`(+`send` **미호출**·`sent_at=None`). `send` 호출 횟수를 기록하는 fake로 "정확히 1회 전송" 단언. [Source: epics.md AC(608-611)]
  - [x] **(AC2 — insert-then-send 순서·crash-after-send):** `reserve` 가 `send` **보다 먼저** 호출됨을 순서 기록(call-order list)으로 단언(유니크 제약 선확보). crash-after-send 시뮬: 1회차에서 reserve는 성공시키되 `send` 직후 "기록 유실"을 모사(SENT DeliveryLog를 버림)하고 **같은 key로 2회차** 호출 → reserve 충돌로 `DUPLICATE_BLOCKED`·`send` 미호출(재전송 안 됨). [Source: epics.md AC(613-616), architecture.md(313-314)]
  - [x] **(AC2 — send 예외 전파·미분류):** `send` 가 예외를 던지면 `deliver_once` 가 그 예외를 **전파**(`pytest.raises`)하고, `DeliveryLog(error_code=...)` 로 삼키거나 분류하지 **않음**(3.6 경계). reserve는 이미 호출된 상태(insert-then-send) 확인. [Source: architecture.md(329·346), epics.md Story 3.6(623-643)]
  - [x] **(AC3 — 오차단 방지):** `target_id`/`channel_id`/`collected_at`/`template_version`/`message_hash` 중 **하나만 다른** 두 job → dedup key가 달라 둘 다 reserve 성공·둘 다 `SENT`(오차단 0). 특히 **같은 Message·같은 target, channel만 다른** 두 job(3.4 fan-out 산출)이 서로 차단하지 않음(scope 비축소). [Source: project-context.md(92), epics.md AC(618-620)]
  - [x] **(AC3/AC6 — duplicate_blocked 기록):** 차단된 전송이 `DeliveryLog(status=DUPLICATE_BLOCKED, dedup_key=<동일 key>, sent_at=None, error_code=None)` 로 반환됨(관측 가능), 그리고 `DUPLICATE_BLOCKED` 는 reserve를 다시 시도하지 않는 audit 기록임(유니크 제약은 SENT에만). [Source: epics.md AC(621), architecture.md(173)]
  - [x] **(AC4 — 계약·frozen·기본값):** `DeliveryLog`/`DeliveryStatus` frozen(`pytest.raises(FrozenInstanceError)`), `DeliveryStatus` 멤버가 **정확히 `{SENT, DUPLICATE_BLOCKED}`**(3.6 실패 카테고리 미선점 단언), `DeliveryStatus.SENT == "SENT"`(str enum), `DeliveryLog(error_code 기본 None·sent_at 기본 None)`, 필드 집합이 계약(`id·message_id·channel_id·status·dedup_key·error_code·sent_at`)과 일치. [Source: data-api-contract.md(34), src/rider_server/domain/states.py(74-80)]
  - [x] **(재노출):** `from rider_server.domain import DeliveryLog, DeliveryStatus` 와 `from rider_server.services import IdempotentDeliveryService` 동작, 각 `__all__` 포함. [Source: src/rider_server/domain/__init__.py, src/rider_server/services/__init__.py]
  - [x] **(비노출):** dedup key·`DeliveryLog` 어떤 필드에도 평문 secret/식별자 원문이 없음(가짜 `chat_id` 숫자/봇토큰을 fixture에 넣지 않음). fixture는 가짜 값만(`"mt-1"`·`"ch-tg"`·`"msg-1"`·`"a"*64` 류 hash·`Messenger.TELEGRAM`). [Source: project-context.md(81), 3-4 스토리(79)]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~8)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 **866** — HEAD `3d767cc`(3.4 종료) 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `test_run_once_split.py`·`test_dispatch_fanout.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`test_domain_models.py`·`tests/test_app.py`), `test_domain_models.py` lock 갱신(11모델·DeliveryStatus)은 **계약 반영 변경**이지 회귀가 아님을 확인. 신규 케이스만큼만 증가가 정상(순수 additive). [Source: 3-4 스토리(81·203), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat` 에 **신규 `domain/delivery_log.py`·`services/idempotency.py`·`tests/server/test_idempotency.py` + `domain/states.py`(DeliveryStatus additive)·`domain/__init__.py`(재노출)·`services/__init__.py`(재노출)·`test_domain_models.py`(lock additive)만** 보이고 **`src/rider_crawl/`·`pyproject.toml`·3.1~3.4 services 본문·기존 도메인 모델 변경 0줄**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건, `src/rider_crawl/` 에 `rider_server` import가 **새로 생기지 않았음**(ast 기반 권장 — 단순 문자열 grep은 docstring 오탐) 확인. [Source: project-context.md(64·81), 3-4 스토리(83)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: epic-2-retro-2026-06-13.md(115), 3-4 스토리(84·221)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `domain/delivery_log.py`·`services/idempotency.py`·`tests/server/test_idempotency.py` + additive 수정 `domain/states.py`(`DeliveryStatus` enum)·`domain/__init__.py`(재노출)·`services/__init__.py`(재노출)·`tests/server/test_domain_models.py`(11모델·DeliveryStatus lock 갱신). **`src/rider_crawl/`·`pyproject.toml` 무변경, 3.1 `dispatch_service.py`·3.2 `snapshot_normalizer.py`·3.3 `message_render_service.py`·3.4 `dispatch_fanout_service.py` 무변경, 기존 도메인 모델(`delivery_rule.py`/`messenger_channel.py`/`message.py`/`snapshot.py`/`tenant.py`...) 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(`app.py` 의 `send_only_on_change`/`_is_duplicate`/`_write_last_hash`/`_message_scope_key` 파일 기반 dedup — 보존·재사용만, run_once 호환 경로가 계속 소유), `DispatchJob`/`FanoutOutcome`/`DispatchFanoutService`(3.4 — import·소비만), `DeliveryRule`/`MessengerChannel`/`Message`/`Snapshot`(2.5/3.2/3.3 — import만), `DispatchService.dispatch`(3.1 단일 전송 parity), 채널별 실패 분류·재시도·`AUTH_REQUIRED`·backoff·circuit breaker(3.6), Telegram 중앙 webhook/sendMessage(3.7), dry-run·승인(3.8), `delivery_logs`/`jobs` 테이블·`uq_delivery_logs_dedup_key` DB UNIQUE·ORM/Alembic/Pydantic/async·런타임 교체·tenant 템플릿(`template_id`)·migration seed 실제 적재(Epic 5), Kakao 실제 PC 자동화 전송(Epic 4). [Source: epics.md Story 3.6~3.8(623-693), architecture.md(417-444), implementation-contract.md(51-52)]

### 위치 결정 — 왜 `DeliveryLog` 는 domain/, `IdempotentDeliveryService` 는 services/ 인가 (반드시 읽을 것)

- **`DeliveryLog` 는 domain/ (3.2 `Snapshot`·3.3 `Message` 선례 — `DispatchJob` 과 정반대).** 3.4가 `DispatchJob` 을 services에 둔 이유는 그것이 **독립 계약 테이블이 아니라** generic `jobs` 로 매핑되는 파이프라인 단위였기 때문이다. `DeliveryLog` 는 정반대다 — data-api-contract **13 핵심 도메인 모델 중 하나(16행)**이고 **자체 계약 테이블 `delivery_logs`(34행)**를 가진다. `Snapshot`(`snapshots`)·`Message`(`messages`)가 도메인 모델로 domain/에 들어가 `domain.__all__` 9·10번째가 됐듯, `DeliveryLog` 는 **11번째 도메인 모델**로 domain/에 둔다. 따라서 본 스토리는 3.4와 달리 **`domain/__init__.py`·`test_domain_models.py` lock을 갱신**한다(3.2/3.3 패턴). [Source: data-api-contract.md(16·34), src/rider_server/domain/snapshot.py(19-34), src/rider_server/domain/message.py(22-29), src/rider_server/services/dispatch_fanout_service.py(19-21·95), 3-4 스토리(95-96)]
- **`IdempotentDeliveryService`(dedup key 합성 + insert-then-send 정책)는 services/.** architecture 428이 **`services/idempotency.py # dedup key + insert-then-send`** 를 정본 위치로 못 박았다. 도메인(`domain/`)은 순수 레코드, 변환·정책(dedup key 합성, reserve→send 오케스트레이션)은 services(`SnapshotNormalizer`·`MessageRenderService` 와 동형). [Source: architecture.md(425-428·487-489), src/rider_server/services/snapshot_normalizer.py(46-65)]

### dedup key 5차원 ↔ 입력 출처 (AC1 — 정밀 계약)

| dedup 차원 | 입력 출처 | 근거 |
|---|---|---|
| `monitoring_target_id` | `DispatchJob.target_id` | 데이터 흐름의 "플랫폼·URL·센터" 식별(3.4가 보존). |
| `messenger_channel_id` | `DispatchJob.channel_id` | **전송 대상(채널) 차원** — scope 비축소 핵심(3.4 AC3). 같은 Message·target라도 채널이 다르면 key가 다르다. |
| `snapshot_collected_at` | **호출부 주입** `collected_at`(`Snapshot.collected_at`) | `DispatchJob` 은 `collected_at` 미보유 — `message_id`→`Message.snapshot_id`→`Snapshot.collected_at` 조인은 Epic 5 영속이 하고, 본 서비스는 **인자로 주입**받는다(순수·결정성). |
| `template_version` | `DispatchJob.template_version` | 메시지 포맷 버전(3.3). 같은 snapshot·다른 템플릿은 다른 전송. |
| `message_hash` | `DispatchJob.message_hash`(=`Message.text_hash`=sha256(text)) | 메시지 내용 변경 감지 차원(3.1/3.3 동일 계산). |

- **dedup key(data-api-contract 146-156) = `monitoring_target_id + messenger_channel_id + snapshot_collected_at + template_version + message_hash`.** 3.4가 5차원 중 4개를 `DispatchJob` 에 보존했고, `collected_at` 만 주입으로 채운다. **5필드 전량 포함·축소 금지**(project-context 92: scope key 축소 시 다른 탭/계정 중복 판단이 섞임). `collected_at.isoformat()` 로 시각 정규화. [Source: data-api-contract.md(146-156), src/rider_server/services/dispatch_fanout_service.py(60-65), src/rider_server/domain/snapshot.py(24)]
- **legacy(run_once) dedup과의 관계:** `app.py:_message_scope_key`(98-118)는 마지막 메시지 해시를 `messenger+platform+url+center+(telegram chat/thread | kakao room)` 에 묶는다. ID 모델에서 그 scope는 `target_id`(platform·url·center 응축) + `channel_id`(messenger+chat/room) 로 매핑되고, 거기에 `collected_at`·`template_version` 차원이 더해진 게 5필드 key다. **두 dedup은 공존**한다 — legacy는 파일(`last_message.<scope>.sha256`) 기반으로 run_once 호환 경로가 계속 쓰고, 신규는 `DeliveryLog.dedup_key` 기반(런타임 교체는 Epic 5). 본 스토리는 legacy를 갈아끼우지 않는다. [Source: src/rider_crawl/app.py(54-65·98-118), project-context.md(92·95)]
- **migration seed와의 관계:** 2.7 `MigrationSeed`(`monitoring_target_id`·`message_hash`·`scope_hash`)가 old `last_message` hash에서 **부분 seed**(5차원 중 2개)를 만들어 뒀다(migration-contract 102). 나머지 3차원(`channel_id`·`collected_at`·`template_version`)은 Epic 3 도입분이다. 본 스토리는 5필드 key **정의**를 완성하지만, seed→`DeliveryLog` 실제 적재는 Epic 5 영속이다. [Source: src/rider_server/migration/runner.py(86-97), implementation-contract.md(102)]

### insert-then-send + crash-after-send (AC2 — 핵심 의미론, 놓치기 쉬움)

- **순서가 전부다: `reserve(key)` → (성공이면) `send(job)` → SENT 기록.** "성공 전송 전에 dedup key 유니크 제약을 먼저 확보"(architecture 313-314) = reserve가 send보다 **반드시 먼저**. 그래야 두 동시/재시도 전송 중 하나만 reserve를 이겨 한 번만 send한다(나머지는 충돌→`DUPLICATE_BLOCKED`).
- **crash-after-send 안전:** reserve로 key가 이미 확보된 뒤 send가 일어나므로, 전송 직후 SENT 기록을 못 남기고 크래시해도 **재시도 시 같은 key의 reserve가 충돌**→`DUPLICATE_BLOCKED`→**재전송 없음**. 비용: reserve 성공 후 send **전에** 크래시하면 그 메시지는 영영 미발송될 수 있다 — 그러나 spec은 **fail-closed(오발송보다 미발송 선택)·"exactly-once 가정하지 않음"**(architecture 43·143, epics 616)을 명시 우선하므로 수용된다.
- **at-least-once의 의미:** 본 서비스가 보장하는 건 "성공 확보된 dedup key는 재전송 안 함"(중복 차단)이다. messenger 자체의 단일 전달 보장(중앙 Telegram 재시도·Kakao Agent 큐)은 3.6/3.7/Epic 4 소유다. job 레이어의 at-least-once 재시도(3.6)는 이 idempotency 위에서 **안전**해진다. [Source: architecture.md(43·142-143·313-314·346), epics.md AC(613-616)]
- **유니크 제약은 SENT 레코드에만:** architecture 173/357 — `delivery_logs` **성공 레코드**에 DB UNIQUE. `DUPLICATE_BLOCKED` `DeliveryLog` 는 reserve를 다시 시도하지 않는 **audit 기록**이라 같은 `dedup_key` 를 가져도 유니크 제약과 충돌하지 않는다(Epic 5 영속 설계 시 partial unique index 또는 status 조건). 본 스토리의 in-memory fake reserve는 "성공 key 집합"만 모델링하면 충분하다. [Source: architecture.md(173·357·359), data-api-contract.md(34)]

### 3.6/3.7/Epic 5와의 경계 — 본 스토리가 하지 않는 것

- **3.6(실패 운영 정책 — 안 함):** `DeliveryLog.error_code` 는 본 스토리에서 **항상 None**. `send` 예외 발생 시 `deliver_once` 는 try/except로 **삼키지 않고 전파**한다 — error_code 분류(`telegram_failure`/`kakao_failure`/`auth_required`), 재시도 가능 vs 사람 개입(`AUTH_REQUIRED`), backoff·circuit breaker, reserve **release**(미발송 key 회수)는 모두 3.6/Epic 5. `DeliveryStatus` 에 실패 멤버를 추가하지 않는다(2.5 `SubscriptionStatus`·3.2 `SnapshotQualityState` 가 "값 정의 vs 게이트 로직" 경계를 둔 것과 동형 — 본 스토리는 `SENT`/`DUPLICATE_BLOCKED` 만). [Source: epics.md Story 3.6(623-643), architecture.md(323-328·361-366), src/rider_server/domain/states.py(34-42·123-136)]
- **3.4(채널 격리 — 안 함):** 여러 `DispatchJob` 의 채널 격리 루프(`dispatch_all`)는 3.4 소유. 본 스토리는 **단일 job idempotent primitive(`deliver_once`)** 만 제공한다. `deliver_once` 를 `dispatch_all` 의 `send` 콜백에 조립(채널마다 dedup 가드 통과)하는 것은 **Epic 5 wiring**이다 — 본 스토리는 `deliver_all` 같은 배치 메서드를 추가해 3.4를 재구현하지 않는다(스코프 규율). [Source: src/rider_server/services/dispatch_fanout_service.py(127-152)]
- **Epic 5(영속·배선 — 안 함):** `reserve`/`send`/`log_id_for`/`sent_at`/`collected_at` 은 전부 **주입 seam** — 실제 DB UNIQUE 인덱스·SQLAlchemy ORM·async session·런타임 sender 배선·migration seed 적재는 Epic 5. 본 스토리는 "정의만, 런타임 미배선"(2.5/2.6/3.1~3.4 동일). [Source: architecture.md(422-428·493·524-526), 3-4 스토리(101)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml`·3.1~3.4 services 본문·기존 도메인 모델 무변경** — `git diff -w` = 신규 `domain/delivery_log.py`·`services/idempotency.py`·`test_idempotency.py` + additive `domain/states.py`·`domain/__init__.py`·`services/__init__.py`·`test_domain_models.py`(lock)만. (b) **의존성 단방향** — `rider_server → rider_crawl` 만, 역방향 0(ast 가드 통과). (c) **`DispatchJob`(3.4)·`DeliveryRule`/`MessengerChannel`/`Message`/`Snapshot` 무변경** — import·소비만. (d) **순수·결정적** — `build_dedup_key`/`deliver_once` 내부 `datetime.now()`/`uuid4()` 금지(`id`/`sent_at`/`collected_at` 주입). (e) **frozen 불변** — `DeliveryLog` 은 `@dataclass(frozen=True)`, `DeliveryStatus` 는 `(str, Enum)`. (f) **insert-then-send 순서** — reserve가 send보다 먼저. (g) **5필드 dedup key·축소 금지** — 한 차원이라도 다르면 다른 key(오차단 방지·scope 비축소). (h) **유니크 제약은 SENT에만·duplicate_blocked는 audit 기록** — 차단 전송도 `DeliveryLog` 로 관측. (i) **error_code=None·실패 미분류·send 예외 전파** — 3.6 경계. (j) **비노출** — dedup key·`DeliveryLog` 에 평문 secret/식별자 원문 0. [Source: project-context.md(35·64·81·82·92), architecture.md(173·313-314·329), data-api-contract.md(34·146-156)]

### 이전 스토리 인텔리전스 (Epic 2 → 3.1 → 3.2 → 3.3 → 3.4 → 3.5 이월 교훈)

- **3.1이 본 스토리에 남긴 명시 위임:** `dispatch_service.py` docstring(12·41행)이 "`DeliveryLog`/idempotency seam은 Story 3.5가 채운다 … `skipped` 항상 False(dedup 미이관)" 라고 못 박았다. 본 스토리는 정확히 그 seam을 채운다 — 단, `DispatchService.dispatch`(단일 전송 parity가 run_once와 잠김) **본문은 무변경**이고, idempotency는 **별도 서비스**로 additive하게 붙인다(3.4가 fan-out을 `dispatch` 에 욱여넣지 않고 별도 서비스로 둔 것과 동형). [Source: src/rider_server/services/dispatch_service.py(8-16·41·65)]
- **3.4가 깐 입력 계약:** `DispatchJob`(`target_id`·`channel_id`·`message_id`·`messenger`·`template_version`·`message_hash`)이 dedup 5차원 중 4개를 이미 보존 — 본 스토리는 `collected_at` 만 주입으로 더해 5필드를 완성한다. 3.4 docstring(9-10행)이 "DeliveryLog/idempotency dedup key … insert-then-send = Story 3.5" 로 본 스토리를 명시 위임. [Source: src/rider_server/services/dispatch_fanout_service.py(9-10·48-65)]
- **3.2/3.3이 깐 도메인 모델 등재 패턴 계승:** 계약 테이블 backing record는 domain/에 frozen dataclass로 두고 `domain.__all__` + `test_domain_models.py` lock을 **additive 갱신**(Snapshot 9번째·Message 10번째). 본 스토리 `DeliveryLog` 는 11번째 — 같은 절차. enum은 `states.py` 에 추가(3.2 `SnapshotQualityState` 선례). [Source: src/rider_server/domain/snapshot.py(19-34), src/rider_server/domain/message.py(22-29), tests/server/test_domain_models.py(251-297)]
- **무회귀 비결 = "새 필드가 아니라 새 뷰/단위"**(epic-2-retro 64-67·149): 3.2 Snapshot·3.3 Message·3.4 DispatchJob·3.5 DeliveryLog 모두 **기존 코드를 갈아엎지 않고 옆에 레코드/서비스를 추가**(재사용·wrapping). 가장 비침습적. [Source: epic-2-retro-2026-06-13.md(64-69·149), 3-4 스토리(137)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2/3.1~3.4 모두 stale 수치로 MEDIUM 재발(3.4 856/+10 → 866/+20 정정). 기준선 866(3.4 종료, HEAD `3d767cc`)은 **참고값**(본인 재측정). [Source: epic-2-retro-2026-06-13.md(49·115), 3-4 스토리(138·221)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 선행 작업으로 권고. dev는 신규 코드·테스트 평문 secret 0건을 **수동 grep**으로 확인(봇토큰/`chat_id=digits`/한국휴대폰/이메일). dedup key는 hash·id만 담으므로 구조적으로 secret-free지만 fixture에도 가짜 값만. [Source: epic-2-retro-2026-06-13.md(114·129), 3-4 스토리(139)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/domain/delivery_log.py`, `src/rider_server/services/idempotency.py`, `tests/server/test_idempotency.py`. 수정(additive): `src/rider_server/domain/states.py`(`DeliveryStatus`), `src/rider_server/domain/__init__.py`(재노출), `src/rider_server/services/__init__.py`(재노출), `tests/server/test_domain_models.py`(11모델·DeliveryStatus lock). `.agents/`·`.claude/`·`_bmad/`·`src/rider_crawl/` 는 대상 아님. [Source: project-context.md(64), architecture.md(417-428)]
- **`services/` 채움:** architecture(425-428)가 정본 위치 — `services/idempotency.py`(428행이 명시)에 `IdempotentDeliveryService` 를 두어 `DispatchService`(3.1)·`SnapshotNormalizer`(3.2)·`MessageRenderService`(3.3)·`DispatchFanoutService`(3.4)와 동거. [Source: architecture.md(425-428), src/rider_server/services/]
- **`domain/` 채움:** `delivery_log.py` 추가 — `snapshot.py`(3.2)·`message.py`(3.3) 옆 11번째 모델. enum은 `states.py` 정본에 `DeliveryStatus` 추가(`SnapshotQualityState` 선례). [Source: src/rider_server/domain/, architecture.md(417-421)]
- **테스트 위치:** 평면 `tests/server/`(현재 `test_dispatch_fanout.py`·`test_domain_models.py` 등)에 `test_idempotency.py` 추가. `__init__.py` 미추가(평면 컨벤션, basename 고유). [Source: tests/server/, pyproject.toml(testpaths)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]` 로 `rider_server.services.idempotency`·`rider_server.domain.delivery_log` import 동작(서버 패키징·async/ORM은 Epic 5). [Source: pyproject.toml(pythonpath), 3-4 스토리(147)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.5(600-621)] — Epic 3 의도(한 번 수집 → 정규화 → 여러 채널 fan-out, 중복 없이 채널별 추적), Story 3.5 user story·3 AC 원문(dedup key 5필드·재시도 시 성공 전송 재전송 안 함·at-least-once + insert-then-send·exactly-once 미가정·오차단 방지·duplicate_blocked 기록).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.6~3.8(623-693)·#FR-10(38·163)·#FR-11(39·164)] — 다운스트림 위임처: 3.6 채널별 실패 상태 분리·재시도·error_code 분류·`AUTH_REQUIRED`·backoff, 3.7 Telegram 중앙, 3.8 dry-run; FR-10(중복 발송 방지=DeliveryLog+idempotency key), FR-11(재시도/실패 상태 — backoff/circuit breaker는 Epic 5).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(18·51·102)] — 흐름("… DispatchJob -> DeliveryLog"), **P2-05("Implement DeliveryLog and idempotency key. | Re-running the same message does not send a duplicate.")**, P2-06(3.6 실패 분리) 위임, migration-contract "Seed DeliveryLog dedup from old last_message hash"(102).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(16·34·146-156)] — DeliveryLog 모델("Delivery status, dedup key, errors, and sent time"), `delivery_logs`(id, message_id, channel_id, status, dedup_key, error_code, sent_at), **Dedup Key 5필드(monitoring_target_id + messenger_channel_id + snapshot_collected_at + template_version + message_hash)**.
- [Source: _bmad-output/planning-artifacts/architecture.md(43·142-143·313-314·323-330·346·357·359·428·493·524-526)] — fail-closed(오발송보다 미발송)·at-least-once+idempotency, job 의미론(at-least-once·exactly-once 미가정), 멱등성(성공 전송 전 dedup key 유니크 제약 먼저 확보=insert-then-send), 에러 분류 카테고리(duplicate_blocked 포함=3.6), `delivery_logs.dedup_key` 성공 레코드 UNIQUE·INSERT 충돌 차단·409 DUPLICATE_BLOCKED, `services/idempotency.py` 정본 위치, 단일 PostgreSQL 트랜잭션 일관성, 데이터 흐름(… DispatchJob → DeliveryLog(dedup) → Admin 가시화).
- [Source: src/rider_server/services/dispatch_fanout_service.py(9-10·48-65·127-152)] — 소비 대상 `DispatchJob`(dedup 4차원 보존)·`dispatch_all`(send 콜백 seam)·3.5 위임 명시(docstring 9-10행).
- [Source: src/rider_server/services/dispatch_service.py(8-16·41·63·65)] — 3.1 `DispatchService.dispatch`(무변경, dedup 미이관·`skipped` 항상 False)·docstring의 3.5 idempotency seam 명시 위임(12·41행)·`message_hash = sha256(text)`(63행).
- [Source: src/rider_server/domain/snapshot.py(19-34)·message.py(22-29)] — `Snapshot.collected_at`(dedup 차원, 호출부 주입)·`Message.template_version`/`text_hash`(dedup 차원); domain 모델 frozen dataclass 패턴(DeliveryLog 동형).
- [Source: src/rider_server/domain/states.py(34-42·74-81·123-136)] — `(str, Enum)` + 멤버명==대문자값 정본, "값 정의 vs 로직 경계"(SubscriptionStatus·SnapshotQualityState) — DeliveryStatus 2멤버 정의 근거.
- [Source: src/rider_server/domain/__init__.py(34-60)·tests/server/test_domain_models.py(251-297)] — `domain.__all__` 10모델·enum 재노출 + lock 테스트(본 스토리가 DeliveryLog 11번째·DeliveryStatus additive 갱신할 대상).
- [Source: src/rider_crawl/app.py(43-65·98-118)] — `send_only_on_change` 파일 dedup(`_is_duplicate`/`_write_last_hash`/`_message_scope_key`) — 본 스토리 0줄 변경, run_once 호환 경로 소유. 신규 dedup key는 공존(런타임 교체는 Epic 5).
- [Source: src/rider_crawl/redaction.py(130)] — `redact(text, *, mask_operational_ids=False) -> str`(P0-04 재사용, 본 스토리가 새 로그/메시지를 만들면 통과시킴).
- [Source: src/rider_server/migration/runner.py(86-97)] — 2.7 `MigrationSeed`(monitoring_target_id·message_hash·scope_hash) = old last_message hash 부분 seed(5차원 중 2개); 나머지 3차원은 Epic 3, 실제 적재는 Epic 5.
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-06-13.md(49·64-69·114-115·149)] — "새 뷰/단위" 무회귀 패턴, A1′(secret 게이트)·A2′(수치 단일 정본).
- [Source: _bmad-output/project-context.md(35·36·64·81·82·92)] — 순수·결정성, 파서/전송 오류 조용히 기본값 금지(fail-closed), 단방향 의존, secret 비노출, 범위 규율, **send_only_on_change scope key 비축소(92)**.
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P2-05/FR-10(DeliveryLog + idempotency key·중복 발송 방지)·ADD-5(crash-after-send·insert-then-send·at-least-once)·NFR-1~4(신뢰성)·NFR-15(duplicate_blocked 관측)·FR-2(기존 자산 재사용·무변경). 실패 상태 분류·재시도=3.6, Telegram 중앙=3.7, dry-run=3.8, `delivery_logs` 테이블·DB UNIQUE·ORM/async·런타임 교체·seed 적재=Epic 5, Kakao 실전송=Epic 4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, BMAD dev-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest -q` — 전체 스위트 **887 passed**(기준선 866, HEAD `3d767cc` 대비 신규 +21만 증가: `test_idempotency.py` 14 + `test_idempotency_e2e.py` 5(QA E2E/통합) + `test_domain_models.py` lock 2). 회귀 0. [리뷰 재측정값 — A2′ 단일 정본; dev-story 잠정값 882/+16는 QA e2e 5케이스 추가 전 수치라 정정함]
- `git diff -w --stat` — `src/rider_crawl/`·`pyproject.toml`·3.1~3.4 services 본문·기존 도메인 모델 **0줄 변경** 확인(additive: `domain/states.py`·`domain/__init__.py`·`services/__init__.py`·`tests/server/test_domain_models.py` + 신규 3파일).
- 의존성 방향 가드 `test_rider_crawl_never_imports_rider_server`(ast) 통과 — 역방향 import 0.
- 누출 grep(봇토큰/`chat_id=digits`/한국휴대폰/이메일) — 신규 코드·테스트 평문 secret **0건**.

### Completion Notes List

- **Task 1 — `DeliveryStatus` enum:** `domain/states.py` 에 `(str, Enum)` 2멤버(`SENT`/`DUPLICATE_BLOCKED`)만 additive 추가. "값 정의 vs 로직 경계" docstring으로 실패 카테고리(`*_FAILURE`/`AUTH_REQUIRED`)는 3.6/Epic 5 소유임을 명시(선점 금지).
- **Task 2 — `DeliveryLog` 도메인 모델(11번째):** `domain/delivery_log.py` 신규 — frozen dataclass, 계약 컬럼(`id`·`message_id`·`channel_id`·`status`·`dedup_key`·`error_code=None`·`sent_at=None`). `rider_crawl` import 0(순수 레코드). dedup 5차원은 `dedup_key` 문자열에 합성.
- **Task 3 — 도메인 재노출 + lock:** `domain/__init__.py` 에 `DeliveryLog`(11번째)·`DeliveryStatus` additive 재노출. `test_domain_models.py` lock(`__all__` set·model_names)에 additive 등재 + `DeliveryLog`/`DeliveryStatus` 필드·frozen·기본값·2멤버 잠금 단언 2개 추가.
- **Task 4 — `IdempotentDeliveryService`:** `services/idempotency.py` 신규(architecture 428 정본). `build_dedup_key`(5필드 `"|"` join, `collected_at.isoformat()` 정규화, 결정적·축소 금지), `deliver_once`(insert-then-send: `reserve(key)` → 성공이면 `send(job)` → `SENT`, 충돌이면 `send` 미호출 `DUPLICATE_BLOCKED`). `send` 예외는 분류·삼킴 없이 전파(3.6 경계). 내부 `now()`/`uuid4()` 미호출 — `id`/`sent_at`/`collected_at` 주입. `redact` 는 새 로그/메시지를 만들지 않아 미import(불필요).
- **Task 5 — 서비스 재노출:** `services/__init__.py` 에 `IdempotentDeliveryService` additive(`DeliveryLog`/`DeliveryStatus` 는 domain 소속이라 services 재노출 안 함).
- **Task 6 — 테스트:** `tests/server/test_idempotency.py` 신규 14케이스 — dedup key 5차원·결정성, 성공 후 재시도 차단(정확히 1회 전송), insert-then-send 순서, crash-after-send 재전송 차단, send 예외 전파, 오차단 방지(채널만 다른 fan-out 포함), duplicate_blocked 관측, 계약/frozen/기본값, 재노출, 비노출. 추가로 `tests/server/test_idempotency_e2e.py` 신규 5케이스(QA `qa-generate-e2e-tests`) — 실제 상류(`Snapshot`→`Message`→`DeliveryRule`→`DispatchFanoutService.plan`→`deliver_once`) 관통 통합 커버리지: (A) plan→deliver_once fan-out 멱등성·재실행 차단, (B) send 실패 후 reserve release 안 함→재시도 차단(3.6 경계·fail-closed), (C) collected_at 정규화 경계(tz-aware 결정성·마이크로초 구분), (D) 내용 변경/새 수집 시각은 오차단 없이 재전송. 제품 코드 무변경(test 안에서 seam 수준 Epic 5 wiring 시뮬레이션).
- **Task 7 — 검증:** 위 Debug Log 참조(882 pass·범위·방향·누출 전부 clean). 순수 additive·런타임 미배선(실제 DB UNIQUE/ORM/async/sender 배선·migration seed 적재는 Epic 5).
- **경계 준수:** `error_code` 항상 None(3.6), `dispatch_all` fan-out 루프 무변경(3.4), `reserve`/`send`/`log_id_for`/`sent_at`/`collected_at` 전부 주입 seam(Epic 5 wiring).

### File List

신규(additive):
- `src/rider_server/domain/delivery_log.py` — `DeliveryLog` frozen dataclass(11번째 도메인 모델)
- `src/rider_server/services/idempotency.py` — `IdempotentDeliveryService`(`build_dedup_key`·`deliver_once`)
- `tests/server/test_idempotency.py` — idempotency/DeliveryLog 단위 14 테스트
- `tests/server/test_idempotency_e2e.py` — QA E2E/통합 5 테스트(plan→deliver_once fan-out 멱등성·release 안 함·`collected_at` 정규화 경계·내용/시각 변경 재전송)

수정(additive only):
- `src/rider_server/domain/states.py` — `DeliveryStatus` enum 추가
- `src/rider_server/domain/__init__.py` — `DeliveryLog`·`DeliveryStatus` 재노출
- `src/rider_server/services/__init__.py` — `IdempotentDeliveryService` 재노출
- `tests/server/test_domain_models.py` — 11모델·`DeliveryStatus` lock 갱신 + 계약 잠금 2케이스
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 3.5 상태 전이(ready-for-dev → in-progress → review)

## Change Log

| Date | Version | Description |
|---|---|---|
| 2026-06-13 | 0.1 | Story 3.5 구현 완료 — `DeliveryLog`(11번째 도메인 모델)·`DeliveryStatus` enum·`IdempotentDeliveryService`(5필드 dedup key + insert-then-send) 순수 additive 추가. `rider_crawl`/3.1~3.4/기존 도메인 0줄 변경. Status → review. |
| 2026-06-13 | 0.2 | Senior Developer Review(AI, 자동 수정 모드) 통과 — CRITICAL 0. MEDIUM 2(File List에 `test_idempotency_e2e.py` 누락·stale 테스트 수치 882/+16) + LOW 1(완료 노트에 e2e 모듈 미기재) 자동 수정: File List에 QA e2e 모듈 등재, 리뷰 재측정값 **887 passed(+21, 회귀 0)** 로 정정(A2′ 단일 정본), 완료 노트 보강. 코드 변경 없음(구현·전 AC 검증 통과). Status → done. |

## Senior Developer Review (AI)

- **리뷰어:** lsy9344 · **일자:** 2026-06-13 · **모드:** story-automator 자동 리뷰(adversarial, 자동 수정)
- **결과: Approve** — CRITICAL 0 / HIGH 0 / MEDIUM 2(수정 완료) / LOW 1(수정 완료).

### 검증한 것 (claim vs 실제)

- **AC1~AC8 전부 IMPLEMENTED.** `build_dedup_key` 는 5차원(`target_id`·`channel_id`·`collected_at.isoformat()`·`template_version`·`message_hash`)을 `"|"` 로 join — 한 차원만 달라도 distinct key, 같은 입력 결정적. `deliver_once` 는 insert-then-send 순서(`reserve` → 성공 시 `send` → `SENT`; 충돌 시 `send` 미호출·`DUPLICATE_BLOCKED`), `send` 예외는 분류·삼킴 없이 전파, 내부 `now()`/`uuid4()` 미호출. `DeliveryLog`(11번째 frozen dataclass, 계약 7필드)·`DeliveryStatus`(2멤버) 계약 일치.
- **Task 1~7 [x] 전부 실제 완료 확인** — 코드·테스트 증거 대조. 누락·허위 완료 없음.
- **범위 규율 PASS** — `git diff -w --stat`: `src/rider_crawl/`·`pyproject.toml`·3.1~3.4 services 본문·기존 도메인 모델 **0줄**. 의존성 단방향(ast 가드 `test_rider_crawl_never_imports_rider_server` 통과). 신규 코드·테스트 평문 secret 0(grep 통과).
- **테스트 재측정(정본):** `.venv/Scripts/python.exe -m pytest -q` → **887 passed**(기준선 866 +21, 회귀 0). 단위 14 + QA e2e 5 + 도메인 lock 2.

### 발견 → 조치

1. **[MEDIUM·수정완료] File List 누락** — git에 untracked `tests/server/test_idempotency_e2e.py`(QA e2e 5케이스)가 있으나 File List 미기재. → File List에 additive 등재.
2. **[MEDIUM·수정완료] stale 테스트 수치(A2′)** — Debug Log/Change Log의 `882/+16` 은 QA e2e 추가 전 dev 잠정값. → 리뷰 재측정 `887/+21` 로 정정(단일 정본).
3. **[LOW·수정완료] 완료 노트에 e2e 모듈 미기재** — Task 6 노트에 통합 커버리지(Gap A~D) 추가 기술.

### 비고

- 코드 자체 변경은 없음(구현이 전 AC를 정확히 충족). 본 리뷰 수정은 스토리 기록(File List·수치·노트)의 투명성 정정에 한정.
- 런타임 미배선(주입 seam)·실제 DB UNIQUE/ORM/async/sender 배선·migration seed 적재 = Epic 5, 실패 분류·재시도·release = 3.6 — 본 스토리 경계 준수 확인.
