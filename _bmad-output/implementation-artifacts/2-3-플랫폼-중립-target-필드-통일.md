---
baseline_commit: c5d7b00
---

# Story 2.3: 플랫폼 중립 Target 필드 통일

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 개발자,
I want 배민과 쿠팡이 같은 모니터링 대상 모델을 쓰도록 **플랫폼 중립 필드 `center_name`·`display_name`·`target_external_id`·`primary_url`** 를 도입하되, 기존 공개 경계 이름(`coupang_eats_url`·`baemin_center_name`·`baemin_center_id`·`performance_url`)은 **넓은 변경 없이 legacy alias로 매핑**하고, 쿠팡 기대 센터/상점명이 비었거나 배민 기본값이면 **위험 상태로 분류될 수 있는(비차단) 필드 상태**를 노출하고 싶다,
so that 플랫폼별로 갈라진 식별 필드 없이 하나의 Target 필드 집합으로 대상을 다루면서도, 30+ 호출부의 기존 필드명을 깨지 않고(ADD-8), 쿠팡의 기대 센터/상점명 검증 토대(FR-20)를 유지한다.

> **이 스토리의 성격 — "이름만 통일하는 얇은 alias 레이어." 데이터 이전도, 도메인 클래스 신설도 아니다.** P1-05는 **새 저장 필드를 추가하지 않는다.** 기존 `UiSettings`/`AppConfig`가 이미 보유한 필드(`performance_url`·`baemin_center_name`·`baemin_center_id`·`legacy_alias`/`crawl_name`)를 **플랫폼 중립 이름으로 읽는 read-only 접근자(`@property`)** 를 더하는 것이 전부다. 그래서 직렬화(`asdict`)·JSON 호환·마이그레이션·9탭 로딩에 **영향이 0**이다(`@property`는 dataclass 필드가 아니라 직렬화에 안 잡힌다). [Source: implementation-contract.md P1-05(40), architecture.md Naming(269-270), src/rider_crawl/ui_settings.py(263-267)]
>
> **엄격한 범위 경계(스코프 크립 방지).** 본 스토리는 **오직** (1) 4개 중립 접근자(`primary_url`·`center_name`·`target_external_id`·`display_name`)를 `UiSettings`·`AppConfig`에 추가하고, (2) 비차단 위험 분류기(쿠팡 기대 센터/상점명 empty/배민-기본값 → risk flag)를 추가하는 것만 한다. 아래는 **다른 스토리 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **기존 필드명 일괄 rename 금지.** `coupang_eats_url`/`baemin_center_name`/`baemin_center_id`/`performance_url`을 sed로 넓게 바꾸지 않는다. 기존 30+ 호출부(crawler·coupang/crawler·app·browser_launcher·telegram_commands·redaction·ui)는 **그대로 둔다**(legacy 이름 = 저장 필드, 중립 이름 = 읽기 별칭). [Source: project-context.md(67), architecture.md(269-270)]
> - **도메인 dataclass/Enum**(`MonitoringTarget`/`Tenant`/상태 enum) 신설 → **Story 2.5**(ADD-7/FR-30). 본 스토리는 `Target` 같은 **새 클래스를 만들지 않는다** — 기존 모델에 접근자만 더한다. enum(`ACTIVE`/`CENTER_MISMATCH` 등)도 2.5 소유라 위험 분류기는 enum이 아니라 단순 bool/reason으로 반환한다.
> - **secret 분리·`*_ref`화** → **Story 2.4**(P1-06).
> - **실제 실행 차단**(위험 대상의 작업 미생성/미발송) → **Epic 4**(FR-14/FR-20). 본 스토리의 분류기는 **분류만** 하고 **막지 않는다**. 기존 저장 단계 차단(`_validate_coupang_expected_center`, ui.py 1174-1193 / `_require_coupang_center`, config.py 261-277)은 **그대로 유지**한다(약화 금지).
> - **dedup scope key 재배선 금지.** `app._message_scope_key`(app.py 98-115)는 `coupang_eats_url`/`baemin_center_name`/`baemin_center_id`를 직접 읽는다. 중립 접근자로 바꿔도 값은 같지만, scope key는 중복 판단의 정본이라 **건드리지 않는다**(project-context.md 92: scope key 축소/변경은 다른 탭/계정 중복 판단을 섞을 위험). [Source: src/rider_crawl/app.py(98-115), project-context.md(92)]
>
> **기준선 회귀 0.** 현재 HEAD(`c5d7b00`, Story 2.2 done)에서 전체 스위트는 **618 collected**(참고값 — 복사 금지, 본인이 `.venv/Scripts/python.exe -m pytest --collect-only -q`로 재측정). 신규/수정 테스트 케이스만큼만 변동이 정상이고, 기존 통과 테스트가 새로 깨지면 실패다(NFR-20). **A2 교훈: dev 노트에 잠정 pass 수치를 박아 stale를 만들지 말 것 — 리뷰 시점 재측정값 1개만 정본으로 기록한다.** [Source: epic-1-retro-2026-06-13.md 액션 A2, memory/dev-env-quirks]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 신규/수정 테스트·fixture에 실제 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 기존 테스트가 쓰는 명백한 가짜값(`"token"`, `"-100123"`, `"77"`)과 placeholder 센터명(`"강남센터"` 등 가공명)만 쓴다. [Source: project-context.md(81·89), epic-1-retro 액션 A1]

## Acceptance Criteria

