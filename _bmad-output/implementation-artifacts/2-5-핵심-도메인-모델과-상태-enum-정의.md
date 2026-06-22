---
baseline_commit: a155b31
---

# Story 2.5: 핵심 도메인 모델과 상태 enum 정의

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 고객·구독·플랫폼계정·대상·브라우저프로필·채널·전송규칙·secret 참조 등 **핵심 ID 기반 도메인 모델(dataclass)** 과 **상태 enum(Customer lifecycle / Subscription 실행 게이트 / Baemin auth state)** 을 `data-api-contract`의 계약(필드·관계)에 맞게 **순수 정의(pure definition)** 로 만들고 싶다,
so that 이후 수집(Epic 3)·전송·Agent(Epic 4)·서버(Epic 5) 에픽이 **동일한 ID 기반 도메인 모델과 상태값 정본(single source of truth)** 을 공유하고, 탭 순번이 아니라 안정적인 ID와 대문자 enum 문자열로 운영 상태를 일관되게 식별한다(ADD-7·ADD-9, FR-4·FR-30).

> **이 스토리의 성격 — "순수 도메인 정의(dataclass + Enum)만." 영속(DB/Alembic)도, 서비스 로직도, 기존 `UiSettings`/`AppConfig`와의 wiring도 아니다.** ADD-7(도메인 모델)·ADD-9(상태머신)의 본 스토리 deliverable은 **"`data-api-contract`의 8개 모델과 3개 상태머신이 frozen dataclass + `(str, Enum)`로 계약대로 정의되고, 관계(FK id)·soft delete 표현·대문자 enum 문자열이 테스트로 잠긴다"** 이다. **PostgreSQL 13 테이블·SQLAlchemy ORM·Alembic 마이그레이션은 Epic 5(P4-02) 소유이고, 본 스토리는 만들지 않는다.** Pydantic v2 API 스키마(`schemas/`)도 Epic 5 소유다 — 본 스토리는 **도메인 dataclass만** 둔다(architecture.md 171·303 "dataclass는 Agent 내부 도메인 객체에 유지, API 경계=Pydantic v2"). [Source: epics.md Epic 2(353-355)·Story 2.5(440-461), architecture.md(166-171·415-419·499), implementation-contract.md P4-02(71)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **오직** 신규 패키지 `src/rider_server/domain/`에 (1) **8개 dataclass**(Tenant, Subscription, PlatformAccount, MonitoringTarget, BrowserProfile, MessengerChannel, DeliveryRule, SecretRef), (2) **상태/지원 enum**(`CustomerLifecycleState`, `SubscriptionStatus`, `BaeminAuthState` + `Platform`/`Messenger`/`SecretStorageClass` + 대상/채널/프로필 상태 enum), (3) `domain/__init__.py` 재노출, (4) 신규 테스트만 추가한다. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부(기존 코드 무변경).** `ui_settings.py`·`config.py`(`AppConfig`)·`secret_store.py`·`models.py`·`message.py`·`sender.py`·`platforms/`·`messengers/` 등 **단 한 줄도 바꾸지 않는다.** 본 스토리는 **신규 패키지 추가만** 하는 **순수 additive** 작업이다. 2.1~2.4가 진화시킨 `UiSettings`(ID 발급·atomic write·중립 필드·`*_ref` 분리)와 신규 도메인 모델의 **wiring/마이그레이션은 Story 2.7**(ADD-16, FR-31)이 한다. [Source: epics.md Story 2.7(487-509), 2-4 스토리 범위 경계]
> - **DB 테이블/ORM/Alembic·`db/models/`** → **Epic 5 Story 5.2**(P4-02). 본 스토리는 **테이블·컬럼·인덱스·유니크 제약을 만들지 않는다** — `data-api-contract`의 **필드 이름·관계만** dataclass로 표현한다. [Source: implementation-contract.md P4-02(71), architecture.md(420-422)]
> - **Pydantic v2 API 스키마·`schemas/`·`to_api_model`/`from_api_model` 변환** → **Epic 5**. [Source: architecture.md(171·303-304·423)]
> - **`secret_store.py`(Story 2.4) 재배선/치환 금지.** 2.4의 `SECRET_STORAGE_*` 소문자 문자열 분류와 `LocalFileSecretStore` seam은 **그대로 둔다.** 본 스토리는 정식 `SecretRef` dataclass와 대문자 `SecretStorageClass` enum을 **새로 정의만** 하고, 2.4 seam을 그 enum으로 갈아끼우지 **않는다**(reconcile는 Epic 5 DB/secret 레이어). [Source: src/rider_crawl/secret_store.py(20-39), 2-4 스토리(20)]
> - **Snapshot·Message·DeliveryLog 모델** → **Epic 3**(P2-02/03/05). **Agent·Job·AuthSession 모델** → **Epic 4/5**. `data-api-contract`는 13 모델을 정의하지만 본 스토리 AC가 명시하는 건 **8개**뿐이다 — 나머지 5개는 끌어오지 않는다. FK 참조(`agent_id`, `template_id`)는 아직 모델 없는 **forward-reference이므로 `str` ID 필드로만** 둔다. [Source: epics.md Story 2.5 AC1(450), implementation-contract.md P2(46-52)·P3-P4, data-api-contract.md(14-18)]
> - **`SubscriptionGate` 실행 게이트 로직** → **Story 2.6**(FR-6). 본 스토리는 `SubscriptionStatus` enum **값만** 정의하고, "ACTIVE가 아니면 job 차단" 같은 **평가 로직은 만들지 않는다**. [Source: epics.md Story 2.6(463-485)]
>
> **기준선 회귀 0(NFR-20).** 현재 HEAD(`a155b31`, Story 2.4 done)에서 2.4 리뷰 정본 수치는 **670 passed**(참고값 — 복사 금지, 본인이 `.venv/Scripts/python.exe -m pytest -q`로 재측정). 본 스토리는 **신규 파일만 추가**하므로 기존 670개는 **한 개도 깨지지 않아야** 하고 신규 테스트 케이스만큼만 증가가 정상이다. **A2 교훈: dev 노트에 잠정 pass 수치를 박아 stale를 만들지 말 것 — 리뷰 시점 재측정값 1개만 정본으로 기록한다.** [Source: 2-4 스토리(27·177), epic-1-retro 액션 A2, memory/dev-env-quirks]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 본 스토리는 **순수 정의**라 실제 secret을 다루지 않지만, 테스트 fixture에도 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 명백한 가짜값(`"tnt-1"`, `"vault://…/ref"`, `"-100123"`)만 쓴다. `SecretRef`는 **참조 핸들만** 들고 **평문 값을 절대 필드로 갖지 않는다**(SecretRef의 존재 이유 = 평문을 모델 밖에 두는 것). [Source: project-context.md(81·89), data-api-contract.md(19), epic-1-retro 액션 A1]

## Acceptance Criteria

**AC1 — 8개 핵심 도메인 모델을 `data-api-contract` 계약(필드·관계)에 맞게 dataclass로 정의 (ADD-7, FR-4)**

1. **Given** ID 기반 운영 모델이 필요할 때 **When** 도메인 모델을 frozen `dataclass`로 정의하면(ADD-7, FR-4) **Then** `Tenant`, `Subscription`, `PlatformAccount`, `MonitoringTarget`, `BrowserProfile`, `MessengerChannel`, `DeliveryRule`, `SecretRef` **8개**가 `data-api-contract`의 **Required Tables 필드 이름**과 **관계(FK id)** 에 맞게 정의되고, `from rider_server.domain import Tenant, MonitoringTarget, …`로 임포트 가능하다(`domain/__init__.py` 재노출). [Source: data-api-contract.md Core Domain Models(5-19)·Required Tables(21-38), architecture.md(415-419)]
2. **And** 모니터링 대상은 **플랫폼·계정(`platform_account_id`)·기대 센터/상점명(`center_name`)·URL(`url`)·식별자(`external_id`)·연결된 브라우저 프로필(`BrowserProfile.target_id` 역참조)** 을 가지며(FR-20 연계 — `center_name`은 쿠팡 기대 센터/상점명 검증의 정본), `PlatformAccount`는 평문 자격증명이 아니라 **`SecretRef` 참조**(`username_ref`/`password_ref`)로 secret을 가리킨다. [Source: epics.md AC1(450-452), data-api-contract.md(10·27·9), 2-3 스토리(center_name 중립 필드)]
3. **And** 전송 규칙은 **하나의 대상에서 하나 이상의 채널로** 연결될 수 있다(FR-9 토대): `DeliveryRule`은 `(target_id, channel_id)` 매핑이고, 같은 `target_id`에 **`channel_id`가 다른 여러 `DeliveryRule` 인스턴스**로 fan-out을 표현한다(한 대상 → 2개 이상 채널이 테스트로 표현 가능). [Source: epics.md AC1(452), data-api-contract.md(13·31), implementation-contract.md P2-04(50)]

**AC2 — 상태 enum을 대문자 enum 문자열로 정의, MVP 핵심 상태 구분 (ADD-9, FR-30)**

4. **Given** 상태값이 코드/DB/API에서 일관돼야 할 때 **When** 상태 enum을 정의하면(ADD-9, FR-30) **Then** **Customer lifecycle**(`LEAD → SIGNED_UP → PAYMENT_ACTIVE → SETUP_PENDING → PLATFORM_AUTH_PENDING → MESSENGER_VERIFY_PENDING → TEST_RUNNING → ACTIVE → DEGRADED → AUTH_REQUIRED → SUSPENDED`)과 **Baemin auth state**(`UNKNOWN / ACTIVE / AUTH_REQUIRED / USER_ACTION_PENDING / AUTH_VERIFIED / CENTER_MISMATCH / BLOCKED_OR_CAPTCHA`)가 **`(str, Enum)` 대문자 문자열**(멤버 이름 == 값)로 정의되고, JSON/DB 직렬화 시 대문자 문자열로 나간다(`CustomerLifecycleState.ACTIVE == "ACTIVE"`). [Source: epics.md AC2(454-457), data-api-contract.md State Machines(94-131), architecture.md(254·318)]
5. **And** `ACTIVE`/`AUTH_REQUIRED`/`DEGRADED`/`SUSPENDED`가 MVP에서 **서로 구분되는 별개 멤버**로 존재한다(`CustomerLifecycleState`에 4개 모두 존재·상호 구별). **And** **Subscription 실행 게이트** 상태(`PAYMENT_ACTIVE / PAYMENT_FAILED_GRACE / SUSPENDED / CANCELLED`)가 별도 enum(`SubscriptionStatus`)으로 정의된다(게이트 **평가 로직**은 Story 2.6 소유 — 본 스토리는 **값만**). [Source: epics.md AC2(457), data-api-contract.md Subscription execution gate(112-119), epics.md Story 2.6(471-475)]

**AC3 — soft delete / inactive 상태로 운영 이력 보존, 물리 삭제 금지 (FR-4)**

6. **Given** 삭제 대신 비활성화를 지원해야 할 때 **When** 대상/채널/규칙을 비활성화하면 **Then** **`MonitoringTarget`(`status`에 `INACTIVE`)·`MessengerChannel`(`state`에 `INACTIVE`)·`DeliveryRule`(`enabled=False`)** 로 비활성을 **상태 전이**로 표현하고, 모델은 **물리 삭제(필드/이력 제거)가 아니라 상태값 변경**으로 비활성화를 나타낸다(frozen dataclass이므로 `dataclasses.replace`로 status/enabled만 바꾼 **새 인스턴스**를 만들고 나머지 식별·이력 필드는 보존). [Source: epics.md AC3(459-461), data-api-contract.md(28·30·31), architecture.md(317-319)]
7. **And** soft delete가 운영 이력을 보존함을 테스트로 잠근다: 비활성 전이 후에도 `id`·관계 FK·생성 정보가 그대로 남고(물리 삭제 아님), "비활성"을 판별 가능한 상태값이 존재한다(`INACTIVE`/`enabled=False`). [Source: epics.md AC3(459-461), data-api-contract.md(28)]

## Tasks / Subtasks

- [x] **Task 1 — 신규 패키지 골격 생성 (AC: 1)**
  - [x] `src/rider_server/__init__.py`(짧은 docstring만), `src/rider_server/domain/__init__.py`를 만든다. `pyproject.toml`의 `pythonpath = ["src"]` 덕분에 별도 설치 없이 `import rider_server.domain.*`가 동작한다(검증: 테스트가 임포트만으로 통과). **`pyproject.toml`은 수정하지 않는다** — `[tool.hatch.build.targets.wheel] packages = ["src/rider_crawl"]`(34-35)는 Agent 배포(PyInstaller) 경계라 본 스토리 범위 밖이다(서버 패키징은 Epic 5). [Source: pyproject.toml(27-35), architecture.md(411-419·530)]
  - [x] `domain/__init__.py`는 8개 dataclass와 모든 enum을 **재노출**(`from .tenant import Tenant` … `from .states import *` 대신 명시적 재노출 권장)해 `from rider_server.domain import Tenant, CustomerLifecycleState`가 되게 한다. `__all__`을 명시한다. [Source: architecture.md(415-419)]
- [x] **Task 2 — 상태/지원 enum 정의: `domain/states.py` (AC: 2, 5)**
  - [x] 모든 enum은 **`class X(str, Enum)`** 로 정의하고 **멤버 이름 == 값(대문자 문자열)** 으로 둔다(`ACTIVE = "ACTIVE"`). Python `>=3.10` 호환을 위해 `StrEnum`(3.11+) 대신 `(str, Enum)`을 쓴다. 이렇게 하면 `X.ACTIVE == "ACTIVE"`·`json.dumps`가 `"ACTIVE"`로 직렬화돼 DB/API 문자열 정본과 일치한다(architecture.md "Python Enum ↔ DB 문자열 일치"). [Source: architecture.md(254·318), data-api-contract.md(94-131), project-context.md(66)]
  - [x] **`CustomerLifecycleState`** — 11 멤버, 계약 순서대로: `LEAD, SIGNED_UP, PAYMENT_ACTIVE, SETUP_PENDING, PLATFORM_AUTH_PENDING, MESSENGER_VERIFY_PENDING, TEST_RUNNING, ACTIVE, DEGRADED, AUTH_REQUIRED, SUSPENDED`. (`ACTIVE`/`AUTH_REQUIRED`/`DEGRADED`/`SUSPENDED` 4개가 별개 멤버로 구분 — AC5.) [Source: data-api-contract.md(96-110)]
  - [x] **`SubscriptionStatus`** — 4 멤버: `PAYMENT_ACTIVE, PAYMENT_FAILED_GRACE, SUSPENDED, CANCELLED`. (값만 — 게이트 평가는 Story 2.6.) [Source: data-api-contract.md(112-119)]
  - [x] **`BaeminAuthState`** — 7 멤버: `UNKNOWN, ACTIVE, AUTH_REQUIRED, USER_ACTION_PENDING, AUTH_VERIFIED, CENTER_MISMATCH, BLOCKED_OR_CAPTCHA`. [Source: data-api-contract.md(122-131)]
  - [x] **지원 enum**(모델 필드 타이핑용): `Platform`(`BAEMIN, COUPANG`), `Messenger`(`TELEGRAM, KAKAO`), `SecretStorageClass`(`CENTRAL, AGENT_LOCAL, NOT_STORED`), `MonitoringTargetStatus`(`ACTIVE, PAUSED, INACTIVE`), `MessengerChannelState`(`PENDING, VERIFIED, ACTIVE, INACTIVE`), `BrowserProfileState`(`UNKNOWN, READY, IN_USE, INACTIVE`). 모두 대문자 `(str, Enum)`. **주의: `Platform`/`Messenger` 도메인 enum은 대문자 정본이고, 기존 `rider_crawl.platforms`/`messengers` registry의 소문자 plugin 키(`"baemin"`)와는 별개 레이어다 — registry를 바꾸지 않는다.** [Source: data-api-contract.md(27·30·9·12), architecture.md(43-44·254), operations-security-test-contract.md(5-11)]
  - [x] `SecretStorageClass` 대문자 3종은 Story 2.4 `secret_store.py`의 소문자 `central`/`agent_local`/`not_stored`와 **1:1 대응**하지만 **다른 레이어(도메인/DB-facing enum vs 설정-직렬화 seam)** 다. 본 스토리는 2.4 seam을 건드리지 않고 enum만 새로 정의한다 — reconcile는 Epic 5 DB/secret 레이어 소유. 짧은 정책 주석으로 이 경계를 남긴다(project-context §38 "코드만으로 알기 어려운 곳에 짧게"). [Source: src/rider_crawl/secret_store.py(20-39), architecture.md(257·343)]
- [x] **Task 3 — `SecretRef` 값 객체 정의: `domain/secret_ref.py` (AC: 1, 2)**
  - [x] `@dataclass(frozen=True) class SecretRef`: 필드 = `ref: str`(설정/DB 밖에 저장된 secret을 가리키는 불투명 핸들 — 2.4의 `vault://…`/`local:…` 핸들과 호환), `storage_class: SecretStorageClass`, (선택) `secret_kind: str = ""`(예: `"telegram_bot_token"`). **평문 secret 값을 필드로 절대 갖지 않는다**(SecretRef의 존재 이유 = 평문을 모델 밖에 두는 것 — data-api-contract 19). [Source: data-api-contract.md(19), architecture.md(418·492-494), operations-security-test-contract.md(5-11)]
- [x] **Task 4 — 8개 dataclass 정의 (AC: 1, 2, 3)** — 각 모델은 **자기 모듈**에 `@dataclass(frozen=True)` + `from __future__ import annotations`(기존 `models.py` 패턴). 필드는 `data-api-contract` Required Tables 이름과 일치. 타임스탬프는 `datetime`(타입 명료), 자동 `now()` 기본값 금지(순수·결정적 — 호출부가 주입). dict류는 `field(default_factory=dict)`:
  - [x] **`tenant.py` → `Tenant`**: `id: str`, `name: str`, `status: CustomerLifecycleState`, `created_at: datetime`. [Source: data-api-contract.md(25·7)]
  - [x] **`subscription.py` → `Subscription`**: `id: str`, `tenant_id: str`, `plan: str`, `status: SubscriptionStatus`, `current_period_end: datetime | None = None`, `quotas: dict[str, int] = field(default_factory=dict)`. (관계: `tenant_id` → Tenant.) [Source: data-api-contract.md(26·8)]
  - [x] **`platform_account.py` → `PlatformAccount`**: `id: str`, `tenant_id: str`, `platform: Platform`, `label: str`, `username_ref: SecretRef`, `password_ref: SecretRef`, `auth_state: BaeminAuthState = BaeminAuthState.UNKNOWN`. (secret refs, **평문 자격증명 아님** — 계약 9. 관계: tenant_id → Tenant, *_ref → SecretRef. 쿠팡 Gmail reauth 전용 상태는 Epic 4에서 확장 — 본 스토리는 Baemin auth state 정본만.) [Source: data-api-contract.md(27·9), epics.md AC1(450)]
  - [x] **`monitoring_target.py` → `MonitoringTarget`**: `id: str`, `tenant_id: str`, `platform_account_id: str`, `name: str`(표시명 — 2.3 `display_name` 대응), `center_name: str`(**기대 센터/상점명 — FR-20 정본**, 2.3 `center_name` 대응), `external_id: str = ""`(2.3 `target_external_id` 대응), `url: str = ""`(2.3 `primary_url` 대응), `interval_minutes: int = 0`, `status: MonitoringTargetStatus = MonitoringTargetStatus.ACTIVE`. (관계: tenant_id → Tenant, platform_account_id → PlatformAccount. `center_name`은 계약 bare table엔 없지만 AC2가 명시 요구 — FR-20 쿠팡 검증 정본이므로 추가. **2.1~2.3 `UiSettings` 중립 필드와의 매핑은 문서화만 하고 wiring은 안 한다 — Story 2.7.**) [Source: data-api-contract.md(28·10), epics.md AC1(450-451), 2-3 스토리 중립 필드]
  - [x] **`browser_profile.py` → `BrowserProfile`**: `id: str`, `agent_id: str`(Agent 모델은 Epic 4/5 — `str` FK placeholder), `target_id: str`, `profile_path_ref: SecretRef`, `cdp_port: int | None = None`, `state: BrowserProfileState = BrowserProfileState.UNKNOWN`. (관계: target_id → MonitoringTarget(역참조로 "연결된 브라우저 프로필" 표현), profile_path_ref → SecretRef. 계약 11 "server stores profile id/ref, not raw sensitive path" → path를 SecretRef로.) [Source: data-api-contract.md(29·11), operations-security-test-contract.md(11)]
  - [x] **`messenger_channel.py` → `MessengerChannel`**: `id: str`, `tenant_id: str`, `messenger: Messenger`, `telegram_chat_id: str | None = None`, `thread_id: str | None = None`, `kakao_room_name: str | None = None`, `state: MessengerChannelState = MessengerChannelState.PENDING`. (Telegram chat/topic 또는 Kakao room 매핑 — 둘 중 하나만 채워짐. `telegram_chat_id`/`thread_id`는 **라우팅 식별자라 secret 아님** — 2.4 결정 계승, ref화 금지.) [Source: data-api-contract.md(30·12), 2-4 스토리(23)]
  - [x] **`delivery_rule.py` → `DeliveryRule`**: `id: str`, `target_id: str`, `channel_id: str`, `template_id: str = ""`(Message template은 Epic 3 — `str` FK placeholder), `enabled: bool = True`, `send_only_on_change: bool = False`. (관계: target_id → MonitoringTarget, channel_id → MessengerChannel. **한 target_id + 여러 channel_id = fan-out** — AC3.) [Source: data-api-contract.md(31·13), implementation-contract.md P2-04(50)]
- [x] **Task 5 — soft delete 표현 잠금 (AC: 6, 7)**
  - [x] `MonitoringTargetStatus`·`MessengerChannelState`에 `INACTIVE`, `DeliveryRule.enabled: bool`로 비활성을 **상태값**으로 표현한다(별도 `is_deleted` 플래그/물리 제거 아님). frozen이므로 비활성화는 `dataclasses.replace(target, status=MonitoringTargetStatus.INACTIVE)`로 **나머지 필드를 보존한 새 인스턴스**를 만든다 — 운영 이력(id·관계·이름) 보존. [Source: data-api-contract.md(28·30·31), architecture.md(317-319)]
  - [x] 짧은 모듈/필드 주석으로 "물리 삭제 금지 — soft delete(INACTIVE/enabled=False)로 이력 보존(FR-4)" 도메인 규칙을 남긴다(운영 정책 주석만). [Source: epics.md AC3(459-461), project-context.md(38)]
- [x] **Task 6 — 테스트 추가: `tests/server/test_domain_states.py` + `tests/server/test_domain_models.py` (AC: 1~7)** — 외부 호출 없음, 순수 객체, 가짜 ID만:
  - [x] **(AC2/5 enum — `test_domain_states.py`):** 세 상태머신의 **멤버 집합·이름==값(대문자)·`(str, Enum)` 직렬화**(`json.dumps([X.ACTIVE]) == '["ACTIVE"]'`)를 단언. `CustomerLifecycleState`가 11 멤버 전부·정확한 이름을 갖고 `ACTIVE/AUTH_REQUIRED/DEGRADED/SUSPENDED` 4개가 **상호 구별**됨(`len({...}) == 4`). `BaeminAuthState` 7 멤버, `SubscriptionStatus` 4 멤버 정확. 지원 enum(`Platform`/`Messenger`/`SecretStorageClass`/대상·채널·프로필 상태) 멤버 확인. [Source: data-api-contract.md(94-131)]
  - [x] **(AC1 모델 필드·임포트 — `test_domain_models.py`):** `from rider_server.domain import (8개 모델 전부)` 가 되고, 각 dataclass가 계약 필드를 가짐(`dataclasses.fields`로 필드 이름 집합 단언). frozen 확인(`with pytest.raises(FrozenInstanceError): obj.id = ...`). [Source: data-api-contract.md(21-38)]
  - [x] **(AC2 관계 — `test_domain_models.py`):** `MonitoringTarget`이 `platform_account_id`·`center_name`·`url`·`external_id`를 갖고, `BrowserProfile.target_id`로 대상↔프로필 연결을 표현. `PlatformAccount.username_ref`/`password_ref`가 **`SecretRef` 인스턴스**이고 SecretRef가 **평문 필드를 안 가짐**(필드 집합에 `value`/`secret`/`password` 같은 평문 키 없음). [Source: data-api-contract.md(27·29·10·19), epics.md AC1(450-452)]
  - [x] **(AC3 fan-out — `test_domain_models.py`):** 같은 `target_id`에 `channel_id`가 다른 `DeliveryRule` **2개 이상**을 만들어 한 대상→2채널 fan-out이 표현 가능함을 단언(FR-9 토대). [Source: data-api-contract.md(13·31)]
  - [x] **(AC6/7 soft delete — `test_domain_models.py`):** `dataclasses.replace`로 `MonitoringTarget.status=INACTIVE`(또는 `MessengerChannel.state=INACTIVE`, `DeliveryRule.enabled=False`) 전이 후 **id·관계 FK·이름이 보존**되고 비활성 판별 가능함을 단언(물리 삭제 아님). [Source: data-api-contract.md(28), architecture.md(317-319)]
  - [x] secret/식별자 비노출: 모든 fixture는 가짜 ID(`"tnt-1"`·`"acc-1"`·`"mt-1"`·`"ch-1"`)·가짜 ref(`SecretRef("vault://t/ref", SecretStorageClass.CENTRAL)`)만 사용. 실제 토큰/전화/이메일/`chat_id` 형태 금지. [Source: project-context.md(81), epic-1-retro 액션 A1]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`. WSL 시스템 `python3` 금지(pytest 미설치). 기준선 **670**(참고값 — 본인이 재측정) 대비 기존 통과가 **하나도** 안 깨지고 신규 케이스만큼만 증가가 정상(본 스토리는 순수 additive). [Source: 2-4 스토리(83·177), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `src/rider_server/**` + 신규 `tests/server/**`만** 보이고 **`src/rider_crawl/` 변경 0줄**임을 확인(순수 additive — 기존 코드 무변경). CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep: 신규 코드·테스트에 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·한국 휴대폰 평문이 없는지 확인. `SecretRef` 직렬화에 평문 secret이 안 남는지(핸들만) 확인. [Source: epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2). [Source: epic-1-retro 액션 A2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 패키지 `src/rider_server/domain/`에 dataclass·enum·`__init__` + 신규 `tests/server/` 테스트만 추가한다. **`src/rider_crawl/` 전체와 `pyproject.toml`은 무변경.** 변경 표면: 신규 파일 ~11개(8 모델 모듈 + `states.py` + `secret_ref.py` + 2 `__init__.py`) + 테스트 2개.
- **건드리지 않는다:** DB 테이블/ORM/Alembic(Epic 5 P4-02), Pydantic 스키마(Epic 5), `secret_store.py`(2.4 — 재배선 금지), `UiSettings`/`AppConfig` wiring·마이그레이션(Story 2.7), `SubscriptionGate` 평가 로직(Story 2.6), Snapshot/Message/DeliveryLog(Epic 3), Agent/Job/AuthSession(Epic 4/5), `rider_crawl.platforms`/`messengers` registry. [Source: epics.md Epic 2(353-355)·Story 2.5~2.7, architecture.md(166-171·411-423)]

### 위치 결정 — 왜 `src/rider_server/domain/`인가 (반드시 읽을 것)

- **architecture.md가 명시적으로 매핑한다:** "② ID 모델(FR-4~6) → `rider_server/domain/`, db/models/, migrations/"(499)이고, 디렉터리 트리(415-419)가 `rider_server/domain/{tenant.py … states.py}`를 그린다. `states.py`는 "Customer/Subscription/Baemin auth enum"(419)으로 명시. **본 스토리는 이 정본 위치를 따른다.** [Source: architecture.md(415-419·499)]
- **rider_server가 아직 없어도 안전하다:** 본 스토리 산출물은 **FastAPI/SQLAlchemy 의존이 0인 순수 dataclass/Enum**이라 FastAPI 스캐폴딩(Story 5.1)보다 먼저 패키지 일부(`domain/`)를 만들어도 부작용이 없다. 5.1이 나중에 `main.py`/`settings.py`/`db/`를 같은 패키지에 추가한다(additive). [Source: architecture.md(133·411-419), implementation-contract.md P4-01(70)]
- **Agent(rider_agent)와의 경계:** architecture는 "Cloud=async, Agent=sync, 두 런타임은 HTTP(JSON)로만 통신, 코드 직접 호출 없음"(482)을 둔다. 이는 **런타임 RPC 금지**이지 빌드타임 공유 모듈 금지가 아니다. Agent는 도메인 인스턴스를 **JSON(pull_remote_config/job payload)으로 수신**해 다루므로 `rider_server`를 import할 필요가 없다 — 정본 모델이 control plane(rider_server)에 사는 것과 모순되지 않는다. (만약 후속 Agent 스토리가 도메인 클래스 import가 정말 필요하다고 판명되면 그때 공유 위치로 승격을 검토 — 본 스토리는 architecture 정본대로 rider_server/domain에 둔다.) [Source: architecture.md(481-483·512-524·98-107 Agent Loop)]
- **`src/rider_crawl/`에 두지 않는 이유:** rider_crawl은 **기존 보존 경계**(배민/쿠팡 parser·renderer·Gmail 2FA·Kakao — 공유 도메인이지만 *legacy/agent-side 실행 자산*)다. 신규 control-plane 도메인 모델을 거기 섞으면 architecture의 3-패키지 책임 분리(rider_crawl 공유실행 / rider_server control plane / rider_agent)를 흐린다. [Source: architecture.md(274-279·393-409·559)]

### `data-api-contract` 모델 ↔ dataclass 매핑 (AC1 — 정밀 계약)

| 모델 | 모듈 | 필드(계약 Required Tables) | 관계(FK) |
|---|---|---|---|
| `Tenant` | `tenant.py` | id, name, status:`CustomerLifecycleState`, created_at:datetime | — |
| `Subscription` | `subscription.py` | id, tenant_id, plan, status:`SubscriptionStatus`, current_period_end:datetime?, quotas:dict | tenant_id→Tenant |
| `PlatformAccount` | `platform_account.py` | id, tenant_id, platform:`Platform`, label, username_ref:`SecretRef`, password_ref:`SecretRef`, auth_state:`BaeminAuthState` | tenant_id→Tenant, *_ref→SecretRef |
| `MonitoringTarget` | `monitoring_target.py` | id, tenant_id, platform_account_id, name, **center_name**, external_id, url, interval_minutes, status:`MonitoringTargetStatus` | tenant_id→Tenant, platform_account_id→PlatformAccount |
| `BrowserProfile` | `browser_profile.py` | id, agent_id, target_id, profile_path_ref:`SecretRef`, cdp_port:int?, state:`BrowserProfileState` | target_id→MonitoringTarget, agent_id→(Agent:Epic4/5 str), profile_path_ref→SecretRef |
| `MessengerChannel` | `messenger_channel.py` | id, tenant_id, messenger:`Messenger`, telegram_chat_id?, thread_id?, kakao_room_name?, state:`MessengerChannelState` | tenant_id→Tenant |
| `DeliveryRule` | `delivery_rule.py` | id, target_id, channel_id, template_id, enabled:bool, send_only_on_change:bool | target_id→MonitoringTarget, channel_id→MessengerChannel, template_id→(Message:Epic3 str) |
| `SecretRef` | `secret_ref.py` | ref:str, storage_class:`SecretStorageClass`, secret_kind:str="" | — (값 객체, 평문 필드 없음) |

- **`center_name` 추가 근거:** 계약 `monitoring_targets` bare table엔 `name`만 있으나 AC2가 "기대 센터/상점명"을 명시 요구하고 FR-20(쿠팡 기대 센터/상점명 검증)의 정본이다. 2.3이 `UiSettings`에 도입한 중립 `center_name`(property)을 도메인 모델로 승격한다. `name`=표시명(2.3 display_name), `center_name`=검증 정본. [Source: epics.md AC1(450-451), 2-3 스토리, data-api-contract.md(28)]
- **forward-reference FK는 `str`:** `agent_id`(Agent=Epic4/5), `template_id`(Message=Epic3)는 아직 모델이 없으므로 **모델 import 없이 `str` ID 필드로만** 둔다. 정식 모델이 생기면 후속 에픽이 타이핑을 강화한다. [Source: data-api-contract.md(14-18·29·33)]

### enum 정본 (AC2 — 대문자 `(str, Enum)`)

- `(str, Enum)`을 쓰는 이유: `CustomerLifecycleState.ACTIVE`가 **문자열 `"ACTIVE"`와 동등**하고 `json.dumps`가 `"ACTIVE"`로 직렬화돼 architecture의 "Python Enum ↔ DB 문자열 일치"(318)·"값은 대문자 enum 문자열"(254)을 만족한다. `StrEnum`은 3.11+라 `>=3.10` 호환 위해 `(str, Enum)` 사용. [Source: project-context.md(20·66), architecture.md(254·318)]
- **세 상태머신**(계약 정본): `CustomerLifecycleState`(11), `BaeminAuthState`(7), `SubscriptionStatus`(4 — 실행 게이트 값). **지원 enum**: `Platform`/`Messenger`/`SecretStorageClass`/`MonitoringTargetStatus`/`MessengerChannelState`/`BrowserProfileState`. [Source: data-api-contract.md(94-131·27·30·9·12)]
- **Baemin auth state의 `ACTIVE`와 Customer lifecycle의 `ACTIVE`는 다른 enum의 동명 멤버**다(서로 다른 타입). 혼동 방지를 위해 필드 타입을 명확히(account.auth_state=`BaeminAuthState`, tenant.status=`CustomerLifecycleState`). [Source: data-api-contract.md(106·124)]
- **`SecretStorageClass`(대문자) vs 2.4 `secret_store.py`(소문자):** 1:1 대응(`CENTRAL↔central`)이지만 레이어가 다르다(도메인/DB enum vs 설정-직렬화 분류 문자열). 본 스토리는 **enum만 정의**하고 2.4 seam을 갈아끼우지 않는다(reconcile=Epic 5). 주석으로 경계 명시. [Source: src/rider_crawl/secret_store.py(20-39), 2-4 스토리(20)]

### soft delete (AC3 — 물리 삭제 금지, FR-4)

- 비활성을 **상태값 전이**로 표현: `MonitoringTarget.status=INACTIVE`, `MessengerChannel.state=INACTIVE`, `DeliveryRule.enabled=False`. 별도 `is_deleted`/물리 제거 없음 — id·관계·이름이 보존돼 운영 이력이 남는다. frozen dataclass라 전이는 `dataclasses.replace`로 새 인스턴스 생성(불변 업데이트 — architecture 317-319). [Source: epics.md AC3(459-461), data-api-contract.md(28·30·31), architecture.md(317-319)]
- AC3가 명시 대상으로 든 건 **대상/채널/규칙** 3종이다(Tenant/Subscription의 lifecycle 종료는 `SUSPENDED`/`CANCELLED`로 별도 표현). BrowserProfile.state에도 `INACTIVE`를 두되 AC3 핵심 검증은 target/channel/rule. [Source: epics.md AC3(459-461)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **기존 코드 무변경** — `src/rider_crawl/`·`pyproject.toml`·기존 테스트는 한 줄도 안 바뀐다(`git diff -w` = rider_server/tests 신규만). 본 스토리는 순수 additive. (b) **enum 직렬화 정본** — 대문자 문자열(`"ACTIVE"`), 멤버 이름==값. (c) **SecretRef 평문 비보유** — 참조 핸들만(평문 secret 필드 0 — NFR-8/ADD-15). (d) **순수·결정적** — dataclass에 `datetime.now()`/`uuid4()` 자동 기본값 금지(호출부 주입 — 테스트 결정성). (e) **frozen 불변** — 상태 전이는 `replace`로(직접 mutate 아님). [Source: project-context.md(32·82), architecture.md(317-319), data-api-contract.md(19)]

### 이전 스토리 인텔리전스 (Epic 1 → 2.1 → 2.2 → 2.3 → 2.4 → 2.5 이월 교훈)

- **A1(secret 게이트):** 신규 테스트 값은 가짜 ID/ref만(`"tnt-1"`·`"vault://t/ref"`). 실제 토큰/전화/이메일/`chat_id` 형태 금지. SecretRef를 다루므로 평문 비보유를 테스트로 단언. [Source: epic-1-retro 액션 A1]
- **A2(테스트 수치 stale):** dev 노트에 잠정 pass 수치 박지 말 것 — **리뷰 시점 재측정값 1개만** 정본. 2.1~2.4 모두 dev-story 수치가 stale가 돼 리뷰에서 정정됐다. [Source: epic-1-retro 액션 A2, 2-4 스토리(177)]
- **2.1~2.4 범위 규율(직교 보강):** 2.1=ID 발급, 2.2=경로/atomic/rotation, 2.3=중립 이름 alias, 2.4=secret ref 분리 seam. 각 스토리가 **한 가지**만 했다. 2.5도 "순수 도메인 정의"만 하고 wiring(2.7)·게이트(2.6)·DB(Epic 5)를 끌어오지 않는다. **2.4가 `SecretRef`/`SecretStorageClass`를 명시적으로 2.5에 위임**했으니 본 스토리가 그 정식 모델을 만든다(단, 2.4 seam 재배선은 안 함). [Source: 2-1~2-4 스토리 범위 경계, 2-4 스토리(20)]
- **2.3/2.4 교훈(enum 대신 단순 타입의 "잠정" 규율 해제):** 2.3은 `CENTER_MISMATCH` enum 대신 `(bool, str)`을, 2.4는 분류에 소문자 문자열을 썼는데 **둘 다 "정식 enum은 2.5 소유"라 미뤘다.** 본 스토리가 그 정식 enum(`BaeminAuthState.CENTER_MISMATCH`, `SecretStorageClass`)을 도입한다 — 단, **기존 2.3/2.4 코드를 그 enum으로 갈아끼우지 않는다**(additive only, reconcile는 후속). [Source: 2-3 스토리, 2-4 스토리(20·101)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님). git tree CRLF/LF 노이즈 — 범위 확인 `git diff -w`. [Source: memory/dev-env-quirks]

### Project Structure Notes

- 신규: `src/rider_server/__init__.py`, `src/rider_server/domain/__init__.py`, `domain/states.py`, `domain/secret_ref.py`, `domain/tenant.py`, `domain/subscription.py`, `domain/platform_account.py`, `domain/monitoring_target.py`, `domain/browser_profile.py`, `domain/messenger_channel.py`, `domain/delivery_rule.py` + `tests/server/test_domain_states.py`, `tests/server/test_domain_models.py`. `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64), architecture.md(411-419·456-461)]
- **테스트 위치:** architecture가 `tests/server/`를 제안(456-459)하므로 본 스토리(첫 rider_server 코드)가 이 디렉터리를 신설한다. 현재 `tests/`는 평면 구조에 `__init__.py`가 없다 — pytest importmode=prepend는 **basename이 고유하면** `tests/server/test_*.py`를 충돌 없이 수집한다(신규 파일명 `test_domain_states.py`·`test_domain_models.py`는 기존과 안 겹침). `__init__.py`는 추가하지 않는다(기존 평면 컨벤션 유지). 만약 수집 충돌이 나면 평면 `tests/test_domain_*.py`로 대체 허용. [Source: pyproject.toml(27-29), architecture.md(280-282·456-461)]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]`로 import는 동작하고, wheel `packages=["src/rider_crawl"]`(Agent 배포 경계)는 본 스토리가 바꾸지 않는다(서버 패키징=Epic 5). [Source: pyproject.toml(28·34-35)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.5(440-461)] — user story·3개 Given/When/Then AC 원문(8 모델 정의·data-api-contract 계약 일치·대상의 플랫폼/계정/center_name/url/식별자/브라우저프로필·DeliveryRule fan-out, Customer lifecycle/Baemin auth state 대문자 enum·ACTIVE/AUTH_REQUIRED/DEGRADED/SUSPENDED 구분, soft delete/inactive 물리삭제 금지).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2(353-355)] — Epic 2 목표(탭 번호 대신 ID, 도메인 모델·상태enum·legacy alias·secret_ref·last_message seed; **도메인 dataclass/Enum과 rider_crawl 설정 진화를 다루고, PostgreSQL 13 테이블·Alembic은 Epic 5(P4-02)**).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(5-38·94-156)] — 13 Core Domain Models 계약·13 Required Tables 필드, 3 State Machines(Customer lifecycle 96-110 / Subscription execution gate 112-119 / Baemin auth state 122-131), dedup key. 본 스토리는 8 모델 + 3 상태머신만 정의(나머지 5 모델=Epic 3/4/5).
- [Source: _bmad-output/planning-artifacts/architecture.md(166-171·254·303-304·317-319·411-423·499)] — data-api-contract 13 모델/테이블 채택, dataclass=Agent 내부 도메인/Pydantic=API 경계, 상태값 대문자 enum 문자열 단일 정본·Enum↔DB 일치, `rider_server/domain/{… states.py}` 구조, FR-4~6→rider_server/domain. db/Alembic·schemas는 Epic 5.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(16·46-52·71)] — Crawling1..9 → Tenant/Customer/PlatformAccount/MonitoringTarget, Snapshot/Message/DeliveryRule/DeliveryLog(P2=Epic3), P4-02 PostgreSQL/Alembic(Epic5).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md(5-11·42-51)] — Secret Storage 분류(Telegram=central, Coupang/Gmail/Agent=agent-local), Tests(Unit: domain 포함 CI 통과).
- [Source: src/rider_crawl/models.py(1-6·40·59)] — 기존 `@dataclass(frozen=True)` + `from __future__ import annotations` 패턴(따를 컨벤션).
- [Source: src/rider_crawl/secret_store.py(20-39)] — Story 2.4 소문자 `SECRET_STORAGE_*` 분류·`LocalFileSecretStore`(정식 `SecretRef`/enum을 2.5에 위임 — 본 스토리가 정의, seam은 무변경).
- [Source: src/rider_crawl/ui_settings.py(37·125-144·344-365)] — 2.1~2.3 `UiSettings` ID 발급·중립 필드(center_name/display_name/target_external_id/primary_url property). 도메인 MonitoringTarget으로 승격 매핑(wiring은 Story 2.7).
- [Source: _bmad-output/implementation-artifacts/2-4-…(20·177)·2-3-…·epic-1-retro-2026-06-13.md] — 2.4가 SecretRef/PlatformAccount/SecretStorageClass enum을 2.5에 위임, A1(secret 게이트)·A2(테스트 수치 단일 정본).
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: FR-4(ID 기반 CRUD·도메인 모델·soft delete), FR-30(운영자 주도 상태 흐름=상태 모델), FR-9(DeliveryRule fan-out 토대), FR-20(쿠팡 center_name 검증 토대), ADD-7(도메인 모델), ADD-9(상태머신), NFR-8/ADD-15(SecretRef 평문 비보유), NFR-20(회귀 0). 정식 wiring=Story 2.7, 게이트 평가=Story 2.6, DB/Alembic·Pydantic=Epic 5, Snapshot/Message/DeliveryLog=Epic 3, Agent/Job/AuthSession=Epic 4/5.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 기준선 측정: `.venv/Scripts/python.exe -m pytest -q` → **670 passed**(구현 전, HEAD `a155b31`).
- 신규 서버 테스트: `.venv/Scripts/python.exe -m pytest tests/server -q` → 28 passed (states 13 + models 15).
- 회귀 검증(리뷰 시점 정본): `.venv/Scripts/python.exe -m pytest -q` → **698 passed**(670 + 신규 28, 회귀 0).
- 범위 검증: `git diff -w --stat -- src/ pyproject.toml` = 빈 출력(기존 `src/rider_crawl/`·`pyproject.toml` 무변경). 신규 untracked = `src/rider_server/`, `tests/server/`만.
- 누출 검증: `grep -rnE '[0-9]{6,}:[A-Za-z0-9_-]{30,}|chat_id=[0-9]+|01[0-9]-?[0-9]{3,4}-?[0-9]{4}' src/rider_server tests/server` → 매치 없음.

### Completion Notes List

- **순수 additive 완료(NFR-20 회귀 0).** 신규 패키지 `src/rider_server/domain/`에 8개 frozen dataclass + 3개 상태머신 enum + 6개 지원 enum + `SecretRef` 값 객체를 `data-api-contract` 계약(필드·관계)에 맞게 정의. 기존 `rider_crawl`/`pyproject.toml` 한 줄도 변경하지 않음.
- **AC1**: `Tenant`/`Subscription`/`PlatformAccount`/`MonitoringTarget`/`BrowserProfile`/`MessengerChannel`/`DeliveryRule`/`SecretRef` 8개를 각 모듈에 정의, `domain/__init__.py`에서 `__all__`로 명시 재노출(`from rider_server.domain import …` 동작). `MonitoringTarget`은 `center_name`(FR-20 검증 정본) 포함, `PlatformAccount`는 `username_ref`/`password_ref`(SecretRef)로 평문 자격증명 비보유. forward-ref FK(`agent_id`/`template_id`)는 `str` placeholder.
- **AC2/5**: 모든 enum `(str, Enum)` + 멤버 이름==값(대문자). `CustomerLifecycleState`(11, 계약 순서)·`SubscriptionStatus`(4, 값만 — 게이트 평가는 2.6)·`BaeminAuthState`(7) + 지원 enum 6종. `ACTIVE`/`AUTH_REQUIRED`/`DEGRADED`/`SUSPENDED` 4개 별개 멤버, `json.dumps`가 대문자 문자열로 직렬화.
- **AC3/6/7**: soft delete를 상태값으로 표현(`MonitoringTargetStatus.INACTIVE`/`MessengerChannelState.INACTIVE`/`DeliveryRule.enabled=False`). frozen이라 `dataclasses.replace`로 id·관계 FK·이름 보존한 새 인스턴스 생성 — 물리 삭제 아님(테스트로 잠금).
- **경계 준수**: `secret_store.py`(2.4 소문자 seam) 재배선 안 함 — `SecretStorageClass`(대문자) enum만 신규 정의하고 주석으로 1:1 대응·레이어 경계 명시. `rider_crawl.platforms`/`messengers` registry 무변경. DB/Alembic·Pydantic·wiring은 Epic 5/Story 2.7 미터치.
- **순수·결정적**: dataclass에 `datetime.now()`/`uuid4()` 자동 기본값 없음(호출부 주입). dict는 `field(default_factory=dict)`. 테스트 fixture는 가짜 ID/ref만 사용(A1).

### File List

신규(순수 additive):
- `src/rider_server/__init__.py`
- `src/rider_server/domain/__init__.py`
- `src/rider_server/domain/states.py`
- `src/rider_server/domain/secret_ref.py`
- `src/rider_server/domain/tenant.py`
- `src/rider_server/domain/subscription.py`
- `src/rider_server/domain/platform_account.py`
- `src/rider_server/domain/monitoring_target.py`
- `src/rider_server/domain/browser_profile.py`
- `src/rider_server/domain/messenger_channel.py`
- `src/rider_server/domain/delivery_rule.py`
- `tests/server/test_domain_states.py`
- `tests/server/test_domain_models.py`

수정:
- `_bmad-output/implementation-artifacts/sprint-status.yaml`(2-5 상태 `ready-for-dev` → `in-progress` → `review`)

## Change Log

| 날짜 | 변경 | 비고 |
|---|---|---|
| 2026-06-13 | Story 2.5 구현: `rider_server/domain/` 8개 dataclass + 상태/지원 enum + `SecretRef` 정의, `tests/server/` 도메인 테스트 2개 추가 | 순수 additive, 회귀 0(689 passed = 670 + 19) |
| 2026-06-13 | Senior Developer Review(AI): 테스트 수치 stale 정정(19→28 신규 / 689→698 전체, 재측정 정본), 검토 노트 추가, 상태 review→done | 코드 변경 0(문서만). 회귀 0 재확인(698 passed = 670 + 28) |

## Senior Developer Review (AI)

**리뷰어:** 이수열 · **일자:** 2026-06-13 · **결과:** Approve (status → done)

### 범위/리얼리티 검증
- **순수 additive 확인:** git 실제 변경 = 신규 `src/rider_server/**`(11개) + `tests/server/**`(2개)만. `src/rider_crawl/`·`pyproject.toml` 0줄 변경(File List ↔ git 100% 일치, 불일치 0). `__pycache__/`는 `.gitignore`로 제외 — pyc 누출 없음.
- **회귀 0(NFR-20) 재측정 정본:** `.venv/Scripts/python.exe -m pytest -q` → **698 passed**(기준선 670 + 신규 28, 회귀 0). 서버 서브셋 28 passed(states 13 + models 15).

### AC / Task 감사
- **AC1**(8 모델·필드·관계): `data-api-contract` Required Tables(25-31)와 필드명·FK를 라인 단위 대조 — 전부 일치. `MonitoringTarget.center_name`은 계약 bare table엔 없으나 AC2가 명시 요구(FR-20 정본)한 정당한 추가. `Subscription.id`는 계약 행에 누락돼 있으나 PK로 추가(정당). → IMPLEMENTED.
- **AC2/5**(대문자 `(str, Enum)`·상태 구분): CustomerLifecycleState 11(계약 순서)·BaeminAuthState 7(순서)·SubscriptionStatus 4 정확. `json.dumps`·`==`·이름==값 테스트로 잠김. ACTIVE/AUTH_REQUIRED/DEGRADED/SUSPENDED 4 별개 멤버. → IMPLEMENTED.
- **AC3/6/7**(soft delete): `MonitoringTargetStatus.INACTIVE`/`MessengerChannelState.INACTIVE`/`DeliveryRule.enabled=False` + `dataclasses.replace` 이력 보존 테스트. 물리 삭제 없음. → IMPLEMENTED.
- 모든 `[x]` Task: 코드/테스트 증거로 실제 완료 확인.

### 발견 및 처리
- **[MEDIUM·수정완료]** Dev Agent Record의 테스트 수치 stale(신규 19/전체 689) — 리뷰 시점 재측정값(신규 28/전체 698)으로 정정(A2 교훈). "QA 보강 gap-fill" 테스트가 dev의 19-count 실행 이후 추가돼 stale가 됨. 코드 결함 아님(문서 수치만).
- **[LOW·관찰, 코드 변경 없음]** `(str, Enum)`은 `json.dumps`/`==`에선 대문자 문자열로 동작하나(테스트로 잠김), `str()`/f-string은 `"CustomerLifecycleState.ACTIVE"`를 반환한다. 현재 직렬화 소비자가 없어(=Epic 5) 버그 아님 — **Epic 5에서 DB/직렬화 wiring 시 `.value` 사용 또는 `__str__` 보강 권고.** 본 스토리의 "순수 정의/additive only" 경계를 지켜 enum 의미를 지금 바꾸지 않음.
- **[LOW·관찰]** 테스트명 `test_model_field_sets_match_contract`는 `center_name` 확장을 포함하므로 엄밀히는 "계약 + 정당한 확장" — 의도적·문서화됨(변경 불요).

### 결론
CRITICAL/HIGH 0. 코드는 계약대로 정확·완전하고 테스트가 ACs를 견고히 잠근다. MEDIUM(수치 stale) 1건 자동 수정 완료. **0 CRITICAL → status done.**
