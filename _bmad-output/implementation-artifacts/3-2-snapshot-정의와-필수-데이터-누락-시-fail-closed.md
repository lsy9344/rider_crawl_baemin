---
baseline_commit: 807719b
---

# Story 3.2: Snapshot 정의와 필수 데이터 누락 시 fail-closed

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want CrawlService가 수집한 결과(`CrawlSnapshotResult`)를 **정규화된 `Snapshot` 도메인 레코드**(platform·target_id·collected_at·normalized_json·parser_version·quality_state + 고객/플랫폼계정/Agent 추적 필드)로 **wrapping**하되, **필수 실적 데이터가 없으면 기본값(0 등)으로 채우지 않고 `MissingPerformanceDataError` 를 계승한 명확한 예외를 내어 그 실행이 Message 생성으로 이어지지 않게(fail-closed)** 하고, 동시에 **기존 배민/쿠팡 parser 출력은 한 글자도 바꾸지 않고 그대로 감싸기만** 해서 snapshot fixture 동등성이 유지되게 하고 싶다,
so that 누락·부분 데이터로 만들어진 **틀린 실적 메시지가 고객에게 발송되는 사고**가 구조적으로 차단되고(NFR-1 오발송보다 미발송·NFR-2), 이후 3.3(Message 렌더·`snapshot_id`/`text_hash`)·3.5(DeliveryLog dedup의 `snapshot_collected_at`)·Epic 5(snapshots 테이블 영속)가 이 **정규화 Snapshot 계약** 위에 additive로 빌드된다(P2-02, FR-7, NFR-1·2).