**AC1 — 플랫폼 중립 필드 4종 도입, 배민·쿠팡이 같은 Target 필드 집합 사용, center_name으로 쿠팡 검증 유지 (P1-05, FR-20 연계)**
1. **Given** 현재 배민/쿠팡이 같은 의미를 서로 다른/오해 소지 있는 이름으로 쓸 때(예: 주 URL이 `coupang_eats_url`인데 배민 탭도 이 필드를 씀, 기대 센터/상점명이 `baemin_center_name`인데 쿠팡 탭도 이 필드를 재사용) **When** 플랫폼 중립 read-only 접근자 `primary_url`·`center_name`·`target_external_id`·`display_name`을 `UiSettings`와 `AppConfig` 둘 다에 추가하면 **Then** 배민·쿠팡 두 플랫폼이 **동일한 중립 필드 이름**으로 대상을 읽을 수 있고(같은 Target 필드 집합), 각 접근자는 아래 매핑대로 기존 필드 값을 그대로 반환한다. [Source: implementation-contract.md P1-05(40), data-api-contract.md monitoring_targets(28), src/rider_crawl/config.py(36-77), src/rider_crawl/ui_settings.py(22-61)]
   - `primary_url`   ← `UiSettings.performance_url` / `AppConfig.coupang_eats_url` (주 실적/달성현황 URL)
   - `center_name`   ← `baemin_center_name` (양쪽; 쿠팡은 기대 센터/상점명)
   - `target_external_id` ← `baemin_center_id` (양쪽; `monitoring_targets.external_id` 정렬)
   - `display_name`  ← `UiSettings.legacy_alias` / `AppConfig.crawl_name` (사람 표시 라벨)
2. **And** 쿠팡의 기대 센터/상점명 검증이 **`center_name`을 통해 유지**된다: 기존 크롤러 검증(`coupang/crawler._validate_coupang_center`, `_coupang_center_aliases(config.baemin_center_name)`)이 회귀 없이 그대로 통과하고, `center_name` 접근자가 `baemin_center_name`과 항상 동일 값을 반환함을 테스트로 잠근다(FR-20 연계). [Source: src/rider_crawl/platforms/coupang/crawler.py(55-99), epics.md FR-20(54)]

**AC2 — 기존 공개 경계 이름은 legacy alias로 매핑, 임의 rename 금지 (ADD-8, project-context 규칙)**
3. **Given** 공개 경계 이름 호환을 우선해야 할 때(`coupang_eats_url`·`baemin_center_name`·`baemin_center_id`·`performance_url` 등 30+ 호출부) **When** 신규 중립 접근자를 도입하면 **Then** 기존 필드는 **저장 정본(=legacy alias)으로 그대로 남고** 이름만 임의로 바뀌지 않으며, 중립 접근자는 그 위에 얹힌 **읽기 별칭**이다. `git diff -w`에 기존 30+ 호출부의 이름 변경이 **0건**이어야 한다(crawler.py·coupang/crawler.py·app.py·browser_launcher.py·telegram_commands.py·redaction.py·ui.py의 `coupang_eats_url`/`baemin_center_name` 참조 무변경). [Source: project-context.md(67), architecture.md Anti-Patterns·Naming(269-270), 기존 grep 30+ 호출부]
4. **And** 접근자 추가가 **직렬화·JSON 호환·마이그레이션을 흔들지 않는다**: `@property`는 dataclass 필드가 아니라 `_to_jsonable`의 `asdict()`·`save`/`save_all`·`{"crawlings":[...]}`·`ensure_ascii=False, indent=2`·9탭 로딩·2.1 ID 발급/persist·2.2 atomic write·legacy 카카오/쿠팡 추론이 **전부 무변경 통과**한다. `AppConfig`는 `frozen=True`지만 `@property`는 메서드라 frozen과 무관하다. [Source: src/rider_crawl/ui_settings.py(263-267·160-202), src/rider_crawl/config.py(36-37), tests/test_ui_settings.py]

