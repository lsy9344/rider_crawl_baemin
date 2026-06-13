---
baseline_commit: 4636b0e
---

# Story 3.1: run_once를 수집/렌더/전송 서비스로 분리

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 강하게 묶인 `app.run_once(config)` 흐름의 세 책임(수집·렌더링·전송)을 신규 패키지 `src/rider_server/services/`에 **순수·동기 `CrawlService` / `MessageRenderService` / `DispatchService`** 로 **분리**해, 각 단계를 **독립적으로 호출·테스트·재시도**할 수 있고 **주입 가능한(adapter 경계) crawler/sender** 를 받아 fake로 대체할 수 있게 하되, **기존 `run_once` 호환 경로는 한 줄도 바꾸지 않고 그대로 두어** 레거시 tkinter UI의 1회 실행 결과가 분리 전과 **완전히 동일하게** 유지되게 하고 싶다,
so that 이후 Epic 3(3.2 Snapshot·3.3 Message·3.4 fan-out·3.5 DeliveryLog/idempotency·3.6 실패 상태 분리)과 Epic 5(scheduler·queue·async/DB 와이어링)가 이 **세 서비스 경계** 위에 additive로 빌드되며, 한 단계 실패가 다음 단계로 전파되지 않는(FR-7) 안전한 재배선의 토대가 된다(P2-01, FR-7·FR-8, NFR-20·FR-2).

