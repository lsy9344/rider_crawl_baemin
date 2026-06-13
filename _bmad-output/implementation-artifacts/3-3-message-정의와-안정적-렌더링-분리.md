---
baseline_commit: 9131862
---

# Story 3.3: Message 정의와 안정적 렌더링 분리

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want Snapshot에서 메시지를 렌더링하는 단계를 수집과 **분리**하고, 신규 도메인 레코드 **`Message`**(`id`·`snapshot_id`·`template_version`·`text`·`text_hash`·`text_redacted_preview`)를 정의하되, 3.1이 만든 **`MessageRenderService.render(snapshot)->str` 는 한 줄도 바꾸지 않고**(run_once parity 보존) 그 옆에 **additive로 `render_message(...)->Message`** 를 추가해 **안정적 `text_hash`(=기존 `message_hash` 와 동일한 `sha256(text)`)** 와 **재수집 없이 재현 가능한 재렌더링**을 제공하고, 동시에 **기존 `render_current_screen_message` renderer 출력은 한 글자도 바꾸지 않고 재사용만** 해서 **의도치 않은 렌더링 변경이 골든 테스트로 실패 식별**되게 하고 싶다,
so that 같은 Snapshot을 **재수집 없이 다시 렌더링·비교**할 수 있어 포맷 변경을 안전하게 검증하고(FR-8, dry-run 비교 FR-3 토대), 이후 **3.4 fan-out**(같은 Message를 N 채널로)·**3.5 DeliveryLog dedup key**(`template_version + message_hash` = 본 스토리의 `text_hash`)·**Epic 5 `messages` 테이블 영속**(`text_redacted_preview`)이 이 **Message 계약** 위에 additive로 빌드된다(P2-03, FR-8, FR-2).