> **이 스토리의 성격 — "수집 결과를 정규화 `Snapshot` 레코드로 정의 + 필수데이터 fail-closed 정규화 게이트, 그것만."** 3.1이 `run_once`를 세 서비스로 **구조 분리**했고(데이터는 여전히 기존 `CrawlSnapshotResult`), 본 스토리는 그 수집 결과를 **정규화 도메인 레코드 `Snapshot`** 으로 승격하고 **필수데이터 누락 시 예외(미발송)** 라는 데이터-품질 게이트를 추가한다. 본 스토리는 **Message 정의/렌더(snapshot_id·template_version·text_hash)=3.3, DeliveryRule fan-out=3.4, DeliveryLog/idempotency=3.5, 실패 상태 분류·재시도=3.6, Telegram 중앙=3.7, dry-run 비교·승인=3.8, snapshots 테이블/ORM/Alembic·async wiring·런타임 교체=Epic 5, Kakao 실제 전송=Epic 4** 를 끌어오지 않는다. [Source: epics.md Epic 3(511-513)·Story 3.2(536-557)·Story 3.3~3.8(558-693), implementation-contract.md P2-02(48), data-api-contract.md(14·32·301-304), 3-1 스토리(17·21)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 도메인 모듈 `domain/snapshot.py` + 신규 서비스 `services/snapshot_normalizer.py` + `domain/states.py`·`domain/__init__.py`·`services/__init__.py` 재노출 추가 + 신규 테스트 `tests/server/test_snapshot_normalize.py`. 아래는 **다른 스토리/에픽 소유 — 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `parser.py`/`platforms/coupang/parser.py`/`models.py`/`message.py`/`app.py` 등 어떤 파일도 수정하지 않는다. 본 스토리는 그것들을 **import해서 재사용·wrapping만** 한다. **이유 1(parser 동작 보존 — AC3):** 정규화는 parser 출력을 **감싸기만** 하므로 배민/쿠팡 parser fixture 동작이 의도치 않게 바뀌면 안 된다(implementation-contract Reuse "Wrap output in normalized Snapshot and keep fixture tests"). **이유 2(의존성 방향 — 절대 규칙):** `rider_server` → `rider_crawl` import만 허용, 역방향 `rider_crawl` → `rider_server` 금지(project-context.md 64, architecture.md 482). **이유 3(회귀 그물):** `tests/test_baemin_parser.py`·`test_coupang_parser.py`·`test_parser.py`·`tests/test_app.py`가 parser/run_once 동작을 `==`로 잠갔다 — rider_crawl을 건드리면 깨진다. [Source: implementation-contract.md(7·48), project-context.md(36·58·64·82), architecture.md(482·301-304)]
> - **`CrawlService.crawl` 시그니처·반환 무변경(3.1 parity 보존).** `CrawlService.crawl(config, *, crawl_snapshot=None) -> CrawlSnapshotResult` 는 **그대로** 둔다 — 정규화는 **별도 seam**(`SnapshotNormalizer.normalize`)으로 additive하게 붙인다. crawl이 `Snapshot`을 반환하도록 바꾸면 3.1의 run_once parity 테스트(`MessageRenderService.render(CrawlSnapshotResult, ...)`)와 합성 동등성이 깨진다. 런타임에서 crawl→normalize→render를 잇는 wiring은 **Epic 5**다. [Source: 3-1 스토리(40·60·173), src/rider_server/services/crawl_service.py(34-42)]
> - **`Message`/`snapshot_id`/`template_version`/`text_hash`/렌더링** → **Story 3.3**(P2-03). 본 스토리는 `Snapshot`까지만 만들고 Message 모델/렌더 분리·hash를 추가하지 않는다. (`MessageRenderService.render`는 3.1대로 `CrawlSnapshotResult`를 받아 텍스트만 반환 — 무변경.) [Source: epics.md Story 3.3(558-577), data-api-contract.md(15·33)]
> - **`DeliveryRule` fan-out·`DeliveryLog`·idempotency dedup key(`snapshot_collected_at` 포함)** → **3.4/3.5**(P2-04·05). 본 스토리는 `Snapshot.collected_at`을 **정의만** 하고 dedup 계산에 쓰지 않는다. [Source: epics.md Story 3.4~3.5(579-621), data-api-contract.md(146-156)]
> - **수집/렌더/전송 실패 상태 분류·재시도·`crawl_failure` 등 운영 카테고리** → **3.6**(P2-06, FR-11). 본 스토리의 fail-closed는 "**필수데이터 누락 → 예외 전파 → Message 미생성**" 이라는 단일 불변식만 보장하고, 실패를 운영 상태값(`AUTH_REQUIRED`/`error_code`/retry)으로 분류·기록하지 않는다. [Source: epics.md Story 3.6(623-643), architecture.md(323-328)]
> - **`snapshots` 테이블/SQLAlchemy ORM/Alembic·Pydantic 스키마·async wiring·런타임 교체** → **Epic 5**(P4-02). 본 스토리는 **순수 dataclass 도메인 + 순수 동기 정규화 함수 + 테스트만**, **런타임 미배선**이다(2.5/2.6/3.1과 동일 — `rider_server`는 정의만, UI는 계속 `run_once` 사용). [Source: architecture.md(164-171·422-424·514-517), implementation-contract.md P4-02(71)]
>
> **순수·결정적·의존성 0(2.5/2.6/3.1 토대 제약 계승).** `Snapshot` dataclass와 `SnapshotNormalizer` 는 FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬이다. **내부에서 `datetime.now()`/`uuid4()` 를 호출하지 않는다** — `snapshot_id`·`collected_at` 같은 비결정 값은 **호출부 주입**이다(테스트 결정성; 2.5 `Tenant.created_at` 선례 "자동 now() 기본값 금지"). [Source: src/rider_server/domain/tenant.py(16), 3-1 스토리(30), project-context.md(35)]
>
> **fail-closed = 최상위 안전 원칙의 본 스토리 구체 형태(NFR-1·2).** 3.1이 "한 단계 실패가 다음 단계로 전파되지 않는다"(구조)를 보장했다면, 3.2는 그 위에 "**필수 실적 데이터가 없으면 0/기본값으로 메시지를 만들지 않고 예외를 낸다**"(데이터 품질)를 얹는다. parser는 이미 파싱 중 누락 시 `MissingPerformanceDataError`를 던지고(아래 Dev Notes), 본 스토리의 정규화 게이트는 그 예외를 **삼키지 않고 전파**하며 + parser 출력이 구조적으로 present여도 **필수 의미값(예: 빈 `center_name`)이 비면 거부**하는 2중 방어를 둔다. [Source: epics.md AC(549-552), project-context.md(36·86-88), src/rider_crawl/parser.py(11·535-545)]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** `Snapshot.normalized_json`·테스트 fixture·예외 메시지에 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. fixture는 명백한 가짜 값만(`"제이앤에이치플러스 의정부남부"` 같은 센터명은 실 데이터가 아니라 기존 테스트 fixture 문자열). [Source: project-context.md(81), epic-1-retro 액션 A1, 3-1 스토리(34)]

## Acceptance Criteria

**AC1 — 정규화 `Snapshot` 레코드 정의 + 추적성 (P2-02, FR-7)**

1. **Given** `CrawlService`가 대상 화면을 수집해 `CrawlSnapshotResult`(배민 `CurrentScreenSnapshot` 또는 쿠팡 `PerformanceSnapshot`)를 만들 때 **When** 그 결과를 정규화 `Snapshot`으로 wrapping하면(P2-02, FR-7) **Then** `Snapshot`(frozen dataclass)은 **`platform`(Platform enum), `target_id`(str), `collected_at`(datetime), `normalized_json`(안정적 키 dict), `parser_version`(str), `quality_state`(`SnapshotQualityState` enum)** 필드를 갖는다(data-api-contract `snapshots` Required fields + P2-02의 `platform`). [Source: epics.md AC(544-547), data-api-contract.md(14·32), implementation-contract.md P2-02(48), architecture.md(301-302·419)]
2. **And** 어떤 **고객(`tenant_id`)·플랫폼계정(`platform_account_id`)·대상(`target_id`)·실행 시각(`collected_at`)·Agent(`agent_id`)** 에서 만들어졌는지 추적 가능하다 — 이 추적 필드를 `Snapshot`이 직접 보유한다(사전-Cloud/Agent 컨텍스트에서 미상일 수 있는 `tenant_id`/`platform_account_id`/`agent_id`는 기본값 `""`, `target_id`/`collected_at`은 필수). [Source: epics.md AC(547), data-api-contract.md(28·35-36), architecture.md(524-526)]
3. **And** `normalized_json`은 parser 출력의 **모든 필드를 안정적 키로 보존**한 JSON-직렬화 가능 dict이고(`dataclasses.asdict`로 중첩 dataclass까지 재귀 변환), `parser_version`은 snapshot 형태(배민 current-screen / 쿠팡 peak-dashboard)별 server-side 버전 문자열로 기록된다(parser 출력 shape가 바뀌면 bump — canary는 P6). [Source: architecture.md(301-302), implementation-contract.md Reuse(8), epics.md FR-8 토대]

**AC2 — 필수 데이터 누락 시 fail-closed: 기본값 금지·명확한 예외·Message 미생성 (NFR-2, FR-7)**

4. **Given** 필수 실적 데이터가 누락됐을 때(수집 결과가 `None`이거나, parser 출력이 구조적으로는 present여도 필수 의미값 — 예: 기대 센터/상점명 `center_name` — 이 비었을 때) **When** `SnapshotNormalizer.normalize(...)`로 정규화를 시도하면 **Then** **기본값(0/""/빈 Snapshot)으로 채우지 않고** `MissingPerformanceDataError`를 **계승한** 명확한 예외(`MissingSnapshotDataError`)를 **raise** 한다(NFR-2). [Source: epics.md AC(549-551), project-context.md(36·88), src/rider_crawl/parser.py(11)]
5. **And** 그 예외가 **전파되어** 해당 실행은 **Message 생성(렌더)으로 이어지지 않는다**(FR-7) — 정규화 단계가 예외를 `try/except`로 삼켜 부분/기본 Snapshot을 만들지 않으며, 합성 경로(`crawl → normalize → render`)에서 normalize가 raise하면 `MessageRenderService.render`가 **호출되지 않음**을 테스트가 단언한다. 또한 `CrawlService.crawl`이 parser의 `MissingPerformanceDataError`를 던지는 경우에도 normalize/render에 **도달하지 않는다**(3.1 AC3 계승). [Source: epics.md AC(552), 3-1 스토리(50·117), architecture.md(488-490)]

**AC3 — 기존 배민/쿠팡 parser 동작 보존: snapshot fixture 동등성 (implementation-contract Reuse)**

6. **Given** 기존 배민/쿠팡 parser 동작을 보존해야 할 때 **When** parser 출력을 `Snapshot`으로 wrapping하면 **Then** **배민/쿠팡 snapshot fixture가 정규화를 통과**하고(`CurrentScreenSnapshot`·`PerformanceSnapshot` → `quality_state=OK`인 `Snapshot`), `normalized_json`이 `dataclasses.asdict(raw)`와 **정확히 일치**해(필드 추가·삭제·기본값 주입 없음) 기존 parser 출력이 의도 없이 바뀌지 않음을 잠근다. [Source: epics.md AC(554-556), implementation-contract.md(7·8·12), data-api-contract.md(301)]
7. **And** `src/rider_crawl/` **0줄 변경**(`git diff -w --stat`)으로 기존 parser/run_once 회귀 그물(`tests/test_baemin_parser.py`·`test_coupang_parser.py`·`test_parser.py`·`tests/test_app.py`)이 **전부 그대로 통과**하고, 본 스토리는 신규 정규화 케이스만큼만 테스트 수가 증가한다(순수 additive). [Source: project-context.md(58·82), 3-1 스토리(79-80), tests/test_baemin_parser.py·test_coupang_parser.py]

## Tasks / Subtasks

- [x] **Task 1 — `SnapshotQualityState` enum 추가: `domain/states.py` (AC: 1)**
  - [x] 기존 `states.py`에 `class SnapshotQualityState(str, Enum)` 를 **additive로** 추가한다. 멤버: **`OK = "OK"`**(필수데이터 present로 정규화 성공), **`MISSING_REQUIRED = "MISSING_REQUIRED"`**(필수데이터 누락을 표현하는 도메인 어휘). `(str, Enum)` + 멤버이름==값(대문자) 규약 준수(2.5 정본). [Source: src/rider_server/domain/states.py(1-11·97-103), architecture.md(254·318)]
  - [x] **값 정의 vs 로직 경계 주석:** 본 스토리의 fail-closed 정규화는 필수데이터 누락 시 `MISSING_REQUIRED` Snapshot을 **반환하지 않고 예외를 raise** 한다(AC2). `MISSING_REQUIRED`는 **실패를 기록(persist)할 Epic 5 DB 레이어용 어휘**로 값만 미리 둔다(2.5가 `SubscriptionStatus` 값만 정의하고 게이트 로직은 2.6에 둔 선례와 동형). 짧은 docstring으로 이 경계를 남긴다(project-context §38). [Source: 2-5 스토리(42·57), epics.md AC(551)]
- [x] **Task 2 — `Snapshot` 도메인 모델 정의: `domain/snapshot.py` (AC: 1, 2)** — `models.py`/2.5 dataclass 패턴(`@dataclass(frozen=True)` + `from __future__ import annotations`):
  - [x] 필드(필수 먼저, 기본값 뒤 — dataclass 순서 규칙): `id: str`, `target_id: str`(→ MonitoringTarget), `platform: Platform`, `collected_at: datetime`(호출부 주입 — 자동 `now()` 금지), `normalized_json: dict[str, Any]`, `parser_version: str`, `quality_state: SnapshotQualityState`, 그다음 추적 기본값 `tenant_id: str = ""`, `platform_account_id: str = ""`, `agent_id: str = ""`. [Source: data-api-contract.md(32·28), epics.md AC(546-547), src/rider_server/domain/tenant.py(11-16)]
  - [x] **`domain/`은 순수 유지 — `rider_crawl` import 금지.** `Snapshot`은 `Platform`(`.states`)·`SnapshotQualityState`만 import한다(2.5 도메인 모델은 의도적으로 rider_crawl-free). parser 출력→Snapshot **변환(bridge)** 은 Task 3의 서비스가 담당한다(레이어 분리). [Source: 2-5 스토리(98-99), architecture.md(277-279·482)]
  - [x] `normalized_json` 타입은 `dict[str, Any]`(`from typing import Any`). 짧은 주석으로 "안정적 키 + parser 출력 전량 보존(asdict)" 정책(architecture 301)을 남긴다. [Source: architecture.md(301-302)]
- [x] **Task 3 — `SnapshotNormalizer` 정규화 서비스 + fail-closed 예외: `services/snapshot_normalizer.py` (AC: 1, 2, 3)**
  - [x] **예외:** `class MissingSnapshotDataError(MissingPerformanceDataError)` — base는 **`from rider_crawl.parser import MissingPerformanceDataError`**(범용/배민 정본). 이렇게 "`MissingPerformanceDataError` 계승"(AC2)을 만족하고 기존 `except MissingPerformanceDataError`/`except ValueError`가 그대로 잡는다. **주의(2-class 함정):** `rider_crawl.platforms.coupang.parser`에도 **동명의 별개 클래스**가 있다 — base로는 **`rider_crawl.parser` 쪽만** import한다(혼동 금지). [Source: epics.md AC(551), src/rider_crawl/parser.py(11), src/rider_crawl/platforms/coupang/parser.py(9)]
  - [x] **parser_version 상수(server-side):** `_BAEMIN_PARSER_VERSION = "baemin.current_screen.v1"`, `_COUPANG_PARSER_VERSION = "coupang.peak_dashboard.v1"`. rider_crawl에 버전 필드가 없으므로 server-side에 둔다(rider_crawl 무변경). 출력 shape 변경 시 bump. [Source: architecture.md(301), implementation-contract.md(8)]
  - [x] **`@staticmethod def normalize(raw: CrawlSnapshotResult | None, *, snapshot_id: str, target_id: str, collected_at: datetime, tenant_id: str = "", platform_account_id: str = "", agent_id: str = "") -> Snapshot`** 로 둔다(순수·결정적; 내부 `now()`/`uuid4()` 금지 — 식별자·시각은 인자). [Source: src/rider_server/services/crawl_service.py(34-42), project-context.md(35)]
  - [x] **정규화 로직:** (1) `raw is None` → `raise MissingSnapshotDataError`(수집 결과 없음 — 기본 Snapshot 만들지 않음). (2) `type(raw)`로 `platform`·`parser_version` 결정: `CurrentScreenSnapshot` → `Platform.BAEMIN`/배민 버전, `PerformanceSnapshot` → `Platform.COUPANG`/쿠팡 버전, 그 외 → `raise MissingSnapshotDataError`(예상 외 shape). (3) `normalized_json = dataclasses.asdict(raw)`(중첩 dataclass 재귀 변환 — 안정적 키, JSON-safe). (4) **필수 의미값 검증**(아래 Dev Notes "필수 필드 계약"): 누락/None/빈-센티넬이면 `raise MissingSnapshotDataError`(0/기본값 주입 금지). (5) `return Snapshot(id=snapshot_id, target_id=target_id, platform=platform, collected_at=collected_at, normalized_json=normalized_json, parser_version=parser_version, quality_state=SnapshotQualityState.OK, tenant_id=..., platform_account_id=..., agent_id=...)`. [Source: epics.md AC(544-556), src/rider_crawl/models.py(6-71), project-context.md(36·88)]
  - [x] **import은 단방향만:** `from rider_crawl.models import CrawlSnapshotResult, CurrentScreenSnapshot, PerformanceSnapshot`, `from rider_crawl.parser import MissingPerformanceDataError`, `from rider_server.domain import Snapshot, SnapshotQualityState, Platform`. 역방향(`rider_crawl` → `rider_server`) 코드는 추가하지 않는다. [Source: project-context.md(64), src/rider_server/services/crawl_service.py(26-28)]
- [x] **Task 4 — 재노출 갱신: `domain/__init__.py` + `services/__init__.py` (AC: 1)**
  - [x] `domain/__init__.py`에 `Snapshot`·`SnapshotQualityState`를 **additive로** import·`__all__` 추가(2.5 8모델 재노출은 무삭제). → `from rider_server.domain import Snapshot, SnapshotQualityState`. [Source: src/rider_server/domain/__init__.py(11-52)]
  - [x] `services/__init__.py`에 `SnapshotNormalizer`·`MissingSnapshotDataError`를 **additive로** 추가(3.1/2.6 재노출 무삭제). → `from rider_server.services import SnapshotNormalizer`. docstring 1줄로 "3.2가 `Snapshot` 정규화·fail-closed를 같은 레이어에 additive로 추가" 명시. [Source: src/rider_server/services/__init__.py(13-34)]
- [x] **Task 5 — 테스트 추가: `tests/server/test_snapshot_normalize.py` (AC: 1~7)** — 외부 호출 없음(fake/in-memory), 가짜 값만. 평면 `tests/server/`에 두고(`__init__.py` 미추가 — 기존 컨벤션), `test_app.py`의 `_snapshot`/`_performance_snapshot` 동등 fixture를 자급자족으로 재구성(또는 `rider_crawl.models`로 직접 구성):
  - [x] **(AC1·AC3 happy path):** 배민 `CurrentScreenSnapshot` + 쿠팡 `PerformanceSnapshot` 각각을 `normalize(raw, snapshot_id="snap-1", target_id="mt-1", collected_at=<고정 datetime>)` 하면 `Snapshot` 필드(`platform`=BAEMIN/COUPANG, `parser_version`=각 상수, `quality_state`=OK, `target_id`/`collected_at` 보존)가 기대대로이고 `normalized_json == dataclasses.asdict(raw)`(필드 전량 보존·중첩 변환). 추적 필드 주입(`tenant_id="tnt-1"` 등) 시 그대로 보존. [Source: epics.md AC(544-556), tests/test_app.py(351-392)]
  - [x] **(AC2 fail-closed — 예외·기본값 금지):** `normalize(None, ...)` → `pytest.raises(MissingSnapshotDataError)`. `dataclasses.replace(baemin_snap, center_name="")`(필수 의미값 누락) → `pytest.raises(MissingSnapshotDataError)`. 두 케이스 모두 `MissingPerformanceDataError`(base)·`ValueError`로도 잡힘을 단언(계승 확인). 예외가 났을 때 **Snapshot이 반환되지 않음**(0/빈 값 주입 없음)을 확인. [Source: epics.md AC(549-551), src/rider_crawl/parser.py(11)]
  - [x] **(AC2 — Message 미진입, FR-7):** 합성 경로에서 (a) `normalize`가 raise하면 그 뒤 `MessageRenderService.render` fake가 **호출되지 않음**을 단언(예: `crawl → normalize → render` 순서를 try로 감싸고 render 호출 카운터가 0). (b) `CrawlService.crawl(config, crawl_snapshot=<MissingPerformanceDataError raise>)` → 예외 전파로 normalize/render **미도달**(3.1 AC3 계승). [Source: epics.md AC(552), 3-1 스토리(50·73), src/rider_server/services/message_render_service.py]
  - [x] **(AC1 결정성·frozen):** `Snapshot`이 `frozen`(`with pytest.raises(FrozenInstanceError): snap.id = ...`)이고, 같은 `raw`+같은 인자로 두 번 normalize하면 `normalized_json`·필드가 동일(내부 `now()`/`uuid4()` 미호출 — 결정적). [Source: 2-5 스토리(76), project-context.md(35)]
  - [x] **(회귀·누출):** fixture는 가짜 값(`"mt-1"`·`"snap-1"`·고정 `datetime(2026,1,1,...)`·기존 센터명 문자열)만. 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)/`chat_id=<digits>`/한국 휴대폰/이메일 원문 금지. `normalized_json`에 평문 secret이 없음을 확인. [Source: project-context.md(55·81), 3-1 스토리(76)]
