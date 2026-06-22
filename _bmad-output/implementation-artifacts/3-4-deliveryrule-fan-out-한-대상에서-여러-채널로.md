---
baseline_commit: d81f027
---

# Story 3.4: DeliveryRule fan-out — 한 대상에서 여러 채널로

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 고객사 담당자,
I want 하나의 모니터링 대상에서 렌더된 **하나의 `Message`(3.3)** 가, 그 대상에 연결된 **여러 활성 `DeliveryRule`(2.5)** 을 따라 **채널마다 별도의 fan-out 단위(`DispatchJob`)** 로 펼쳐지고, 한 채널 전송 실패가 다른 채널 전송 성공을 **무효화하지 않게(채널 격리)** 하고 싶다. 단, 2.5가 만든 **`DeliveryRule` dataclass·`MessengerChannel` dataclass는 한 줄도 바꾸지 않고**(재사용만), 3.1 **`DispatchService.dispatch`(단일 전송) 시그니처·본문도 무변경**(parity 보존)으로, **순수 additive**하게 신규 fan-out 값 객체 **`DispatchJob`** 과 신규 **`DispatchFanoutService`**(`plan` = Message + 활성 rules → `list[DispatchJob]`, `dispatch_all` = 주입된 sender로 채널별 격리 전송)만 추가한다,
so that 같은 실적 내용을 Telegram 그룹과 KakaoTalk 방에서 **모두** 받을 수 있고(FR-9), 이 fan-out 단위(`DispatchJob`) 위에 **3.5 DeliveryLog/idempotency dedup key**(`target_id + channel_id + collected_at + template_version + message_hash`)·**3.6 채널별 실패 상태 분류·재시도**(FR-11·26)·**3.7 Telegram 중앙 전송**·**Epic 5 영속(jobs/delivery_logs 테이블·async wiring)**·**Epic 4 Kakao 실제 전송** 이 additive로 빌드된다(P2-04, FR-9, NFR-1~4).

