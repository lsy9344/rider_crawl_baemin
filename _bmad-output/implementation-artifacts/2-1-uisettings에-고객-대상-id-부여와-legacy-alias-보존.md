---
baseline_commit: d701aa4
---

# Story 2.1: UiSettings에 고객/대상 ID 부여와 legacy alias 보존

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 기존 탭별 설정(`UiSettings`)에 **고객 ID(`customer_id`)·고객명(`customer_name`)·플랫폼 계정 ID(`platform_account_id`)·모니터링 대상 ID(`monitoring_target_id`)** 를 부여하고 **기존 탭명(`크롤링N`)은 `legacy_alias`로만 보존**하며, 기존 `ui_settings.json`이 자동으로 마이그레이션되어 로드되길 원한다,
so that 이후 운영을 탭 번호가 아니라 **안정적인 ID**로 추적하면서, 기존 탭과의 연결(표시명)도 잃지 않고, 1.5 §3이 잠근 공개 동작(9탭 로딩·JSON 호환·legacy 카카오/쿠팡 추론)을 깨지 않는다.

> **이 스토리의 성격(중요 — Epic 2의 첫 "제품 코드" 스토리).** Epic 1(P0)이 거의 전부 문서·테스트였던 것과 달리, 본 스토리는 **`src/rider_crawl/ui_settings.py` 제품 코드를 실제로 수정**한다. "제품 코드 무변경" 안전 마진은 끝났고, Epic 1이 만든 가드/골든 테스트(특히 `test_ui_settings.py`)가 이 시점부터 **능동 회귀 그물**로 작동한다. [Source: epic-1-retro-2026-06-13.md(80-84)]
>
> **엄격한 범위 경계(스코프 크립 방지).** 본 스토리는 **오직** `UiSettings` 스키마에 5개 필드(4개 ID류 + `legacy_alias`)를 더하고, 로드 시 자동 마이그레이션으로 ID를 안정적으로 발급·보존하는 것만 한다. 아래는 **다른 스토리 소유 — 본 스토리에서 절대 손대지 않는다:**
> - `state_subdir`를 `crawlingN` → `targets/<monitoring_target_id>`로 바꾸기 → **Story 2.2** (P1-02). `ui.py`의 `state_subdir=f"crawling{n}"` 호출부는 **그대로 둔다.**
> - atomic write(temp→fsync→rename)·로그 rotation → **Story 2.2** (P1-03/04). 본 스토리는 기존 `save_all`(평범한 `write_text`)을 그대로 쓴다.
> - 플랫폼 중립 필드(`center_name`/`display_name`/`target_external_id`/`primary_url`) 도입 → **Story 2.3** (P1-05).
> - secret 값 분리·`*_ref`화 → **Story 2.4** (P1-06).
> - 도메인 dataclass/Enum(Tenant/MonitoringTarget 등) 정의 → **Story 2.5**.
> - 백업·dry-run·old/new 메시지 비교 등 **오케스트레이션 마이그레이션 러너** → **Story 2.7**.
>
> **기준선 회귀 0.** 현재 HEAD(`d701aa4`)에서 전체 스위트는 **584 collected**(실측 — 복사 금지, 본인이 `.venv/Scripts/python.exe -m pytest --collect-only -q`로 재측정). 본 스토리는 신규/수정 테스트 케이스만큼만 변동이 정상이고, 기존 통과 테스트가 새로 깨지면 실패다(NFR-20). **A2 교훈: dev 노트에 잠정 pass 수치를 박아 stale를 만들지 말 것 — 리뷰 시점 재측정값 1개만 정본으로 기록한다.** [Source: epic-1-retro-2026-06-13.md 액션 A2]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 신규/수정 테스트·fixture에 실제 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. placeholder/가짜값(`010-0000-0000`, `rider@example.com`)만 쓴다. [Source: project-context.md(81·89), epic-1-retro 액션 A1]

## Acceptance Criteria