- [x] **Task 6 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 **~808** — HEAD `807719b` 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_baemin_parser.py`·`test_coupang_parser.py`·`test_parser.py`·`tests/test_app.py`·`tests/server/test_*`) 신규 정규화 케이스만큼만 증가가 정상(순수 additive). [Source: 3-1 스토리(79·175), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `domain/snapshot.py`·`services/snapshot_normalizer.py`·`tests/server/test_snapshot_normalize.py` + `domain/states.py`·`domain/__init__.py`·`services/__init__.py`(재노출 추가)만** 보이고 **`src/rider_crawl/`·`pyproject.toml` 변경 0줄**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건, 그리고 `src/rider_crawl/`에 `rider_server` import가 **새로 생기지 않았음**(ast 기반 권장 — 단순 문자열 grep은 docstring 오탐, 3-1 Debug Log 참고)을 확인. [Source: project-context.md(64·81), 3-1 스토리(164)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: epic-2-retro-2026-06-13.md(115), 3-1 스토리(82·127)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `domain/snapshot.py`·`services/snapshot_normalizer.py`·`tests/server/test_snapshot_normalize.py` + `domain/states.py`(enum 추가)·`domain/__init__.py`·`services/__init__.py`(재노출 추가). **`src/rider_crawl/`·`pyproject.toml`·`CrawlService.crawl` 본문은 무변경.**
- **건드리지 않는다:** `rider_crawl` 전부(parser/models/message/app — 보존·재사용만), `CrawlService.crawl` 반환 타입(3.1 parity), Message/snapshot_id/template_version/text_hash(3.3), DeliveryRule fan-out(3.4), DeliveryLog/idempotency/dedup(3.5), 실패 상태 분류·재시도(3.6), Telegram 중앙(3.7), dry-run·승인(3.8), snapshots 테이블/ORM/Alembic/Pydantic·async·런타임 교체(Epic 5), Kakao 실제 전송(Epic 4). [Source: epics.md Story 3.3~3.8(558-693), architecture.md(420-432·514-517), implementation-contract.md(49-52)]

### 위치·레이어 결정 — 왜 도메인 모델(domain/)과 정규화 서비스(services/)를 분리하나 (반드시 읽을 것)

- **`Snapshot`은 도메인 레코드 → `domain/snapshot.py`(architecture 정본).** architecture.md(419-420)가 `rider_server/domain/{… snapshot.py / message.py / delivery.py}`를 명시하고, data-api-contract(14·32)가 Snapshot을 13 핵심 모델/테이블의 하나로 둔다. 2.5가 8개 도메인 모델을 깔았고, 본 스토리가 같은 디렉터리에 9번째(`Snapshot`)를 additive로 채운다. [Source: architecture.md(417-420), data-api-contract.md(14·32), src/rider_server/domain/__init__.py(31-52)]
- **정규화(bridge)는 서비스 → `services/snapshot_normalizer.py`.** 정규화는 `rider_crawl.models`(parser 출력)·`rider_crawl.parser.MissingPerformanceDataError`를 import해 도메인 `Snapshot`으로 변환하는 **rider_crawl↔domain 브리지**다. 2.5 도메인 모델은 의도적으로 **rider_crawl-free**(순수 레코드)이므로, 이 import 결합을 `domain/snapshot.py`(classmethod 등)에 넣으면 도메인 레이어 순수성이 깨진다. 따라서 변환 로직은 **서비스 레이어**(3.1 `services/`)에 둔다 — `domain`=순수 레코드, `services`=정책/변환. [Source: 2-5 스토리(98-99), architecture.md(277-279·487-490), src/rider_server/domain/tenant.py(1-16)]
- **`CrawlService.crawl` 무변경 = 3.1 parity 보존.** 3.1 `crawl`은 `CrawlSnapshotResult`를 반환하고 그 위에서 run_once parity(message/sent/message_hash 동등)가 잠겨 있다. 정규화를 crawl에 합치면 parity가 깨지므로 **별도 seam**(`SnapshotNormalizer.normalize`)으로 둔다. 런타임에서 crawl→normalize→render를 잇는 wiring은 Epic 5(UI는 계속 run_once). [Source: 3-1 스토리(40·60·170·173), src/rider_server/services/crawl_service.py(34-47)]

### `Snapshot` 필드 ↔ 계약 매핑 (AC1 — 정밀 계약)

| 필드 | 타입 | 출처/근거 |
|---|---|---|
| `id` | `str` | data-api-contract `snapshots.id`(32). 호출부 주입(서비스 내부 `uuid4()` 금지). |
| `target_id` | `str` | `snapshots.target_id`(32) → MonitoringTarget. **추적: 대상**(AC2). |
| `platform` | `Platform` | **P2-02 명시 필드**(48). bare table엔 없으나 spec이 요구(2.5 `center_name` 추가 선례와 동형). `raw` 타입에서 derive. |
| `collected_at` | `datetime` | `snapshots.collected_at`(32). **추적: 실행 시각**(AC2). 호출부 주입(자동 `now()` 금지). |
| `normalized_json` | `dict[str, Any]` | `snapshots.normalized_json`(32) + architecture(301) "안정적 키 + parser_version". `asdict(raw)` 전량 보존. |
| `parser_version` | `str` | `snapshots.parser_version`(32). server-side 상수(rider_crawl에 버전 없음). |
| `quality_state` | `SnapshotQualityState` | `snapshots.quality_state`(32). 정규화 성공 → `OK`. |
| `tenant_id` | `str = ""` | **추적: 고객**(AC2). bare table은 target_id→MonitoringTarget→tenant_id로 derive하나 AC2가 직접 추적성 요구 → 필드로 보유(미상 시 ""). |
| `platform_account_id` | `str = ""` | **추적: 플랫폼계정**(AC2). 동(미상 시 ""). |
| `agent_id` | `str = ""` | **추적: Agent**(AC2). Agent 모델은 Epic 4/5 — `str` placeholder FK(2.5 `agent_id` 선례). |

- **추적 필드 default `""` 근거:** 본 스토리는 런타임 미배선이라 `tenant_id`/`platform_account_id`/`agent_id`는 Epic 5 wiring 전까지 미상일 수 있다. `target_id`·`collected_at`은 정규화에 필수라 default 없음. `name`이 bare table에 없어도 AC가 요구하면 추가했던 2.5 `MonitoringTarget.center_name`과 동일한 "계약 bare table + AC 명시 요구" 패턴. [Source: 2-5 스토리(114-115·67), data-api-contract.md(28·35)]
- **forward-ref FK는 `str`:** `agent_id`(Agent=Epic4/5)는 모델 import 없이 `str`. [Source: 2-5 스토리(115)]

### 필수 필드 계약 — 정규화 fail-closed 게이트 (AC2 — 핵심)

- **parser가 이미 1차 fail-closed를 한다(보존·재사용).** `rider_crawl/parser.py`·`platforms/coupang/parser.py`는 파싱 중 필수 데이터가 없으면 `MissingPerformanceDataError`를 **던진다**(예: 배민 배달현황 필수 열 누락 535, 빈 값 541, 숫자 변환 실패 545; 쿠팡 78·162·174). 즉 `CrawlService.crawl`이 `CrawlSnapshotResult`를 **반환했다는 것 자체가** parser 1차 검증 통과를 뜻하고, 실패 시 crawl이 예외를 전파해 정규화에 **도달조차 안 한다**(3.1 AC3). 본 스토리는 그 예외를 **삼키지 않고 전파**(`try/except`로 기본 Snapshot 만들지 않음)만 보장하면 된다. [Source: src/rider_crawl/parser.py(11·535-558), src/rider_crawl/platforms/coupang/parser.py(9·78-174), 3-1 스토리(50·117)]
- **정규화 2차 게이트(본 스토리 신규, defense-in-depth):** parser 출력이 **구조적으로 present여도 의미적으로 비면** 잘못된 메시지로 이어질 수 있다. 정규화는 다음을 검증하고 누락 시 `MissingSnapshotDataError`를 raise한다(0/기본값 주입 금지):
  - `raw is None` → raise(수집 결과 없음).
  - 예상 외 타입(`CurrentScreenSnapshot`/`PerformanceSnapshot` 아님) → raise.
  - **배민(`CurrentScreenSnapshot`):** `center_name`이 빈 문자열/공백이면 raise. **근거:** project-context §88 — "쿠팡 탭에서 `center_name`이 비어 있거나 배민 기본값이면 **다른 계정 실적 오발송 위험**"이라 fail-closed의 1순위 의미값이다. (수치 필드는 parser가 이미 보장 — 추가 0-체크는 과검증이라 하지 않는다.)
  - **쿠팡(`PerformanceSnapshot`):** `peak_dashboard`가 없으면 raise(필수 — dataclass상 항상 present지만 방어적 None 체크). `current_screen`은 **선택값**(쿠팡은 보통 `None`이 정상 — models.py 61-65)이므로 **검증 대상 아님**(정상 None을 누락으로 오판하지 말 것). [Source: project-context.md(88·36), src/rider_crawl/models.py(59-66), epics.md AC(549-551)]
- **"실패로 기록"(AC2 원문)의 본 스토리 해석:** 런타임 미배선·DB 없음(Epic 5)이라 "기록"의 구체 형태는 **명확한 예외 raise + 전파**(미발송)다. 실패를 운영 상태값/카테고리(`crawl_failure` 등)로 분류·persist하는 것은 **3.6/Epic 5** 소유 — 본 스토리는 끌어오지 않는다. `SnapshotQualityState.MISSING_REQUIRED`는 그 persist 어휘로 값만 미리 둔다. [Source: epics.md AC(551)·Story 3.6(623-643), architecture.md(323-328)]

### `MissingPerformanceDataError` 계승 — 2-class 함정 주의 (AC2)

- **base는 `rider_crawl.parser.MissingPerformanceDataError`**(11행, `ValueError` 계승). `MissingSnapshotDataError(MissingPerformanceDataError)`로 두면 (a) AC2의 "계승" 충족, (b) 기존 `except MissingPerformanceDataError`/`except ValueError` 코드·테스트가 그대로 잡음.
- **함정:** `rider_crawl/platforms/coupang/parser.py`(9행)에 **동명의 별개 클래스**가 있다(서로 subclass 관계 아님). base로는 **`rider_crawl.parser` 쪽만** import한다. 쿠팡 crawl이 던지는 coupang쪽 예외도 결국 `ValueError`라 전파(미발송)는 동일하게 성립하지만, 본 스토리의 normalize가 **새로 raise**하는 예외 계층은 `rider_crawl.parser` 정본을 base로 통일한다. [Source: src/rider_crawl/parser.py(11), src/rider_crawl/platforms/coupang/parser.py(9), grep MissingPerformanceDataError]

### `normalized_json` 직렬화 (AC1·AC3 — 안정적 키·parser 보존)

- `dataclasses.asdict(raw)`를 쓴다 — `CurrentScreenSnapshot`(평면), `PerformanceSnapshot`(중첩 `current_screen`/`peak_dashboard`/`PeakPeriodSnapshot`)를 **재귀로 dict 변환**해 안정적 키와 parser 출력 전량을 보존한다(필드 추가·삭제·기본값 주입 0 → AC3 동등성). 모든 필드가 `str`/`int`/`float`/`None`이라 JSON-safe(architecture 297-298 시각은 문자열). 별도 키 재명명·정렬·필터 금지(보존이 목적). [Source: src/rider_crawl/models.py(6-71), architecture.md(297-302)]
- `quality_state`는 `normalized_json` **밖**의 `Snapshot` 필드다(데이터 vs 품질 메타 분리). `parser_version`도 `Snapshot` 필드로 둔다(architecture 301이 "normalized_json에 parser_version 포함"이라 했으나, 계약 `snapshots` 테이블은 `parser_version`을 **별도 컬럼**으로 둔다(32) — 컬럼 정본을 따르고, normalized_json은 parser 출력 순수 보존만). [Source: data-api-contract.md(32), architecture.md(301)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml` 무변경** — `git diff -w` = `domain/`·`services/` 신규/재노출 + 신규 테스트만. (b) **의존성 단방향** — `rider_server → rider_crawl`만, 역방향 0(ast 가드 권장). (c) **`CrawlService.crawl` 무변경** — 3.1 parity 보존. (d) **parser 출력 동등** — `normalized_json == asdict(raw)`, 필드 주입/삭제 0(AC3). (e) **fail-closed** — 필수 누락 시 raise(0/기본값 금지). (f) **순수·결정적** — 서비스/모델 내부 `datetime.now()`/`uuid4()` 금지(인자 주입). (g) **frozen 불변** — `Snapshot`은 `@dataclass(frozen=True)`. (h) **쿠팡 `current_screen=None`은 정상** — 누락으로 오판 금지. [Source: project-context.md(35·36·64·82), src/rider_crawl/models.py(59-66), 3-1 스토리(121)]