> **이 스토리의 성격 — "한 Message → (활성 DeliveryRule마다) 채널별 `DispatchJob` 생성 + 채널 격리 전송, 그것만."** 3.1이 `run_once` 를 세 서비스로 **구조 분리**했고(`DispatchService.dispatch` = 단일 전송, `skipped` 항상 False), 3.2가 수집을 `Snapshot` 으로, 3.3이 렌더를 `Message` 로 승격했다. 본 스토리는 그 **단일 Message** 를 **연결된 채널 수만큼 fan-out**(N개 `DispatchJob`)하고, 각 채널 전송을 **서로 격리**(한 채널 실패 ≠ 다른 채널 무효화)한다. 본 스토리는 **DeliveryLog·idempotency dedup key·insert-then-send=3.5, 채널별 실패 상태 분류·재시도·AUTH_REQUIRED·backoff=3.6, Telegram 중앙 sendMessage/webhook=3.7, dry-run 비교·승인 활성화=3.8, jobs/delivery_logs 테이블·SQLAlchemy ORM/Alembic·Pydantic·async wiring·런타임 교체·tenant 템플릿 다중화(`template_id`)=Epic 5, Kakao 실제 PC 자동화 전송=Epic 4** 를 끌어오지 않는다. [Source: epics.md Epic 3(511-513)·Story 3.4(579-598)·Story 3.5~3.8(600-693), implementation-contract.md P2-04(50)·flow(18), data-api-contract.md(13·31·34·172-173), 3-3 스토리(22-25), src/rider_server/services/dispatch_service.py(8-16)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 `services/dispatch_fanout_service.py`(`DispatchJob` 값 객체 + `FanoutOutcome` + `DispatchFanoutService`) + `services/__init__.py` 재노출 additive + 신규 테스트 `tests/server/test_dispatch_fanout.py`. **도메인 모델·`rider_crawl`·`pyproject.toml`·3.1/3.2/3.3 기존 코드 무변경.** 아래는 **다른 스토리/에픽 소유 — 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `app.py`(run_once·`_message_scope_key`·`send_only_on_change` dedup)/`message.py`/`messengers/`/`sender.py`/`redaction.py` 등 어떤 파일도 수정하지 않는다. **import해서 재사용만** 한다(`redact` 만 사용). **이유 1(의존성 방향 — 절대 규칙):** `rider_server → rider_crawl` import만 허용, 역방향 금지(project-context.md 64, architecture.md 482). **이유 2(scope key 보존 — AC3):** `send_only_on_change` 의 마지막 메시지 해시·`_message_scope_key`(app.py 98-118)는 **run_once 호환 경로가 계속 소유**한다 — 본 스토리는 그 scope key를 **읽지도 바꾸지도 줄이지도 않는다**(3.1이 dedup 미이관). 신규 경로의 scope 비축소는 `DispatchJob` 이 `target_id` + `channel_id` 를 **둘 다** 보존하는 것으로 표현한다(아래 AC3 참조). [Source: project-context.md(64·92), src/rider_crawl/app.py(43-65·98-118), src/rider_server/services/dispatch_service.py(8-13)]
> - **`DeliveryRule`(2.5)·`MessengerChannel`(2.5) dataclass 무변경.** 본 스토리는 두 모델을 **import·재사용**만 한다. `DeliveryRule` 은 이미 `(target_id, channel_id)` 매핑이라 **같은 `target_id` 에 `channel_id` 다른 여러 인스턴스**로 fan-out을 표현한다(delivery_rule.py 1-6 docstring이 명시). 필드를 더하거나 바꾸지 않는다. [Source: src/rider_server/domain/delivery_rule.py(1-22), src/rider_server/domain/messenger_channel.py(16-24)]
> - **`DispatchService.dispatch(config, message, *, send_message) -> DispatchResult`(3.1) 시그니처·본문 무변경.** 3.1 `dispatch` 는 **단일 전송** parity(`message`/`sent`/`skipped`/`message_hash`)가 `run_once` 와 잠겨 있다(`tests/server/test_run_once_split.py`). 본 스토리는 `dispatch` 를 **그대로 두고** fan-out을 **별도 서비스**(`DispatchFanoutService`)로 additive하게 붙인다. fan-out의 채널별 실제 전송은 **주입된 sender 콜백**으로 하며(3.1 `dispatch` 를 직접 재호출하지 않아도 됨 — Dev Notes "왜 별도 서비스인가" 참조), 런타임 교체·중앙 Telegram·Kakao 실전송 배선은 3.7/Epic 4/Epic 5다. [Source: src/rider_server/services/dispatch_service.py(35-69), tests/server/test_run_once_split.py(157-189)]
> - **`DeliveryLog`·idempotency dedup key(`target_id + channel_id + collected_at + template_version + message_hash`)·insert-then-send·중복 차단(`duplicate_blocked`)·`send_only_on_change` 의 마지막 해시 비교/기록** → **3.5**(P2-05, ADD-5). 본 스토리는 `DispatchJob` 에 dedup **차원(=`target_id`·`channel_id`·`template_version`·`message_hash`, `collected_at` 은 `message_id`→snapshot 조인)** 을 **보존만** 하고 dedup key를 **조립·비교·기록하지 않는다**. [Source: epics.md Story 3.5(600-621), data-api-contract.md(34·172-173)]
> - **채널별 실패 상태 분류·재시도·`AUTH_REQUIRED`·`telegram_failure`/`kakao_failure` 카테고리·backoff** → **3.6**(P2-06, FR-11·26). 본 스토리의 채널 격리는 **구조적**(한 채널 예외가 루프를 중단시키지 않음, `sent=False` 로 contain)일 뿐, error_code 분류·재시도 정책·상태 전이를 **하지 않는다**. `FanoutOutcome.error_redacted` 는 redaction 통과한 **분류 안 된 breadcrumb** 일 뿐 운영 상태값이 아니다. [Source: epics.md Story 3.6(623-643), architecture.md(323-330)]
> - **Telegram 중앙 sendMessage/webhook(중앙 dispatcher)** → **3.7/Epic 5**. 본 스토리의 `dispatch_all` 은 **주입된 sender 콜백**만 호출한다 — 중앙 webhook/per-Agent 경로 선택은 호출부(3.7/Epic 5) 책임이다. [Source: architecture.md(192-194·433-434), src/rider_server/services/dispatch_service.py(14-16)]
> - **`jobs`/`delivery_logs` 테이블·SQLAlchemy ORM/Alembic·Pydantic 스키마·async wiring·런타임 교체·tenant 템플릿 선택(`template_id`)** → **Epic 5**. 본 스토리는 **순수 dataclass 값 객체 + 순수 동기 서비스 + 테스트만**, **런타임 미배선**이다(2.5/2.6/3.1/3.2/3.3 동일). `DispatchJob` 은 영속 시 generic `jobs`(type=DISPATCH_*) + `delivery_logs` 로 매핑되나, 본 스토리는 in-memory 값 객체만 정의한다. [Source: architecture.md(417-444·524-526), data-api-contract.md(33-34 jobs/delivery_logs), implementation-contract.md(18)]
>
> **순수·결정적·의존성 0(2.5/2.6/3.1/3.2/3.3 토대 제약 계승).** `DispatchJob`·`FanoutOutcome`·`DispatchFanoutService` 는 FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬이다. **내부에서 `datetime.now()`/`uuid4()` 를 호출하지 않는다** — `DispatchJob.id`(job_id)는 **호출부 주입**(`job_id_for` 콜백; 2.5 `Tenant.created_at`·3.2 `snapshot_id`·3.3 `message_id` 주입 선례). [Source: project-context.md(35), src/rider_server/services/snapshot_normalizer.py(55-65), 3-3 스토리(27)]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** `MessengerChannel.telegram_chat_id`/`thread_id`/`kakao_room_name` 은 **라우팅 식별자라 secret이 아니다**(2.4 결정 계승) — 그러나 `DispatchJob` 은 **`channel_id`(불투명 FK)만** 들고 chat_id/room_name 원문을 들지 않는다(라우팅 해석은 전송 seam/Epic 5). `FanoutOutcome.error_redacted` 는 기존 `redaction.redact` 를 통과시킨다(P0-04 재사용·defense-in-depth). 테스트 fixture·예외 메시지에 실제 봇 토큰/비밀번호/OTP/`chat_id` 숫자/전화/이메일 원문을 넣지 않는다. [Source: project-context.md(81), src/rider_server/domain/messenger_channel.py(3-5·21-22), src/rider_crawl/redaction.py(130)]

## Acceptance Criteria

**AC1 — 한 Message → 채널마다 별도 `DispatchJob` 생성, 최소 2채널 fan-out (P2-04, FR-9)**

1. **Given** 하나의 모니터링 대상(`target_id`)에 **활성** `DeliveryRule` 이 여러 개(예: Telegram 채널 1개 + Kakao 방 1개) 연결돼 있고 그 대상의 단일 `Message`(3.3)가 있을 때 **When** `DispatchFanoutService.plan(message, rules, channels=..., job_id_for=...)` 으로 fan-out하면(P2-04, FR-9) **Then** **활성 `DeliveryRule` 마다 별도의 `DispatchJob`** 이 생성되고(1 Message → N `DispatchJob`), 각 `DispatchJob` 은 **`id`(주입), `target_id`(rule), `channel_id`(rule), `message_id`(message.id), `messenger`(channel.messenger로 derive), `template_version`(message), `message_hash`(=message.text_hash)** 를 가지며, 반환 리스트는 입력 rule 순서를 보존한다. [Source: epics.md AC(587-590), implementation-contract.md P2-04(50), data-api-contract.md(13·31·172-173), src/rider_server/domain/delivery_rule.py(15-21), src/rider_server/domain/message.py]
2. **And** **`enabled=False` 인 `DeliveryRule`(soft delete)은 fan-out에서 제외**된다(`DispatchJob` 미생성) — 물리 삭제가 아니라 비활성 상태값이므로 전송 대상에서 빠진다. [Source: src/rider_server/domain/delivery_rule.py(4·20), project-context.md(36 fail-closed)]
3. **And** **한 번의 수집(=단일 Message)에서 최소 두 개 채널(Telegram + Kakao)로 fan-out** 되는 시나리오가 테스트로 검증된다(서로 다른 `channel_id`·`messenger`, 같은 `message_id`·`message_hash`·`template_version`). [Source: epics.md AC(590 "최소 두 개 채널로 fan-out"), implementation-contract.md P2-04(50 "fans out to at least two channels"), SPEC.md(83)]

**AC2 — 채널 격리: 한 채널 전송 실패가 다른 채널 성공을 무효화하지 않음 (FR-9)**

4. **Given** fan-out된 `DispatchJob` 들을 전송할 때 **When** 한 채널의 전송이 예외로 실패하고 다른 채널은 정상 전송되면 — `DispatchFanoutService.dispatch_all(message, jobs, send=...)` 가 **각 `DispatchJob` 의 전송을 서로 격리**(한 job의 예외가 루프를 중단시키지 않음)해서 처리하면 **Then** 실패 채널은 `FanoutOutcome(sent=False)`, 정상 채널은 `FanoutOutcome(sent=True)` 로 **각각 독립 기록**되고, **특정 채널 실패가 다른 채널의 성공을 무효화하지 않는다**(FR-9). 모든 `DispatchJob` 이 정확히 한 번씩 시도되고 결과 리스트는 입력 순서를 보존한다. [Source: epics.md AC(592-594), implementation-contract.md P2-04(50)]
5. **And** 전송 실패는 **분류 없이 contain** 만 된다 — `FanoutOutcome.error_redacted` 는 `redact(...)` 통과한 문자열(또는 None)일 뿐, **error_code 분류·재시도·`AUTH_REQUIRED` 상태 전이를 수행하지 않는다**(그것은 3.6). 즉 `dispatch_all` 은 채널 격리만 보장하고 실패 운영 정책은 호출부(3.6)에 위임한다. [Source: epics.md Story 3.6(623-643), architecture.md(323-330), project-context.md(81)]

**AC3 — `send_only_on_change` scope 비축소: dedup 차원에 전송 대상(channel) 보존 (project-context 규칙)**

6. **Given** `DeliveryRule.send_only_on_change` 가 설정될 수 있을 때 **When** 한 Message가 여러 채널로 fan-out되면 **Then** 각 `DispatchJob` 은 **`target_id`(플랫폼·URL·센터 식별) 와 `channel_id`(전송 대상) 를 둘 다 보존**해, 미래의 변경 감지(3.5 DeliveryLog dedup key = `target_id + channel_id + collected_at + template_version + message_hash`)가 **채널별로 독립 판단**되도록 한다 — 즉 **scope key가 `target_id` 단독으로 축소되지 않는다**(project-context.md 92: "scope key를 줄이면 다른 탭/계정의 중복 판단이 섞일 수 있다"). 같은 Message·같은 `target_id` 라도 채널이 다르면 `DispatchJob.channel_id` 가 다르다(전송 대상 차원 보존). [Source: project-context.md(92), data-api-contract.md(172-173), src/rider_crawl/app.py(98-118)]
7. **And** 본 스토리는 `send_only_on_change` 의 **마지막 해시 비교·기록·중복 차단을 수행하지 않는다**(3.5 소유) — `rider_crawl/app.py` 의 `_message_scope_key`/`_is_duplicate`/`_write_last_hash`(43-65·98-118)는 **0줄 변경**이고 run_once 호환 경로가 계속 소유한다. 본 스토리의 AC3 의무는 신규 fan-out 단위가 **dedup 차원(특히 `channel_id`)을 잃지 않게** 보존하는 **구조적 보장**에 한정된다. `DeliveryRule.send_only_on_change` 플래그는 채널(rule)마다 독립이며, 본 스토리는 그 값을 **읽어 `DispatchJob` 에 차원으로 옮기지 않아도**(3.5가 rule에서 직접 읽음) 채널 독립성이 `channel_id` 보존으로 성립함을 테스트로 잠근다. [Source: src/rider_crawl/app.py(43-65), src/rider_server/domain/delivery_rule.py(21), epics.md Story 3.5(600-621)]

**AC4 — 순수 additive·무회귀·단방향·비노출 (FR-2, NFR-20, 토대 제약)**

8. **And** `src/rider_crawl/`·`pyproject.toml`·`src/rider_server/domain/`·3.1 `dispatch_service.py`·3.2 `snapshot_normalizer.py`·3.3 `message_render_service.py` **0줄 변경**(`git diff -w --stat`)으로 기존 회귀 그물(`tests/server/test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`tests/test_message.py`·`test_app.py`)이 **전부 그대로 통과**하고, 본 스토리는 신규 fan-out 케이스만큼만 테스트 수가 증가한다(순수 additive). 의존성은 **단방향**(`rider_server → rider_crawl` 만, 역방향 0 — ast 가드 `test_rider_crawl_never_imports_rider_server` 통과)이고, 신규 코드·테스트에 평문 secret 0건이다. [Source: project-context.md(58·64·82), 3-3 스토리(47·77), epic-2-retro-2026-06-13.md(114-115)]

## Tasks / Subtasks

- [x] **Task 1 — `DispatchJob` fan-out 값 객체 + `FanoutOutcome` 정의: `services/dispatch_fanout_service.py` (AC: 1, 2, 3)** — `dispatch_service.py` 의 `DispatchResult` 패턴(`@dataclass(frozen=True)` + `from __future__ import annotations`)과 동형. **domain/ 이 아니라 services/ 에 둔다**(아래 Dev Notes "위치 결정" 참조 — `DispatchResult` 선례):
  - [x] **`@dataclass(frozen=True) class DispatchJob`** 필드(모두 필수·기본값 없음, dataclass 순서 규칙): `id: str`(주입 job id), `target_id: str`(→ MonitoringTarget, rule.target_id), `channel_id: str`(→ MessengerChannel, rule.channel_id), `message_id: str`(→ Message, message.id), `messenger: Messenger`(라우팅 enum, channel.messenger로 derive), `template_version: str`(message.template_version — dedup 차원), `message_hash: str`(=message.text_hash — dedup 차원). [Source: data-api-contract.md(13·31·172-173), src/rider_server/domain/delivery_rule.py(15-21), src/rider_server/domain/states.py(74-80), src/rider_server/services/dispatch_service.py(35-48)]
  - [x] **`@dataclass(frozen=True) class FanoutOutcome`** 필드: `job: DispatchJob`, `sent: bool`, `error_redacted: str | None = None`(redaction 통과 breadcrumb — **분류 안 함**, 3.6 위임). [Source: AC2(4-5), project-context.md(81)]
  - [x] **import은 단방향만:** `from dataclasses import dataclass`, `from typing import Callable, Mapping, Sequence`, `from rider_server.domain import DeliveryRule, Message, Messenger, MessengerChannel`, `from rider_crawl.redaction import redact`. 역방향(`rider_crawl` → `rider_server`) 코드 0. [Source: project-context.md(64), src/rider_server/services/message_render_service.py(import 패턴)]
  - [x] 모듈 상단 docstring으로 책임(P2-04 fan-out)·위임처(dedup/DeliveryLog=3.5, 실패 분류·재시도=3.6, 중앙 Telegram=3.7, Kakao 실전송=Epic 4, 영속/ORM/template_id=Epic 5)·`DispatchJob` ↔ generic `jobs`/`delivery_logs` 매핑 의도를 2~4줄로 남긴다(`dispatch_service.py` 1-23 docstring 형식 계승). [Source: src/rider_server/services/dispatch_service.py(1-23)]
- [x] **Task 2 — `DispatchFanoutService.plan` 추가: `services/dispatch_fanout_service.py` (AC: 1, 2, 3)** — 순수·결정적 staticmethod:
  - [x] **`@staticmethod def plan(message: Message, rules: Sequence[DeliveryRule], *, channels: Mapping[str, MessengerChannel], job_id_for: Callable[[DeliveryRule], str]) -> list[DispatchJob]`**. 동작: 입력 `rules` 순서대로 순회하며 (1) `rule.enabled` 가 False면 **skip**(AC1.2 soft delete 제외), (2) `channels[rule.channel_id]` 로 채널 해석 — **없으면 fail-closed로 명확한 예외**(`KeyError` 또는 모듈 정의 `UnknownChannelError(KeyError)`) raise(dangling FK = 설정 무결성 버그 → 조용히 미전송하지 않고 surface; project-context 36 "조용히 기본값 금지" 정신), (3) `messenger = channel.messenger`, (4) `DispatchJob(id=job_id_for(rule), target_id=rule.target_id, channel_id=rule.channel_id, message_id=message.id, messenger=messenger, template_version=message.template_version, message_hash=message.text_hash)` 생성·append. 내부 `uuid4()`/`now()` 미호출(결정적 — id는 `job_id_for` 주입). [Source: epics.md AC(587-590·596-598), src/rider_server/domain/delivery_rule.py(15-21), src/rider_server/domain/messenger_channel.py(20), project-context.md(35·36)]
  - [x] **호출부 계약 명시(docstring):** `rules` 는 **이미 해당 대상(`target_id`)으로 필터된** 활성/비활성 혼합 후보다(대상 scope 쿼리는 Epic 5 소유). `plan` 은 `target_id` 일관성을 message로부터 재검증하지 않는다(Message는 `target_id` 가 아니라 `snapshot_id` 만 보유). `channels` 는 `channel_id → MessengerChannel` 조회 맵(라우팅 enum derive용). [Source: src/rider_server/domain/message.py, data-api-contract.md(33)]
- [x] **Task 3 — `DispatchFanoutService.dispatch_all` 추가: 채널 격리 전송 (AC: 2, 5)** — 순수 구조(부작용은 주입 sender):
  - [x] **`@staticmethod def dispatch_all(message: Message, jobs: Sequence[DispatchJob], *, send: Callable[[DispatchJob, str], None]) -> list[FanoutOutcome]`**. 동작: 각 `job` 에 대해 `try: send(job, message.text); outcome=FanoutOutcome(job, sent=True, error_redacted=None)` / `except Exception as exc: outcome=FanoutOutcome(job, sent=False, error_redacted=redact(repr(exc)))` 후 append — **한 job의 예외가 다음 job 전송을 막지 않는다(채널 격리, AC2)**. 결과 리스트는 입력 순서 보존. `send` 는 **필수 인자(기본값 없음)** — 중앙 Telegram(3.7)/Kakao Agent(Epic 4)/실 sender 배선은 호출부 책임이며, 본 서비스는 sender를 직접 구성하지 않는다(3.1 `dispatch` 와 달리 기본 adapter를 두지 않는 이유는 채널별 라우팅이 Epic 5 config 배선 전이기 때문 — Dev Notes 참조). [Source: epics.md AC(592-594), src/rider_server/services/dispatch_service.py(54-69), project-context.md(81)]
  - [x] **분류 금지(스코프 경계):** `except` 에서 error_code 매핑/재시도/상태 전이/`AUTH_REQUIRED` 판정을 **하지 않는다**(3.6). `error_redacted` 는 redaction 통과 문자열만(누출 방지) — 운영 카테고리 아님. [Source: epics.md Story 3.6(636-639), architecture.md(323-326)]
- [x] **Task 4 — 재노출 갱신: `services/__init__.py` (AC: 1)** — additive only:
  - [x] `from .dispatch_fanout_service import DispatchFanoutService, DispatchJob, FanoutOutcome` import 추가, `__all__` 에 `"DispatchFanoutService"`, `"DispatchJob"`, `"FanoutOutcome"` additive 추가(3.1 `DispatchService`/`DispatchResult`, 3.2 `SnapshotNormalizer`, 3.3 무삭제 — 기존 심볼 보존). docstring에 "Story 3.4(P2-04, FR-9)가 `DispatchFanoutService`/`DispatchJob`/`FanoutOutcome`(한 Message → N 채널 fan-out + 채널 격리 전송)을 additive로 추가" 1단락 보강. [Source: src/rider_server/services/__init__.py(1-50)]
  - [x] **`domain/__init__.py` 무변경**(`DispatchJob` 은 services 소속 — `DispatchResult` 선례). `test_domain_models.py` 의 `domain.__all__` 잠금(10모델)도 무변경. [Source: src/rider_server/services/dispatch_service.py(35), src/rider_server/domain/__init__.py(34-60)]
- [x] **Task 5 — 테스트 추가: `tests/server/test_dispatch_fanout.py` (AC: 1~8)** — 외부 호출 없음(fake/in-memory), 가짜 값만. 평면 `tests/server/`(`__init__.py` 미추가 — 기존 컨벤션). `test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py` 의 fixture 패턴 재사용:
  - [x] **(AC1·AC3 happy path — fan-out 필드·≥2채널):** 같은 `target_id` 에 Telegram rule + Kakao rule(둘 다 `enabled=True`) + 각 `MessengerChannel`(messenger=TELEGRAM/KAKAO) + 가짜 `Message`(`id="msg-1"`, `template_version="baemin.realtime.v1"`, `text_hash="<sha256>"`)로 `plan(...)` 하면 **2개 `DispatchJob`** 반환 — 각 `channel_id`·`messenger` 다르고, `message_id`/`message_hash`/`template_version`/`target_id` 는 동일. `job_id_for=lambda r: f"dj-{r.channel_id}"`. [Source: epics.md AC(587-590)]
  - [x] **(AC1.2 — disabled rule 제외):** `enabled=False` rule을 섞으면 그 rule의 `DispatchJob` 은 생성되지 않음(활성만 fan-out). [Source: src/rider_server/domain/delivery_rule.py(20)]
  - [x] **(AC3 — scope 비축소):** 같은 `target_id`·같은 Message·다른 channel 2개 → 두 `DispatchJob` 의 `(target_id, channel_id)` 가 **distinct**(channel 차원 보존), 그리고 미래 dedup 차원 튜플 `(target_id, channel_id, template_version, message_hash)` 가 채널별로 **`channel_id` 만 다르고 나머지는 같음**을 단언(전송 대상 scope 비축소). [Source: project-context.md(92), data-api-contract.md(172-173)]
  - [x] **(AC2 — 채널 격리):** `dispatch_all(message, jobs, send=...)` 에서 `send` 가 첫 job(예: Kakao)엔 예외를 던지고 둘째 job(Telegram)엔 성공하도록 fake 구성 → 결과: 첫 `FanoutOutcome.sent is False`(+ `error_redacted` not None), 둘째 `sent is True`. **둘째 채널 전송이 정상 수행**되고(호출 기록 확인) 첫 채널 실패가 무효화하지 않음. 순서·시도 횟수(각 1회) 보존. [Source: epics.md AC(592-594)]
  - [x] **(AC2/AC5 — 분류 안 함·누출 방지):** `error_redacted` 는 `redact(repr(exc))` 와 일치하고, 예외 메시지에 봇토큰/`chat_id` 숫자를 넣은 fake로 던져도 `error_redacted` 에 원문이 남지 않음(redaction 통과). error_code/카테고리 필드가 `FanoutOutcome` 에 **없음**(3.6 미선점) 확인. [Source: project-context.md(81), src/rider_crawl/redaction.py(130)]
  - [x] **(fail-closed — unknown channel):** `plan` 에 `channels` 맵에 없는 `channel_id` 를 가진 rule을 주면 `pytest.raises((KeyError,))`(dangling FK = 조용히 미전송 금지, surface). [Source: project-context.md(36)]
  - [x] **(frozen·결정성):** `DispatchJob`/`FanoutOutcome` 이 `frozen`(`with pytest.raises(FrozenInstanceError)`); 같은 입력으로 `plan` 두 번 호출 시 `DispatchJob` 들이 동일(내부 `uuid4()`/`now()` 미호출 — id는 `job_id_for` 결정). [Source: 3-3 스토리(71), project-context.md(35)]
  - [x] **(재노출):** `from rider_server.services import DispatchFanoutService, DispatchJob, FanoutOutcome` 가 동작하고 `services.__all__` 에 포함됨. [Source: src/rider_server/services/__init__.py(34-50)]
  - [x] fixture는 가짜 값만(`"mt-1"`·`"ch-tg"`·`"ch-kakao"`·`"msg-1"`·`"a"*64` 류 hash·`Messenger.TELEGRAM`/`KAKAO`). 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/`chat_id=<digits>`/한국 휴대폰/이메일 원문 금지. [Source: project-context.md(81), 3-3 스토리(73)]
- [x] **Task 6 — 회귀·범위·누출 검증 및 마무리 (AC: 1~8)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 **846** — HEAD `d81f027`(3.3 종료) 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `tests/server/test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`tests/test_message.py`·`test_app.py`) 신규 fan-out 케이스만큼만 증가가 정상(순수 additive). 신규 파일 제외(`--ignore=tests/server/test_dispatch_fanout.py`) 시 정확히 846으로 기준선 재확인. [Source: 3-3 스토리(75·182), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat` 에 **신규 `services/dispatch_fanout_service.py`·`tests/server/test_dispatch_fanout.py` + `services/__init__.py`(재노출·docstring)만** 보이고 **`src/rider_crawl/`·`pyproject.toml`·`src/rider_server/domain/`·3.1 `dispatch_service.py`·3.3 `message_render_service.py` 변경 0줄**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건, `src/rider_crawl/` 에 `rider_server` import가 **새로 생기지 않았음**(ast 기반 권장 — 단순 문자열 grep은 docstring 오탐) 확인. [Source: project-context.md(64·81), 3-3 스토리(77)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: epic-2-retro-2026-06-13.md(115), 3-3 스토리(78·131)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `services/dispatch_fanout_service.py`(`DispatchJob` + `FanoutOutcome` + `DispatchFanoutService`)·`tests/server/test_dispatch_fanout.py` + `services/__init__.py`(재노출·docstring). **`src/rider_crawl/`·`pyproject.toml`·`src/rider_server/domain/` 무변경, 3.1 `dispatch_service.py`·3.2 `snapshot_normalizer.py`·3.3 `message_render_service.py` 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(app.py `_message_scope_key`/`send_only_on_change` dedup·messengers·redaction — 보존·재사용만), `DeliveryRule`/`MessengerChannel`/`Message`(2.5/3.3 — import만), `DispatchService.dispatch`(3.1 단일 전송 parity), DeliveryLog/idempotency/dedup key/insert-then-send(3.5), 채널별 실패 분류·재시도·AUTH_REQUIRED·backoff(3.6), Telegram 중앙 webhook/sendMessage(3.7), dry-run·승인(3.8), jobs/delivery_logs 테이블·ORM/Alembic/Pydantic/async·런타임 교체·tenant 템플릿(`template_id`)(Epic 5), Kakao 실제 PC 자동화 전송(Epic 4). [Source: epics.md Story 3.5~3.8(600-693), architecture.md(417-444), implementation-contract.md(50-52)]

### 위치 결정 — 왜 `DispatchJob` 을 domain/ 이 아니라 services/ 에 두나 (반드시 읽을 것)

- **`DispatchJob` 은 fan-out 파이프라인 값 객체 → `services/`(3.1 `DispatchResult` 선례).** 3.1이 `DispatchResult` 를 **domain이 아니라 `services/dispatch_service.py`** 에 둔 것과 동형이다. 도메인(`domain/`)은 **data-api-contract의 계약 테이블 백킹 레코드**(Snapshot=`snapshots`, Message=`messages` 등 + 상태 enum)만 둔다. `DispatchJob` 은 **독립 계약 테이블이 아니다** — 영속 시 generic `jobs`(type=DISPATCH_TELEGRAM/KAKAO_SEND) + `delivery_logs` 로 매핑되는 **전송 파이프라인 단위**다. 따라서 `DispatchResult` 와 같은 레이어(services)에 둔다. [Source: src/rider_server/services/dispatch_service.py(35-48), data-api-contract.md(7-21·33-34 jobs/delivery_logs), src/rider_server/domain/__init__.py(34-47)]
- **그 결과 `domain/__init__.py`·`test_domain_models.py` 의 `domain.__all__`(10모델 잠금)은 무변경**이다 — 3.2(9번째 Snapshot)·3.3(10번째 Message)이 domain 모델이라 lock을 갱신했던 것과 달리, 본 스토리는 domain 모델을 추가하지 않으므로 그 회귀-net을 건드리지 않는다. (architecture.md 419가 `domain/delivery.py` 를 적었으나, 그건 `DeliveryRule`(이미 `delivery_rule.py`, 계약 테이블)을 가리키며 `DispatchJob`(비-계약 파이프라인 단위)을 domain으로 끌어오라는 뜻이 아니다 — 3.3이 architecture 397의 "message.py에 template_version" 을 server-side 상수로 재해석한 것과 동형 판단.) [Source: src/rider_server/domain/__init__.py(34-60), tests/server/test_domain_models.py(251-297), architecture.md(417-419), 3-3 스토리(106-108)]

### 왜 별도 서비스(`DispatchFanoutService`)인가 — 3.1 `dispatch` 무변경

- **3.1 `DispatchService.dispatch` 는 "단일 전송" parity 가 `run_once` 와 잠겨 있다**(`tests/server/test_run_once_split.py` 157-189: `message`/`sent`/`skipped`/`message_hash` 동등). fan-out을 `dispatch` 에 욱여넣으면 parity가 깨진다. 그래서 fan-out은 **별도 서비스**로 둔다 — 3.1이 docstring(14행)에서 "단일 전송만: DeliveryRule fan-out(1 대상 → N 채널)은 Story 3.4" 라고 **명시 위임**한 그 경계를 정확히 채운다. [Source: src/rider_server/services/dispatch_service.py(8-16·54-69), tests/server/test_run_once_split.py(157-189)]
- **`dispatch_all` 의 `send` 콜백이 기본 adapter를 두지 않는 이유:** 3.1 `dispatch` 는 `AppConfig` 1개로 messenger registry(`messengers.dispatch_text_message`)에 위임하는 기본 adapter가 있다. 하지만 fan-out은 **채널마다 라우팅(어떤 chat_id/room으로)** 이 다르고, 채널→`AppConfig`(또는 중앙 Telegram payload) 배선은 **Epic 5(런타임 교체)·3.7(중앙 Telegram)·Epic 4(Kakao)** 소유다. 따라서 본 스토리는 `send` 를 **필수 주입 seam**으로 두고 기본 sender를 만들지 않는다(미배선 원칙 — 2.5/3.1/3.2/3.3 과 동일하게 "정의만, 런타임 미배선"). 테스트는 fake `send` 로 채널 격리를 검증한다. [Source: src/rider_server/services/dispatch_service.py(54-74), architecture.md(192-194·433-434·524-526), project-context.md(35)]

### `DispatchJob` 필드 ↔ 계약/dedup 매핑 (AC1·AC3 — 정밀 계약)

| 필드 | 타입 | 출처/근거 |
|---|---|---|
| `id` | `str` | 영속 시 `jobs.id`. 호출부 주입(`job_id_for` 콜백 — 서비스 내부 `uuid4()` 금지, 3.3 `message_id` 선례). |
| `target_id` | `str` | `rule.target_id` → MonitoringTarget. **dedup 차원 #1**(데이터 흐름의 "플랫폼·URL·센터" 식별). |
| `channel_id` | `str` | `rule.channel_id` → MessengerChannel. **dedup 차원 #2(전송 대상)** — AC3 scope 비축소의 핵심. |
| `message_id` | `str` | `message.id` → Message(→ `snapshot_id` → `collected_at`, 3.5가 dedup 차원 `collected_at` 을 조인으로 해석). |
| `messenger` | `Messenger` | `channel.messenger`(TELEGRAM/KAKAO) — 영속/실행 시 라우팅(중앙 Telegram vs Agent Kakao queue). |
| `template_version` | `str` | `message.template_version`. **dedup 차원 #4**. |
| `message_hash` | `str` | `message.text_hash`(=3.1 `message_hash`, sha256(text)). **dedup 차원 #5**. |

- **dedup key(3.5, data-api-contract 172-173) = `target_id + channel_id + collected_at + template_version + message_hash`.** 본 스토리는 이 5개 차원 중 4개(`target_id`·`channel_id`·`template_version`·`message_hash`)를 `DispatchJob` 에 **직접 보존**하고, `collected_at` 은 `message_id`→snapshot 조인으로 도달 가능하게 둔다. **본 스토리는 key를 조립·비교·기록하지 않는다**(3.5 소유) — 차원 보존만으로 AC3(scope 비축소)를 만족한다. [Source: data-api-contract.md(172-173), epics.md Story 3.5(608-611), src/rider_server/services/dispatch_service.py(63)]
- **`text` 를 `DispatchJob` 에 중복 보관하지 않는다(단일 정본 = `Message.text`).** `dispatch_all(message, jobs, send=...)` 가 `message.text` 를 sender에 넘긴다. 같은 Message가 모든 채널로 동일 텍스트로 fan-out("같은 실적 내용을 Telegram·Kakao 모두") — 채널별 텍스트 다중화/tenant 템플릿(`template_id`)은 Epic 5. [Source: epics.md AC(581-583·588), src/rider_server/domain/delivery_rule.py(5-6·19), src/rider_server/domain/message.py]

### AC3 핵심 — `send_only_on_change` scope 비축소를 "channel_id 보존"으로 표현 (놓치기 쉬움)

- **legacy(run_once) scope key:** `app.py:_message_scope_key`(98-118)는 마지막 메시지 해시를 `messenger + platform + coupang_url + peak_url + center_name + center_id + (telegram token/chat/thread | kakao room)` 에 묶는다. project-context.md(92): "scope key를 줄이면 다른 탭/계정의 중복 판단이 섞일 수 있다."
- **신규(ID 모델) 대응:** 그 scope는 ID 모델에서 `target_id`(platform·url·center 식별을 1개 ID로 응축) + `channel_id`(전송 대상 = messenger+chat/room) 로 매핑된다. 따라서 **신규 경로의 scope 비축소 = `DispatchJob` 이 `target_id` 와 `channel_id` 를 둘 다 유지**하는 것이다. 한 Message를 N채널로 fan-out할 때 `channel_id` 를 떨어뜨리거나 `target_id` 로만 dedup하면, 한 채널의 변경-미발송 판단이 다른 채널을 막아 AC2(채널 독립) 도 깨진다. **그래서 AC3는 AC2의 구조적 토대다.** [Source: project-context.md(92), src/rider_crawl/app.py(98-118), data-api-contract.md(172-173)]
- **본 스토리가 하지 않는 것:** 마지막 해시 비교(`_is_duplicate`)·기록(`_write_last_hash`)·`duplicate_blocked` 기록은 **3.5**. `send_only_on_change` 플래그 자체는 `DeliveryRule`(rule)마다 독립값(`delivery_rule.py:21`)이라 3.5/3.6이 rule에서 직접 읽으면 된다 — 본 스토리는 그 값을 `DispatchJob` 으로 옮길 필요조차 없고, **채널 독립성은 `channel_id` 보존만으로 성립**한다. [Source: src/rider_crawl/app.py(43-65), src/rider_server/domain/delivery_rule.py(21), epics.md Story 3.5(600-621)]

### 채널 격리(AC2) — 구조적 contain vs 운영 분류(3.6) 경계

- **본 스토리(구조적):** `dispatch_all` 은 각 `DispatchJob` 전송을 `try/except` 로 감싸 **한 채널의 예외가 루프를 중단시키지 않게** 한다. 실패 채널은 `FanoutOutcome(sent=False, error_redacted=<redacted>)`, 성공 채널은 `sent=True`. "특정 채널 실패가 다른 채널 성공을 무효화하지 않는다"(FR-9)를 **이 격리 + 독립 outcome** 으로 보장한다.
- **3.6(운영 분류 — 하지 않음):** error_code별 분류(`telegram_failure`/`kakao_failure`/`auth_required`), 재시도 가능 vs 사람 개입(`AUTH_REQUIRED`), backoff·circuit breaker, 채널별 상태 영속은 **3.6/Epic 5**. `error_redacted` 는 redaction 통과한 **미분류 breadcrumb** 일 뿐이다(누출 방지용). `FanoutOutcome` 에 error_code/category/retry 필드를 추가하지 않는다(3.6 선점 금지). [Source: epics.md Story 3.6(623-643), architecture.md(323-330·193-195), project-context.md(81)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml`·`domain/` 무변경** — `git diff -w` = `services/dispatch_fanout_service.py`(신규)·`services/__init__.py`(재노출) + 신규 테스트만. (b) **의존성 단방향** — `rider_server → rider_crawl` 만, 역방향 0(ast 가드 `test_rider_crawl_never_imports_rider_server` 통과). (c) **`DispatchService.dispatch`(3.1)·`DeliveryRule`/`MessengerChannel`(2.5)·`Message`(3.3) 무변경** — import·재사용만. (d) **순수·결정적** — `plan`/`dispatch_all` 내부 `datetime.now()`/`uuid4()` 금지(id는 `job_id_for` 주입). (e) **frozen 불변** — `DispatchJob`/`FanoutOutcome` 은 `@dataclass(frozen=True)`. (f) **disabled rule 제외** — `enabled=False` 는 fan-out 대상 아님(soft delete). (g) **fail-closed** — unknown `channel_id` 는 조용히 미전송하지 않고 명확한 예외. (h) **redaction** — `error_redacted` 는 `redact()` 통과. (i) **scope 비축소** — `DispatchJob` 이 `target_id`+`channel_id` 둘 다 보존. [Source: project-context.md(35·36·64·81·82·92), src/rider_server/services/dispatch_service.py(8-16)]

### 이전 스토리 인텔리전스 (Epic 2 → 3.1 → 3.2 → 3.3 → 3.4 이월 교훈)

- **3.1이 본 스토리에 남긴 명시 위임:** `dispatch_service.py` docstring(14행)이 "단일 전송만: DeliveryRule fan-out(1 대상 → N 채널)은 Story 3.4" 라고 못 박았다. 본 스토리는 정확히 그 경계만 채운다 — 3.1 `dispatch` 본문 무변경, fan-out은 별도 서비스. `DispatchResult`(services 소속 값 객체) 선례를 그대로 따라 `DispatchJob` 도 services에 둔다. [Source: src/rider_server/services/dispatch_service.py(8-16·35-48)]
- **3.2/3.3이 깐 변환 패턴 계승:** server-side 순수·결정적 staticmethod(`SnapshotNormalizer.normalize`/`MessageRenderService.render_message`) + 단방향 import + 주입 id/now + frozen dataclass + 회귀-net additive. 본 스토리 `DispatchFanoutService.plan`/`dispatch_all` 도 동형이다(단, domain 모델 미추가라 `domain.__all__` lock은 무변경). [Source: src/rider_server/services/snapshot_normalizer.py(46-65), 3-3 스토리(56-66·129)]
- **무회귀 비결 = "새 필드가 아니라 새 뷰/단위"**(epic-2-retro 64-67·149): 3.2 Snapshot·3.3 Message·3.4 DispatchJob 모두 **기존 코드를 갈아엎지 않고 옆에 레코드/단위를 추가**(재사용·wrapping). 가장 비침습적. [Source: epic-2-retro-2026-06-13.md(64-69·149), 3-3 스토리(130)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2/3.1/3.2/3.3 모두 stale 수치로 MEDIUM 재발(3.3 842/+9 → 846/+13 정정). 기준선 846(3.3 종료, HEAD `d81f027`)은 **참고값**(본인 재측정). [Source: epic-2-retro-2026-06-13.md(49·115), 3-3 스토리(131·205)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 선행 작업으로 권고. dev는 신규 코드·테스트 평문 secret 0건을 **수동 grep**으로 확인(봇토큰/`chat_id=digits`/한국휴대폰/이메일). [Source: epic-2-retro-2026-06-13.md(114·129), 3-3 스토리(132)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/services/dispatch_fanout_service.py`, `tests/server/test_dispatch_fanout.py`. 수정(additive): `src/rider_server/services/__init__.py`(`DispatchFanoutService`/`DispatchJob`/`FanoutOutcome` 재노출 + docstring). `.agents/`·`.claude/`·`_bmad/`·`src/rider_server/domain/`·`src/rider_crawl/` 는 대상 아님. [Source: project-context.md(64), architecture.md(425-428)]
- **`services/` 채움:** architecture(425-428)가 정본 위치 — `services/` 에 `DispatchService`(3.1)·`SnapshotNormalizer`(3.2)·`MessageRenderService`(3.3)와 동거. `idempotency.py`(dedup + insert-then-send, 428)는 **3.5** 가 같은 디렉터리에 additive로 덧붙인다. [Source: architecture.md(425-429), src/rider_server/services/]
- **테스트 위치:** 평면 `tests/server/`(현재 `test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py`·`test_snapshot_normalize.py`)에 `test_dispatch_fanout.py` 추가. `__init__.py` 미추가(평면 컨벤션, basename 고유). [Source: tests/server/, pyproject.toml(testpaths)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]` 로 `rider_server.services`·`rider_server.domain` import 동작(서버 패키징은 Epic 5). [Source: pyproject.toml(pythonpath), 3-3 스토리(140)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.4(579-598)] — Epic 3 의도(한 번 수집 → 정규화 → 여러 채널 fan-out, 중복 없이 채널별 추적), Story 3.4 user story·3 AC 원문(여러 DeliveryRule 연결 시 채널마다 별도 DispatchJob·최소 2채널 fan-out 검증·한 채널 실패가 다른 채널 무효화 안 함·send_only_on_change scope 비축소).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.5~3.8(600-693)·#FR-9(37·162)·#FR-26(66·179)] — 다운스트림 위임처: 3.5 DeliveryLog/idempotency(dedup key 5필드)·3.6 채널별 실패 상태 분리·재시도·3.7 Telegram 중앙·3.8 dry-run; FR-9 fan-out(채널 실패 격리·채널별 상태), FR-26 채널별 전송 이력.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(18·47-52·105)] — 흐름("… Message -> DispatchJob -> DeliveryLog"), **P2-04("Define DeliveryRule that maps one target to multiple messenger channels. | One crawl fans out to at least two channels in test.")**, P2-01(3.1)·P2-05(3.5 dedup)·P2-06(3.6 실패 분리) 위임, "Activate new DeliveryRules only after operator approval"(3.8).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(13·31·33-34·172-173)] — DeliveryRule 모델("Mapping from target snapshot/message to one or more channels"), `delivery_rules`(id, target_id, channel_id, template_id, enabled, send_only_on_change), `jobs`/`delivery_logs`(DispatchJob 영속 매핑), **dedup key 5필드(target_id + channel_id + collected_at + template_version + message_hash=3.5)**.
- [Source: _bmad-output/planning-artifacts/architecture.md(192-195·323-330·417-444·524-526)] — Telegram 중앙 전송/에러 분류(3.6/3.7), 에러 핸들링 카테고리(telegram_failure/kakao_failure/duplicate_blocked), `services/`(CrawlService/MessageRenderService/DispatchService/idempotency) 위치, 데이터 흐름(MessageRenderService → DeliveryRule fan-out → DispatchJob(Telegram=중앙/Kakao=Agent queue) → DeliveryLog(dedup)).
- [Source: src/rider_server/services/dispatch_service.py(1-74)] — 3.1 `DispatchService.dispatch`(무변경 대상)·`DispatchResult`(services 소속 값 객체 선례)·docstring의 3.4 fan-out 명시 위임(14행)·`message_hash = sha256(text)`(63행).
- [Source: src/rider_server/domain/delivery_rule.py(1-22)] — 재사용 대상 `DeliveryRule`(id/target_id/channel_id/template_id/enabled/send_only_on_change), "(target_id, channel_id) 매핑 → 같은 target에 channel 다른 여러 인스턴스로 fan-out 표현"(docstring 1-6).
- [Source: src/rider_server/domain/messenger_channel.py(1-25)·states.py(74-80·105-111)] — `MessengerChannel`(messenger/telegram_chat_id/thread_id/kakao_room_name/state, 라우팅 식별자=secret 아님), `Messenger`(TELEGRAM/KAKAO) — `DispatchJob.messenger` derive 기준.
- [Source: src/rider_server/domain/message.py·__init__.py(34-60)] — `Message`(id/snapshot_id/template_version/text/text_hash) 재사용; `domain.__all__` 10모델 잠금(본 스토리 무변경 — DispatchJob은 services 소속).
- [Source: src/rider_crawl/app.py(43-65·98-118)] — `send_only_on_change` dedup(`_is_duplicate`/`_write_last_hash`)·`_message_scope_key`(scope 비축소 정본) — 본 스토리 0줄 변경, run_once 호환 경로 소유. AC3은 신규 경로 `channel_id` 보존으로 대응.
- [Source: src/rider_crawl/redaction.py(130)] — `redact(text, *, mask_operational_ids=False) -> str`(P0-04 재사용, `error_redacted` 생성).
- [Source: tests/server/test_run_once_split.py(157-189·451)·test_domain_models.py(251-297)] — `DispatchService.dispatch` 테스트 패턴(fake sender·격리)·평면 tests/server/ 자급자족 컨벤션·`domain.__all__` 회귀-net(본 스토리 무변경 확인용).
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-06-13.md(49·64-69·114-115·149)] — "새 뷰/단위" 무회귀 패턴, A1′(secret 게이트)·A2′(수치 단일 정본).
- [Source: _bmad-output/project-context.md(35·36·44·64·81·82·92)] — 순수·결정성, 파서/전송 오류 조용히 기본값 금지(fail-closed), 메신저 adapter 경계, 단방향 의존, secret 비노출, 범위 규율, **send_only_on_change scope key 비축소(92)**.
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P2-04/FR-9(DeliveryRule fan-out 1 대상 → N 채널·채널 실패 격리)·FR-2(기존 자산 재사용·무변경)·NFR-1~4(신뢰성). DeliveryLog/idempotency=3.5, 실패 상태 분류·재시도=3.6, Telegram 중앙=3.7, dry-run=3.8, jobs/delivery_logs 테이블·ORM/async·tenant 템플릿·런타임 교체=Epic 5, Kakao 실전송=Epic 4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, BMAD dev-story workflow)

### Debug Log References

- 전체 스위트(리뷰 시점 재측정, 운영 venv `.venv/Scripts/python.exe -m pytest -q`): **866 passed** — 신규 파일 제외(`--ignore=tests/server/test_dispatch_fanout.py`) 시 정확히 **846 passed**(기준선 HEAD `d81f027` 재확인, +20 = 신규 fan-out 케이스만큼만 증가 → 순수 additive 확인). 신규 파일 단독 **20 passed**. (QA gap 보강 10케이스가 dev-story 10케이스에 additive로 추가되어 총 20 — dev 노트의 잠정 856/+10 수치는 리뷰 재측정 866/+20 로 정정, A2′ 단일 정본.)
- 범위(`git diff -w`): tracked 변경은 `services/__init__.py`(+11, 재노출·docstring)뿐 — 신규 `dispatch_fanout_service.py`·`test_dispatch_fanout.py` 2파일 추가. `src/rider_crawl/`·`src/rider_server/domain/`·`dispatch_service.py`·`snapshot_normalizer.py`·`message_render_service.py`·`pyproject.toml` **0줄 변경**.
- 누출 grep: 신규 코드·테스트에 평문 secret 0건. 유일 매치는 `test_dispatch_fanout.py:189` 의 **의도된 fake**(`chat_id=987654321`·`8:AAE-fake-token-bodyxyz`) — `error_redacted` 가 이를 마스킹함을 단언하는 redaction 증명용(실제 봇 토큰 형태 아님).
- 의존성 방향: `src/rider_crawl/` 에 `rider_server` import 0(ast 가드 `test_rider_crawl_never_imports_rider_server` 통과). 단방향 `rider_server → rider_crawl`(`redact` 재사용)만.

### Completion Notes List

- **순수 additive fan-out 단위 추가(P2-04, FR-9).** 신규 `services/dispatch_fanout_service.py` 에 `DispatchJob`(frozen 값 객체)·`FanoutOutcome`(frozen)·`UnknownChannelError(KeyError)`·`DispatchFanoutService`(`plan`/`dispatch_all` 순수 staticmethod)를 정의. `DispatchResult`(3.1) 선례를 따라 services 레이어에 둠 — `domain/__init__.py`·`domain.__all__`(10모델 lock) 무변경.
- **AC1**: `plan(message, rules, *, channels, job_id_for)` 이 활성 rule마다 별도 `DispatchJob` 을 입력 순서대로 생성(1 Message → N 채널). 각 job은 `id`(주입)·`target_id`·`channel_id`·`message_id`·`messenger`(channel.messenger derive)·`template_version`·`message_hash`(=message.text_hash) 보유. 테스트로 ≥2채널(Telegram+Kakao) fan-out 검증.
- **AC1.2**: `enabled=False` rule은 skip(soft delete 제외).
- **AC2/AC5**: `dispatch_all(message, jobs, *, send)` 이 각 job 전송을 `try/except` 로 격리 — 한 채널 예외가 루프를 막지 않고, 실패 채널은 `FanoutOutcome(sent=False, error_redacted=redact(repr(exc)))`, 성공 채널은 `sent=True` 로 독립 기록. error_code 분류·재시도·`AUTH_REQUIRED` 미수행(3.6 위임), `FanoutOutcome` 에 운영 카테고리 필드 미추가(3.6 미선점). `send` 는 기본 adapter 없는 필수 주입 seam(중앙 Telegram=3.7/Kakao=Epic 4/배선=Epic 5).
- **AC3**: `DispatchJob` 이 `target_id`+`channel_id` 둘 다 보존 → 채널별 dedup 차원 비축소. dedup key는 조립·비교·기록하지 않음(3.5 소유), `rider_crawl/app.py` 의 `_message_scope_key`/`send_only_on_change` 는 0줄 변경.
- **AC4**: 순수·결정적(내부 `uuid4()`/`now()` 미호출 — id는 `job_id_for` 주입)·frozen·단방향 import·fail-closed(unknown channel → `UnknownChannelError`)·redaction 통과. 무회귀(846→866, baseline 재확인 846, +20 = 신규 케이스만큼만).

### File List

- `src/rider_server/services/dispatch_fanout_service.py` (신규) — `DispatchJob`/`FanoutOutcome`/`UnknownChannelError`/`DispatchFanoutService.plan`/`dispatch_all`
- `src/rider_server/services/__init__.py` (수정, additive) — `DispatchFanoutService`/`DispatchJob`/`FanoutOutcome` 재노출 + docstring 1단락 보강
- `tests/server/test_dispatch_fanout.py` (신규) — AC1~AC8 커버 **20케이스**(dev-story 10 + QA gap 보강 10: 빈입력·순서·동일 messenger·체이닝·BaseException 비삼킴)
- `_bmad-output/implementation-artifacts/tests/test-summary-3-4.md` (신규, QA 산출물) — 테스트 자동화 요약(20케이스·866 passed)

## Senior Developer Review (AI)

**리뷰어:** Noah Lee · **일자:** 2026-06-13 · **결과:** Approve (CRITICAL/HIGH 0건) · **방식:** 적대적 코드 리뷰(story-automator-review, auto-fix)

### 검증 요약 (모든 주장 실측 재확인)

- **테스트(운영 venv `.venv/Scripts/python.exe -m pytest -q` 재측정):** 전체 **866 passed**, 신규 파일 제외 시 **846 passed**(기준선 HEAD `d81f027` 재확인), 신규 파일 단독 **20 passed** → 순수 additive **+20**, 회귀 0. 핵심 회귀-net(`test_run_once_split.py`·`test_domain_models.py`·`test_message_render.py`·`test_snapshot_normalize.py`·`test_app.py`) 전부 통과.
- **스코프(`git diff -w --stat`):** tracked 소스 변경은 `services/__init__.py`(재노출·docstring)뿐. 신규 `dispatch_fanout_service.py`·`test_dispatch_fanout.py` 추가. `src/rider_crawl/`·`src/rider_server/domain/`·3.1~3.3 services·`pyproject.toml` **0줄 변경** 확인.
- **의존성 방향:** ast 가드 `test_rider_crawl_never_imports_rider_server` **통과**(역방향 import 0). 단방향 `rider_server → rider_crawl`(`redact` 재사용)만.
- **누출:** 신규 코드·테스트에 신규 평문 secret 0건. 유일 매치는 `test_dispatch_fanout.py:189` 의 **의도된 redaction-proof fake**(`chat_id=987654321`·`8:AAE-fake-token-bodyxyz`) — 테스트가 `error_redacted` 에서 원문이 마스킹됨을 단언(A1′ 충족, 실제 봇 토큰 형태 아님).

### AC 추적성 (8/8 구현·검증)

- **AC1**(채널마다 별도 `DispatchJob`·≥2채널 fan-out·필드 계약·입력 순서 보존) — `plan` 구현·테스트 검증. **PASS**
- **AC1.2**(`enabled=False` soft delete 제외) — `if not rule.enabled: continue`. **PASS**
- **AC2**(채널 격리·한 채널 실패가 다른 채널 무효화 안 함·각 1회 시도·순서 보존) — `dispatch_all` per-job try/except, 3채널 중간 실패·전체 실패 케이스까지 잠금. **PASS**
- **AC2/AC5**(분류 없이 contain·`error_redacted=redact(repr(exc))`·운영 카테고리 필드 미선점) — `FanoutOutcome` 필드 `{job, sent, error_redacted}` 만. **PASS**
- **AC3**(scope 비축소 — `target_id`+`channel_id` 둘 다 보존·동일 messenger도 `channel_id` distinct) — 검증. **PASS**
- **AC4**(순수·결정적·frozen·단방향·fail-closed `UnknownChannelError`·redaction·무회귀) — 846 기준선 무변경. **PASS**

### 적발·자동 수정 사항

| 심각도 | 항목 | 조치 |
|---|---|---|
| 🟡 MEDIUM | **A2′ 잠정 테스트 수치 정본화** — Dev Agent Record가 `856/+10/10케이스`로 stale(QA gap 보강 10케이스 추가분 미반영). 실측 `866/+20/20케이스`. | Debug Log·Completion Notes·File List를 리뷰 재측정값(866/+20/20)으로 정정(단일 정본). |
| 🟢 LOW | QA 산출물 `test-summary-3-4.md` 가 File List에 미기재. | File List에 추가(투명성). |

> **CRITICAL/HIGH 0건.** 구현은 기능적으로 정확하며 스코프 경계(3.5/3.6/3.7/Epic 4/Epic 5 위임)를 정밀하게 준수한다. 유일한 결함은 dev-story 노트의 stale 테스트 수치(Epic 2/3.1~3.3에서 반복된 A2′ 패턴)였고 본 리뷰에서 자동 정정했다.

## Change Log

- 2026-06-13 — Story 3.4 구현(P2-04, FR-9): `DispatchFanoutService`(`plan`=1 Message → N 채널 `DispatchJob` fan-out, `dispatch_all`=채널 격리 전송) + `DispatchJob`/`FanoutOutcome` 값 객체를 services 레이어에 순수 additive로 추가. `services/__init__.py` 재노출. 회귀 0(846→856). Status: ready-for-dev → review.
- 2026-06-13 — Senior Developer Review(AI, story-automator-review): 적대적 리뷰 + auto-fix. CRITICAL/HIGH 0건, Approve. A2′ stale 테스트 수치(856/+10 → 실측 866/+20/20케이스) 정정, `test-summary-3-4.md` File List 기재. 전체 866 passed·기준선 846 재확인·스코프/단방향/누출 게이트 통과. Status: review → done.