**AC3 — 쿠팡 기대 센터/상점명 empty/배민-기본값 → 비차단 위험 분류 (FR-20 토대, 실제 차단은 Epic 4)**
5. **Given** 쿠팡 탭의 기대 센터/상점명(`center_name`)이 비어 있거나 배민 기본값(`DEFAULT_BAEMIN_CENTER_NAME`)일 때 **When** Target의 `center_name`을 (platform_name과 함께) 위험 분류기로 검증하면 **Then** 그 대상은 **위험 상태로 분류될 수 있는 필드 상태**(예: `is_risky=True` + 사유)를 반환받는다 — 이는 `_validate_coupang_expected_center`/`_require_coupang_center`가 쓰는 **동일한 두 조건**(empty / 배민-기본값)을 **예외를 던지지 않고** 비차단으로 노출한 것이다(FR-20 토대). [Source: src/rider_crawl/ui.py(1174-1193), src/rider_crawl/config.py(261-277), epics.md FR-20(54)]
6. **And** 분류기는 **분류만 하고 차단하지 않는다**: 위험이어도 예외를 던지지 않고, 작업 생성/메시지 발송을 막지 않는다(실제 차단·상태 전이는 Epic 4 FR-14/FR-20 소유). 배민 탭(`platform_name != "coupang"`)은 이 분류에서 위험으로 보지 않는다(배민은 별도 센터명/ID 규칙 — ui.py `_validate_active_baemin_center_identity` 소유, 본 분류기 범위 밖). 기존 **저장 단계 차단**(`_validate_coupang_expected_center`)은 약화하지 않고 그대로 둔다. [Source: epics.md AC#3(418-420)·FR-14(45)·FR-20(54), src/rider_crawl/ui.py(1127-1148·1174-1193)]
7. **And** enum을 새로 만들지 않는다(상태 enum은 Story 2.5 소유): 위험 표현은 단순 bool + 사유 문자열(또는 작은 frozen 결과)로 두고 `ACTIVE`/`CENTER_MISMATCH` 같은 대문자 enum 문자열을 **여기서 정의하지 않는다**. [Source: epics.md Story 2.5(440-461), data-api-contract.md State Machines(94-131)]

## Tasks / Subtasks

- [x] **Task 1 — `UiSettings`에 플랫폼 중립 read-only 접근자 추가 (AC: 1, 2, 4)**
  - [x] `src/rider_crawl/ui_settings.py`의 `UiSettings` dataclass에 `@property` 4개를 추가한다(필드 아님 — 메서드): `primary_url`→`self.performance_url`, `center_name`→`self.baemin_center_name`, `target_external_id`→`self.baemin_center_id`, `display_name`→`self.legacy_alias`. 각 접근자는 **순수 읽기**이며 strip/가공 없이 원본 값을 그대로 반환한다(소비자가 기존처럼 `.strip()`을 호출하므로 여기서 가공하면 의미가 갈라진다). [Source: src/rider_crawl/ui_settings.py(22-61)]
  - [x] 접근자 이름이 기존 필드와 충돌하지 않음을 확인한다(UiSettings 필드에 `primary_url`/`center_name`/`target_external_id`/`display_name` 없음 — 충돌 0). dataclass 필드 순서/기본값 규칙은 건드리지 않는다(필드를 추가하지 않으므로 영향 없음). [Source: src/rider_crawl/ui_settings.py(22-61)]
  - [x] `_to_jsonable`(263-267)·`_settings_from_mapping`(270-284)은 **변경 금지**: `asdict`는 `@property`를 직렬화하지 않으므로 JSON 출력이 100% 동일하다. 추가 매핑 코드가 필요한지 확인하고 불필요하면 만들지 않는다(wheel 재발명 금지). [Source: src/rider_crawl/ui_settings.py(263-284)]
- [x] **Task 2 — `AppConfig`에 동일한 중립 접근자 추가 (AC: 1, 2, 4)**
  - [x] `src/rider_crawl/config.py`의 `AppConfig`(frozen dataclass)에 `@property` 4개를 추가한다: `primary_url`→`self.coupang_eats_url`, `center_name`→`self.baemin_center_name`, `target_external_id`→`self.baemin_center_id`, `display_name`→`self.crawl_name`. `frozen=True`는 필드 할당만 막고 `@property` 정의에는 영향 없다. 기존 `runtime_dir`/`state_dir` 프로퍼티 패턴(108-134) 바로 옆/아래에 둔다. [Source: src/rider_crawl/config.py(36-77·108-134)]
  - [x] **소비자 재배선 금지(범위 최소화).** 기존 30+ 호출부(`crawler.py`·`platforms/coupang/crawler.py`·`app.py`·`browser_launcher.py`·`telegram_commands.py`·`redaction.py`)는 계속 `config.coupang_eats_url`/`config.baemin_center_name`을 쓴다. 본 스토리는 **중립 이름을 노출만** 하고, 소비자를 중립 접근자로 바꾸는 광범위 리팩토링은 **하지 않는다**(legacy alias 매핑의 의도 = 이름 추가지 일괄 교체 아님). 특히 `app._message_scope_key`(98-115)는 dedup 정본이라 절대 건드리지 않는다. [Source: project-context.md(67·92), src/rider_crawl/app.py(98-115)]
- [x] **Task 3 — 비차단 위험 분류기 추가 (AC: 3, 5, 6, 7)**
  - [x] `src/rider_crawl/config.py`에 **순수 함수**를 추가한다(예: `def coupang_center_name_risk(platform_name: str, center_name: str) -> tuple[bool, str]:` 또는 작은 frozen 결과 반환). 동작: `platform_name.strip().casefold() != "coupang"`이면 `(False, "")`. 쿠팡이고 `center_name.strip()`이 비면 `(True, "<empty 사유>")`, `center_name.strip() == DEFAULT_BAEMIN_CENTER_NAME`이면 `(True, "<배민 기본값 사유>")`, 그 외 `(False, "")`. **예외를 던지지 않는다**(비차단). [Source: src/rider_crawl/config.py(261-277), src/rider_crawl/ui.py(1180-1193)]
  - [x] **조건 단일 소스화(wheel 재발명/드리프트 방지 — 권장, 비필수).** 이 두 조건(empty / 배민-기본값)은 이미 `_require_coupang_center`(config.py 261-277)와 `_validate_coupang_expected_center`(ui.py 1174-1193)가 **각각 raise로** 구현한다. 가능하면 **조건 판정만** 순수 predicate로 뽑아 분류기가 재사용한다. `_require_coupang_center`를 그 predicate로 리팩토링해도 되지만 **raise 동작·메시지·`test_config.py` 통과를 100% 보존**해야 한다(보존 못 하면 리팩토링하지 말고 분류기만 독립 추가). ui.py의 raise는 본 스토리에서 건드리지 않아도 된다(중복 허용 — 안전이 우선). [Source: src/rider_crawl/config.py(261-277), tests/test_config.py]
  - [x] **편의 접근자(선택).** `UiSettings`(또는 분류기 호출 헬퍼)에서 `coupang_center_name_risk(self.platform_name, self.center_name)`를 쉽게 부를 수 있게 한다. 단 새 enum/도메인 클래스는 만들지 않는다(2.5). 분류 결과를 어디에 저장하거나 상태 전이에 쓰지 않는다(Epic 4). [Source: epics.md AC#3(418-420)·Story 2.5(440-461)]
- [x] **Task 4 — 테스트 추가/보강 (AC: 1~7)** — 기존 패턴(`tmp_path`, 순수 객체, 외부 미호출, 가짜값) 사용:
  - [x] **(AC1 매핑) — `tests/test_ui_settings.py` + `tests/test_config.py`:** 값을 채운 `UiSettings`/`AppConfig`에서 `primary_url == performance_url`(UiSettings)/`== coupang_eats_url`(AppConfig), `center_name == baemin_center_name`, `target_external_id == baemin_center_id`, `display_name == legacy_alias`(UiSettings)/`== crawl_name`(AppConfig)임을 단언(배민·쿠팡 두 platform_name 모두). [Source: src/rider_crawl/ui_settings.py(102-135), src/rider_crawl/config.py(36-77)]
  - [x] **(AC2 직렬화 불변) — `tests/test_ui_settings.py`:** 중립 접근자가 있어도 `save`/`load`·`save_all`/`load_all` 라운드트립 텍스트에 `primary_url`/`center_name`/`target_external_id`/`display_name` **키가 들어가지 않음**(=`asdict`에 안 잡힘)과 `ensure_ascii=False`·`{"crawlings":[...]}` 보존을 단언. 기존 라운드트립·2.1 persist·2.2 atomic·9탭·legacy 추론 테스트가 무수정 통과 확인. [Source: tests/test_ui_settings.py(74-145), src/rider_crawl/ui_settings.py(263-267)]
  - [x] **(AC1 쿠팡 검증 유지) — `tests/test_coupang_crawler.py`(또는 기존 위치):** `center_name` 접근자가 `baemin_center_name`과 동일 값을 반환하고, 기존 `_validate_coupang_center`/`_coupang_center_aliases` 경로가 회귀 없이 통과함을 확인(이미 있는 검증 테스트가 깨지지 않으면 충분). [Source: src/rider_crawl/platforms/coupang/crawler.py(55-99)]
  - [x] **(AC3 위험 분류) — `tests/test_config.py`:** `coupang_center_name_risk` 단위 테스트 — (a) coupang + 빈 center_name → `is_risky True`, (b) coupang + `DEFAULT_BAEMIN_CENTER_NAME` → `True`, (c) coupang + 실제 가공 센터명(`"강남센터"`) → `False`, (d) baemin + 어떤 값이든 → `False`, (e) **예외 미발생**(비차단) 단언. 만약 `_require_coupang_center`를 predicate로 리팩토링했다면 그 raise 테스트가 무수정 통과 확인. [Source: src/rider_crawl/config.py(261-277), src/rider_crawl/ui.py(1180-1193)]
  - [x] **(AC6 차단 미약화) — 회귀 확인:** 기존 저장 단계 차단 테스트(`_validate_coupang_expected_center`/`_validate_active_coupang_urls`의 raise, ui helpers 테스트)와 `_require_coupang_center`의 raise 테스트가 **그대로 통과**함을 확인(분류기 추가가 차단을 약화하지 않음). [Source: src/rider_crawl/ui.py(1154-1193), tests/test_ui_helpers.py, tests/test_config.py]
  - [x] secret 비노출: 신규 테스트 값은 placeholder/가짜값만(`"강남센터"`·`"DP000"`·`"https://partner.coupangeats.com/page/peak-dashboard"`·`"token"`·`"-100123"`). 실제 토큰/전화/이메일 형태 금지. [Source: project-context.md(81), epic-1-retro 액션 A1]
- [x] **Task 5 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`. WSL 시스템 `python3` 사용 금지(pytest 미설치). 기준선 **618**(참고값 — 본인이 재측정) 대비 기존 통과가 새로 깨지지 않고 신규 케이스만큼만 증가가 정상. [Source: memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 `ui_settings.py` + `config.py` + 관련 테스트만 보이고, **`crawler.py`/`platforms/coupang/crawler.py`/`app.py`/`browser_launcher.py`/`telegram_commands.py`/`redaction.py`/`ui.py`의 기존 `coupang_eats_url`/`baemin_center_name` 참조는 무변경**임을 확인(legacy 이름 일괄 rename 0건). CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. 모든 테스트는 `tmp_path`/순수 객체로(실 `runtime/`·`ui_settings.json` 미변형). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep: 신규/수정 코드·테스트에 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·한국 휴대폰 평문이 없는지 확인. [Source: epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2). [Source: epic-1-retro 액션 A2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **2개 추가**만 한다: (P1-05) 플랫폼 중립 read-only 접근자 4종(`UiSettings`·`AppConfig`), (FR-20 토대) 비차단 쿠팡 센터 위험 분류기 1개. 변경 표면: `ui_settings.py`(+4 property), `config.py`(+4 property +1 분류기 함수), + 테스트.
- **건드리지 않는다:** 기존 필드명 일괄 rename(legacy 이름 = 저장 정본 유지), 30+ 소비자 재배선, `app._message_scope_key`(dedup 정본), `_to_jsonable`/`_settings_from_mapping`(직렬화 불변), 2.1 ID 발급/persist·2.2 atomic write/state_subdir·2.2 로그 rotation, secret_ref(2.4), 도메인 dataclass/Enum(2.5), 마이그레이션 러너(2.7), 실제 차단·상태 전이(Epic 4). 기존 저장 단계 차단(`_validate_coupang_expected_center`/`_require_coupang_center`)은 **약화 금지**. [Source: implementation-contract.md(40), project-context.md(67·92)]

### 핵심 설계 — 왜 새 필드/클래스가 아니라 `@property` alias인가 (AC1·AC2·AC4, 반드시 읽을 것)

- **현 상태 매핑(오해 소지 있는 이름 → 중립 이름):**
  - 주 URL: `UiSettings.performance_url` → `AppConfig.coupang_eats_url`. 이름은 "coupang_eats_url"이지만 **배민/쿠팡 공용 주 URL**이다(config.py 58-60 주석이 명시). 중립 이름 `primary_url`로 노출한다.
  - 기대 센터/상점명: `baemin_center_name`. 이름은 "baemin"이지만 **쿠팡 탭이 기대 센터/상점명으로 재사용**한다(crawler·coupang/crawler·ui 1177·redaction 182가 명시). 중립 이름 `center_name`으로 노출한다.
  - 외부 식별자: `baemin_center_id`(예 `DP2605181318`) → `monitoring_targets.external_id` 정렬, 중립 이름 `target_external_id`.
  - 표시 라벨: `UiSettings.legacy_alias`(2.1이 보존한 `크롤링N` 표시명) / `AppConfig.crawl_name`(런타임 `크롤링{n}` 라벨) → 중립 이름 `display_name`.
- **왜 read-only `@property`인가:**
  1. **JSON 호환·마이그레이션 0 영향.** `_to_jsonable`은 `asdict(settings)`를 쓰는데 `asdict`는 dataclass **필드만** 직렬화한다. `@property`는 필드가 아니므로 저장 JSON에 새 키가 안 생긴다 → 2.1/2.2가 잠근 `ensure_ascii=False, indent=2`·`{"crawlings":[...]}`·9탭·atomic write·persist-on-issue·legacy 추론이 전부 무변경. 새 저장 필드를 넣었다면 dual-write·migration·sync 위험이 생긴다 — 그래서 필드가 아니라 별칭이다.
  2. **"legacy alias로 매핑"의 정확한 의미(AC2).** epics 문구 "기존 `coupang_eats_url`·`baemin_center_name` 등은 legacy alias로 매핑"에서, **기존 이름이 legacy alias(저장 정본)** 이고 **중립 이름이 canonical 읽기 뷰**다. 저장 필드를 rename하지 않으므로 30+ 호출부가 안 깨진다(ADD-8/project-context 67). [Source: architecture.md(269-270)]
  3. **frozen 무관.** `AppConfig(frozen=True)`는 필드 재할당만 막는다. `@property`는 메서드라 frozen에서도 정상 동작한다(기존 `runtime_dir`/`state_dir` 프로퍼티가 증거).
- **왜 `Target`/`MonitoringTarget` 클래스를 지금 만들지 않나:** 도메인 dataclass `MonitoringTarget`(필드·관계·soft delete)은 **Story 2.5**(ADD-7) 소유다. 지금 별도 `Target` 클래스를 만들면 2.5와 표면이 겹쳐 재작업/충돌이 난다. P1-05의 deliverable은 **"중립 필드 이름"** 이지 새 컨테이너가 아니다 — 기존 모델에 접근자를 얹는 게 경계상 정확하다. [Source: epics.md Story 2.5(440-461), 2-2 스토리 범위 경계(23)]

### 위험 분류기 설계 (AC3 — FR-20 토대, 비차단)

- **무엇을 만드나:** 쿠팡 기대 센터/상점명이 (a) 비었거나 (b) 배민 기본값(`DEFAULT_BAEMIN_CENTER_NAME = "표준서울마포B이츠앤홀딩스3"`)이면 **위험으로 분류**하는 **순수·비차단** 함수. 두 조건은 이미 코드 두 곳이 raise로 구현 중이다 — 같은 조건을 **예외 없이 flag로** 노출하는 게 본 스토리의 신규 부분이다.
  - `_require_coupang_center`(config.py 261-277): env/CLI 경로, **raise**.
  - `_validate_coupang_expected_center`(ui.py 1174-1193): UI 저장 경로, **raise**.
  - `_validate_coupang_center`(coupang/crawler.py 50-87): 런타임 화면 대조, **raise**.
- **왜 비차단인가(Epic 경계):** 실제 작업 차단·상태 전이(위험 대상의 CrawlJob 미생성, `CENTER_MISMATCH` 상태)는 **Epic 4 FR-14/FR-20** 소유다. 본 스토리는 "Target이 위험으로 **분류될 수 있는 필드 상태**를 가진다"는 토대만 만든다(epics AC#3 "실제 차단은 Epic 4"). 그래서 분류기는 bool/reason만 반환하고 흐름을 막지 않는다. 기존 raise 차단은 그대로 둬서(약화 금지) 현재 안전망은 유지된다. [Source: epics.md(418-420), src/rider_crawl/ui.py(1174-1193)]
- **왜 enum이 아니라 bool/reason인가:** 상태 enum(`ACTIVE`/`AUTH_REQUIRED`/`CENTER_MISMATCH` 대문자 문자열)은 **Story 2.5**(ADD-9/FR-30)가 정본을 정의한다. 지금 `CENTER_MISMATCH` 같은 값을 만들면 2.5와 충돌하거나 선점한다. 단순 `(is_risky: bool, reason: str)`로 두고, 2.5가 enum을 만들 때 그쪽으로 승격하게 한다. [Source: data-api-contract.md State Machines(94-131), epics.md Story 2.5(454-457)]
- **DRY vs 안전:** 조건을 predicate로 단일화해 분류기·`_require_coupang_center`가 공유하면 깔끔하다(wheel 재발명 방지). 단 `_require_coupang_center`의 **raise 메시지/동작·`test_config.py`를 100% 보존**할 수 있을 때만 리팩토링하고, 위태로우면 **분류기만 독립 추가**(조건 중복 허용 — 안전 우선). ui.py의 raise는 본 스토리에서 손대지 않아도 된다.

### 보존해야 할 공개 동작 (깨면 regression)

- (a) **JSON 직렬화 불변** — `@property` 추가가 `asdict`/저장 텍스트에 새 키를 만들면 안 된다(2.1/2.2가 잠금). (b) **쿠팡 센터 검증** — `center_name`이 `baemin_center_name`과 항상 동일 값 → 기존 `_validate_coupang_center`/alias 경로 무회귀. (c) **저장 단계 차단 유지** — 분류기 추가가 `_validate_coupang_expected_center`/`_require_coupang_center`의 raise를 약화하면 안 된다. (d) **기존 필드명·30+ 호출부 무변경** — legacy 이름 rename 0건(ADD-8). [Source: project-context.md(47·67·68·92), src/rider_crawl/platforms/coupang/crawler.py(55-99)]

### 코드 앵커 (변경 대상 정밀 위치)

- `src/rider_crawl/ui_settings.py`
  - `UiSettings` dataclass 22-61: **여기에 `@property` 4개 추가**(`primary_url`/`center_name`/`target_external_id`/`display_name`). 필드 추가 아님.
  - `to_app_config()` 102-135: **변경 금지**(중립 접근자는 AppConfig에도 따로 추가하므로 여기 매핑 불필요).
  - `_to_jsonable()` 263-267, `_settings_from_mapping()` 270-284: **변경 금지**(asdict가 property 무시 → 직렬화 불변).
  - `2.1` ID 발급/persist(154-202·238-260), `2.2` `_atomic_write_text`(205-235): **무변경**, 참조만.
- `src/rider_crawl/config.py`
  - `AppConfig` 36-77: **여기에 `@property` 4개 추가**(`primary_url`→`coupang_eats_url`, `center_name`→`baemin_center_name`, `target_external_id`→`baemin_center_id`, `display_name`→`crawl_name`). 기존 `runtime_dir`/`state_dir`(108-134) 패턴 따름.
  - `_require_coupang_center()` 261-277, `DEFAULT_BAEMIN_CENTER_NAME` 237: 분류기가 쓰는 두 조건·상수. 분류기 추가(가능하면 조건 predicate 공유).
- **참조만(변경 금지):** `app._message_scope_key`(98-115, dedup 정본), `crawler.py`(26·335·339·374), `platforms/coupang/crawler.py`(55-99·360-440), `browser_launcher.py`(108·150), `telegram_commands.py`(313·541), `redaction.py`(23·182), `ui.py`(141·233·338·1132·1180·1189). 이들의 `coupang_eats_url`/`baemin_center_name` 참조는 **그대로 둔다**.

### 이전 스토리 인텔리전스 (Epic 1 → 2.1 → 2.2 → 2.3 이월 교훈)

- **A1(secret 게이트):** 신규 테스트 값은 명백한 가짜값만(`"강남센터"`·`"DP000"`·`"token"`·`"-100123"`). 실제 토큰/전화/이메일 형태 금지. [Source: epic-1-retro 액션 A1]
- **A2(테스트 수치 stale):** dev 노트에 잠정 pass 수치 박지 말 것 — **리뷰 시점 재측정값 1개만** 정본. 2.1/2.2 모두 dev-story 수치가 QA 갭 보강 후 stale가 돼 리뷰에서 정정됐다(2.1 M1, 2.2 M1). [Source: epic-1-retro 액션 A2, 2-1 Review M1, 2-2 Review M1]
- **2.1/2.2 교훈(범위 규율):** 2.1은 ID 발급만, 2.2는 그 ID를 경로에 연결만 했다(직교 보강 분리). 본 스토리도 "이름 통일 + 위험 분류 토대"만 하고 실제 차단(Epic 4)·도메인 클래스(2.5)를 끌어오지 않는다. [Source: 2-1 스토리(19-26), 2-2 스토리(19-26)]
- **dev-env:** pytest는 반드시 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — pytest 미설치). git tree는 CRLF/LF 노이즈 — 범위 확인은 `git diff -w`. [Source: memory/dev-env-quirks]
- **테스트 컨벤션:** `tests/`는 미러 구조 + `tmp_path` + 순수 객체/파일 I/O(외부 브라우저/네트워크/PC앱 미호출). 설정 테스트는 `test_config.py`/`test_ui_settings.py`, 쿠팡 검증은 `test_coupang_crawler.py`. [Source: project-context.md(53-54·57), architecture.md(280-282)]

### Project Structure Notes

- 변경: `src/rider_crawl/ui_settings.py`·`config.py`(제품 코드) + 테스트(`test_ui_settings.py`·`test_config.py`, 필요 시 `test_coupang_crawler.py`). `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64)]
- 중립 이름은 data-api-contract `monitoring_targets`(name=`display_name`, external_id=`target_external_id`, url=`primary_url`, 기대 센터/상점명=`center_name`) 의미와 정렬한다. 단 본 스토리는 DB 컬럼이 아니라 dataclass **접근자**라 테이블 생성/Alembic은 하지 않는다(Epic 5 P4-02). [Source: data-api-contract.md(28), architecture.md Data(162-174)]
- 상태 식별을 `crawlingN`에 다시 묶지 않는다(architecture Anti-Pattern 365) — 본 스토리와 직접 무관하나 회귀로 보존. [Source: architecture.md(361-366)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.3(401-420)] — user story·3개 Given/When/Then AC 원문(중립 필드 4종 도입·동일 Target 모델, legacy alias 매핑·임의 rename 금지(ADD-8), 쿠팡 기대 센터/상점명 empty/배민-기본값 → 위험 상태(FR-20 토대, 차단은 Epic 4)).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2(353-355)] — Epic 2 목표(탭 번호 대신 안정 ID, 도메인 모델·legacy alias·secret_ref 분리; 도메인 dataclass/Enum은 2.5, 테이블/Alembic은 Epic 5).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-20(54)·FR-14(45)·FR-4(29)] — 플랫폼 대상 검증(쿠팡 기대 센터/상점명 empty/기본값 → 위험, 다른 화면 전송 중단), Browser Profile/CDP 격리·센터 검증(Epic 4), 대상 모델(플랫폼·계정·기대 센터/상점명·URL/식별자 보유).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P1-05(40)] — 플랫폼 중립 필드 `center_name`/`display_name`/`target_external_id`/`primary_url` 사용, 배민·쿠팡이 같은 Target 모델 사용.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(10·28)] — MonitoringTarget 계약(platform account + center/store/url/interval/status), `monitoring_targets`(id, tenant_id, platform_account_id, name, external_id, url, interval_minutes, status) — 중립 이름 정렬용.
- [Source: _bmad-output/planning-artifacts/architecture.md(265-270)] — Python 네이밍, **공개 경계 이름 호환 우선**(`coupang_eats_url`·`baemin_center_name` 넓은 변경 없이 legacy alias 매핑).
- [Source: _bmad-output/planning-artifacts/architecture.md(361-366)] — Anti-Patterns(camelCase 변환·필수값 0 채움·state_subdir crawlingN 재식별 금지).
- [Source: src/rider_crawl/config.py(36-77·108-134·237·261-277)] — `AppConfig`(frozen, `coupang_eats_url`/`baemin_center_name`/`baemin_center_id`/`crawl_name`), `runtime_dir`/`state_dir` property 패턴, `DEFAULT_BAEMIN_CENTER_NAME`, `_require_coupang_center`(empty/배민-기본값 raise).
- [Source: src/rider_crawl/ui_settings.py(22-61·102-135·263-284)] — `UiSettings`(`performance_url`/`baemin_center_name`/`baemin_center_id`/`legacy_alias`), `to_app_config`, `_to_jsonable`(asdict)/`_settings_from_mapping`(직렬화 불변 — property 무시).
- [Source: src/rider_crawl/platforms/coupang/crawler.py(50-99)] — `_validate_coupang_center`/`_coupang_center_aliases(config.baemin_center_name)`(center_name으로 쿠팡 검증 유지 근거).
- [Source: src/rider_crawl/ui.py(1127-1148·1154-1193)] — 배민 센터 identity 검증, 쿠팡 URL/기대 센터 저장 단계 차단(`_validate_coupang_expected_center` empty/배민-기본값 raise — 비차단 분류기의 조건 출처, 약화 금지).
- [Source: src/rider_crawl/app.py(98-115)] — `_message_scope_key`(coupang_eats_url/baemin_center_name/baemin_center_id 직접 참조, dedup 정본 — 변경 금지).
- [Source: _bmad-output/implementation-artifacts/2-1-uisettings에-고객-대상-id-부여와-legacy-alias-보존.md(19-26)] — 2.3 경계 명시(플랫폼 중립 필드는 2.3 소유), legacy_alias 도입.
- [Source: _bmad-output/implementation-artifacts/2-2-대상별-상태-경로-분리와-atomic-write-로그-rotation.md(19-26)] — 2.3/2.4/2.5 경계, 직렬화 형식 보존 교훈.
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-06-13.md] — 액션 A1(secret 게이트)·A2(테스트 수치 단일 정본).
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P1-05(플랫폼 중립 필드), FR-20(플랫폼 대상 검증 토대), ADD-8(공개 경계 이름 호환/네이밍 정본), NFR-19/NFR-20(JSON 호환·각 단계 회귀). 차단/상태 전이=Epic 4 FR-14/FR-20, 도메인 dataclass/Enum=Story 2.5, 테이블/Alembic=Epic 5 P4-02.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 기준선 재측정: `.venv/Scripts/python.exe -m pytest --collect-only -q` → **618 collected** (스토리 참고값과 일치).
- RED 확인: 신규 테스트는 구현 전 `ImportError: cannot import name 'coupang_center_name_risk'`로 실패(접근자 미존재 포함).
- 범위 점검: `git diff -w` 상 `crawler.py`/`coupang/crawler.py`/`app.py`/`browser_launcher.py`/`telegram_commands.py`/`redaction.py`/`ui.py` 변경 **0줄**(legacy 이름 rename 0건, dedup scope key 무변경).
- 누출 grep: 봇 토큰/`chat_id=<digits>`/한국 휴대폰 평문 — 신규/수정 diff에서 **0건**.
- **리뷰 시점 재측정(A2 정본, 2026-06-13):** `.venv/Scripts/python.exe -m pytest -q` → **642 passed**(기준선 618 + 신규 24, 회귀 0). `--collect-only -q` → 642 collected. dev-story 시점 수치(632/신규 14)는 QA 갭 테스트 10건 추가 후 stale가 되어 리뷰에서 642/신규 24로 정정함(2.1 M1·2.2 M1과 동일 패턴).