### 이전 스토리 인텔리전스 (Epic 2 → 3.1 → 3.2 이월 교훈)

- **3.1이 본 스토리에 남긴 명시 위임:** `crawl_service.py` docstring(8-10)·3-1 범위 노트(21)가 "정규화 Snapshot(platform/target_id/collected_at/parser_version/quality_state)+fail-closed = **Story 3.2**"라 못 박았다. 본 스토리는 정확히 그 경계만 채운다 — 3.1 `crawl` 본문은 무변경. [Source: src/rider_server/services/crawl_service.py(8-10), 3-1 스토리(21)]
- **무회귀 비결 = "새 필드가 아니라 새 뷰/레코드"**(epic-2-retro 64-67·149): 2.3 `@property` 별칭, 2.6 `SubscriptionStateChange` 별도 레코드, 3.1 신규 서비스 경계처럼, 3.2는 **기존 parser 출력을 갈아엎지 않고 정규화 레코드 `Snapshot`을 옆에 추가**(asdict로 감싸기만). 가장 비침습적. [Source: epic-2-retro-2026-06-13.md(64-69·149), 3-1 스토리(126)]
- **값 정의 vs 로직 분리(2.5 선례):** 2.5가 `SubscriptionStatus` **값만** 정의하고 게이트 평가는 2.6에 뒀듯, 3.2는 `SnapshotQualityState.MISSING_REQUIRED` **값만** 두고 실패 persist/분류는 3.6/Epic 5에 둔다. [Source: 2-5 스토리(42·57)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2 전 스토리가 dev 수치 stale로 MEDIUM 재발, 3.1도 799→808 정정됐다. 기준선 ~808(3.1 종료)은 **참고값**(본인 재측정). [Source: epic-2-retro-2026-06-13.md(49·115), 3-1 스토리(127·197)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 선행 작업으로 권고. `.pre-commit-config.yaml`은 TEA/도구 소유라 본 스토리 AC에 포함하지 않되, dev는 신규 코드·테스트 평문 secret 0건을 **수동 grep**으로 확인한다. [Source: epic-2-retro-2026-06-13.md(114·129), 3-1 스토리(128)]
- **(str, Enum) 규약·`str()` 함정(2.5/2.7 관찰):** `SnapshotQualityState`는 `(str, Enum)`·멤버이름==값(대문자)·직렬화 `.value`/`==`(2.5/2.6 선례). [Source: 2-5 스토리(55), epic-2-retro-2026-06-13.md(81)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/domain/snapshot.py`, `src/rider_server/services/snapshot_normalizer.py`, `tests/server/test_snapshot_normalize.py`. 수정(재노출 추가): `domain/states.py`(`SnapshotQualityState`), `domain/__init__.py`, `services/__init__.py`. `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64), architecture.md(417-429)]
- **`domain/`·`services/` 채움:** architecture(417-429)가 정본 위치 — `domain/snapshot.py`(419), `services/`(425-429, 2.6 `subscription_gate.py`·3.1 세 서비스와 동거). Epic 5가 같은 디렉터리에 `db/models/`·`idempotency.py`·async wiring을 additive로 덧붙인다. [Source: architecture.md(417-429)]
- **테스트 위치:** 평면 `tests/server/`(현재 `test_domain_*.py`·`test_subscription_gate.py`·`test_run_once_split.py`)에 `test_snapshot_normalize.py` 추가. `__init__.py` 미추가(평면 컨벤션, basename 고유). 기존 parser 회귀는 `tests/test_baemin_parser.py`·`test_coupang_parser.py`·`test_parser.py`(무변경)가 계속 보증. [Source: tests/server/, tests/test_baemin_parser.py, pyproject.toml(testpaths)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]`로 `rider_server.domain`·`rider_server.services` import 동작(서버 패키징은 Epic 5). [Source: pyproject.toml(pythonpath), 3-1 스토리(138)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.2(536-557)] — Epic 3 의도(한 번 수집 → 정규화 Snapshot → 여러 채널), Story 3.2 user story·3 AC 원문(Snapshot 필드·추적성, 필수데이터 누락 시 예외·Message 미생성, 배민/쿠팡 fixture 보존).
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.1(515-534)·#Story-3.3~3.8(558-693)] — 직전 스토리(서비스 3-분리)와 다운스트림 위임처: 3.3 Message/snapshot_id/text_hash, 3.4 fan-out, 3.5 DeliveryLog/idempotency(snapshot_collected_at), 3.6 실패 상태 분리, 3.7 Telegram, 3.8 dry-run(본 스토리 아님).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(7-8·12·48)] — Reuse 표("Baemin parser → Wrap output in normalized Snapshot and keep fixture tests", "Coupang peak-dashboard parser → parser version recording", "Existing tests → merge regression with new domain/service tests"), **P2-02("Define Snapshot with platform, target_id, collected_at, normalized_data, parser_version, quality_state. | Baemin/Coupang snapshot fixture tests pass.")**.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(14·32·301-304·146-156)] — Snapshot 모델 계약("Normalized crawl result with parser version and quality state"), `snapshots` Required fields(id, target_id, collected_at, normalized_json, parser_version, quality_state), dedup key 5필드(=3.5, snapshot_collected_at 포함).
- [Source: _bmad-output/planning-artifacts/architecture.md(254·277-279·297-304·318·417-429·482·487-490·514-526)] — 도메인/서비스 레이어 분리, (str,Enum) 대문자 정본, normalized_json 안정적 키·필수누락 시 MissingPerformanceDataError 계승, 단방향 import, `domain/snapshot.py`·`services/` 위치, fail-closed/오발송 금지 anti-pattern(363), 데이터 흐름(CrawlJob→Snapshot→Message).
- [Source: src/rider_crawl/models.py(6-71)] — 재사용/wrapping 대상: `CurrentScreenSnapshot`(평면), `PerformanceSnapshot`(`current_screen: …|None`(선택)·`peak_dashboard`), `PeakDashboardSnapshot`/`PeakPeriodSnapshot`(중첩), `CrawlSnapshotResult = CurrentScreenSnapshot | PerformanceSnapshot`.
- [Source: src/rider_crawl/parser.py(11·535-558)·platforms/coupang/parser.py(9·78-174)] — `MissingPerformanceDataError`(ValueError 계승) **2개 정의**(base는 parser.py 정본), parser 1차 fail-closed 지점들(필수 열/빈 값/숫자 변환 실패).
- [Source: src/rider_server/services/crawl_service.py(8-10·34-47)] — 3.1 `CrawlService.crawl`(무변경 대상)과 docstring의 3.2 위임 명시(정규화 Snapshot·fail-closed → Story 3.2).
- [Source: src/rider_server/domain/states.py(1-11·97-103)·__init__.py(11-52)·tenant.py(1-16)] — enum/dataclass 정본 패턴((str,Enum) 대문자, frozen dataclass, datetime 주입·자동 now() 금지, 명시 재노출) — 본 스토리가 따를 패턴.
- [Source: tests/test_app.py(316-392)·tests/server/test_run_once_split.py(1-55)] — `_config`/`_snapshot`/`_performance_snapshot` fixture(정규화 테스트 재사용), 평면 tests/server/ 자급자족 컨벤션.
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-06-13.md(49·64-69·81·114-115·149)] — "새 뷰/레코드" 무회귀 패턴, 값 정의 vs 로직 분리, A1′(secret 게이트)·A2′(수치 단일 정본), (str,Enum) 규약.
- [Source: _bmad-output/project-context.md(35·36·58·64·81·82·88)] — 순수·결정성, 파서 오류 조용히 기본값 금지(`MissingPerformanceDataError`), 파서 변경 정상/누락 케이스 동시 테스트, 단방향 의존, secret 비노출, 범위 규율, 쿠팡 center_name 비면 오발송 위험.
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P2-02/FR-7(Snapshot 정의·필수누락 fail-closed)·NFR-1·2(오발송보다 미발송·필수데이터 누락 시 미발송)·implementation-contract Reuse(parser wrapping·fixture 보존). Message/hash=3.3, fan-out=3.4, DeliveryLog/idempotency=3.5, 실패상태=3.6, Telegram=3.7, dry-run=3.8, snapshots 테이블/ORM/async·런타임 교체=Epic 5, Kakao 전송=Epic 4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, bmad-dev-story workflow)

### Debug Log References

- 전체 스위트 1회 실행 시 기존 `tests/server/test_domain_models.py::test_package_all_reexports_eight_models_and_all_enums` 가 `domain.__all__` 집합을 **정확히** 잠그고 있어, Snapshot/SnapshotQualityState **additive** 재노출로 1건 실패. 이는 회귀가 아니라 계약의 additive 확장이므로(2.5가 모델 추가 시 동일 패턴) `expected` 집합과 `model_names` 에 두 심볼을 additive로 추가해 정정 → 재실행 green.
- `git diff -w --stat` 로 범위 확인: `src/rider_crawl/`·`pyproject.toml` **0줄**(`git diff --numstat` 빈 출력). ast 기반 가드로 `rider_crawl → rider_server` 역방향 import **0건** 재확인.

### Completion Notes List

- **AC1 (정규화 Snapshot + 추적성):** `domain/snapshot.py` 에 frozen `Snapshot`(id·target_id·platform·collected_at·normalized_json·parser_version·quality_state + 추적 default `tenant_id`/`platform_account_id`/`agent_id=""`) 정의. `SnapshotNormalizer.normalize` 가 `type(raw)` 로 platform·server-side `parser_version`(`baemin.current_screen.v1`/`coupang.peak_dashboard.v1`)을 derive하고 `dataclasses.asdict(raw)` 로 parser 출력 전량을 안정적 키로 보존. 식별자·시각은 호출부 주입(내부 `now()`/`uuid4()` 0 — 결정적).
- **AC2 (fail-closed):** `MissingSnapshotDataError(MissingPerformanceDataError)` 로 base를 `rider_crawl.parser` 정본에 통일(coupang 동명 별개 클래스 미사용). `raw is None`·예상 외 타입·배민 `center_name` 공백·쿠팡 `peak_dashboard` None 시 raise(0/기본값 주입 금지). 합성 경로 테스트로 normalize raise 시 render 미호출, crawl의 `MissingPerformanceDataError` 전파 시 normalize/render 미도달(3.1 AC3 계승) 단언.
- **AC3 (parser 동작 보존):** 배민/쿠팡 fixture가 `quality_state=OK` 로 통과하고 `normalized_json == dataclasses.asdict(raw)`(중첩 재귀 변환 포함) 잠금. 쿠팡 `current_screen=None` 정상 케이스를 누락으로 오판하지 않음을 단언. `src/rider_crawl/` 0줄 변경으로 기존 parser/run_once 회귀 그물 전부 통과.
- **순수 additive 확인:** 신규 3파일 + 재노출/enum/회귀-net 4파일 수정만. 단방향 import 유지, 신규 코드·테스트 평문 secret 0건.
- **테스트:** 신규 `tests/server/test_snapshot_normalize.py` **25 케이스 수집**(QA gap-fill +8 포함 — `parametrize` 전개 기준). 전체 스위트 재측정 **833 passed, 0 failed**(`.venv/Scripts/python.exe -m pytest -q`, 리뷰 시점 재측정 — A2′ 단일 정본).

### File List

- `src/rider_server/domain/snapshot.py` (신규) — frozen `Snapshot` 도메인 모델
- `src/rider_server/services/snapshot_normalizer.py` (신규) — `SnapshotNormalizer` + `MissingSnapshotDataError`
- `tests/server/test_snapshot_normalize.py` (신규) — 정규화/fail-closed/보존 테스트 17건
- `src/rider_server/domain/states.py` (수정) — `SnapshotQualityState` enum additive 추가
- `src/rider_server/domain/__init__.py` (수정) — `Snapshot`·`SnapshotQualityState` 재노출 additive
- `src/rider_server/services/__init__.py` (수정) — `SnapshotNormalizer`·`MissingSnapshotDataError` 재노출 additive
- `tests/server/test_domain_models.py` (수정) — `domain.__all__` 회귀-net에 9번째 모델·신규 enum additive 반영

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome:** Approve (auto-fix 적용 후)

**범위·진실성 검증**
- 변경 파일 7건이 git 현실과 정확히 일치(File List == `git status`의 소스 파일). `src/rider_crawl/`·`pyproject.toml` **0줄**(`git diff --numstat 807719b` 빈 출력)으로 순수 additive·의존성 단방향(`rider_server → rider_crawl`) 보존 확인 — **AC7/AC3 충족**.
- **AC1**(정규화 `Snapshot` 필드·추적성·`normalized_json` 전량 보존)·**AC2**(필수누락 시 `MissingSnapshotDataError` raise·계승·Message 미진입)·**AC3**(배민/쿠팡 fixture 동등성·품질 메타 분리) 모두 코드+테스트로 IMPLEMENTED. Task 1~6 모두 실제 완료 확인.
- 재측정 전체 스위트 **833 passed, 0 failed**(`.venv/Scripts/python.exe -m pytest -q`), 신규 파일 **25 케이스 수집**.

**Findings & 조치 (auto-fix)**
- 🟡 **MEDIUM — Dev Agent Record 테스트 수치 stale(A2′ 재발).** Completion Notes/Change Log가 `17 케이스 / 825 passed`로 남아 있었으나 실제는 `25 케이스 / 833 passed`(QA gap-fill +8 미반영). 다운스트림 `test-summary-3.2.md`는 833으로 정정됐지만 스토리 본문은 미동기화 → 정정 완료(단일 정본 = 리뷰 시점 재측정값).
- 🟢 **LOW — `test_crawl_missing_data_does_not_reach_normalize_or_render` 의 도달 불가 라인 타입 오류.** `MessageRenderService.render(snap)`가 도메인 `Snapshot`을 전달(render는 3.1대로 `CrawlSnapshotResult`를 받음). 도달 불가(crawl이 먼저 raise)라 테스트는 통과했으나 파이프라인 오개념 → `render(raw)`로 정정.

## Change Log

| 날짜 | 변경 | 비고 |
|---|---|---|
| 2026-06-13 | Story 3.2 구현 — 정규화 `Snapshot` 레코드 + `SnapshotNormalizer` fail-closed 정규화 게이트(순수 additive, `rider_crawl` 0줄). | Status → review |
| 2026-06-13 | Senior Developer Review (AI) — AC1~3·Task1~6 검증 통과. auto-fix 2건: (MEDIUM) Dev Agent Record 테스트 수치 825/17 → **833/25** 정정(A2′), (LOW) 테스트 `render(snap)` → `render(raw)` 타입 정정. 재측정 **833 passed, 0 failed**. | Status → done |