**AC1 — UiSettings에 ID 필드·legacy_alias 추가 + JSON 호환 유지 (P1-01, FR-5)**
1. **Given** 기존 `runtime/state/ui_settings.json`(최대 9탭, `{"crawlings":[...]}` 구조)이 있을 때 **When** `UiSettings` dataclass에 `customer_id`, `customer_name`, `platform_account_id`, `monitoring_target_id`, `legacy_alias` 5개 필드를 추가하면 **Then** 기존 `ui_settings.json`이 자동으로 마이그레이션되어 로드되고(누락 필드는 기본값으로 채워짐, 기존 키는 보존), 신규 필드는 `save`/`load`·`save_all`/`load_all` 라운드트립에서 손실 없이 보존된다. [Source: epics.md Story 2.1 AC#1(365-369), implementation-contract.md P1-01(36), src/rider_crawl/ui_settings.py(19-49·177-191)]
2. **And** 저장 JSON은 기존 **`ensure_ascii=False, indent=2`** 스타일과 `{"crawlings":[...]}` 키 구조를 그대로 유지한다(신규 필드도 같은 직렬화 정책을 따른다). [Source: epics.md AC#1(369), project-context.md(68), src/rider_crawl/ui_settings.py(159-167)]
3. **And** 추가된 5개 필드는 dataclass **기본값을 가진(defaulted)** 필드로, 기존 비기본 필드(`page_timeout_seconds`까지) **뒤**에 선언되어 `UiSettings.defaults()`/`default_for_tab()`/dataclass 필드 순서 규칙을 깨지 않는다. `to_app_config()`/`AppConfig`에는 **연결하지 않는다**(런타임 실행 스냅샷은 본 스토리 범위 밖 — state_subdir은 2.2 소유). [Source: src/rider_crawl/ui_settings.py(20-49·90-123), config.py(50-57)]

**AC2 — legacy_alias는 표시명/보조 식별자로만, 9탭·legacy 추론 비파괴 (FR-5, NFR-19)**
4. **Given** 기존 탭명(`크롤링N`)을 잃지 않아야 할 때 **When** 마이그레이션이 `legacy_alias`를 채우면 **Then** `legacy_alias`에는 기존 탭의 표시명(예: `크롤링1`)이 보존되고, 이 값은 **표시명/보조 식별자로만** 쓰이며 내부 주 식별자(상태 경로·dedup·라우팅 키)로는 절대 쓰이지 않는다(주 식별자는 `monitoring_target_id`). 이미 `legacy_alias`가 있으면 보존하고 덮어쓰지 않는다. [Source: epics.md AC#1(368), implementation-contract.md P1-01(36)]
5. **And** **Given** 9탭 로딩 호환을 깨면 안 될 때 **When** `UiSettingsStore.load_all(max_tabs=9)`로 로드하면 **Then** 정확히 9개 `UiSettings`가 반환되고, **legacy 카카오 설정 추론**(`messenger_name` 없고 텔레그램 필드 없는 카카오 매핑→`kakao`), **쿠팡 플랫폼 추론**(URL→`coupang`), **legacy `refresh_interval_seconds`→`interval_minutes`** 마이그레이션, 기존 라운드트립이 **모두 그대로 통과**한다. 기존 `test_ui_settings.py`의 모든 테스트가 회귀 없이 유지된다. [Source: epics.md AC#2(371-374), project-context.md(47·54·59), src/rider_crawl/ui_settings.py(139-219), tests/test_ui_settings.py(101-254)]

**AC3 — 발급된 ID는 재로드 시 안정적으로 동일 (마이그레이션 계약 — 핵심)**
6. **Given** ID가 없던 기존 설정을 처음 로드할 때 **When** 마이그레이션이 활성(=실제 내용이 있는) 탭에 `customer_id`/`platform_account_id`/`monitoring_target_id`를 발급하면 **Then** 발급된 ID는 즉시 영속화(persist)되어, **같은 파일을 다시 로드하면 동일한 ID가 그대로 유지**된다(재로드마다 새 ID가 생기지 않는다). [Source: epics.md AC#3(376-378), implementation-contract.md Migration Contract(96-104)]
7. **And** 이미 ID가 있는 탭을 로드하면 그 ID를 **그대로 보존**하고 절대 재발급하지 않는다(idempotent). ID는 불투명(opaque) 문자열이며 탭 순서/표시 이름과 무관하다(탭을 재정렬해도 ID는 따라간다 — 실제 상태 경로 분리는 2.2). [Source: implementation-contract.md P1-02 의도(37), architecture.md Anti-Patterns(365)]
8. **And** 빈/미사용 filler 탭(파일에 없거나 내용 없는 기본 탭)에는 ID를 발급하지 않는다(가짜 ID로 파일을 부풀리지 않는다). "활성 탭" 판정은 `ui.active_crawling_settings`와 **동일한 의미**(=`performance_url.strip()`가 비어 있지 않음)를 쓰되, **그 함수를 import하지 말고 `ui_settings.py` 안에서 인라인으로 동일 검사**를 한다(아래 순환 import 주의). [Source: implementation-contract.md Migration Contract(99-100), src/rider_crawl/ui.py(61-62)]

## Tasks / Subtasks

- [x] **Task 1 — `UiSettings`에 ID/alias 필드 추가 (AC: 1, 2, 3)**
  - [x] `src/rider_crawl/ui_settings.py`의 `UiSettings` dataclass에 `customer_id: str = ""`, `customer_name: str = ""`, `platform_account_id: str = ""`, `monitoring_target_id: str = ""`, `legacy_alias: str = ""`를 **기존 defaulted 필드 블록(`coupang_auto_email_2fa_enabled` 근처)에** 추가한다. 비기본 필드 뒤에 와야 dataclass 순서 오류가 안 난다. [Source: src/rider_crawl/ui_settings.py(20-49)]
  - [x] `defaults()`/`default_for_tab()`는 키워드 생성이라 빈 기본값으로 자동 처리되지만, 의도를 명확히 하려면 신규 필드는 빈 문자열로 둔다(여기서 ID를 발급하지 않는다 — 발급은 로드 마이그레이션에서만). [Source: src/rider_crawl/ui_settings.py(51-88)]
  - [x] `_settings_from_mapping`은 이미 `for key, value in raw.items(): if key in data: data[key]=value`로 dataclass에 있는 키를 복사하므로, 필드만 추가하면 신규 키가 **자동 라운드트립**된다. 추가 매핑 코드가 필요한지 확인하고 불필요하면 만들지 않는다(wheel 재발명 금지). [Source: src/rider_crawl/ui_settings.py(177-191)]
  - [x] `_to_jsonable`은 `asdict`를 쓰므로 신규 필드가 자동 직렬화된다. `ensure_ascii=False, indent=2`·`{"crawlings":[...]}` 구조 불변 확인. **to_app_config/AppConfig는 건드리지 않는다.** [Source: src/rider_crawl/ui_settings.py(157-174·90-123)]
- [x] **Task 2 — 안정적 ID 발급 + legacy_alias seed 마이그레이션 (AC: 3, 2)**
  - [x] **순수 발급 헬퍼**를 추가한다(예: 모듈 함수 `_issue_missing_ids(settings: UiSettings, *, legacy_alias: str) -> bool` 또는 `UiSettings` 메서드). 동작: 비어 있는 `monitoring_target_id`/`customer_id`/`platform_account_id`에만 불투명 ID(`uuid.uuid4().hex` 권장)를 채우고, **이미 값이 있으면 그대로 보존**한다. `legacy_alias`가 비어 있으면 인자로 받은 기존 탭명(`크롤링{index}`)으로 seed하고, 있으면 보존한다. 값이 하나라도 새로 채워졌으면 `True`(=영속화 필요)를 반환한다. `customer_name`은 사람 표시명이라 자동 발급 대상이 아니다 — 비워 둔다. [Source: implementation-contract.md Migration Contract(100), epics.md AC#3(376-378)]
  - [x] **활성 탭에만 발급(AC3 #8).** `load_all`에서 **내용 있는 탭**에만 발급한다. "내용 있음" = `settings.performance_url.strip()`가 비어 있지 않음(= `ui.active_crawling_settings`와 동일 의미). ⚠️ **순환 import 금지:** `ui.py`가 이미 `ui_settings.py`를 import하므로(`from .ui_settings import ...`, ui.py:25) `ui_settings.py`에서 `ui`를 import하면 순환 import로 깨진다 — `active_crawling_settings`를 가져오지 말고 인라인으로 같은 검사를 한다. legacy_alias의 인덱스(`크롤링{index}`)는 `load_all`의 `range(1, max_tabs+1)` 인덱스를 쓴다. [Source: src/rider_crawl/ui.py(25·61-62), src/rider_crawl/ui_settings.py(139-155)]
  - [x] **재로드 안정성(AC3 #6) = persist-on-first-issue.** `load`/`load_all`에서 마이그레이션이 새 ID를 발급했고 **원본 파일이 존재할 때만**, 발급 결과를 기존 `save`/`save_all`로 **한 번 영속화**해 다음 로드가 동일 ID를 읽도록 한다. **가드:** 파일이 없으면(`self.path.exists()` False → 기본값 경로) **절대 새 파일을 만들지 않는다**(현재 "파일 없음→defaults, no write" 동작 보존). atomic write는 2.2 소유이므로 여기서는 기존 평범한 write를 그대로 쓴다. [Source: src/rider_crawl/ui_settings.py(130-167), implementation-contract.md P1-03=2.2(38)]
  - [x] 설계 노트(Dev Notes "ID 안정성 설계")의 trade-off를 읽고, 불투명 ID(uuid4) + persist-on-issue를 채택한다(내용 해시 기반 결정적 ID는 URL 편집 시 ID가 바뀌어 "안정 ID" 의도를 위반하므로 채택하지 않는다).
- [x] **Task 3 — 테스트 추가/보강 (AC: 1, 2, 3)** — `tests/test_ui_settings.py`에 기존 패턴(`tmp_path` fixture, 순수 파일 I/O, 외부 호출 없음)으로 추가:
  - [x] **필드 라운드트립(AC1):** ID/alias를 채운 `UiSettings`를 `save`/`load` 및 `save_all`/`load_all` 라운드트립 후 값이 동일한지 단언. 저장 파일 텍스트에 한글이 escape 안 되고(`ensure_ascii=False`) `"crawlings"` 키가 있는지 단언. [Source: tests/test_ui_settings.py(74-145)]
  - [x] **재로드 ID 안정성(AC3 #6·#7):** ID 없는 legacy `{"crawlings":[{performance_url...}]}` 파일을 `load_all` → 활성 탭에 `monitoring_target_id`가 생기고, **같은 store로 다시 `load_all` → 동일 ID**임을 단언. 이미 ID가 있는 파일을 로드하면 ID가 **불변**임을 별도 단언. [Source: epics.md AC#3(376-378)]
  - [x] **활성 탭만 발급 + filler 무발급(AC3 #8):** 1개 활성 탭만 있는 파일 로드 시, 활성 탭은 ID 보유·빈 filler 탭(`settings[1..8]`)은 `monitoring_target_id == ""`임을 단언. [Source: src/rider_crawl/ui.py(61-66)]
  - [x] **파일 없음 → write 안 함(AC3 가드):** 존재하지 않는 경로로 `load_all` 호출 후 그 경로 파일이 **생성되지 않았는지** 단언(`path.exists() is False`). [Source: src/rider_crawl/ui_settings.py(139-141)]
  - [x] **legacy_alias seed/표시 전용(AC2 #4):** alias 없는 탭 로드 시 `legacy_alias`가 `크롤링{index}`로 seed되고, 이미 alias가 있으면 보존됨을 단언. [Source: epics.md AC#1(368)]
  - [x] **기존 공개 동작 비파괴(AC2 #5):** 기존 `test_ui_settings.py` 전체(9탭 로딩, legacy 카카오/쿠팡 추론, refresh_seconds 마이그레이션, 2FA, to_app_config)가 그대로 통과하는지 회귀 확인(수정 불필요해야 정상 — 깨지면 스키마 변경이 공개 동작을 건드린 것). [Source: tests/test_ui_settings.py(8-324)]
  - [x] secret 비노출: 신규 테스트의 telegram/2FA 값은 placeholder/가짜값만(`"token"`, `"-100123"` 등 기존 테스트가 쓰는 명백한 가짜값 재사용). [Source: project-context.md(81)]
- [x] **Task 4 — 회귀·범위·누출 검증 및 마무리 (AC: 1, 2, 3)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`. WSL 시스템 `python3`로 실행하지 않는다(pytest 미설치). 기준선 **584**(참고값 — 본인이 재측정) 대비 기존 통과가 새로 깨지지 않고 신규 케이스만큼만 증가가 정상. [Source: memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 **`src/rider_crawl/ui_settings.py` + `tests/test_ui_settings.py`** 만(필요 시 신규 테스트 파일) 보이고, `ui.py`/`config.py`/`app.py`/`state_subdir` 관련 파일은 **무변경**임을 확인. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. 기존 `runtime/`/`logs/`/실 `ui_settings.json` 원본은 테스트에서 미변형(전부 `tmp_path`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep: 신규/수정 코드·테스트에 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·한국 휴대폰 평문이 없는지 확인. [Source: epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2). [Source: epic-1-retro 액션 A2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **`UiSettings` 스키마 + 로드 마이그레이션**만 바꾼다. 단일 파일 `src/rider_crawl/ui_settings.py`(+ 테스트)가 변경 표면이다.
- **건드리지 않는다:** `to_app_config()`/`AppConfig`(런타임 스냅샷), `ui.py`의 `state_subdir=f"crawling{n}"`/`crawl_name=f"크롤링{n}"` 호출부(2.2), atomic write/로그 rotation(2.2), 플랫폼 중립 필드(2.3), secret_ref(2.4), 도메인 모델(2.5), 마이그레이션 러너·백업·dry-run(2.7). 신규 ID를 `state_subdir`에 연결하고 싶은 충동을 참는다 — 그건 **Story 2.2**의 AC다. [Source: implementation-contract.md(36-41·96-104)]

### 코드 앵커 (변경 대상 정밀 위치)

- `src/rider_crawl/ui_settings.py`
  - `UiSettings` dataclass: 비기본 필드 19~41, defaulted 필드 44~49 → **신규 5필드는 44~49 블록에 추가**.
  - `defaults()` 51~75, `default_for_tab()` 77~88: 키워드 생성, 신규 필드 자동 빈값.
  - `to_app_config()` 90~123: **변경 금지**(AppConfig에 ID 미연결).
  - `UiSettingsStore.load()` 130~137, `load_all()` 139~155: **여기서 발급+persist-on-issue**.
  - `save()` 157~162, `save_all()` 164~167: 영속화에 재사용(평범한 write, atomic은 2.2).
  - `_to_jsonable()` 170~174(`asdict` → 신규 필드 자동), `_settings_from_mapping()` 177~191(generic key 복사 → 신규 키 자동 라운드트립).
- `src/rider_crawl/ui.py`
  - `active_crawling_settings()` 61~62: "활성 탭" = `performance_url.strip()` 비어있지 않음. 발급 판정은 이 **의미만 인라인 복제**(import 금지 — 순환 import).
  - line 25 `from .ui_settings import ...`: 의존 방향이 ui→ui_settings이므로 역방향 import 금지.
  - `state_subdir=f"crawling{n}"` 호출부 96·524·614·808: **이 스토리에서 변경 금지(2.2 소유).**

### ID 안정성 설계 (AC3 — 핵심 결정, 반드시 읽을 것)

문제: AC3은 "ID가 없던 설정을 처음 로드 → 마이그레이션이 발급 → 재로드 시 동일 ID"를 요구한다. 순수 in-memory `uuid4`만 쓰면 매 로드마다 새 ID가 생겨 AC3을 위반한다. 두 가지 해법:

1. **(채택) 불투명 uuid4 + persist-on-first-issue.** 발급은 빈 필드에만, 결과를 **즉시 파일에 한 번 영속화** → 파일이 정본이 되어 재로드가 동일 ID를 읽는다. ID는 불투명·영구·탭 순서 무관. **가드:** 파일이 없을 때는 발급/쓰기 안 함(현재 "no file→defaults, no write" 보존). 이는 implementation-contract Migration Contract("Issue ... for each active tab" + 새 설정 파일에 ID가 실림)의 의도와 일치한다.
   - 주의: 기존 in-memory 마이그레이션(`refresh_seconds`/kakao 추론)은 write를 안 했다. persist-on-issue는 **파일이 존재할 때만** write하는 새 동작이다. 기존 `test_ui_settings.py`의 read-only fixture는 전부 `tmp_path`라 안전하다. 실 `runtime/state/ui_settings.json`은 업그레이드 후 첫 기동에서 한 번 ID가 채워져 다시 쓰이는데, 그게 곧 마이그레이션(원하는 동작)이며 atomic화는 2.2가 맡는다.
2. **(불채택) 내용 해시 기반 결정적 ID.** write가 필요 없지만, 사용자가 URL/센터를 편집하면 ID가 바뀌어 "안정적인 식별자" 의도를 위반한다 → 채택하지 않는다.

ID 포맷 권장: `uuid.uuid4().hex`(32자 소문자 hex) 평문 문자열. `customer_id`/`platform_account_id`/`monitoring_target_id` 각각 독립 발급(같은 값 재사용 금지). `customer_name`은 자동 발급 안 함(운영자가 이후 Admin/UI에서 채움 — Epic 5).

> **결정 기록(YOLO 기본값):** ID 전략은 위 **1안(불투명 uuid4 + persist-on-first-issue, no-file 가드)** 으로 구현한다. 운영자가 "결정적/내용기반 ID"나 "발급은 2.7 러너에서만, load는 in-memory만" 같은 다른 정책을 원하면 dev 전에 알려주면 조정한다. 합의가 없으면 1안으로 진행한다.

### 보존해야 할 공개 동작 (1.5 §3이 잠근 것 — 깨면 regression)

- (a) **저장 JSON 호환** — `ensure_ascii=False, indent=2`, `{"crawlings":[...]}` 구조, legacy 카카오 설정. (b) **9탭 로딩** — `load_all(max_tabs=9)` 정확히 9개. (c) **쿠팡 플랫폼 추론** — `performance_url`/`peak_dashboard_url`에 `partner.coupangeats.com` → `coupang`. (d) **렌더링 결과**(이 스토리는 직접 무관하나 회귀로 확인). 신규 필드 추가가 이 네 가지를 흔들면 안 된다. [Source: project-context.md(47·54·59·68), 1-5 스토리 §3]

### 이전 스토리 인텔리전스 (Epic 1 → 2.1 이월 교훈)

- **A1(secret 게이트):** Epic 1에서 secret near-miss가 반복됐다. 신규 테스트의 telegram/2FA 값은 기존 테스트가 쓰는 명백한 가짜값(`"token"`, `"-100123"`, `"77"`)을 재사용하고 실제 토큰 형태를 만들지 않는다. [Source: epic-1-retro 액션 A1]
- **A2(테스트 수치 stale):** 1.4·1.5에서 dev 노트의 잠정 pass 수치가 QA 보강 후 stale가 됐다. **Change Log/노트에 잠정 수치를 박지 말고, 리뷰 시점 재측정값 1개만** 정본으로 적는다. [Source: epic-1-retro 액션 A2, 1-5 Completion Notes(169)]
- **dev-env:** pytest는 반드시 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님). git tree는 CRLF/LF 노이즈가 있어 범위 확인은 `git diff -w`로 본다. [Source: memory/dev-env-quirks]
- **테스트 컨벤션:** `tests/test_ui_settings.py`는 평면 구조 + `tmp_path` + 순수 파일 I/O(외부 브라우저/네트워크 미호출). 신규 테스트도 동일. [Source: project-context.md(53-57), tests/test_ui_settings.py]

### Project Structure Notes

- 변경: `src/rider_crawl/ui_settings.py`(제품 코드) + `tests/test_ui_settings.py`(또는 신규 `tests/test_ui_settings_ids.py`). `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64)]
- 신규 ID 명명은 data-api-contract 정본(`monitoring_targets`: id, tenant_id, platform_account_id, ...)과 의미를 맞춘다. 단, 이 스토리는 DB가 아닌 `UiSettings`(dataclass) 레벨이라 컬럼이 아니라 필드명으로 둔다(`customer_id`는 추후 `tenant_id`로 이어지는 운영 개념 — 본 스토리는 UiSettings 필드명 그대로 사용). [Source: data-api-contract.md(25-28), architecture.md(248-270)]
- 상태 식별을 `crawlingN` 순번에 다시 묶지 않는다(architecture Anti-Pattern). 단 실제 `state_subdir` 전환은 2.2. [Source: architecture.md(365)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.1(357-378)] — user story·3개 Given/When/Then AC 원문(ID 4종 추가, legacy_alias 보존, 9탭/legacy 추론 비파괴, 재로드 ID 안정).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2(353-355)] — Epic 2 목표: 탭 번호 대신 안정 ID, 원본 손실·중복 발송·비활성 자동활성화 없는 마이그레이션. 도메인 모델/Alembic은 Epic 5.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P1-01(36)] — `customer_id`/`customer_name`/`platform_account_id`/`monitoring_target_id` 추가, 기존 탭명은 `legacy_alias`로만, 자동 마이그레이션. (P1-02=2.2 state_subdir, P1-03=2.2 atomic write 경계 확인)
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#Migration-Contract(96-104)] — 활성 탭만 후보 분류, 탭별 ID 발급, 원본 미삭제(전체 러너는 2.7).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(25-28)] — monitoring_targets/platform_accounts/tenants 필드 정본(ID 명명 정렬용).
- [Source: _bmad-output/planning-artifacts/architecture.md(246-288·338-346·361-366)] — 코드 네이밍(snake_case, 공개 경계 호환), 구조/파일 패턴(JSON 직렬화 정책), enforcement, Anti-Pattern(state_subdir를 crawlingN으로 식별 금지).
- [Source: _bmad-output/project-context.md(47·52-60·64·68·81-82)] — 9탭 모델, 테스트 규칙(tmp_path·외부 미호출), JSON `ensure_ascii=False,indent=2`, secret/식별자 비노출, 무관 변경 되돌리지 않기.
- [Source: src/rider_crawl/ui_settings.py(19-219)] — `UiSettings`/`UiSettingsStore`/`_settings_from_mapping`/`_to_jsonable` 현재 구조(변경 대상).
- [Source: src/rider_crawl/ui.py(25·61-62·96)] — `ui→ui_settings` import 방향(역방향 금지), `active_crawling_settings`(=`performance_url.strip()`), `state_subdir=f"crawling{n}"` 호출부(본 스토리 변경 금지).
- [Source: src/rider_crawl/config.py(50-57)] — `AppConfig` defaulted 필드 패턴 참고(연결은 안 함).
- [Source: tests/test_ui_settings.py(1-324)] — 기존 테스트 패턴·가짜값 컨벤션·회귀 잠금(9탭 로딩·legacy 추론·라운드트립).
- [Source: _bmad-output/implementation-artifacts/1-5-기존-자산-재사용-경계와-금지-행위-명문화.md] — 보존 공개 동작 4종(§3)·권위 계층·secret 가드 철학.
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-06-13.md(72-90)] — 2.1 의존성("1.5 §3 공개 동작 비파괴"), Epic 2가 첫 제품코드 에픽, 액션 A1(secret 게이트)·A2(테스트 수치 단일화).
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인은 `git diff -w`.
- 요구사항 추적: FR-5(legacy alias 보존), FR-4(ID 기반 모델 토대), FR-31(마이그레이션 안전 — 비활성 자동활성화 금지·원본 보존, 본 스토리는 ID 발급 부분), NFR-18(원본 보존), NFR-19/NFR-20(기존 JSON 호환 테스트·각 단계 회귀), P1-01.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code dev-story workflow)

### Debug Log References

- `.venv/Scripts/python.exe -m pytest tests/test_ui_settings.py -q` → 32 passed (18 기존 + 14 신규 = dev 8 + QA 자동화 갭 6).
- `.venv/Scripts/python.exe -m pytest -q` → 전체 회귀 통과(598 passed).
- `git diff -w --stat -- src/ tests/` → 변경 파일 2개만(아래 File List), `ui.py`/`config.py`/`app.py`/`state_subdir` 무변경 확인.
- 누출 grep(`[0-9]{6,}:[A-Za-z0-9_-]{30,}` / `chat_id=<digits>` / KR 휴대폰) → NO LEAKS FOUND.

### Completion Notes List

- **AC1** — `UiSettings`에 `customer_id`/`customer_name`/`platform_account_id`/`monitoring_target_id`/`legacy_alias` 5개 defaulted 필드를 기존 defaulted 블록 끝에 추가. `_settings_from_mapping`의 generic key-copy와 `_to_jsonable`의 `asdict` 덕분에 추가 매핑/직렬화 코드 없이 자동 라운드트립(save/load·save_all/load_all). `ensure_ascii=False, indent=2`·`{"crawlings":[...]}` 구조 불변. `to_app_config()`/`AppConfig`는 미연결(2.2 범위).
- **AC3 (핵심)** — 불투명 `uuid.uuid4().hex` + **persist-on-first-issue** 채택. 순수 헬퍼 `_issue_missing_ids(settings, *, legacy_alias)`가 빈 ID 3종에만 발급하고 이미 있는 값은 보존(idempotent). 새로 발급되면 1회 영속화 → 재로드 시 동일 ID. (내용 해시 기반 결정적 ID는 채택 안 함.)
- **AC3 #8 / 순환 import 가드** — 활성 탭 판정은 `ui.active_crawling_settings`와 동일한 의미(`performance_url.strip()`)를 `ui_settings.py` 안에서 **인라인 복제**(ui→ui_settings 의존 방향이라 역방향 import 금지). filler(빈) 탭에는 발급하지 않음.
- **원본 보존(NFR-18) 설계 결정** — 운영 경로는 `load_all`/`save_all`만 사용(ui.py:180·509 확인). 단일 `load()`/`save()`는 테스트 전용이며 `save()`는 평면 객체를 쓰므로, **다중 탭(`{"crawlings":[...]}`) 파일을 `load()`로 읽을 때는 ID 발급/영속화를 하지 않고** 기존 동작(tab 0 반환, write 없음)을 보존한다(평면 save로 영속화하면 나머지 탭 유실). 단일 객체 파일·load_all 경로에서만 persist-on-issue. 파일이 없을 때는 발급/write 안 함(no-file 가드).
- **회귀** — 기존 `test_ui_settings.py` 18개 전부 무수정 통과(9탭 로딩·legacy 카카오/쿠팡 추론·refresh_seconds 마이그레이션·2FA·to_app_config). 스키마 변경이 공개 동작을 건드리지 않음.
- **테스트 수치(리뷰 시점 재측정, 단일 정본):** `.venv/Scripts/python.exe -m pytest -q` → **598 passed** (기준선 584 + 신규 14). 신규 14 = dev-story 8 + QA 자동화 갭 보강 6(`test-summary-2.1.md`). 회귀 0.
- **secret 비노출** — 신규 테스트는 `"token"`/`"-100123"`/`example.test`·店名 placeholder만 사용. 누출 grep 통과.

### File List

- `src/rider_crawl/ui_settings.py` (수정) — `UiSettings` 5개 ID/alias 필드 추가, `_issue_missing_ids` 헬퍼 추가, `load`/`load_all`에 활성 탭 ID 발급 + persist-on-first-issue.
- `tests/test_ui_settings.py` (수정) — Story 2.1 신규 테스트 14개 추가 = dev-story 8개(필드 라운드트립·재로드 ID 안정성·idempotent 보존·활성 탭만 발급·no-file 가드·legacy_alias seed/보존·단일 객체 load 안정성) + QA 자동화 갭 보강 6개(3개 ID 독립 발급·customer_name 자동발급 금지·필드 단위 멱등·공백 URL 비활성·완전발급 파일 무재기록·AppConfig ID 미노출).
- `_bmad-output/implementation-artifacts/tests/test-summary-2.1.md` (신규) — QA 자동화 테스트 요약/갭 분석/커버리지 매트릭스(리뷰 대상 아님 — _bmad-output 산출물, File List 정합성 위해 기재).

## Senior Developer Review (AI)

- **리뷰어:** lsy9344 · **일자:** 2026-06-13 · **결과:** Approve (CRITICAL 0 / HIGH 0)
- **검증 범위:** `git status`/`git diff -w`로 실제 변경 추출, AC1~AC3·Task 1~4를 구현·테스트와 1:1 추적, `ui.py`/`config.py` 교차검증, 전체 스위트 `.venv/Scripts/python.exe -m pytest -q` → **598 passed** 실측.
- **AC 결과:** AC1·AC2·AC3 **전부 IMPLEMENTED**. 5개 defaulted 필드 + generic key-copy 자동 라운드트립, `legacy_alias` seed/보존, uuid4 + persist-on-first-issue(필드 단위 멱등), 활성 탭 한정(`performance_url.strip()` 인라인 — 순환 import 없음), no-file 가드 모두 테스트로 잠김. `to_app_config()`/`AppConfig` 미연결(누출 가드 테스트 `test_to_app_config_does_not_expose_id_fields` 존재).
- **범위 규율:** diff = `ui_settings.py`(+63) + `test_ui_settings.py`(+258)만. `ui.py`의 `state_subdir=f"crawling{n}"`(96·524·614·808)·`config.py` 무변경 확인 — 2.2~2.5 경계 침범 없음.
- **수정한 지적(자동 수정):**
  - **[MEDIUM] M1 — Dev Agent Record 수치 stale(A2 위반):** "신규 8 / 592 passed / 26(18+8)"로 박혀 있었으나 QA 갭 6개 보강 반영 시 실측은 "신규 14 / 598 passed / 32(18+14)". Debug Log·Completion Notes·File List를 리뷰 시점 단일 정본(598)으로 정정.
  - **[MEDIUM] M2 — File List 누락:** Story 2.1 QA 산출물 `tests/test-summary-2.1.md`를 File List에 기재.
- **미수정 관찰:**
  - **[LOW] L1:** `load()`(테스트 전용 경로)는 비현실 입력 `{"crawlings": []}`(빈 리스트)에서 fall-through로 기본 URL을 활성으로 보고 평면 객체를 write할 수 있음. 운영 경로(`load_all`)·실제 파일과 무관하고 정상 동작 코드라 미수정(스코프 보존). 차후 입력 검증 강화 시 후속.
  - atomic write·`state_subdir` 전환은 의도대로 본 스토리 범위 밖(Story 2.2).

## Change Log

| 날짜 | 변경 | 비고 |
| --- | --- | --- |
| 2026-06-13 | Senior Developer Review(AI) 완료: AC1~AC3 전부 구현 확인, CRITICAL/HIGH 0. Dev Agent Record 테스트 수치 stale(M1) + File List 누락(M2) 자동 수정. | Status → done. 전체 스위트 598 passed(재측정, 단일 정본). 소스 코드 무변경. |
| 2026-06-13 | Story 2.1 컨텍스트 작성: UiSettings ID/legacy_alias 필드 + 재로드 안정 마이그레이션 스펙. | Status → ready-for-dev. 기준선 584 collected(d701aa4). |
| 2026-06-13 | Story 2.1 구현: `UiSettings`에 customer/platform_account/monitoring_target ID + legacy_alias 추가, 활성 탭 대상 uuid4 발급 + persist-on-first-issue 마이그레이션, 신규 테스트 8개. | Status → review. 전체 스위트 592 passed(재측정). 변경: `ui_settings.py`·`test_ui_settings.py`만. |