### Completion Notes List

- **Task 1 — UiSettings 중립 접근자 4종**: `primary_url`→`performance_url`, `center_name`→`baemin_center_name`, `target_external_id`→`baemin_center_id`, `display_name`→`legacy_alias`. 순수 읽기(`@property`, strip 없음). `_to_jsonable`/`_settings_from_mapping`은 무변경 — `asdict`가 property를 직렬화하지 않아 JSON 출력 불변.
- **Task 2 — AppConfig 동일 중립 접근자 4종**: `primary_url`→`coupang_eats_url`, `center_name`→`baemin_center_name`, `target_external_id`→`baemin_center_id`, `display_name`→`crawl_name`. 기존 `runtime_dir`/`state_dir` property 패턴 옆에 배치(`frozen=True`와 무관).
- **Task 3 — 비차단 위험 분류기**: `coupang_center_name_risk(platform_name, center_name) -> tuple[bool, str]` 추가. 비쿠팡 → `(False, "")`; 쿠팡+empty/배민-기본값 → `(True, 사유)`; 그 외 `(False, "")`. **예외 미발생(비차단)**, 새 enum 미정의(2.5 소유). 두 조건은 `_coupang_center_name_issue` predicate로 단일화해 `_require_coupang_center`(raise)와 공유 — **raise 메시지/동작·`test_config.py` 100% 보존**(empty="BAEMIN_CENTER_NAME…", default="배민 기본값…"). `UiSettings.coupang_center_name_risk()` 편의 메서드도 추가(분류만, 저장/상태 전이 없음).
- **저장 단계 차단 미약화(AC6)**: `_validate_coupang_expected_center`(ui.py)·`_require_coupang_center`(config.py)의 raise는 그대로 유지. 분류기는 별도 비차단 경로로만 추가.
- **테스트(Task 4)**: 매핑(배민·쿠팡 양쪽)·strip 미수행·직렬화 불변(중립 키 미노출 + `{"crawlings"}` 보존)·쿠팡 `center_name`↔`baemin_center_name` 동일성·위험 분류 5케이스·비차단(예외 미발생) 추가.
- **회귀(Task 5)**: 전체 스위트 **642 passed**(기준선 618 + 신규 24, 기존 통과 무변동). 운영 venv(`.venv/Scripts/python.exe`)로 실행. (dev-story 시점 632/신규 14는 QA 갭 테스트 10건 추가 후 stale가 되어 리뷰 시점 642/신규 24로 정정 — A2.)