> **이 스토리의 성격 — "강결합 `run_once`의 구조적 3-분리(structural split)만." Snapshot/Message/DeliveryLog 데이터 모델 정의도, fan-out도, idempotency dedup도, scheduler/queue/async/DB 와이어링도, 런타임 교체도 아니다.** Epic 3는 "실행 흐름 재배선" 에픽이고(epic-2-retro 104), 본 스토리의 P2-01 deliverable은 **"`run_once`를 `CrawlService`·`MessageRenderService`·`DispatchService`로 쪼개되, 각 서비스가 독립 호출·주입 가능·fake 대체 가능하고, 기존 UI 1회 실행 결과는 동일하게 보존된다"** 이다(implementation-contract P2-01: "Split `run_once` into CrawlService/MessageRenderService/DispatchService. Existing UI one-run result remains equivalent."). **정규화 Snapshot(platform/target_id/collected_at/normalized_json/parser_version/quality_state)+fail-closed는 Story 3.2(P2-02), Message(snapshot_id/template_version/text/text_hash)+안정 hash는 Story 3.3(P2-03), DeliveryRule fan-out은 Story 3.4(P2-04), DeliveryLog+idempotency dedup은 Story 3.5(P2-05), 수집/전송 실패 상태 분리·재시도는 Story 3.6(P2-06), Telegram 중앙 webhook은 Story 3.7, dry-run 비교·승인 활성화는 Story 3.8, async/FastAPI/SQLAlchemy/Alembic·scheduler·queue·lease 영속은 Epic 5, Kakao 실제 UI 전송은 Epic 4 소유다.** 본 스토리는 그 위에 얹힐 **세 서비스의 경계와 주입 seam만** 둔다(2.5 도메인 → 2.6 게이트 → 3.1 서비스 경계 순서). [Source: implementation-contract.md(43-52), epics.md Epic 3(511-513)·Story 3.1(515-534)·Story 3.2~3.8(536-693), architecture.md(425-429·487-490·502-503), epic-2-retro-2026-06-13.md(96-106·149)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 패키지 `src/rider_server/services/`에 세 서비스 모듈 + 결과 dataclass + `__init__.py` 재노출, 그리고 신규 테스트 `tests/server/`만 추가한다. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — `app.run_once` 포함 0줄 변경(가장 중요).** `app.py`/`message.py`/`models.py`/`platforms/`/`messengers/`/`lock.py` 등 어떤 파일도 수정하지 않는다. 본 스토리는 그것들을 **import해서 재사용만** 한다. **이유 1(호환 경로 — AC2):** 레거시 tkinter UI가 `app.run_once`를 직접 호출하므로(architecture.md 396·483·539), `run_once`를 그대로 두면 "기존 UI 1회 실행 결과 동일"(AC1 둘째 절·P2-01 acceptance·NFR-20·FR-2)이 자명하게 성립한다. **이유 2(의존성 방향 — 절대 규칙):** project-context.md(64)·architecture.md(482)가 **`rider_server` → `rider_crawl` import는 허용, 역방향 `rider_crawl` → `rider_server`는 금지**라 못 박았다. 따라서 `run_once`가 신규 서비스를 호출하도록 고치면 **금지된 역방향 의존**이 생긴다 — 절대 안 됨. 신규 서비스가 `rider_crawl`을 import하는 **단방향**만 옳다. **이유 3(회귀 그물):** `tests/test_app.py`의 run_once 테스트 ~15개(15-300)가 dedup scope·run lock·source_label·thread-id 정규화·병렬 차단을 `==`로 잠갔다 — `run_once`를 건드리면 이 골든 테스트가 깨진다(NFR-20 위반). [Source: project-context.md(64·82), architecture.md(396·482-486·539), tests/test_app.py(15-300), epics.md NFR-20(111)·FR-2(24)]
> - **정규화 `Snapshot` 도메인/dataclass·fail-closed(필수 데이터 누락 시 `MissingPerformanceDataError`)** → **Story 3.2**(P2-02). 본 스토리 `CrawlService.crawl`은 **기존 `CrawlSnapshotResult`**(`models.py` 71, `CurrentScreenSnapshot | PerformanceSnapshot`)를 그대로 반환한다 — 새 Snapshot 모델(platform/target_id/collected_at/parser_version/quality_state)을 정의하지 않는다. [Source: epics.md Story 3.2(536-557), data-api-contract.md(14·32), src/rider_crawl/models.py(59-71)]
> - **`Message` dataclass(snapshot_id/template_version/text/text_hash)·안정적 hash·재렌더링 비교** → **Story 3.3**(P2-03). 본 스토리 `MessageRenderService.render`는 **기존 `render_current_screen_message`**(`message.py` 35)를 재사용해 **텍스트(str)만** 반환한다 — `template_version`/`text_hash`/Message 모델을 추가하지 않는다. [Source: epics.md Story 3.3(558-577), data-api-contract.md(15·33), src/rider_crawl/message.py(35-43)]
> - **DeliveryRule fan-out(1 대상 → N 채널)** → **Story 3.4**(P2-04). 본 스토리 `DispatchService.dispatch`는 **단일 메시지 → 단일 전송**(기존 `dispatch_text_message` 1회)만 다룬다. [Source: epics.md Story 3.4(579-598)]
> - **`DeliveryLog`·idempotency dedup key·insert-then-send·`send_only_on_change`의 신규 dedup 이관** → **Story 3.5**(P2-05, ADD-5). 본 스토리는 **기존 파일 기반 dedup(`last_message` hash)과 RunLock을 신규 서비스로 옮기지 않는다** — 그것은 `run_once` 호환 경로가 계속 소유하고, 3.5가 `DispatchService`에 DeliveryLog/idempotency를 additive로 붙인다(아래 Dev Notes "lock·dedup 소유권"). dedup **scope key(plat·URL·center·전송대상)** 를 절대 축소하지 않는다(project-context 규칙). [Source: epics.md Story 3.5(600-621), architecture.md(308-316·357), data-api-contract.md(146-156), project-context.md(92), src/rider_crawl/app.py(54-118)]
> - **수집/렌더/전송 실패의 상태 분류·재시도/backoff·circuit breaker** → **Story 3.6**(P2-06, FR-11). 본 스토리는 "한 단계 실패가 다음 단계로 안 이어진다"(FR-7, AC3)는 **구조적 독립 실패**만 보장하고(예외가 전파되어 호출부가 다음 단계를 호출하지 않음), 실패 상태값(`AUTH_REQUIRED`/`error_code`/retry)을 정의하지 않는다. [Source: epics.md Story 3.6(623-640), architecture.md(322-330)]
> - **Telegram 중앙 webhook/dispatcher** → **Story 3.7**·**Epic 5**. 본 스토리 `DispatchService` 기본 adapter는 **기존 `messengers.dispatch_text_message`**(per-Agent 경로)다 — 중앙 sendMessage로 옮기지 않는다. [Source: epics.md Story 3.7(649-669), implementation-contract.md(10)]
> - **scheduler·job queue·worker claim·lease·async/FastAPI/SQLAlchemy/Alembic·Pydantic 스키마·런타임 교체(UI가 신규 서비스를 쓰게 하기)** → **Epic 5**(P4-02~05). 본 스토리는 **순수·동기 서비스 + 테스트만** 만들고 **런타임 미배선**이다(2.5/2.6과 동일 — `rider_server`는 아직 런타임 미사용). UI는 계속 `run_once`를 호출한다. [Source: architecture.md(332-336·430-432·482-486·514-517), epic-2-retro-2026-06-13.md(139)]
> - **`src/rider_crawl/` 전부·`pyproject.toml`·`src/rider_server/domain/`** → 무변경(순수 additive). [Source: project-context.md(64·82)]
>
> **순수·결정적·의존성 0(Epic 2/3 토대 제약).** 세 서비스는 **FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬**이다(2.6 `SubscriptionGate` 선례 — services/라고 async/DB여야 하는 게 아님). 서비스 내부에서 `datetime.now()`/`uuid4()` 같은 비결정 기본값을 호출하지 않는다 — 시각·식별자가 필요하면 호출부 주입이다(테스트 결정성). Cloud async wiring(api→service→db, executor 경계)은 Epic 5. [Source: architecture.md(332-336·482), src/rider_server/services/subscription_gate.py(11-14·24-28), project-context.md(35)]
>
> **fail-closed가 최상위 안전 원칙(NFR-1 — 오발송보다 미발송).** 본 스토리에서 그 구체 형태는 **"한 단계 실패가 다음 단계로 전파되지 않는다"**(FR-7·AC3): `CrawlService.crawl`이 예외를 내면 `MessageRenderService.render`·`DispatchService.dispatch`로 **이어지지 않는다**(서비스가 예외를 삼켜 기본값 메시지를 만들지 않음). 이것이 "필수 데이터 누락 시 잘못된 실적 메시지 발송 금지"(NFR-2)의 구조적 토대다(정규화 fail-closed 자체는 3.2). [Source: epics.md FR-7(35)·AC3(532-534), architecture.md(329·488-490), project-context.md(36·86-87)]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 서비스·테스트 fixture·로그·예외 메시지에 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 명백한 가짜값만 쓰고, fake sender는 메시지를 메모리 리스트에 모은다(실제 전송 없음). [Source: project-context.md(55·81), epic-1-retro 액션 A1, 2-6 스토리(32)]

## Acceptance Criteria

**AC1 — `run_once`를 독립 호출·주입 가능한 3 서비스로 분리하되 기존 UI 1회 실행 결과 동일 (P2-01, FR-7·FR-8, NFR-20·FR-2)**

1. **Given** 기존 `app.run_once(config)` 경계가 수집·메시지·전송을 한 함수에 묶고 있을 때 **When** 신규 패키지 `src/rider_server/services/`에 `CrawlService`(수집)·`MessageRenderService`(렌더)·`DispatchService`(전송)를 분리해 만들면(P2-01, FR-7·8) **Then** 세 서비스는 **각각 독립적으로 호출 가능**하다: `CrawlService.crawl(config) -> CrawlSnapshotResult`, `MessageRenderService.render(snapshot, source_label=...) -> str`, `DispatchService.dispatch(config, message) -> DispatchResult`(frozen). [Source: epics.md AC(523-526), implementation-contract.md P2-01(47), architecture.md(425-426·487-490)]
2. **And** 각 서비스는 **주입 가능한(adapter 경계) crawler/sender** 를 받아 테스트에서 fake로 대체할 수 있다: `CrawlService.crawl(config, *, crawl_snapshot=<fake>)`·`DispatchService.dispatch(config, message, *, send_message=<fake>)`. 기본 adapter는 기존 `run_once`와 동일한 경로(`platforms.crawl_snapshot(config, platform_name=config.platform_name)`·`messengers.dispatch_text_message(config, message)`)를 위임한다. [Source: epics.md AC(525), project-context.md(35·42-44), src/rider_crawl/app.py(29-30·131-140)]
3. **And** **기존 UI 1회 실행 결과가 분리 전과 동일하게 유지된다**(NFR-20, FR-2): `app.run_once`와 그 회귀 테스트(`tests/test_app.py`)는 **0줄 변경**이고 전부 계속 통과하며, 세 서비스를 같은 입력으로 합성(crawl→render→dispatch)하면 `run_once`의 `message` 텍스트·`sent` 플래그·`message_hash`가 **재현된다**(배민 `CurrentScreenSnapshot` + 쿠팡 `PerformanceSnapshot`, dry-run(`send_enabled=False`)·실발송(`send_enabled=True`) 경로 모두). [Source: implementation-contract.md P2-01 acceptance(47), epics.md NFR-20(111)·FR-2(24), src/rider_crawl/app.py(23-51), tests/test_app.py(15-300)]

**AC2 — 기존 `run_once` 호환 경로 보존으로 레거시 UI 계속 동작 (addendum 호환 경로, NFR-18·19)**

4. **Given** 마이그레이션 중 기존 호환 경로가 필요할 때 **When** 신규 분리 구조를 **additive로** 도입하면(런타임 미배선 — UI는 계속 `run_once` 호출) **Then** 기존 `run_once` 호환 경로가 **그대로 유지되어**(0줄 변경) 레거시 tkinter UI 실행이 계속 동작한다. 신규 서비스가 `rider_crawl`을 **단방향으로만** import하고(역방향 `rider_crawl → rider_server` 의존 0 — project-context.md 64), `git diff -w --stat`에 `src/rider_crawl/`·`pyproject.toml` 변경이 **0줄**이다. [Source: epics.md AC(528-530), architecture.md(396·482-486·539), project-context.md(64·82)]

**AC3 — 각 단계 실패의 독립성: 수집 실패가 렌더/전송으로 이어지지 않음 (FR-7, NFR-1)**

5. **Given** 각 단계 실패가 독립적이어야 할 때 **When** `CrawlService.crawl`이 실패(adapter가 예외)하면 **Then** 그 예외가 **전파되어** Message 생성(`render`)이나 Dispatch(`dispatch`)로 **이어지지 않는다**(FR-7) — 서비스는 예외를 삼켜 기본값/빈 Snapshot으로 메시지를 만들지 않으며(NFR-1·NFR-2 토대), 테스트가 "crawl 예외 시 render·dispatch fake가 호출되지 않음"을 단언한다. [Source: epics.md AC(532-534)·FR-7(35), architecture.md(329·488-490), project-context.md(36·86-87)]

## Tasks / Subtasks

- [x] **Task 1 — 세 서비스 모듈 골격을 `src/rider_server/services/`에 추가 (AC: 1, 2)**
  - [x] `src/rider_server/services/crawl_service.py`·`message_render_service.py`·`dispatch_service.py` 3개 모듈을 신설한다(2.6가 만든 `services/` 패키지에 additive). 각 모듈 상단에 짧은 docstring으로 책임·범위·위임처(Snapshot=3.2/Message=3.3/dedup=3.5/Telegram=3.7/wiring=Epic 5)를 명시한다. [Source: architecture.md(425-429), src/rider_server/services/subscription_gate.py(1-22)]
  - [x] **import는 단방향만**: 신규 서비스는 `from rider_crawl.config import AppConfig`, `from rider_crawl.models import CrawlSnapshotResult`, `from rider_crawl import platforms, messengers`, `from rider_crawl.message import render_current_screen_message`처럼 **`rider_crawl`만 import**한다. **`rider_crawl`을 import하는 역방향 코드는 절대 추가하지 않는다.** `pythonpath = ["src"]` 덕분에 별도 설치 없이 동작한다. [Source: project-context.md(64), pyproject.toml(pythonpath), src/rider_crawl/app.py(9-12·132·138)]
- [x] **Task 2 — `CrawlService`: 수집 단계 분리 + 주입 가능 crawler (AC: 1, 2, 3)**
  - [x] `class CrawlService`에 `@staticmethod def crawl(config: AppConfig, *, crawl_snapshot: Callable[[AppConfig], CrawlSnapshotResult] | None = None) -> CrawlSnapshotResult`. 기본 adapter는 **기존 `app._crawl_snapshot`와 동일**: `platforms.crawl_snapshot(config, platform_name=config.platform_name)`. 주입 시 fake로 대체(테스트). [Source: src/rider_crawl/app.py(29·131-134), src/rider_crawl/platforms/__init__.py(crawl_snapshot), project-context.md(42-43)]
  - [x] **독립 실패(FR-7·AC3)**: adapter가 예외를 내면 **그대로 전파**한다 — `try/except`로 삼켜 빈/기본 Snapshot을 만들지 않는다. (`crawl`은 Snapshot **반환 또는 예외**만; 다음 단계 호출은 호출부 책임이고, 예외가 나면 호출부가 render/dispatch를 호출하지 않는다.) [Source: epics.md FR-7(35)·AC3(532-534), project-context.md(36)]
  - [x] **반환 타입은 기존 `CrawlSnapshotResult`**(`CurrentScreenSnapshot | PerformanceSnapshot`) 그대로 — 정규화 Snapshot 모델은 정의하지 않는다(3.2). [Source: src/rider_crawl/models.py(59-71), epics.md Story 3.2(536-557)]
- [x] **Task 3 — `MessageRenderService`: 렌더 단계 분리(순수) (AC: 1)**
  - [x] `class MessageRenderService`에 `@staticmethod def render(snapshot: CrawlSnapshotResult, *, source_label: str = "") -> str`. **기존 `render_current_screen_message(snapshot, source_label=source_label)` 재사용** — 렌더 로직을 재구현하지 않는다(의도치 않은 렌더링 변경은 FR-2 위반·regression). 순수·결정적(내부 `datetime.now()` 호출 금지 — 필요 시 호출부 `now` 주입은 3.3 영역). [Source: src/rider_crawl/message.py(35-43), epics.md FR-8(36)·FR-2(24), project-context.md(35)]
  - [x] `source_label`은 **호출부가 derive**한다(서비스를 config-bound로 만들지 않음): `run_once`와 동일 규칙 `config.baemin_center_name.strip() or config.crawl_name`을 AC1 합성/parity 테스트와 Epic 5 wiring이 적용한다. (서비스 시그니처는 `source_label: str`만 받아 독립 호출·재렌더링 가능.) [Source: src/rider_crawl/app.py(37-40), epics.md Story 3.3(571-573)]
  - [x] `template_version`/`text_hash`/`Message` dataclass는 **추가하지 않는다**(3.3). 본 단계는 텍스트(str)만 반환. [Source: epics.md Story 3.3(558-577), data-api-contract.md(15·33)]
- [x] **Task 4 — `DispatchService`: 전송 단계 분리 + 주입 가능 sender + 결과 dataclass (AC: 1, 2)**
  - [x] `@dataclass(frozen=True) class DispatchResult`: `message: str`, `sent: bool`, `skipped: bool`, `message_hash: str` — 기존 `app.RunResult`(15-21)와 **동일 필드 집합**(합성 결과를 `run_once`와 비교 가능하게). [Source: src/rider_crawl/app.py(15-21)]
  - [x] `class DispatchService`에 `@staticmethod def dispatch(config: AppConfig, message: str, *, send_message: Callable[[AppConfig, str], None] | None = None) -> DispatchResult`. 기본 adapter는 **기존 `app._send_message`와 동일**: `messengers.dispatch_text_message(config, message)`. `message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()`(run_once와 동일 계산). `config.send_enabled`이면 send 후 `DispatchResult(message, sent=True, skipped=False, message_hash)`, 아니면 `sent=False, skipped=False`(dry-run). [Source: src/rider_crawl/app.py(41·46-51·137-140), src/rider_crawl/messengers/__init__.py(dispatch_text_message), epics.md FR-9(37 — 단일 전송만, fan-out=3.4)]
  - [x] **lock·dedup 소유권 경계(반드시 읽을 것 — Dev Notes 참조):** `RunLock`(브라우저 scope)·`send_only_on_change`의 `last_message` 파일 dedup은 **본 스토리에서 신규 서비스로 옮기지 않는다.** 그대로 `run_once` 호환 경로가 소유하고, Story 3.5가 `DispatchService`에 `DeliveryLog`/idempotency를 additive로 붙인다. 따라서 `DispatchService.dispatch`는 3.1에서 **dedup을 수행하지 않는다**(`skipped`는 항상 `False`; 향후 3.5의 idempotency seam이 채운다). dedup **scope key를 새로 만들거나 축소하지 않는다**. [Source: src/rider_crawl/app.py(43·54-118), epics.md Story 3.5(600-621), architecture.md(308-316·357), project-context.md(92)]
- [x] **Task 5 — `services/__init__.py` 재노출 갱신 (AC: 1)**
  - [x] 기존 `services/__init__.py`(2.6의 `SubscriptionGate` 재노출)에 `CrawlService`/`MessageRenderService`/`DispatchService`/`DispatchResult`를 **additive로** 추가해 `from rider_server.services import CrawlService, MessageRenderService, DispatchService, DispatchResult`가 동작하게 한다. 2.6 심볼은 그대로 둔다(무삭제). [Source: src/rider_server/services/__init__.py(1-26)]
  - [x] **docstring 정합 정정(같은 파일 내 — 스코프 내):** 현재 `__init__.py` docstring(4-6줄)은 "Epic 5가 같은 디렉터리에 `CrawlService`/`DispatchService`/`idempotency`를 additive로 덧붙인다"고 적혀 있으나, **`CrawlService`/`MessageRenderService`/`DispatchService`는 본 스토리(Epic 3, 3.1, P2-01)가 지금 추가**하고 **idempotency만 Story 3.5/Epic 5 소유**다. 이미 편집 중인 파일이므로 docstring을 실제와 맞게 1줄 정정한다(다른 파일은 건드리지 않음). [Source: src/rider_server/services/__init__.py(3-6), implementation-contract.md P2-01(47)·P2-05(51)]
- [x] **Task 6 — 테스트 추가: `tests/server/test_run_once_split.py` (AC: 1~5)** — 외부 호출 없음(fake/monkeypatch), 가짜 값만:
  - [x] **(AC1·AC3 — 독립 호출 + 독립 실패):** `CrawlService.crawl(config, crawl_snapshot=<fake 반환 snapshot>)`이 snapshot 반환. `crawl_snapshot=<raise>`이면 `pytest.raises`로 예외 전파, 그리고 render/dispatch fake가 **호출되지 않음**을 단언(crawl 예외 시 다음 단계 미진입 — FR-7). `MessageRenderService.render(snapshot, source_label="센터")`가 기대 텍스트. `DispatchService.dispatch`의 dry-run/실발송 분기와 fake sender 호출 여부·`DispatchResult` 필드. [Source: epics.md AC(523-526·532-534), tests/test_app.py(56-67·351-392)]
  - [x] **(AC1 — `run_once` parity, 핵심):** 같은 `config`+같은 snapshot으로 (a) `run_once(config, crawl_snapshot=lambda _c: snap, send_message=fake)`와 (b) 합성 `DispatchService.dispatch(config, MessageRenderService.render(CrawlService.crawl(config, crawl_snapshot=lambda _c: snap), source_label=<run_once와 동일 규칙>), send_message=fake)`의 `message`·`sent`·`message_hash`가 **동일**함을 배민(`_snapshot()`) + 쿠팡(`_performance_snapshot()`), `send_enabled` True/False에 대해 단언. `tests/test_app.py`의 `_config`/`_snapshot`/`_performance_snapshot` 패턴 재사용(또는 동등 fixture). [Source: implementation-contract.md P2-01(47), tests/test_app.py(56-67·316-392)]
  - [x] **(AC2 — 호환 경로·의존성 방향):** `import rider_crawl`가 `rider_server`를 import하지 않음을 보증하는 가드(예: `rider_crawl` 모듈 트리에 `rider_server` 참조 0 — grep 또는 import 그래프 단언). `tests/test_app.py` 전부 통과(무변경). [Source: project-context.md(64), tests/test_app.py(15-300)]
  - [x] **(회귀 안전·누출):** 모든 fixture는 가짜 값(`"센터"`·`tmp_path`)만. 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/`chat_id=<digits>`/한국 휴대폰/이메일 원문 금지. fake sender는 메모리 리스트로 수집(실제 전송 0). [Source: project-context.md(55·81), 2-6 스토리(83·88)]
  - [x] **테스트 위치:** 2.5/2.6이 쓰는 평면 `tests/server/`에 `test_run_once_split.py`로 둔다(`__init__.py` 미추가 — 기존 평면 컨벤션, basename 고유). [Source: tests/server/(test_subscription_gate.py 등), pyproject.toml(testpaths)]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~5)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 ~786 — HEAD `4636b0e` 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_app.py`·`tests/server/test_*`) 신규 split 케이스만큼만 증가가 정상(순수 additive). [Source: epic-2-retro-2026-06-13.md(21·115), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `src/rider_server/services/{crawl_service,message_render_service,dispatch_service}.py` + `services/__init__.py`(재노출 추가) + 신규 `tests/server/test_run_once_split.py`만** 보이고 **`src/rider_crawl/`·`pyproject.toml`·`src/rider_server/domain/` 변경 0줄**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 없음, 그리고 `src/rider_crawl/`에 `rider_server` import가 새로 생기지 않았음을 확인. [Source: project-context.md(64·81), epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: epic-2-retro-2026-06-13.md(115), 2-6 스토리(89)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `src/rider_server/services/{crawl_service,message_render_service,dispatch_service}.py` + `services/__init__.py` 재노출 추가 + 신규 `tests/server/test_run_once_split.py`. **`src/rider_crawl/`(특히 `app.run_once`)·`pyproject.toml`·`src/rider_server/domain/`은 무변경.**
- **건드리지 않는다:** `app.run_once` 및 `rider_crawl` 전부(호환 경로·회귀 그물), 정규화 Snapshot/fail-closed(3.2), Message dataclass/template_version/text_hash(3.3), DeliveryRule fan-out(3.4), DeliveryLog/idempotency/dedup 이관(3.5), 실패 상태 분류·재시도(3.6), Telegram 중앙 webhook(3.7), dry-run 비교·승인(3.8), scheduler/queue/lease/async/DB/Pydantic·런타임 교체(Epic 5), Kakao 실제 UI 전송(Epic 4). [Source: epics.md Story 3.2~3.8(536-693), architecture.md(420-432·514-517), implementation-contract.md(48-52)]

### 위치·의존성 방향 결정 — 왜 `rider_server/services/` + 단방향 import + `rider_crawl` 무변경 (반드시 읽을 것)

- **위치(architecture 정본):** architecture.md(425-426)가 `rider_server/services/`에 "**CrawlService/MessageRenderService/DispatchService**/SubscriptionGate"를 명시 매핑하고, FR-7~11을 "rider_crawl 도메인 + **rider_server/services/** + queue/idempotency.py"로 매핑(502-503)한다. 2.6가 첫 `services/` 코드(`SubscriptionGate`)를 깔았고, 본 스토리가 그 디렉터리에 세 서비스를 additive로 채운다. [Source: architecture.md(425-426·487-490·502-503), src/rider_server/services/(subscription_gate.py)]
- **의존성 방향(절대 규칙):** `rider_server` → `rider_crawl` import만 허용, **역방향 `rider_crawl` → `rider_server` 금지**(project-context.md 64, architecture.md 482). 그래서 "`run_once`를 신규 서비스 호출로 리팩토링" 은 **금지된 역방향 의존을 만들므로 불가**. 옳은 방향: **신규 서비스가 `rider_crawl`(parser/renderer/sender registry)을 import해 재사용**하고, `run_once`는 그대로 둔다. 이 단방향성이 "공유 도메인 `rider_crawl`을 Cloud/Agent가 함께 import"(architecture 482)와 정합한다. [Source: project-context.md(64), architecture.md(276·482-486)]
- **`run_once` 무변경 = AC1·AC2·NFR-20 동시 충족:** UI가 `app.run_once`를 직접 호출(architecture 396·539)하므로 `run_once`를 0줄로 두면 "기존 UI 1회 실행 결과 동일"(AC1 둘째 절·P2-01 acceptance)이 **자명**하고, `tests/test_app.py` 회귀 그물(15-300)이 안 깨진다(NFR-20). 신규 서비스는 같은 building block(`platforms.crawl_snapshot`·`render_current_screen_message`·`messengers.dispatch_text_message`)을 재사용하므로 합성 결과가 `run_once`와 **동일 데이터 흐름**을 낸다(parity 테스트로 잠금). [Source: architecture.md(396·539), src/rider_crawl/app.py(23-51·131-140), tests/test_app.py(15-300)]
- **순수성·미배선:** `services/`라고 async/DB여야 하는 게 아니다 — 2.6 게이트처럼 **FastAPI/SQLAlchemy 의존 0인 순수 동기**다. Cloud async wiring(api→service→db, executor 경계)·scheduler·queue·런타임 교체는 Epic 5가 같은 레이어에 덧붙인다. 본 스토리는 **런타임 미배선**(UI는 계속 run_once 사용 — 2.5/2.6과 동일하게 `rider_server`는 정의만, 미사용). [Source: architecture.md(332-336·482·514-517), epic-2-retro-2026-06-13.md(139)]

### 세 서비스 시그니처·책임 맵 (AC1 — run_once 분해)

| 서비스 | 시그니처(권장) | run_once 대응부 | 재사용(무재구현) | 주입 seam |
|---|---|---|---|---|
| `CrawlService.crawl` | `(config, *, crawl_snapshot=None) -> CrawlSnapshotResult` | `app.py` 36 + `_crawl_snapshot`(131-134) | `platforms.crawl_snapshot(config, platform_name=config.platform_name)` | `crawl_snapshot` fake |
| `MessageRenderService.render` | `(snapshot, *, source_label="") -> str` | `app.py` 39-40 | `render_current_screen_message(snapshot, source_label=...)` | (순수 — 주입 불필요) |
| `DispatchService.dispatch` | `(config, message, *, send_message=None) -> DispatchResult` | `app.py` 41·46-51 + `_send_message`(137-140) | `messengers.dispatch_text_message(config, message)`, `hashlib.sha256` | `send_message` fake |

- `DispatchResult(frozen)` 필드 = `RunResult`(app.py 15-21)와 동일: `message`/`sent`/`skipped`/`message_hash`. 합성 결과를 `run_once`와 1:1 비교하기 위함. [Source: src/rider_crawl/app.py(15-51·131-140), src/rider_crawl/message.py(35-43)]
- `source_label` derive 규칙(run_once 39): `config.baemin_center_name.strip() or config.crawl_name`. 서비스는 이를 **인자로 받아** 독립·재렌더링 가능하게 유지(config 결합 회피); derive는 호출부(parity 테스트·Epic 5 wiring) 책임. [Source: src/rider_crawl/app.py(37-40)]

### lock·dedup 소유권 — 왜 3.1에서 옮기지 않는가 (핵심 설계 결정)

- `run_once`는 데이터 흐름(crawl→render→send) 외에 **3가지 운영 메커니즘**을 더 갖는다: (a) **`RunLock`**(브라우저 scope 직렬화, app.py 35·68-95), (b) **`send_only_on_change` dedup**(`last_message` 파일 hash, 43·54-66·98-118), (c) **`send_enabled` 게이팅**(46-51). 본 스토리는 (c)만 `DispatchService`로 가져오고, **(a)·(b)는 `run_once` 호환 경로가 계속 소유**한다.
- **이유:** (1) (b) 파일 dedup·scope key는 **Story 3.5가 `DeliveryLog`+idempotency(`monitoring_target_id + channel_id + collected_at + template_version + message_hash`)로 교체**할 대상이다 — 3.1에서 신규 서비스에 옮겨 재구현하면 3.5와 충돌하고, **dedup scope key 축소 위험**(project-context 92: "scope key를 줄이면 다른 탭/계정 중복 판단이 섞임")을 진다. (2) `_message_scope_key`/`_run_scope_key`/`_is_duplicate` 등은 `app.py`의 **private 헬퍼**라 신규 서비스가 재사용하려면 `rider_crawl` 공개 API를 바꿔야 하는데, 그건 `rider_crawl` 변경(무변경 원칙 위반)이다. (3) (a) RunLock은 브라우저 scope 자원 보호라 scheduler/agent claim(Epic 4/5)과 함께 배선하는 게 자연스럽다.
- **결과:** 3.1 `DispatchService.dispatch`는 dedup을 하지 않으므로 `skipped`는 항상 `False`다. 이는 의도된 부분 구현이다 — `skipped` 필드는 `RunResult`와의 shape parity를 위해 두고, **Story 3.5의 idempotency seam이 채운다**. parity 테스트는 dedup이 관여하지 않는 경로(실발송·dry-run)에서 `run_once`와 동일함을 잠그고, duplicate-skip 경로의 동일성은 **무변경 `tests/test_app.py`가 계속 보증**한다(run_once 소유). [Source: src/rider_crawl/app.py(35·43·46-51·54-118), epics.md Story 3.5(600-621), data-api-contract.md(146-156), architecture.md(308-316·357), project-context.md(92)]

### FR-7 독립 실패 불변식 (AC3 — 오발송보다 미발송의 구조적 토대)

- **불변식:** `CrawlService.crawl` 실패(adapter 예외) → `render`/`dispatch` **미진입**. 서비스는 예외를 `try/except`로 삼켜 **빈/기본 Snapshot이나 기본값 메시지를 만들지 않는다**(NFR-1·2). 단계 사이 진행 결정은 **호출부**가 하고, 예외가 전파되면 호출부는 다음 단계를 호출하지 않는다(`run_once`도 동일 — crawl 예외 시 lock 안에서 전파). 정규화 단계의 fail-closed(필수 데이터 누락 → `MissingPerformanceDataError`)는 **Story 3.2**가 `CrawlService` 안에서 추가한다 — 3.1은 "예외 전파 = 다음 단계 차단"이라는 **구조**만 보장한다. [Source: epics.md FR-7(35)·AC3(532-534), architecture.md(329·488-490), project-context.md(36·86-87), epics.md Story 3.2(549-552)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml`·`domain/` 무변경** — `git diff -w` = `services/` 신규/재노출 + 신규 테스트만. (b) **의존성 단방향** — `rider_server → rider_crawl`만, 역방향 0. (c) **렌더링 결과 불변** — `render`는 `render_current_screen_message` 재사용(재구현 금지; 의도치 않은 변경은 FR-2 위반). (d) **dedup scope key 불변·미축소**(3.5까지 run_once 소유). (e) **순수·결정적** — 서비스 내부 `datetime.now()`/`uuid4()` 금지. (f) **frozen 불변** — `DispatchResult`는 `@dataclass(frozen=True)`. (g) **fake로 외부 격리** — 테스트는 실제 브라우저/Telegram/Kakao 미호출(project-context 55). [Source: project-context.md(35·36·55·64·82·92), src/rider_crawl/message.py(35-43), src/rider_crawl/app.py(15-21)]