> **이 스토리의 성격 — "Snapshot→Message 렌더 단계를 정규화 도메인 레코드 `Message`(snapshot_id·template_version·text·text_hash·text_redacted_preview)로 정의 + 안정적 hash·재현 가능 재렌더링, 그것만."** 3.1이 `run_once`를 세 서비스로 **구조 분리**했고(`MessageRenderService.render`는 텍스트 str만 반환), 3.2가 수집 결과를 정규화 도메인 레코드 **`Snapshot`** 으로 승격했다. 본 스토리는 그 렌더 단계를 **도메인 레코드 `Message`** 로 승격하고 **안정적 `text_hash`** 와 **재현성**(같은 입력 → 같은 Message)을 추가한다. 본 스토리는 **DeliveryRule fan-out=3.4, DeliveryLog/idempotency dedup key=3.5, 수집/전송 실패 상태 분류·재시도=3.6, Telegram 중앙 전송=3.7, dry-run 비교·승인 활성화=3.8, tenant-level 템플릿 선택·`messages` 테이블/ORM/Alembic·Pydantic 스키마·async wiring·런타임 교체=Epic 5, Kakao 실제 전송=Epic 4** 를 끌어오지 않는다. [Source: epics.md Epic 3(511-513)·Story 3.3(558-577)·Story 3.4~3.8(579-693), implementation-contract.md P2-03(49), data-api-contract.md(15·33), 3-2 스토리(17·22), 3-1 스토리(22·62-64)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 도메인 모듈 `domain/message.py` + 기존 `services/message_render_service.py`에 **`render_message` 메서드·template_version 상수 additive 추가**(기존 `render` 메서드 무변경) + `domain/__init__.py` 재노출 추가 + 신규 테스트 `tests/server/test_message_render.py` + 회귀-net `tests/server/test_domain_models.py`(`domain.__all__` 잠금에 10번째 모델 additive 반영). 아래는 **다른 스토리/에픽 소유 — 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `message.py`(renderer)/`models.py`/`parser.py`/`app.py`/`redaction.py` 등 어떤 파일도 수정하지 않는다. 본 스토리는 그것들을 **import해서 재사용·wrapping만** 한다. **이유 1(렌더링 결과 보존 — AC3):** `render_message`는 기존 `render_current_screen_message`(`message.py` 35)를 **재구현 없이 그대로 호출**하므로 배민/쿠팡 메시지 텍스트가 의도치 않게 바뀌면 안 된다(FR-2, "의도 없이 렌더링 결과가 바뀐 경우 실패로 식별"=AC3). **이유 2(의존성 방향 — 절대 규칙):** `rider_server` → `rider_crawl` import만 허용, 역방향 `rider_crawl` → `rider_server` 금지(project-context.md 64, architecture.md 482). **이유 3(회귀 그물):** `tests/test_message.py`·`tests/test_coupang_message.py`·`tests/test_app.py`가 renderer/run_once 출력을 `==`로 잠갔다 — rider_crawl을 건드리면 깨진다. (architecture.md 397이 "message.py renderer (template_version 추가)"라 적었으나, 3.2의 `parser_version` 선례대로 **버전 상수는 server-side**에 두고 rider_crawl은 무변경한다 — 아래 Dev Notes "template_version 위치" 참조.) [Source: project-context.md(36·64·82), architecture.md(397·482), src/rider_crawl/message.py(35-43), tests/test_message.py·test_coupang_message.py]
> - **`MessageRenderService.render(snapshot, *, source_label="") -> str` 시그니처·본문 무변경(3.1 parity 보존).** 3.1 `render`는 텍스트 str만 반환하고 그 위에서 run_once parity(`message`/`sent`/`message_hash` 동등)가 잠겨 있다 — `render`가 `Message`를 반환하도록 바꾸면 3.1 parity 테스트(`tests/server/test_run_once_split.py`)와 `tests/test_app.py`가 깨진다. 본 스토리는 `render`를 **그대로 두고** `render_message`를 **별도 seam**으로 additive하게 붙인다. 런타임에서 crawl→normalize→render_message→dispatch를 잇는 wiring은 **Epic 5**다. [Source: 3-1 스토리(40·62-64·173), src/rider_server/services/message_render_service.py(27-30)]
> - **`DeliveryRule` fan-out(1 대상 → N 채널)** → **3.4**(P2-04). 본 스토리는 **단일 Snapshot → 단일 Message**만 만든다(fan-out 없음). [Source: epics.md Story 3.4(579-598)]
> - **`DeliveryLog`·idempotency dedup key(`monitoring_target_id + channel_id + snapshot_collected_at + template_version + message_hash`)·insert-then-send** → **3.5**(P2-05, ADD-5). 본 스토리는 `text_hash`(=`message_hash`)·`template_version`을 **정의·계산만** 하고 dedup key를 **조립하지 않는다**. [Source: epics.md Story 3.5(600-621), data-api-contract.md(34·172-173)]
> - **수집/렌더/전송 실패 상태 분류·재시도·`render_failure` 등 운영 카테고리** → **3.6**(P2-06, FR-11). 본 스토리는 렌더 **성공 경로**(유효 Snapshot → Message)만 다루고, 렌더 실패를 운영 상태값으로 분류·기록하지 않는다. [Source: epics.md Story 3.6(623-643), architecture.md(324)]
> - **tenant-level 템플릿 선택·`messages` 테이블/SQLAlchemy ORM/Alembic·Pydantic 스키마·async wiring·런타임 교체** → **Epic 5**(P4-02). 본 스토리는 **순수 dataclass 도메인 + 순수 동기 렌더 함수 + 테스트만**, **런타임 미배선**이다(2.5/2.6/3.1/3.2와 동일 — `rider_server`는 정의만, UI는 계속 `run_once` 사용). template_version은 **플랫폼별 server-side 상수**로 두고, tenant별 템플릿 다중화는 Epic 5+가 같은 seam에 additive로 얹는다. [Source: architecture.md(417-426·514-517), implementation-contract.md(9·71), 3-2 스토리(25)]
>
> **순수·결정적·의존성 0(2.5/2.6/3.1/3.2 토대 제약 계승).** `Message` dataclass와 `render_message` 는 FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬이다. **`render_message` 내부에서 `datetime.now()`/`uuid4()` 를 호출하지 않는다** — `id`(message_id)는 호출부 주입, **시각(`now`)은 인자로 받아 렌더러에 전달**한다(2.5 `Tenant.created_at`·3.2 `Snapshot.collected_at`·`snapshot_id` 주입 선례). [Source: src/rider_server/domain/tenant.py(16), src/rider_server/services/snapshot_normalizer.py(55-65), project-context.md(35)]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** `Message.text_redacted_preview` 는 기존 `redaction.redact` 를 통과시켜 만든다(P0-04 재사용·defense-in-depth). 테스트 fixture·예외 메시지에 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 렌더 메시지 본문은 실적 수치·센터 라벨뿐이라 평문 secret이 없지만, `text_redacted_preview`는 영속·표시용이므로 redaction을 의무화한다. [Source: project-context.md(81), src/rider_crawl/redaction.py(130), architecture.md(183-184), 3-2 스토리(31)]

## Acceptance Criteria

**AC1 — 정규화 `Message` 레코드 정의 + 안정적 `text_hash` (P2-03, FR-8)**

1. **Given** 유효한 Snapshot(배민 `CurrentScreenSnapshot` 또는 쿠팡 `PerformanceSnapshot`)이 있을 때 **When** `MessageRenderService.render_message(...)` 로 Message를 생성하면(P2-03, FR-8) **Then** `Message`(frozen dataclass)는 **`id`(str), `snapshot_id`(str), `template_version`(str), `text`(str), `text_hash`(str), `text_redacted_preview`(str)** 필드를 갖는다(data-api-contract `messages` Required fields + P2-03의 `text`). [Source: epics.md AC(566-568), data-api-contract.md(15·33), implementation-contract.md P2-03(49)]
2. **And** `text_hash` 는 **`hashlib.sha256(text.encode("utf-8")).hexdigest()`** 로 계산된다 — 이는 3.1 `DispatchResult.message_hash`/`run_once` 와 **동일한 계산**이라 같은 텍스트면 `text_hash == message_hash` 이고, 이 동일성이 3.5 dedup key(`… + template_version + message_hash`)의 정합을 보장한다. [Source: 3-1 스토리(67·104), src/rider_server/services/dispatch_service.py, data-api-contract.md(172-173)]
3. **And** **동일 raw Snapshot + 동일 `template_version`(+ 동일 `source_label`·`now`)** 은 **동일한 `text`·`text_hash`** 를 만든다(결정적) — `render_message` 가 내부에서 `datetime.now()`/`uuid4()` 를 호출하지 않아 같은 입력이면 같은 출력이다. (쿠팡은 `now` 가 피크 시간표 선택에 영향을 주므로 — Dev Notes "쿠팡 `now` 결정성" — 같은 `now` 가 같은 hash의 전제다.) [Source: epics.md AC(569), implementation-contract.md P2-03(49 "Same snapshot creates same hash"), src/rider_crawl/message.py(27-43), project-context.md(35)]

**AC2 — 재수집 없이 재렌더링 재현 + 수집 로직 무수정 포맷 검증 (FR-8)**

4. **Given** 같은 raw Snapshot을 다시 렌더링할 때 **When** **재수집(Crawl/정규화) 없이** `render_message` 를 같은 인자로 두 번 호출하면 **Then** 두 `Message` 의 `text`·`text_hash` 가 **동일**하다(재현성) — 수집을 다시 하지 않고도 같은 Snapshot에서 같은 메시지를 얻는다. [Source: epics.md AC(571-573), implementation-contract.md P2-03(49)]
5. **And** **수집 로직(`CrawlService`/`SnapshotNormalizer`) 0줄 변경**으로 포맷/`template_version` 변경을 검증할 수 있다 — `render_message` 는 **raw Snapshot + 주입 인자만** 받는 순수 함수라 렌더 단계가 수집과 완전히 분리된다. [Source: epics.md AC(573 "수집 로직 수정 없이 포맷 변경을 검증"), 3-1 스토리(62)]

**AC3 — 기존 renderer 결과 호환: 의도치 않은 렌더링 변경을 실패로 식별 (FR-2, FR-3 토대)**

6. **Given** 기존 renderer 결과 호환이 필요할 때 **When** `render_message` 가 `text` 를 만들면 **Then** 그 `text` 는 `render_current_screen_message(snapshot, source_label=source_label, now=now)`(기존 renderer) 출력과 **바이트 단위로 동일**(재구현 0)하고, 배민 골든 fixture(기존 `tests/test_message.py` 와 동일 텍스트)·쿠팡(고정 `now`) 텍스트가 **잠겨**, 신규 렌더링 결과가 기존 결과와 다르면(의도치 않은 렌더링 변경) **테스트가 실패로 식별**한다. [Source: epics.md AC(575-577), src/rider_crawl/message.py(35-43), tests/test_message.py(5-42)]
7. **And** `src/rider_crawl/` **0줄 변경**(`git diff -w --stat`)으로 기존 renderer/run_once 회귀 그물(`tests/test_message.py`·`tests/test_coupang_message.py`·`tests/test_app.py`)이 **전부 그대로 통과**하고, **`MessageRenderService.render`(3.1) 시그니처·본문도 무변경**(3.1 run_once parity 보존: `tests/server/test_run_once_split.py` 통과)이며, 본 스토리는 신규 Message 케이스만큼만 테스트 수가 증가한다(순수 additive). [Source: project-context.md(58·82), 3-1 스토리(40·173), 3-2 스토리(49)]

## Tasks / Subtasks

- [x] **Task 1 — `Message` 도메인 모델 정의: `domain/message.py` (AC: 1)** — `snapshot.py`/`tenant.py` dataclass 패턴(`@dataclass(frozen=True)` + `from __future__ import annotations`):
  - [x] 필드(모두 필수·기본값 없음 — dataclass 순서 규칙): `id: str`, `snapshot_id: str`(→ Snapshot FK), `template_version: str`, `text: str`(전송용 전체 렌더 텍스트), `text_hash: str`(`sha256(text)`), `text_redacted_preview: str`(영속·표시용 redaction 통과 미리보기). 모두 `str` — **enum/`rider_crawl` import 불필요**(Snapshot보다 단순). [Source: data-api-contract.md(33·15), epics.md AC(566-568), src/rider_server/domain/snapshot.py(19-34)]
  - [x] **`domain/`은 순수 유지 — `rider_crawl` import 금지.** `Message`는 표준 라이브러리만 쓴다(필드 전부 `str`이라 `typing.Any` 도 불필요). raw Snapshot→`text`/`text_hash`/`preview` **변환(bridge)** 은 Task 2의 서비스가 담당한다(레이어 분리: domain=순수 레코드, services=정책/변환). [Source: 3-2 스토리(58·91), src/rider_server/domain/snapshot.py(1-8)]
  - [x] 모듈 상단 docstring으로 책임(P2-03 Message 레코드)·위임처(fan-out=3.4, dedup=3.5, 영속/ORM/tenant 템플릿=Epic 5)·`text` vs `text_redacted_preview` 분리 의도를 1~2줄로 남긴다. [Source: src/rider_server/domain/snapshot.py(1-8)]
- [x] **Task 2 — `MessageRenderService.render_message` additive 추가: `services/message_render_service.py` (AC: 1, 2, 3)** — 기존 `render` 메서드는 **무변경**:
  - [x] **template_version 상수(server-side):** `_BAEMIN_TEMPLATE_VERSION = "baemin.realtime.v1"`, `_COUPANG_TEMPLATE_VERSION = "coupang.realtime.v1"`. rider_crawl 렌더러에 버전 필드가 없으므로 server-side에 둔다(rider_crawl 무변경 — 3.2 `parser_version` 선례와 동형). 렌더 포맷이 바뀌면 bump. (parser_version과 **별개 축**: parser_version=수집 출력 shape, template_version=메시지 포맷.) [Source: src/rider_server/services/snapshot_normalizer.py(46-49), implementation-contract.md(9), architecture.md(397)]
  - [x] **미리보기 상수:** `_PREVIEW_MAX_CHARS = 500`(영속·표시용 길이 cap). [Source: data-api-contract.md(33 "text_redacted_preview")]
  - [x] **`@staticmethod def render_message(snapshot: CrawlSnapshotResult, *, message_id: str, snapshot_id: str, source_label: str = "", now: datetime | None = None) -> Message`** 로 둔다(순수·결정적; 내부 `now()`/`uuid4()` 금지 — `id`·`now` 는 인자). [Source: src/rider_server/services/snapshot_normalizer.py(55-65), project-context.md(35)]
  - [x] **렌더 로직:** (1) `type(snapshot)` 으로 `template_version` derive: `CurrentScreenSnapshot` → `_BAEMIN_TEMPLATE_VERSION`, `PerformanceSnapshot` → `_COUPANG_TEMPLATE_VERSION`, 그 외 → `raise TypeError`(정규화 통과 후라 정상은 미발생 — 방어적). (2) **`text = render_current_screen_message(snapshot, source_label=source_label, now=now)`**(기존 renderer 재사용·재구현 금지 — AC3). (3) `text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()`(3.1 `message_hash` 와 동일 계산 — AC1.2). (4) `text_redacted_preview = redact(text)[:_PREVIEW_MAX_CHARS]`(P0-04 재사용). (5) `return Message(id=message_id, snapshot_id=snapshot_id, template_version=template_version, text=text, text_hash=text_hash, text_redacted_preview=text_redacted_preview)`. [Source: src/rider_crawl/message.py(35-43), src/rider_crawl/redaction.py(130), epics.md AC(566-577)]
  - [x] **import은 단방향만:** `import hashlib`, `from datetime import datetime`, `from rider_crawl.message import render_current_screen_message`(기존 import 유지), `from rider_crawl.models import CrawlSnapshotResult, CurrentScreenSnapshot, PerformanceSnapshot`, `from rider_crawl.redaction import redact`, `from rider_server.domain import Message`. 역방향(`rider_crawl` → `rider_server`) 코드는 추가하지 않는다. [Source: project-context.md(64), src/rider_server/services/snapshot_normalizer.py(20-35)]
  - [x] **기존 `render(snapshot, *, source_label="") -> str` 메서드 무변경**(3.1 parity). 모듈 docstring의 "Message dataclass … → Story 3.3" 위임 문구(9-10행)를 "Story 3.3가 `render_message`/`Message` 로 구현"으로 1줄 정합 정정(같은 파일 내 — 스코프 내). [Source: src/rider_server/services/message_render_service.py(8-14·27-30)]
- [x] **Task 3 — 재노출 갱신: `domain/__init__.py` (AC: 1)**
  - [x] `domain/__init__.py`에 `Message`를 **additive로** import·`__all__` 추가(2.5 8모델 + 3.2 Snapshot 무삭제, 10번째 모델). → `from rider_server.domain import Message`. 주석으로 "Story 3.3 — Message 렌더 레코드(10번째)" 표기(3.2 Snapshot 주석 형식 따름). [Source: src/rider_server/domain/__init__.py(17·43-44)]
  - [x] `services/__init__.py` 는 **신규 export 심볼 없음**(`MessageRenderService` 이미 재노출, `Message` 는 domain 소속). docstring에 "Story 3.3가 `MessageRenderService.render_message`/`Message`(렌더 레코드+안정적 hash)를 additive로 추가" 1줄만 보강(선택 — 추적성). `__all__` 변경 없음. [Source: src/rider_server/services/__init__.py(1-39)]
- [x] **Task 4 — 회귀-net 갱신: `tests/server/test_domain_models.py` (AC: 1, 7)**
  - [x] `test_package_all_reexports_eight_models_and_all_enums` 의 `expected` 집합과 `model_names` 집합에 **`Message` 를 additive로** 추가한다(3.2가 Snapshot을 9번째로 추가했던 것과 동일 — 이 테스트가 `domain.__all__` 을 **정확히** 잠그므로 미반영 시 1건 실패하나 이는 회귀가 아니라 계약의 additive 확장). 주석 "Story 3.3 — Message 렌더 레코드(10번째)". [Source: tests/server/test_domain_models.py(251-294), 3-2 스토리(178)]
- [x] **Task 5 — 테스트 추가: `tests/server/test_message_render.py` (AC: 1~7)** — 외부 호출 없음(fake/in-memory), 가짜 값만. 평면 `tests/server/`에 두고(`__init__.py` 미추가 — 기존 컨벤션), `tests/test_message.py`·`test_app.py`·`test_snapshot_normalize.py` 의 배민/쿠팡 fixture를 재사용·재구성:
  - [x] **(AC1·AC3 happy path — 필드·골든 동등):** 배민 `CurrentScreenSnapshot` + 쿠팡 `PerformanceSnapshot` 각각을 `render_message(raw, message_id="msg-1", snapshot_id="snap-1", source_label="센터", now=<고정 datetime>)` 하면 `Message` 필드(`template_version`=각 상수, `text_hash`=`sha256(text)`, `snapshot_id`/`id` 보존)가 기대대로이고, `Message.text == render_current_screen_message(raw, source_label="센터", now=<같은 고정 datetime>)`(바이트 동등 — 재구현 0). 배민은 기존 `tests/test_message.py` 의 골든 텍스트와 동일함을 추가 단언(now 무관). [Source: epics.md AC(566-577), tests/test_message.py(5-42), src/rider_crawl/message.py(35-43)]
  - [x] **(AC1.2 hash 정합):** `Message.text_hash == hashlib.sha256(Message.text.encode("utf-8")).hexdigest()` 이고, **3.1 `DispatchService.dispatch(config, Message.text, send_message=fake).message_hash == Message.text_hash`** 임을 단언(3.5 dedup 정합의 토대). [Source: 3-1 스토리(67·104), src/rider_server/services/dispatch_service.py]
  - [x] **(AC1.3·AC2 결정성·재현성):** 같은 raw + 같은 인자(같은 `now`)로 **두 번** `render_message` 하면 `text`·`text_hash`·`template_version` 동일(내부 `now()`/`uuid4()` 미호출 — 결정적). 쿠팡은 `now` 가 다르면(주중 vs 주말 고정 datetime 2개) 피크 시간표가 달라 `text`/`text_hash` 가 달라짐을 함께 보여 `now` 결정성을 명시. [Source: epics.md AC(569·571-573), src/rider_crawl/message.py(27-32)]
  - [x] **(AC1 frozen):** `Message` 가 `frozen`(`with pytest.raises(FrozenInstanceError): msg.text = ...`). [Source: 3-2 스토리(73), src/rider_server/domain/snapshot.py(19)]
  - [x] **(AC3 unexpected type 방어):** `render_message(object(), message_id=..., snapshot_id=...)` → `pytest.raises(TypeError)`(예상 외 타입 — 정규화 후라 정상 미발생, 방어 단언). [Source: src/rider_crawl/message.py(41-43)]
  - [x] **(누출·redaction):** `text_redacted_preview` 는 `redact(text)[:500]` 결과와 일치하고, fixture는 가짜 값(`"msg-1"`·`"snap-1"`·`"센터"`·고정 `datetime(2026,1,5,...)`(월)·`datetime(2026,1,3,...)`(토)·기존 센터명 문자열)만. 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/`chat_id=<digits>`/한국 휴대폰/이메일 원문 금지. [Source: project-context.md(55·81), src/rider_crawl/redaction.py(130), 3-2 스토리(74)]
- [x] **Task 6 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 **~833** — HEAD `9131862`(3.2 종료) 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_message.py`·`tests/test_coupang_message.py`·`tests/test_app.py`·`tests/server/test_run_once_split.py`·`test_snapshot_normalize.py`·`test_domain_models.py`) 신규 Message 케이스만큼만 증가가 정상(순수 additive). [Source: 3-2 스토리(76·187), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `domain/message.py`·`tests/server/test_message_render.py` + `services/message_render_service.py`(render_message 추가)·`domain/__init__.py`(재노출)·`tests/server/test_domain_models.py`(회귀-net) + (선택)`services/__init__.py`(docstring)만** 보이고 **`src/rider_crawl/`·`pyproject.toml` 변경 0줄**, **`MessageRenderService.render`(3.1) 본문 무변경**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), 3-1 스토리(40), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건, 그리고 `src/rider_crawl/`에 `rider_server` import가 **새로 생기지 않았음**(ast 기반 권장 — 단순 문자열 grep은 docstring 오탐, 3-1 Debug Log 참고)을 확인. [Source: project-context.md(64·81), 3-1 스토리(164)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: epic-2-retro-2026-06-13.md(115), 3-2 스토리(79)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `domain/message.py`·`tests/server/test_message_render.py` + `services/message_render_service.py`(`render_message` 메서드·상수 추가, **기존 `render` 무변경**)·`domain/__init__.py`(재노출 추가)·`tests/server/test_domain_models.py`(회귀-net) + (선택)`services/__init__.py`(docstring). **`src/rider_crawl/`·`pyproject.toml` 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(renderer/models/redaction — 보존·재사용만), `MessageRenderService.render`(3.1 parity), `CrawlService`/`SnapshotNormalizer`(수집 — 무관), DeliveryRule fan-out(3.4), DeliveryLog/idempotency/dedup key(3.5), 렌더 실패 상태 분류·재시도(3.6), Telegram 중앙(3.7), dry-run·승인(3.8), tenant 템플릿 다중화·messages 테이블/ORM/Alembic/Pydantic·async·런타임 교체(Epic 5), Kakao 실제 전송(Epic 4). [Source: epics.md Story 3.4~3.8(579-693), architecture.md(417-426·514-517), implementation-contract.md(49-52)]

### 위치·레이어 결정 — 왜 도메인 모델(domain/)과 렌더 서비스(services/)를 분리하나 (반드시 읽을 것)

- **`Message`는 도메인 레코드 → `domain/message.py`(architecture 정본).** architecture.md(419)가 `rider_server/domain/{… snapshot.py / message.py / delivery.py}`를 명시하고, data-api-contract(15·33)가 Message를 핵심 모델/`messages` 테이블로 둔다. 2.5가 8모델, 3.2가 9번째 `Snapshot`을 깔았고, 본 스토리가 같은 디렉터리에 **10번째 `Message`** 를 additive로 채운다. [Source: architecture.md(417-419), data-api-contract.md(15·33), src/rider_server/domain/__init__.py(17·43-44)]
- **렌더(bridge)는 서비스 → `services/message_render_service.py`(기존, 3.1).** 렌더는 `rider_crawl.message.render_current_screen_message`·`rider_crawl.redaction.redact`(rider_crawl 함수)를 import해 도메인 `Message`로 변환하는 **rider_crawl↔domain 브리지**다. 도메인 모델은 의도적으로 **rider_crawl-free**(순수 레코드)이므로 이 import 결합을 `domain/message.py`에 넣으면 도메인 레이어 순수성이 깨진다. 따라서 변환 로직은 **서비스 레이어**에 둔다(3.2 `SnapshotNormalizer`와 동형: `domain`=순수 레코드, `services`=정책/변환). [Source: 3-2 스토리(91), src/rider_server/services/snapshot_normalizer.py(1-18), src/rider_server/domain/snapshot.py(1-8)]
- **`render`(str) 무변경 + `render_message`(Message) 추가 = 3.1 parity 보존.** 3.1 `render`는 텍스트 str을 반환하고 그 위에서 run_once parity가 잠겨 있다(`tests/server/test_run_once_split.py`·`tests/test_app.py`). `render`를 `Message` 반환으로 바꾸면 parity가 깨지므로 **별도 메서드**(`render_message`)로 둔다. 두 메서드는 같은 `render_current_screen_message` 를 호출하므로 `render_message(...).text == render(snapshot, source_label=...)`(같은 now·source_label일 때) — 텍스트 정본은 하나다. [Source: 3-1 스토리(40·62-64·173), src/rider_server/services/message_render_service.py(27-30)]

### `Message` 필드 ↔ 계약 매핑 (AC1 — 정밀 계약)

| 필드 | 타입 | 출처/근거 |
|---|---|---|
| `id` | `str` | data-api-contract `messages.id`(33). 호출부 주입(`message_id` 인자 — 서비스 내부 `uuid4()` 금지). |
| `snapshot_id` | `str` | `messages.snapshot_id`(33) → Snapshot FK. 호출부 주입(어느 Snapshot에서 렌더됐는지 추적). |
| `template_version` | `str` | `messages.template_version`(33) + P2-03. 플랫폼별 server-side 상수(rider_crawl에 버전 없음). |
| `text` | `str` | **P2-03 명시 필드**(49 "Define Message with … text …"). 전송용 전체 렌더 텍스트(영속 컬럼엔 없으나 dispatch/재렌더 비교에 필수 — 2.5 `center_name`·3.2 `platform` 처럼 "계약 bare table + spec/AC 요구" 추가). |
| `text_hash` | `str` | `messages.text_hash`(33). `sha256(text)` — 3.1 `message_hash` 와 동일 계산(3.5 dedup 정합). |
| `text_redacted_preview` | `str` | `messages.text_redacted_preview`(33). `redact(text)[:500]`(영속·표시용·redaction 통과). |

- **`text` vs `text_redacted_preview` 분리 근거:** 계약 `messages` 테이블은 영속 컬럼으로 **`text_redacted_preview`(redaction 통과 미리보기)만** 두고 전체 `text` 는 두지 않는다(원문 영속 최소화·NFR-5). 하지만 P2-03와 dispatch/재렌더 비교는 **전체 `text`** 가 필요하다. 그래서 도메인 `Message`는 둘 다 보유한다 — `text`(런타임 전송·hash·비교용, Epic 5에서 영속 제외 가능) + `text_redacted_preview`(영속·Admin 표시용). 3.2가 bare table에 없는 `platform`/추적 필드를 AC 요구로 추가한 패턴과 동형. [Source: data-api-contract.md(33), implementation-contract.md(49), 3-2 스토리(100·105), architecture.md(183-184)]

### template_version 위치 — 왜 server-side 상수인가 (rider_crawl 무변경)

- architecture.md(397)가 "`message.py` renderer (template_version 추가)"라 적었지만, 본 스토리는 **3.2 `parser_version` 선례**를 따라 **버전 상수를 server-side(`services/message_render_service.py`)** 에 두고 **`rider_crawl/message.py` 는 0줄 변경**한다. 이유: (1) **의존성 단방향**(rider_crawl→rider_server 금지) — 버전을 rider_crawl에 넣으면 도메인 어휘가 server 쪽으로 새어 레이어가 흐려진다. (2) **회귀 그물 보존** — `tests/test_message.py` 가 renderer 출력을 `==`로 잠갔으므로 renderer 시그니처/반환을 바꾸면 깨진다. (3) **3.2 동형** — `parser_version` 도 rider_crawl에 필드가 없어 `snapshot_normalizer.py` 의 server-side 상수(`baemin.current_screen.v1`)로 뒀다. template_version도 `services` 상수로 둔다. tenant-level 템플릿 다중화(implementation-contract 9)는 Epic 5+가 이 seam에 additive로 얹는다. [Source: architecture.md(397·482), src/rider_server/services/snapshot_normalizer.py(46-49), tests/test_message.py(5-42), implementation-contract.md(9)]
- **parser_version vs template_version 은 별개 축:** `parser_version`(3.2)=수집 출력 shape 버전(`baemin.current_screen.v1`), `template_version`(3.3)=메시지 포맷 버전(`baemin.realtime.v1`). 둘을 섞지 말 것 — Snapshot은 parser_version, Message는 template_version을 갖는다. [Source: data-api-contract.md(32·33), src/rider_server/services/snapshot_normalizer.py(48-49)]

### 쿠팡 `now` 결정성 — text_hash 안정성의 전제 (AC1.3·AC2 — 핵심, 놓치기 쉬움)

- **`render_current_screen_message` 의 쿠팡 경로는 `now` 에 의존한다.** `_render_performance_message` → `_peak_times(now=now)` 가 `now.weekday()` 로 **주중/주말 피크 시간표**(`WEEKDAY_PEAK_TIMES`/`WEEKEND_PEAK_TIMES`)를 고른다. `now=None` 이면 렌더러 내부에서 `datetime.now()` 를 호출한다(`message.py` 29). 즉 **같은 쿠팡 Snapshot도 렌더 시각(주중 vs 주말)에 따라 `text` 가 달라져 `text_hash` 가 달라진다.** [Source: src/rider_crawl/message.py(27-32·69-98)]
- **그래서 본 스토리가 `now` 주입을 소유한다(3.1이 "렌더러의 `now` 인자를 호출부가 채우는 Story 3.3 영역"이라 위임).** `render_message(..., now=...)` 로 `now` 를 받아 렌더러에 전달한다. **AC1.3 "동일 Snapshot+동일 template_version → 동일 hash" 는 동일 `now` 전제에서 성립**한다(쿠팡). **재현 가능한 재렌더링(AC2)·Epic 5 영속**을 위해 호출부는 `now=snapshot.collected_at`(또는 기록된 렌더 시각)을 주입하는 것을 권장한다 — 그래야 같은 Snapshot이 항상 같은 Message를 낸다. 배민 경로는 `now` 무관(시간표 미사용)이라 항상 결정적이다. [Source: 3-1 스토리(62·174), src/rider_crawl/message.py(35-43), src/rider_server/domain/snapshot.py(24)]
- **서비스 순수성 유지:** `render_message` 자체는 `datetime.now()` 를 **호출하지 않는다**(인자로 받은 `now` 만 전달). `now=None` 일 때 렌더러가 `now()` 를 부르는 것은 **기존 renderer의 보존된 동작**이고, 본 서비스가 새 비결정성을 도입하는 게 아니다. 결정적 hash가 필요한 호출부/테스트는 반드시 `now` 를 주입한다. [Source: project-context.md(35), src/rider_crawl/message.py(29)]

### text_hash = sha256(text) — 3.5 dedup 정합 (AC1.2)

- **계산식은 3.1 `message_hash` 와 동일**: `hashlib.sha256(text.encode("utf-8")).hexdigest()`. 3.1 `DispatchService.dispatch` 와 `run_once` 가 같은 식으로 `message_hash` 를 만든다. 본 스토리가 같은 식을 쓰면 **같은 텍스트 → `Message.text_hash == DispatchResult.message_hash`** 가 성립한다. [Source: 3-1 스토리(67·104), src/rider_server/services/dispatch_service.py]
- **왜 중요한가(3.5 토대):** data-api-contract dedup key(172-173)는 `target_id + channel_id + collected_at + template_version + **message_hash**`. 3.5가 이 key를 조립할 때 `message_hash` 자리에 본 스토리의 `text_hash` 를 그대로 쓸 수 있어야 한다. 두 계산이 달라지면 3.5 dedup이 어긋난다. **본 스토리는 key를 조립하지 않고**(3.5 소유) `text_hash` 의 계산 정합만 보장한다. [Source: data-api-contract.md(172-173·34), epics.md Story 3.5(600-621)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml` 무변경** — `git diff -w` = `domain/`·`services/` 신규/추가 + 신규/회귀-net 테스트만. (b) **의존성 단방향** — `rider_server → rider_crawl`만, 역방향 0(ast 가드 권장). (c) **`MessageRenderService.render`(3.1) 무변경** — run_once parity 보존(`render_message` 는 별도 메서드). (d) **렌더 출력 동등** — `render_message(...).text == render_current_screen_message(...)`(재구현 0, AC3). (e) **순수·결정적** — `render_message` 내부 `datetime.now()`/`uuid4()` 금지(인자 주입; `now=None` 시 렌더러의 기존 now() 동작은 보존). (f) **frozen 불변** — `Message`는 `@dataclass(frozen=True)`. (g) **hash 정합** — `text_hash = sha256(text)`(3.1 message_hash와 동일식). (h) **redaction** — `text_redacted_preview` 는 `redact()` 통과. [Source: project-context.md(35·64·82), src/rider_crawl/message.py(35-43), 3-1 스토리(40)]

### 이전 스토리 인텔리전스 (Epic 2 → 3.1 → 3.2 → 3.3 이월 교훈)

- **3.1이 본 스토리에 남긴 명시 위임:** `message_render_service.py` docstring(8-10·12-14행)·3-1 범위 노트(22·62-64)가 "Message dataclass(snapshot_id/template_version/text/text_hash)·안정적 hash·재렌더링 비교 = **Story 3.3**", "렌더러의 `now` 인자를 호출부가 채우는 = Story 3.3 영역"이라 못 박았다. 본 스토리는 정확히 그 경계만 채운다 — 3.1 `render` 본문은 무변경. [Source: src/rider_server/services/message_render_service.py(8-14), 3-1 스토리(22·62-64·174)]
- **3.2가 깐 변환 패턴 그대로 계승:** 3.2 `SnapshotNormalizer`(raw→`Snapshot` + server-side `parser_version` 상수 + 순수·결정적 + 단방향 import)와 **동형**으로 `render_message`(raw→`Message` + server-side `template_version` 상수 + 순수·결정적 + 단방향)를 만든다. domain/services 레이어 분리, frozen dataclass, `domain.__all__` 회귀-net additive 갱신까지 같은 절차. [Source: 3-2 스토리(56-68·91·178)]
- **무회귀 비결 = "새 필드가 아니라 새 뷰/레코드"**(epic-2-retro 64-67·149): 3.2 `Snapshot`·3.3 `Message` 모두 **기존 renderer 출력을 갈아엎지 않고 도메인 레코드를 옆에 추가**(renderer 재사용·hash/preview만 덧댐). 가장 비침습적. [Source: epic-2-retro-2026-06-13.md(64-69·149), 3-2 스토리(139)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2/3.1/3.2 모두 stale 수치로 MEDIUM 재발(3.1 799→808, 3.2 825/17→833/25 정정). 기준선 ~833(3.2 종료)은 **참고값**(본인 재측정). [Source: epic-2-retro-2026-06-13.md(49·115), 3-1 스토리(197), 3-2 스토리(141·209)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 선행 작업으로 권고. `.pre-commit-config.yaml`은 TEA/도구 소유라 본 스토리 AC에 포함하지 않되, dev는 신규 코드·테스트 평문 secret 0건을 **수동 grep**으로 확인한다. [Source: epic-2-retro-2026-06-13.md(114·129), 3-2 스토리(142)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/domain/message.py`, `tests/server/test_message_render.py`. 수정(additive): `src/rider_server/services/message_render_service.py`(`render_message`·상수·docstring), `domain/__init__.py`(`Message` 재노출), `tests/server/test_domain_models.py`(`domain.__all__` 회귀-net 10번째 모델), (선택)`services/__init__.py`(docstring). `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64), architecture.md(417-426)]
- **`domain/`·`services/` 채움:** architecture(417-426)가 정본 위치 — `domain/message.py`(419), `services/message_render_service.py`(426, 3.1 생성·3.2 `snapshot_normalizer.py` 와 동거). Epic 5가 같은 디렉터리에 `db/models/messages`·async wiring·tenant 템플릿을 additive로 덧붙인다. [Source: architecture.md(417-426)]
- **테스트 위치:** 평면 `tests/server/`(현재 `test_domain_*.py`·`test_run_once_split.py`·`test_snapshot_normalize.py`)에 `test_message_render.py` 추가. `__init__.py` 미추가(평면 컨벤션, basename 고유). 기존 renderer 회귀는 `tests/test_message.py`·`test_coupang_message.py`(무변경)가 계속 보증. [Source: tests/server/, tests/test_message.py, pyproject.toml(testpaths)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]`로 `rider_server.domain`·`rider_server.services` import 동작(서버 패키징은 Epic 5). [Source: pyproject.toml(pythonpath), 3-2 스토리(151)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.3(558-577)] — Epic 3 의도(한 번 수집 → 정규화 → 여러 채널), Story 3.3 user story·3 AC 원문(Message 필드 snapshot_id/template_version/text/text_hash·동일 Snapshot+template_version→동일 hash, 재수집 없이 재렌더링 재현·수집 로직 무수정 포맷 검증, 기존 renderer 결과 호환·의도치 않은 변경 실패 식별).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.1(515-534)·#Story-3.2(536-557)·#Story-3.4~3.8(579-693)] — 직전 스토리(서비스 분리·Snapshot 정규화)와 다운스트림 위임처: 3.4 fan-out, 3.5 DeliveryLog/idempotency(template_version+message_hash), 3.6 실패 상태 분리, 3.7 Telegram, 3.8 dry-run(본 스토리 아님).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(9·18·47·49·51)] — Reuse 표("Message renderer → Add template_version, tenant-level templates, and stored rendered results"), 흐름("… Snapshot -> Message -> DispatchJob …"), **P2-03("Define Message with snapshot_id, template_version, text, text_hash. | Same snapshot creates same hash.")**, P2-01(3.1 분리)·P2-05(3.5 dedup) 위임.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(15·33·34·172-173)] — Message 모델 계약("Rendered text derived from a Snapshot and template version"), `messages` Required fields(id, snapshot_id, template_version, text_hash, text_redacted_preview), `delivery_logs`(=3.5), dedup key 5필드(target_id+channel_id+collected_at+template_version+message_hash=3.5, message_hash=본 스토리 text_hash).
- [Source: _bmad-output/planning-artifacts/architecture.md(183-184·397·417-426·482·488-490·514-526)] — redaction(message_redacted), `message.py` renderer(template_version — 본 스토리는 server-side 상수로), `domain/message.py`·`services/message_render_service.py` 위치, 단방향 import, MessageRenderService→Message 서비스 경계, 데이터 흐름(Snapshot→MessageRenderService→fan-out→DispatchJob).
- [Source: src/rider_crawl/message.py(27-43·69-98)] — 재사용 대상 `render_current_screen_message(snapshot, *, source_label="", now=None)`, 쿠팡 `_render_performance_message`/`_peak_times(now)` 의 주중·주말 시간표 `now` 의존(결정성 전제), 배민 `_render_baemin_current_screen_message`(now 무관).
- [Source: src/rider_crawl/models.py(6-71)] — `CrawlSnapshotResult = CurrentScreenSnapshot | PerformanceSnapshot`(template_version derive 기준 타입), `PerformanceSnapshot.current_screen` 선택값.
- [Source: src/rider_crawl/redaction.py(130)] — `redact(text, *, mask_operational_ids=False) -> str`(P0-04 재사용, `text_redacted_preview` 생성).
- [Source: src/rider_server/services/message_render_service.py(1-30)] — 3.1 `MessageRenderService.render`(무변경 대상)와 docstring의 3.3 위임 명시(Message/template_version/text_hash·재렌더링 비교 → Story 3.3).
- [Source: src/rider_server/services/snapshot_normalizer.py(46-49·55-117)] — 3.2 변환 패턴 정본(server-side version 상수, 순수·결정적 staticmethod, type-derive, 단방향 import) — 본 스토리가 동형으로 따를 패턴.
- [Source: src/rider_server/domain/snapshot.py(1-34)·__init__.py(17·43-44)·tenant.py(11-16)] — frozen dataclass·domain 순수(rider_crawl-free)·명시 재노출·datetime/uuid 주입 패턴 — `Message`가 따를 패턴.
- [Source: tests/test_message.py(5-42)·tests/server/test_domain_models.py(251-294)·test_snapshot_normalize.py] — 배민 골든 render 텍스트(AC3 동등 기준), `domain.__all__` 회귀-net(10번째 모델 additive), 평면 tests/server/ 자급자족 컨벤션.
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-06-13.md(49·64-69·114-115·149)] — "새 뷰/레코드" 무회귀 패턴, A1′(secret 게이트)·A2′(수치 단일 정본).
- [Source: _bmad-output/project-context.md(35·36·58·64·81·82)] — 순수·결정성, 파서 오류 조용히 기본값 금지, 파서/렌더 변경 정상 케이스 테스트, 단방향 의존, secret 비노출, 범위 규율.
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P2-03/FR-8(Message 렌더 분리·template_version·안정적 text_hash·재현 가능 재렌더링)·FR-2(기존 자산 재사용·렌더링 무변경)·FR-3(dry-run 비교 토대). fan-out=3.4, DeliveryLog/idempotency=3.5, 실패상태=3.6, Telegram=3.7, dry-run=3.8, messages 테이블/ORM/async·tenant 템플릿·런타임 교체=Epic 5, Kakao 전송=Epic 4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code dev-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest -q` (운영 venv — WSL `python3` 금지) → 전체 스위트 통과.
- 범위 확인 `git diff -w --stat` → `src/rider_crawl/`·`pyproject.toml` 0줄 변경 확인.
- 누출 grep(봇토큰/`chat_id=digits`/한국휴대폰/이메일) → 신규 코드·테스트 0건.
- 의존성 방향: `test_rider_crawl_never_imports_rider_server`(ast 기반) 통과 → 역방향 import 0.

### Completion Notes List

- **순수 additive 구현 완료(AC1~AC7).** 신규 `domain/message.py`(frozen `Message` — `id`/`snapshot_id`/`template_version`/`text`/`text_hash`/`text_redacted_preview`) + `services/message_render_service.py`에 `render_message`(staticmethod)·template_version 상수(`baemin.realtime.v1`/`coupang.realtime.v1`)·`_PREVIEW_MAX_CHARS=500` additive 추가. 기존 `render`(3.1) 본문 **무변경**(parity 보존).
- **재구현 0(AC3).** `render_message.text == render_current_screen_message(snapshot, source_label=, now=)` 바이트 동등. 배민 골든 텍스트(test_message.py)와 일치 단언.
- **hash 정합(AC1.2).** `text_hash = sha256(text)` = 3.1 `DispatchResult.message_hash` 와 동일 — 테스트로 `DispatchService.dispatch(...).message_hash == Message.text_hash` 잠금(3.5 dedup 토대).
- **결정성·재현성(AC1.3·AC2).** 같은 입력 두 번 호출 → 동일 `text`/`text_hash`. 쿠팡 `now`(주중/주말) 차이로 hash가 갈리는 것·배민은 `now` 무관임을 함께 명시. 내부 `now()`/`uuid4()` 미호출.
- **redaction(NFR-5).** `text_redacted_preview = redact(text)[:500]`.
- **회귀-net additive(AC1·AC7).** `domain.__all__` 10번째 모델로 `Message` 잠금 갱신.
- **테스트 결과(리뷰 시점 재측정):** `.venv/Scripts/python.exe -m pytest -q` → **846 passed**(기준선 833 → +13 신규 Message 케이스 = AC1~AC7 happy/결정성/frozen/타입방어/redaction 9건 + QA 갭 A~D 4건; 순수 additive·회귀 0). 신규 파일 제외(`--ignore=tests/server/test_message_render.py`) 시 정확히 833 passed로 기준선 확인. `src/rider_crawl/`·`pyproject.toml` 0줄 변경, `MessageRenderService.render` 본문 무변경 확인.

### File List

- `src/rider_server/domain/message.py` (신규 — `Message` frozen dataclass)
- `src/rider_server/domain/__init__.py` (수정 — `Message` import·`__all__` 10번째 재노출)
- `src/rider_server/services/message_render_service.py` (수정 — `render_message`·template_version 상수·`_PREVIEW_MAX_CHARS`·docstring; `render` 무변경)
- `src/rider_server/services/__init__.py` (수정 — docstring 추적성 1단락; `__all__` 무변경)
- `tests/server/test_message_render.py` (신규 — Message 렌더 테스트 13건: AC1~AC7 9건 + QA 갭 A~D 4건)
- `tests/server/test_domain_models.py` (수정 — 회귀-net `domain.__all__` 10번째 모델 `Message` additive)

## Senior Developer Review (AI)

**리뷰어:** Noah Lee · **일자:** 2026-06-13 · **결과:** Approve (수정 반영 후)

**범위 검증:** `git diff -w --stat` — `src/rider_crawl/`·`pyproject.toml` **0줄 변경** 확인, `MessageRenderService.render`(3.1) 본문 무변경(additive only) 확인. 의존성 단방향(`test_rider_crawl_never_imports_rider_server` ast 가드 통과).

**AC 검증:**
- AC1 (정규화 `Message` + 안정적 `text_hash`): ✅ `domain/message.py` frozen dataclass 6필드(`id`/`snapshot_id`/`template_version`/`text`/`text_hash`/`text_redacted_preview`), `text_hash = sha256(text)` (`message_render_service.py:80`), 결정성(내부 `now()`/`uuid4()` 미호출).
- AC2 (재수집 없이 재현 + 수집 로직 무수정): ✅ 순수 함수(raw Snapshot + 주입 인자), `test_render_message_is_deterministic_and_reproducible` 잠금.
- AC3 (renderer 결과 호환·의도치 않은 변경 식별): ✅ `render_message.text == render_current_screen_message(...)` 바이트 동등(재구현 0), 배민 inline 골든(`test_message.py` 동일 fixture·텍스트) + 쿠팡 골든(`test_coupang_message.py` 고정 now) 잠금. `text_hash == DispatchResult.message_hash`(3.5 dedup 정합) 잠금.

**발견·조치:**
- 🟡 **M1 (수정 완료):** Dev Agent Record 테스트 수치가 실측과 불일치(기록 `842/+9/9건` → 실측 **`846`/`+13`/`13건`**; "QA 갭 A~D" 4개 미반영). A2′(수치 단일 정본) 위반 — Completion Notes·File List·Change Log 정정.
- 🟢 **L1 (수정 완료):** `test_render_message_coupang_fields_and_equivalence` 의 동등 단언이 self-reference(동어반복)였음 → 쿠팡 inline 골든 리터럴(고정 `now=주중`) 추가로 강화(배민 패턴과 동형, 실제 포맷 변경 식별 가능). 846 passed 유지.
- 🟢 **L2 (투명성 메모):** `_bmad-output/implementation-artifacts/tests/test-summary-3.3.md` 산출물이 File List에 없음 — `_bmad-output/`(리뷰 제외 영역)이라 소스 File List는 미변경.

**재측정:** `.venv/Scripts/python.exe -m pytest -q` → **846 passed**(기준선 833 `--ignore=tests/server/test_message_render.py` 로 확인, +13 신규·회귀 0).

## Change Log

| 날짜 | 변경 | 작성자 |
|---|---|---|
| 2026-06-13 | Story 3.3 구현 — `Message` 도메인 레코드 + `MessageRenderService.render_message`(안정적 `text_hash`·재현 가능 재렌더링) additive 추가, `render`(3.1)·`rider_crawl` 무변경. 846 passed. | Noah Lee (dev-story) |
| 2026-06-13 | Senior Developer Review (AI) — Approve. M1(테스트 수치 842/+9 → 846/+13 정정) 수정, L1(쿠팡 동등 단언 self-reference → inline 골든 강화) 수정. Status review → done. | Noah Lee (review) |