### File List

- `src/rider_crawl/config.py` — AppConfig 중립 접근자 4개(`primary_url`/`center_name`/`target_external_id`/`display_name`), `coupang_center_name_risk` 분류기, `_coupang_center_name_issue` predicate, `_require_coupang_center` predicate 공유 리팩토링(동작 보존).
- `src/rider_crawl/ui_settings.py` — UiSettings 중립 접근자 4개 + `coupang_center_name_risk()` 편의 메서드, `coupang_center_name_risk` import 추가.
- `tests/test_config.py` — AppConfig 중립 접근자 매핑/strip 테스트, `coupang_center_name_risk` 위험 분류 6 테스트.
- `tests/test_ui_settings.py` — UiSettings 중립 접근자 매핑(배민·쿠팡)/strip/직렬화 불변/편의 메서드 테스트.
- `tests/test_coupang_crawler.py` — `config.center_name == baemin_center_name` 동일성(쿠팡 검증 유지 근거) 테스트.

## Senior Developer Review (AI)

- **리뷰어:** Noah Lee · **일자:** 2026-06-13 · **결과:** Approve (CRITICAL 0) → Status `done`
- **재측정(정본):** `.venv/Scripts/python.exe -m pytest -q` → **642 passed**(기준선 618 + 신규 24, 회귀 0).
- **AC 검증:** AC1(중립 접근자 4종·동일 Target 집합·`center_name`↔`baemin_center_name` 동일성) ✅ / AC2(legacy 이름 rename 0건 — `git diff -w`에 `config.py`·`ui_settings.py`만, 30+ 호출부 무변경) ✅ / AC3·5·6·7(비차단 `coupang_center_name_risk` — empty/배민-기본값만 위험, 예외 미발생, enum 미신설, `_validate_coupang_expected_center`/`_require_coupang_center` raise 미약화) ✅ / AC4(`@property` 미직렬화 — `asdict`/저장 JSON에 중립 키 0) ✅.
- **Task 감사:** 5개 Task 모두 실제 구현 확인(접근자·predicate 단일화·분류기·테스트·회귀). `app._message_scope_key`·`ui.py` 차단 경로 무변경 확인.
- **MEDIUM(수정 완료):** Dev Agent Record/Change Log의 pass 수치가 dev-story 시점값(632/신규 14)으로 stale → 리뷰 시점 642/신규 24로 정정(A2, 2.1 M1·2.2 M1 재발 패턴).
- **LOW(무수정·근거 기록):** `_require_coupang_center`가 공유 predicate(`_coupang_center_name_issue`)의 strip을 타게 되어 *직접* 공백-only 호출 시 raise 동작이 미세 변경되나, 유일 호출부(`_center_name_from_env`)가 선-strip하고 raise 메시지가 바이트 동일하며 `test_config.py` 전량 통과 → 계약상 무회귀(안전).

## Change Log

| Date       | Version | Description                                                                                          |
| ---------- | ------- | ---------------------------------------------------------------------------------------------------- |
| 2026-06-13 | 0.1     | Story 2.3 구현: 플랫폼 중립 read-only 접근자 4종(UiSettings·AppConfig)과 비차단 쿠팡 센터 위험 분류기 추가. legacy 이름·직렬화·저장 단계 차단 무변경. 전체 스위트 632 passed(dev-story 시점). |
| 2026-06-13 | 0.2     | 자동 코드 리뷰(adversarial): CRITICAL 0. A2 정정 — dev-story 시점 pass 수치(632/신규 14)가 QA 갭 테스트 10건 추가 후 stale → 리뷰 시점 재측정 **642 passed**(기준선 618 + 신규 24, 회귀 0)로 정본화. Status → done. |