### 이전 스토리 인텔리전스 (Epic 1 → 2.x → 3.1 이월 교훈)

- **Epic 2 retro가 본 스토리에 명시 지침:** "**Epic 3 서비스 분리도 동일 원칙(기존 `run_once` 호환 경로 보존)으로 가야 한다**"(149), "3.1 서비스 분리 → 2.5 도메인 모델과 `app.run_once` 경계, **2.6이 만든 `services/` 패키지 규약(frozen dataclass + 순수 함수) 위에 직접 빌드**"(97). 본 스토리는 그 패턴을 따른다 — `rider_crawl` 무변경 + `services/`에 순수 동기 추가. [Source: epic-2-retro-2026-06-13.md(97·149)]
- **무회귀 비결 = "새 필드가 아니라 새 뷰/레코드"**(retro 64-67·149): 2.3 `@property` 별칭, 2.6 `SubscriptionStateChange` 별도 레코드처럼, 3.1은 **기존 `run_once`를 갈아엎지 않고 신규 서비스 경계를 옆에 추가**한다(가장 비침습적). [Source: epic-2-retro-2026-06-13.md(64-69·149)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2 전 스토리가 dev 수치 stale로 MEDIUM 7/7 재발했다. 기준선 ~786(2.7 종료)은 **참고값**(본인 재측정). [Source: epic-2-retro-2026-06-13.md(49·79·115), 2-6 스토리(86·89)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 첫 스토리 **선행 작업**으로 격상 권고했다(114). 이는 **TEA/도구 소유의 cross-cutting 작업**(`.pre-commit-config.yaml`)이라 본 스토리의 feature deliverable(서비스 분리) AC에는 포함하지 않되, dev/운영자는 그 게이트 부재를 인지하고 신규 코드·테스트에 평문 secret 0건을 **수동 grep로** 계속 확인한다. [Source: epic-2-retro-2026-06-13.md(114·129·159), project-context.md(81)]
- **범위 규율(2.1~2.7 직교):** 각 스토리가 한 가지만 했다. 3.1도 "구조적 3-분리"만 하고 Snapshot(3.2)·Message(3.3)·fan-out(3.4)·dedup(3.5)·실패상태(3.6)·Telegram(3.7)·wiring(Epic 5)을 끌어오지 않는다. [Source: epic-2-retro-2026-06-13.md(60), epics.md Story 3.2~3.8(536-693)]
- **(str, Enum) `str()` 함정(2.5/2.7 관찰):** 본 스토리는 새 enum이 **불필요**하다(결과는 `DispatchResult` dataclass). 만약 추가하게 되면 `(str, Enum)`·멤버이름==값(대문자)·직렬화는 `.value`/`==`(2.6 선례). 가급적 enum 없이 간다. [Source: epic-2-retro-2026-06-13.md(81), 2-6 스토리(142)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/services/crawl_service.py`·`message_render_service.py`·`dispatch_service.py`, `services/__init__.py` 재노출 추가, `tests/server/test_run_once_split.py`. `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64), architecture.md(411-461)]
- **`services/` 채움:** architecture(425-426)가 정본 위치. 2.6 `subscription_gate.py`와 동거하며, Epic 5가 같은 디렉터리에 `idempotency.py`·async wiring을 additive로 덧붙인다(425-429). [Source: architecture.md(425-429)]
- **테스트 위치:** 평면 `tests/server/`(현재 `test_domain_*.py`·`test_subscription_gate.py`·`test_migration.py`)에 `test_run_once_split.py` 추가. `__init__.py` 미추가(평면 컨벤션). 기존 run_once 회귀는 `tests/test_app.py`(무변경)가 계속 보증. [Source: tests/server/, tests/test_app.py(15-300), pyproject.toml(testpaths)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]`로 `rider_server.services` import 동작(서버 패키징은 Epic 5). [Source: pyproject.toml(pythonpath), 2-6 스토리(150)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.1(515-534)] — Epic 3 의도(한 번 수집 → 여러 채널 fan-out, run_once를 CrawlService/MessageRenderService/DispatchService로 분리), Story 3.1 user story·3 AC 원문(독립 호출·주입 가능 adapter·fake 대체, 기존 UI 1회 실행 결과 동일, 호환 경로 유지, crawl 실패가 message/dispatch로 안 이어짐).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.2~3.8(536-693)] — 다운스트림 위임처: 3.2 Snapshot/fail-closed, 3.3 Message/template_version/hash, 3.4 DeliveryRule fan-out, 3.5 DeliveryLog/idempotency, 3.6 실패 상태 분리, 3.7 Telegram 중앙, 3.8 dry-run 비교·승인(본 스토리 아님).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-7(35)·FR-8(36)·FR-2(24)·FR-9(37)·NFR-1~4(83-86)·NFR-20(111)] — Crawl Job/Snapshot(crawl 실패 비전파), Message 렌더 분리, 기존 자산 재사용·렌더링 무변경, fan-out(=3.4), fail-closed/필수데이터/idempotency/인증(토대), 기존 테스트 계속 실행.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(7-12·18·43-52)] — Reuse 표(parser/renderer/sender 재사용 + 추가 변경처), "Coupled run_once → CrawlJob→Snapshot→Message→DispatchJob→DeliveryLog", **P2-01("Split run_once into CrawlService/MessageRenderService/DispatchService. Existing UI one-run result remains equivalent.")** 및 P2-02~06 위임.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(14-16·32-34·146-156)] — Snapshot/Message/DeliveryLog 모델·테이블 필드(=3.2/3.3/3.5), dedup key 5필드(=3.5).
- [Source: _bmad-output/planning-artifacts/architecture.md(276·329·332-336·396·425-429·482-490·502-503·514-517·539)] — 단방향 import·공유 도메인, fail-closed, async/sync 경계, app.py=run_once 호환 경로, services/에 세 서비스 매핑+idempotency.py(Epic 5), 컴포넌트/서비스 경계(service-only 전이·결합 금지), FR-7~11 매핑, 통합/데이터 흐름, 레거시 UI=`python -m rider_crawl`.
- [Source: src/rider_crawl/app.py(15-51·131-140)] — `RunResult`(message/sent/skipped/message_hash), `run_once`(crawl→render→hash→dedup→send_enabled→send), `_crawl_snapshot`/`_send_message` 기본 adapter, `RunLock`·`_message_scope_key`·`_is_duplicate`(3.1 미이관 — 3.5 소유).
- [Source: src/rider_crawl/message.py(35-43)·models.py(59-71)·platforms/__init__.py·messengers/__init__.py] — 재사용할 `render_current_screen_message`, `CrawlSnapshotResult`(CurrentScreen|Performance), `platforms.crawl_snapshot`, `messengers.dispatch_text_message`.
- [Source: src/rider_server/services/__init__.py·subscription_gate.py(1-28)] — 2.6 `services/` 규약(frozen dataclass + 순수 정적 함수, 의존성 0, datetime/uuid 미호출, 명시 재노출) — 본 스토리가 따를 패턴.
- [Source: tests/test_app.py(15-300·316-392)] — run_once 회귀 테스트(무변경 유지) + `_config`/`_snapshot`/`_performance_snapshot` fixture(parity 테스트 재사용).
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-06-13.md(64-69·96-106·114-115·129·139·149)] — 3.1이 2.6 `services/` 규약 위에 빌드·호환 경로 보존 원칙, "새 뷰/레코드" 무회귀 패턴, A1′(secret 게이트 선행)·A2′(수치 단일 정본), rider_server 런타임 미배선, Epic 3=실행 흐름 재배선 전환점.
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P2-01/FR-7(Crawl→Snapshot 분리·실패 비전파)·FR-8(Message 렌더 분리)·FR-2/NFR-20(기존 자산 재사용·렌더링/UI 실행 결과 동일·기존 테스트 유지)·NFR-1(fail-closed 토대). Snapshot=3.2, Message/hash=3.3, fan-out=3.4, DeliveryLog/idempotency=3.5, 실패상태=3.6, Telegram=3.7, dry-run=3.8, async/DB/scheduler/queue·런타임 교체=Epic 5, Kakao 전송=Epic 4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 의존성 방향 가드 1차 작성 시 단순 문자열 grep(`"rider_server" in text`)이 `src/rider_crawl/redaction.py`의 **docstring 언급**(import 아님)을 오탐 → `ast` 기반 import 엣지(`ast.Import`/`ast.ImportFrom`)만 검사하도록 수정. AC2의 규칙은 import 방향이지 문자열 언급이 아님.

### Completion Notes List

- **순수 additive 3-분리 완료(P2-01):** `src/rider_server/services/`에 `CrawlService`/`MessageRenderService`/`DispatchService` + `DispatchResult`(frozen) 추가. `src/rider_crawl/`(특히 `app.run_once`)·`pyproject.toml`·`src/rider_server/domain/`는 0줄 변경(`git diff -w --stat`로 확인).
- **AC1(독립 호출·주입 가능):** 세 서비스는 각각 정적 호출 가능하며 `crawl_snapshot`/`send_message` fake 주입을 받는다. 기본 adapter는 `run_once`와 동일 경로(`platforms.crawl_snapshot`·`messengers.dispatch_text_message`)에 위임.
- **AC1 parity(핵심):** 같은 config+snapshot으로 합성(crawl→render→dispatch)한 결과의 `message`/`sent`/`message_hash`가 `run_once`와 동일함을 배민(`CurrentScreenSnapshot`)+쿠팡(`PerformanceSnapshot`) × dry-run/실발송 4조합으로 잠금(`send_only_on_change=False` 경로 — dedup 비관여, dedup 경로는 무변경 `tests/test_app.py`가 계속 보증).
- **AC2(호환 경로·단방향):** `run_once` 무변경 → 레거시 tkinter UI 경로 보존. `rider_crawl`에 `rider_server` import 0건(ast 가드 + grep 확인).
- **AC3(FR-7 독립 실패):** `CrawlService.crawl`은 adapter 예외를 삼키지 않고 전파 → render/dispatch 미진입을 테스트로 단언(빈/기본 Snapshot 미생성, 오발송보다 미발송).
- **범위 경계 준수:** 정규화 Snapshot(3.2)·Message/template_version/text_hash(3.3)·fan-out(3.4)·dedup/DeliveryLog(3.5)·실패상태(3.6)·Telegram 중앙(3.7)·async/DB/scheduler wiring(Epic 5)은 끌어오지 않음. `DispatchService.skipped`는 항상 `False`(dedup 미이관 — 3.5 idempotency seam이 채울 shape parity 필드).
- **순수성:** 서비스 내부 `datetime.now()`/`uuid4()` 미호출(렌더러의 `now`는 호출부 책임 — 3.3 영역). 신규 코드/테스트 평문 secret 0건.
- **테스트 재측정값(A2′ 단일 정본):** 운영 venv(`.venv/Scripts/python.exe -m pytest -q`) 전체 **808 passed, 0 failed** (HEAD `4636b0e` 기준선 786 passed 대비 신규 split 22건만 증가 — 순수 additive). 신규 파일 `tests/server/test_run_once_split.py` 단독: 22 passed.

### File List

- `src/rider_server/services/crawl_service.py` (신규)
- `src/rider_server/services/message_render_service.py` (신규)
- `src/rider_server/services/dispatch_service.py` (신규)
- `src/rider_server/services/__init__.py` (수정 — 3 서비스 + `DispatchResult` 재노출 추가, docstring 정정)
- `tests/server/test_run_once_split.py` (신규)

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome:** Approve (auto-fix applied) · **Status → done**

리뷰어가 적대적으로 검증했고 구현은 충실하다 — AC1/AC2/AC3 모두 코드·테스트로 실제 구현 확인. `[x]` 표기된 7개 Task 전부 실제 완료.

**검증한 항목(통과):**
- **AC1(독립 호출·주입·parity):** 세 서비스 정적 호출 + `crawl_snapshot`/`send_message` fake 주입 동작. 기본 adapter는 `run_once._crawl_snapshot`/`_send_message`와 동일 경로 위임(`platforms.crawl_snapshot(config, platform_name=config.platform_name)`·`messengers.dispatch_text_message`). 배민/쿠팡 × dry-run/실발송 4조합 parity(`message`/`sent`/`message_hash`)가 `run_once`와 동일함을 재실행으로 확인.
- **AC2(호환 경로·단방향):** `git diff -w --stat`에 `src/rider_crawl/`·`pyproject.toml` 변경 **0줄**(`services/__init__.py` 11줄 추가 + 신규 4파일만). `rider_crawl/`에 `rider_server` **import 0건**(redaction.py 4행은 docstring 언급 — ast 가드가 정확히 import 엣지만 검사해 오탐 제외).
- **AC3(FR-7):** `CrawlService.crawl`이 adapter 예외를 삼키지 않고 전파 → render/dispatch 미진입을 테스트로 단언.
- **누출:** 신규 코드·테스트 평문 secret(봇 토큰/chat_id/휴대폰) 0건.

**발견 및 자동 수정(1건, MEDIUM — A2′ 위반):** Dev Agent Record·Change Log의 테스트 수치가 stale/오기였다. 기재값 "799 passed, 신규 13건"이나 리뷰 시점 재측정은 기준선(`4636b0e`) **786 passed**, 신규 파일 **22 tests**, 전체 **808 passed, 0 failed**(델타 +22). epic-2 retro가 경고한 바로 그 A2′ 실패 모드 — 단일 정본 재측정값으로 정정 완료. (기능 결함 아님 — 분리 deliverable·전 테스트는 정상.)

0 CRITICAL → Status `done`, sprint-status 동기화.

## Change Log

| 날짜 | 변경 | 작성자 |
|---|---|---|
| 2026-06-13 | Story 3.1 구현: `run_once`를 `CrawlService`/`MessageRenderService`/`DispatchService`로 구조적 3-분리(순수 additive). `rider_crawl` 무변경, 단방향 import, run_once parity·FR-7 독립 실패 테스트 추가(808 passed). Status → review. | claude-opus-4-8 |
| 2026-06-13 | 자동 코드리뷰(story-automator): AC1~3·범위·누출·단방향 적대적 검증 통과. A2′ 테스트 수치 정정(799→808, 신규 13→22; 기준선 786). 0 CRITICAL → Status → done, sprint-status 동기화. | claude-opus-4-8 (review) |
