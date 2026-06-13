---
baseline_commit: f9937e8
---

# Story 2.4: secret 값 분리 — 설정 파일은 ref만 보관

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 보안 담당 개발자,
I want 탭별 설정에 **평문으로 저장되던 secret(텔레그램 봇 토큰·쿠팡 로그인 비밀번호·쿠팡 로그인 아이디)** 을 일반 설정에서 분리해 **설정 JSON(`runtime/state/ui_settings.json`)에는 `*_ref`(참조)만 남기고** 실제 값은 **설정 파일 밖의 로컬 secret store**에 두며, **기존 설정을 로드/마이그레이션할 때도 평문 secret이 신규 설정 파일로 복사되지 않게** 하고, 각 secret을 **저장 위치 분류(중앙 secret store / Agent-local DPAPI / 비저장) 중 하나로 분류**하고 싶다,
so that 설정 파일·diff·export에 평문 token/password가 남지 않아 유출 위험을 줄이고(NFR-8, ADD-15), 이후 Epic 4(DPAPI)·Epic 5(AWS Secrets Manager)가 같은 seam에 백엔드만 끼우면 되도록 한다.

> **이 스토리의 성격 — "설정 직렬화에서 secret을 ref로 갈라내는 얇은 분리 레이어 + 주입 가능한 로컬 store seam + 분류 정책." 도메인 모델 신설도, DPAPI/클라우드 secret 백엔드 구현도 아니다.** P1-06의 deliverable은 **"설정 JSON에 평문 token/password가 없고 `*_ref`만 있다 + 마이그레이션이 평문을 신규 파일에 복사하지 않는다 + secret이 3분류 중 하나로 분류된다"** 이다. 실제 운영 secret 저장소(DPAPI/Credential Manager=Epic 4, AWS Secrets Manager=Epic 5)는 **본 스토리가 구현하지 않는다** — 로컬 파일 MVP 백엔드를 **주입 가능한 seam** 뒤에 둬서 나중에 교체만 하게 한다. [Source: implementation-contract.md P1-06(41), operations-security-test-contract.md Secret Storage(3-11), architecture.md(146-147·179-184·492-494)]
>
> **엄격한 범위 경계(스코프 크립 방지).** 본 스토리는 **오직** (1) `UiSettings`에 `*_ref` 필드 3종을 추가하고 평문 secret 필드를 **비영속(in-memory transient)** 으로 강등, (2) 직렬화 choke point(`_to_jsonable`/`save`/`save_all`)에서 평문 secret을 store로 빼고 ref만 쓰기, (3) 로드 choke point(`_settings_from_mapping`/`load`/`load_all`)에서 legacy 평문을 store로 이관(=ref 발급) 후 in-memory로 resolve, (4) 주입 가능한 로컬 `SecretStore` seam(`put`/`resolve`)과 파일 백엔드 1개, (5) secret 저장 위치 **분류 정책(3분류) 매핑**을 추가하는 것만 한다. 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 절대 손대지 않는다:**
> - **도메인 `SecretRef` dataclass·PlatformAccount(`username_ref`/`password_ref`) 등 정식 도메인 모델** → **Story 2.5**(ADD-7, data-api-contract). 본 스토리는 정식 `SecretRef` 클래스를 만들지 않고 **평문 `*_ref` 문자열 + store seam**만 쓴다(2.3이 enum 대신 `(bool, str)`을 쓴 것과 동일한 경계 규율). [Source: epics.md Story 2.5(440-461), data-api-contract.md(19·27)]
> - **오케스트레이션된 마이그레이션 러너**(백업→활성/비활성 분류→ID 발급→상태 복사→dedup seed 승계) → **Story 2.7**(ADD-16, FR-31). 본 스토리는 직렬화/로드 choke point를 secret-safe로 만들어 **2.7 러너가 그 안전성을 자동 상속**하게 할 뿐, 러너 자체는 만들지 않는다. 2.4의 "마이그레이션"은 2.1의 ID 발급처럼 **기존 load 경로에서 일어나는 per-load 평문→ref 이관**이다. [Source: epics.md Story 2.7(487-503), 2-1 스토리 _issue_missing_ids(266-288)]
> - **실제 DPAPI/Windows Credential Manager 백엔드**(`rider_agent/secure_store.py`) → **Epic 4**. **AWS Secrets Manager·DB `*_ref` 컬럼·Alembic** → **Epic 5**(P4-02). 본 스토리의 store는 그 자리에 끼울 **로컬 파일 MVP 백엔드**다(암호화·OS 자격저장소 아님 — MVP 한계 명시). [Source: architecture.md(146-147·446-447·492-494), operations-security-test-contract.md(7-10)]
> - **`telegram_chat_id`/`telegram_message_thread_id` ref화 금지.** 이들은 **자격증명(secret)이 아니라 라우팅 식별자**다. NFR-8/AC는 token/password 대상이고, 로그 마스킹은 이미 `redaction`이 처리한다. 설정 JSON에 그대로 둔다(평문 token/password만 분리 대상). [Source: redaction.py(168-169), epics.md AC(430-438)]
> - **`AppConfig`/`.env`(`AppConfig.from_env`) 경로 변경 금지.** `AppConfig`는 **디스크에 저장되는 설정 파일이 아니라 런타임 resolved 스냅샷**이다(in-memory 평문 허용 — NFR은 영속/로그 평문만 금지). `.env`는 단일 운영자 CLI/env 메커니즘으로 이미 로컬 secret 파일로 분류·gitignore돼 있다(project-context §81). P1-06가 겨냥하는 "설정 파일"은 고객/탭별로 백업되는 `ui_settings.json`이다 — 거기만 분리한다. [Source: config.py from_env(78-106), project-context.md(81)]
> - **dedup scope key·소비자 재배선 금지.** `app._message_scope_key`(app.py 98-118)는 **resolved** `config.telegram_bot_token.strip()`을 dedup 정본으로 쓴다. resolve 결과 값이 **바이트 동일**하면 scope key가 안 바뀐다 — store 도입 후에도 `to_app_config()`가 **이전과 같은 평문 값**을 채워야 한다(회귀 0). sender/telegram_commands/auth/coupang_email_2fa/ui StringVar는 **모두 무변경**(in-memory 평문 필드를 그대로 읽는다). [Source: app.py(98-118), project-context.md(92), sender.py(288-289)]
>
> **기준선 회귀 0.** 현재 HEAD(`f9937e8`, Story 2.3 done)에서 2.3 리뷰 정본 수치는 **642 passed**(참고값 — 복사 금지, 본인이 `.venv/Scripts/python.exe -m pytest -q`로 재측정). 신규/수정 테스트만큼만 변동이 정상이고, 기존 통과 테스트가 새로 깨지면 실패다(NFR-20). **A2 교훈: dev 노트에 잠정 pass 수치를 박아 stale를 만들지 말 것 — 리뷰 시점 재측정값 1개만 정본으로 기록한다.** [Source: 2-3 스토리(167·189), epic-1-retro 액션 A2, memory/dev-env-quirks]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 신규/수정 테스트·fixture에 실제 봇 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 명백한 가짜값(`"token"`, `"pw"`, `"-100123"`, `"77"`)만 쓰고, 평문 비노출을 검증하는 테스트는 가짜 secret이 **파일 텍스트에 남지 않음**을 단언한다. [Source: project-context.md(81·89), epic-1-retro 액션 A1]

## Acceptance Criteria

**AC1 — 설정 JSON에는 `*_ref`만, 평문 token/password 0; 마이그레이션 시에도 평문 미복사 (P1-06, NFR-8, ADD-15)**

1. **Given** 기존 `runtime/state/ui_settings.json`에 `telegram_bot_token`·`coupang_login_password`·`coupang_login_id`가 **평문**으로 저장돼 있을 때 **When** `UiSettings`에 대응 `*_ref` 필드(`telegram_bot_token_ref`·`coupang_login_password_ref`·`coupang_login_id_ref`)를 추가하고, 저장(`save`/`save_all`) 시 평문 값을 로컬 secret store로 빼낸 뒤 직렬화하면(P1-06) **Then** 새로 쓰인 설정 JSON **텍스트**에는 원본 token/password/login-id의 값 문자열이 **0건** 존재하고 `*_ref` 형태의 참조만 존재한다. [Source: implementation-contract.md P1-06(41), ui_settings.py `_to_jsonable`(291-295)·`save`(221-230)]
2. **And** 기존(legacy) 평문이 든 `ui_settings.json`을 **로드/마이그레이션**할 때(load/load_all)도 평문 secret이 **신규 파일로 복사되지 않는다**: load가 legacy 평문을 store로 이관하고 ref를 발급한 뒤 한 번 영속화하면(2.1 `_issue_missing_ids` persist-on-first-issue와 동일 패턴), 재기록된 파일에는 ref만 남는다(평문 token/password 문자열 0건 — ADD-15 금지행위). [Source: epics.md AC1(430-433), ui_settings.py `load_all`(188-219)·`_issue_missing_ids`(266-288)]
3. **And** 런타임 동작은 **무회귀**다: `to_app_config()`가 ref를 store로 resolve해 **이전과 바이트 동일한 평문 값**을 `AppConfig.telegram_bot_token`/`coupang_login_password`/`coupang_login_id`에 채우므로, sender 전송·쿠팡 2FA·`app._message_scope_key` dedup 정본이 모두 그대로 동작한다(scope key 무변경). [Source: ui_settings.py `to_app_config`(130-163), app.py(98-118), sender.py(288-289), auth/coupang_email_2fa.py(203-204)]

**AC2 — secret 저장 위치 3분류 적용 + DB/로그/스크린샷/config 평문 금지 규칙 (NFR-8, ADD-15)**

4. **Given** secret 저장 위치 분류가 필요할 때 **When** secret 저장 정책을 적용하면 **Then** 각 secret 종류가 **중앙 secret store / Agent-local DPAPI·Credential Manager / 비저장(not stored)** 중 정확히 하나로 분류되고, 그 분류가 코드에서 조회 가능한 매핑(상수/dict)으로 존재한다: 텔레그램 봇 토큰=**중앙**(eventual AWS Secrets Manager), 쿠팡 password·login-id=**Agent-local**(eventual DPAPI), Gmail OAuth token=**Agent-local**(이미 `secrets/google/` 파일-ref), OTP/2FA 코드=**비저장**. [Source: operations-security-test-contract.md Secret Storage(5-11), architecture.md(146-147·181-184·254-257)]
5. **And** "DB/로그/스크린샷/config에 평문 secret을 두지 않는다"는 규칙이 적용·검증된다: (a) 설정 JSON에 평문 없음(AC1), (b) `redaction`이 `*_ref`는 보존하고 secret 어간 키는 마스킹함을 회귀로 확인(이미 구현됨 — 본 스토리는 약화하지 않음), (c) Gmail OAuth token은 설정 JSON에 **경로(ref)만** 있고 토큰 값이 없음을 확인(이미 그러함 — 분류만 명문화). [Source: redaction.py `_is_secret_key`(194-198), config.py gmail paths(66-67·21-22), operations-security-test-contract.md(15·92-93)]

**AC3 — 주입 가능한 로컬 SecretStore seam + MVP 한계 명시 (Epic 4/5 plug 지점)**

6. **Given** 실제 DPAPI/클라우드 secret 백엔드는 Epic 4/5 소유일 때 **When** 본 스토리가 store를 추가하면 **Then** store는 **주입 가능한 seam**(예: `put(value) -> ref`, `resolve(ref) -> str | None` 인터페이스)으로 정의되고, 기본 구현은 **설정 파일과 분리된 로컬 파일 백엔드**(예: `ui_settings.json`과 다른 파일)이며, 테스트는 `tmp_path` 기반 store를 주입해 실 파일을 만지지 않는다. 미래의 DPAPI/Secrets Manager는 같은 seam에 백엔드만 교체해 끼운다. [Source: architecture.md(181-184·446-447·492-494)]
7. **And** MVP 한계를 명시한다: 로컬 store는 **평문 JSON**이며 **암호화/OS 자격증명 저장소(DPAPI)·encryption-at-rest(BitLocker)는 Epic 4(NFR-9) 소유**다. 본 스토리의 보장은 "**설정/config 파일에 secret 평문이 없다**"이지 "store 파일 자체가 암호화된다"가 아니다(과대 주장 금지). store 파일은 gitignore 대상(`runtime/` 하위)이고, ref가 가리키는 값이 store에 없으면 resolve는 빈 값을 돌려 **fail-closed**(평문 노출 없음, 전송은 secret 재입력 전까지 비활성)로 안전하게 처리한다. [Source: architecture.md NFR-9(개념), .gitignore(6·11-17), operations-security-test-contract.md(81)]

## Tasks / Subtasks

- [x] **Task 1 — 로컬 `SecretStore` seam + 파일 백엔드 추가 (AC: 6, 7)**
  - [x] 새 모듈 `src/rider_crawl/secret_store.py`(또는 `secrets_store.py`)에 **주입 가능한 store seam**을 정의한다: 최소 `put(value: str) -> str`(평문을 저장하고 ref 반환), `resolve(ref: str) -> str | None`(ref→평문, 없으면 `None`). 기본 구현 `LocalFileSecretStore(path: Path)`는 **`ui_settings.json`과 분리된 별도 파일**(권장 기본: 같은 디렉터리의 `secrets.local.json`)에 `{ref: value}` 매핑을 `ensure_ascii=False, indent=2`로 저장한다. 쓰기는 2.2의 `_atomic_write_text` 패턴을 재사용한다(wheel 재발명 금지 — 기존 함수 import/공유). [Source: ui_settings.py `_atomic_write_text`(233-263), architecture.md(492-494)]
  - [x] **ref 형식**: 안정적이고 추적 가능한 불투명 핸들을 쓴다. 권장 = `monitoring_target_id`(또는 탭 식별자) + 필드명 기반 **결정적 핸들**(예: `f"local:{monitoring_target_id}:telegram_bot_token"`) — 결정적이라 재로드/재정렬에도 같은 ref가 유지돼 dedup/diff가 안정적이고 테스트가 결정적이다. ref 문자열은 `*_ref` 필드 값으로 들어가며 `redaction`이 보존한다(secret 아님). uuid4 핸들도 허용하나 **persist-on-first-issue로 안정성**을 보장해야 한다(2.1 패턴). [Source: ui_settings.py `_issue_missing_ids`(266-288), redaction.py(196-197)]
  - [x] store는 **OTP/2FA 코드를 저장하지 않는다**(비저장 분류). store API는 token/password/login-id 영속 secret만 다룬다. [Source: operations-security-test-contract.md(15·92-93)]
- [x] **Task 2 — secret 저장 위치 분류 매핑 추가 (AC: 4, 5a)**
  - [x] **신규 `secret_store.py`에** secret 종류 → 분류 **상수/dict**를 둔다(`config.py`는 건드리지 않는다 — AppConfig/from_env 무변경 유지). enum은 2.5 소유라 **만들지 않는다** — 단순 문자열 분류값 사용(2.3이 enum 대신 bool/str 쓴 규율과 동일). 분류값 3종: `"central"`(중앙 secret store), `"agent_local"`(Agent-local DPAPI/Credential Manager), `"not_stored"`(비저장). 매핑: `telegram_bot_token→central`, `coupang_login_password→agent_local`, `coupang_login_id→agent_local`, `gmail_oauth_token→agent_local`, `otp→not_stored`. [Source: operations-security-test-contract.md(5-11), architecture.md(146-147·254-257), data-api-contract.md(27)]
  - [x] 매핑에 짧은 주석으로 "MVP 백엔드는 로컬 파일 1개이고, `central`은 Epic 5(AWS Secrets Manager), `agent_local`은 Epic 4(DPAPI)에서 실제 백엔드로 교체된다"는 정책을 남긴다(운영 정책 주석만 — project-context §38 "코드만으로 알기 어려운 곳에 짧게"). [Source: project-context.md(38), architecture.md(181-184)]
- [x] **Task 3 — `UiSettings`에 `*_ref` 필드 추가 + 평문 secret 필드 비영속화 (AC: 1, 3)**
  - [x] `src/rider_crawl/ui_settings.py`의 `UiSettings` dataclass에 `*_ref` 필드 3종을 추가한다(기본값 `""`): `telegram_bot_token_ref`, `coupang_login_password_ref`, `coupang_login_id_ref`. dataclass 필드 순서/기본값 규칙(기본값 없는 필드 뒤에 기본값 필드)을 지킨다 — 기존 `customer_id` 등과 같은 꼬리 위치에 둔다. [Source: ui_settings.py(48-62)]
  - [x] 기존 평문 필드(`telegram_bot_token`·`coupang_login_password`·`coupang_login_id`)는 **dataclass 필드로 유지하되 "비영속(transient)"으로 강등**한다 — in-memory에서 resolved 평문을 들고 있어 `to_app_config()`·UI StringVar가 그대로 읽지만(무회귀), 직렬화(`_to_jsonable`)에서는 **제외**한다(Task 4). `to_app_config()`(130-163)는 **무변경**(여전히 in-memory 평문을 `AppConfig`에 넘김 → scope key·sender 무회귀). [Source: ui_settings.py(35·49-50·130-163), ui.py StringVar(240·247-248)]
- [x] **Task 4 — 직렬화 choke point에서 secret 분리(평문→store, ref만 기록) (AC: 1)**
  - [x] `_to_jsonable`(291-295) 또는 `save`/`save_all`(221-230)에서, 직렬화 직전에 평문 secret 필드가 비어있지 않으면 **store.put로 빼서 ref를 발급**하고 해당 `*_ref` 필드를 채운다. 그런 다음 **직렬화 dict에서 평문 secret 키 3종을 제거**(또는 빈 문자열로 치환)하고 `*_ref`만 남긴다. **다른 직렬화 형식은 전부 보존**: `ensure_ascii=False, indent=2`, `{"crawlings":[...]}`, `browser_user_data_dir`/`log_dir`의 `str()` 변환, 9탭 구조. [Source: ui_settings.py(221-230·291-295)]
  - [x] store 쓰기와 설정 쓰기의 **순서**를 안전하게 둔다: 먼저 store에 값을 영속화(ref 확정)한 뒤 설정 JSON을 atomic write한다. 그래야 크래시 시에도 "설정엔 ref, 값은 store"가 깨지지 않는다. store 자체도 atomic write. [Source: ui_settings.py `_atomic_write_text`(233-263)]
  - [x] **불변식**: 직렬화 결과 텍스트에 평문 secret 값 문자열이 **0건**이어야 한다(테스트로 잠금 — Task 7). [Source: epics.md AC1(430-433)]
- [x] **Task 5 — 로드/마이그레이션 choke point에서 legacy 평문 이관 + resolve (AC: 1, 2, 3)**
  - [x] `_settings_from_mapping`(298-312)에서 입력 raw에 **legacy 평문 secret 키**(`telegram_bot_token`/`coupang_login_password`/`coupang_login_id`)가 값과 함께 있으면: 값을 store.put로 이관해 ref를 발급하고, in-memory `UiSettings`의 평문 필드(resolved)와 `*_ref` 필드를 모두 채운다. raw에 **이미 `*_ref`** 가 있으면 store.resolve(ref)로 in-memory 평문을 복원한다(평문 키 없음이 정상 — 신규 파일). 둘 다 없으면 빈 값(fail-closed). **precedence(반쪽 마이그레이션 대비):** 한 raw에 평문 secret과 `*_ref`가 **둘 다** non-empty면 **평문을 정본으로** 재이관(ref 덮어쓰기)한다 — 평문은 운영자 최신 입력일 가능성이 높고, 무엇보다 "신규 파일에 평문 잔존 0"을 보장해야 하므로 평문을 store로 흡수해 없앤다. [Source: ui_settings.py(298-312)]
  - [x] 새 ref가 발급됐으면(=legacy 평문 이관 발생) `load`/`load_all`이 **한 번 영속화**해 재로드 시 ref만 남게 한다(2.1 `_issue_missing_ids`의 persist-on-first-issue와 동일 — 파일이 없을 때는 write하지 않는 기존 가드 유지). [Source: ui_settings.py `load`(170-186)·`load_all`(188-219)]
  - [x] `_is_legacy_kakao_mapping`(326-340)이 `telegram_bot_token` 키 존재로 판정하는 로직을 **깨지 않는다**: legacy kakao 추론은 raw 입력 기준이므로, 이관 후에도 추론 결과가 동일하도록 raw 판정 시점·기준을 보존한다(이관은 추론 이후/독립이어야 함). [Source: ui_settings.py(326-340)]
  - [x] store를 `UiSettingsStore`에 **주입**한다(생성자 기본값 = 설정 파일 옆 `secrets.local.json` 백엔드). 테스트는 `tmp_path` store를 주입한다. UI 진입점(`ui.py` 185 `UiSettingsStore(...)`)은 기본 store를 쓰므로 **시그니처 호환**(기본 인자)으로 두어 ui.py 무변경을 유지한다. [Source: ui_settings.py(166-168·221-230), ui.py(185·522)]
- [x] **Task 6 — 분류·비노출 규칙 회귀 확인(코드 무약화) (AC: 2, 5)**
  - [x] `redaction`의 `*_ref` 보존·secret 어간 마스킹을 **약화하지 않는다**(본 스토리는 redaction 코드를 바꾸지 않음 — 기존 `test_redaction.py` 통과 확인). 새 `*_ref` 키가 redaction을 통과해 **보존**됨을 한 케이스로 확인. [Source: redaction.py(194-198), tests/test_redaction.py]
  - [x] Gmail OAuth token이 설정 JSON에 **경로(ref)만** 있고 토큰 값이 없음을 확인(이미 그러함 — 분류 명문화용 1 테스트). `secrets/google/` 경로 정책 무변경. [Source: config.py(21-22·66-67), auth/gmail.py(276-284)]
- [x] **Task 7 — 테스트 추가/보강 (AC: 1~7)** — 기존 패턴(`tmp_path`, 순수 객체, 외부 미호출, 가짜값) 사용:
  - [x] **(AC1 평문 0 — `tests/test_ui_settings.py`):** 평문 secret을 채운 `UiSettings`를 `tmp_path` store 주입 store로 `save`/`save_all` 후, **기록된 파일 텍스트**에 가짜 token/password/login-id 값 문자열이 **없음**(`assert "tok-fake" not in text`)과 `*_ref` 키가 **있음**을 단언. `ensure_ascii=False`·`{"crawlings":[...]}` 보존도 확인. [Source: ui_settings.py(221-230·291-295)]
  - [x] **(AC1 마이그레이션 미복사 — `tests/test_ui_settings.py`):** legacy 평문이 든 raw dict/파일을 `load`/`load_all` → 재기록된 파일 텍스트에 평문 0건·ref만 존재, 그리고 store.resolve(ref)가 **원본 평문과 동일** 단언. 파일 없을 때 write 안 함(기존 가드)도 확인. [Source: ui_settings.py(170-219·266-288)]
  - [x] **(AC3 무회귀 resolve — `tests/test_ui_settings.py`):** ref만 있는 신규 파일을 load → `to_app_config()` 결과 `AppConfig.telegram_bot_token`/`coupang_login_password`/`coupang_login_id`가 store 평문과 **바이트 동일**. store에 값이 없으면 빈 값(fail-closed) 단언. [Source: ui_settings.py(130-163), config.py(52·75-76)]
  - [x] **(AC3 scope key 무변경 — `tests/test_app.py` 또는 기존 위치):** 동일 평문 입력에 대해 `app._message_scope_key` 결과가 store 도입 전과 동일함을 확인(평문→ref→resolve 왕복이 scope 정본을 흔들지 않음). [Source: app.py(98-118)]
  - [x] **(AC4 분류 — `tests/test_secret_store.py` 신규):** 분류 매핑이 5종을 정확한 3분류값으로 반환(`telegram_bot_token→central`, 쿠팡 2종→`agent_local`, gmail→`agent_local`, otp→`not_stored`). [Source: operations-security-test-contract.md(5-11)]
  - [x] **(AC6/7 store seam — `tests/test_secret_store.py` 신규):** `LocalFileSecretStore`의 `put`→`resolve` 왕복, 결정적 ref 안정성(같은 입력 같은 ref), 없는 ref→`None`(fail-closed), atomic write로 손상 없음, store 파일이 설정 파일과 **다른 경로**임을 단언. [Source: ui_settings.py(233-263)]
  - [x] **(AC2/5 회귀 — `tests/test_redaction.py`):** 새 `*_ref` 키가 보존되고 평문 secret 어간 키는 여전히 마스킹됨(기존 테스트 무수정 통과 + `*_ref` 보존 1 케이스). [Source: redaction.py(194-198)]
  - [x] secret 비노출: 모든 신규 테스트 값은 명백한 가짜값(`"tok-fake"`·`"pw-fake"`·`"id-fake"`·`"-100123"`). 실제 봇 토큰/전화/이메일 형태 금지. [Source: project-context.md(81), epic-1-retro 액션 A1]
- [x] **Task 8 — 회귀·범위·누출 검증 및 마무리 (AC: 1~7)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`. WSL 시스템 `python3` 사용 금지(pytest 미설치). 기준선 **642**(참고값 — 본인이 재측정) 대비 기존 통과가 새로 깨지지 않고 신규 케이스만큼만 증가가 정상. [Source: 2-3 스토리(167·189), memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 `ui_settings.py` + 신규 `secret_store.py` + 관련 테스트만 보이고, **`config.py`/`ui.py`/`sender.py`/`app.py`/`telegram_commands.py`/`auth/coupang_email_2fa.py`/`redaction.py`의 secret 참조는 무변경**임을 확인(소비자 재배선 0, dedup scope key 무변경). CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. 모든 테스트는 `tmp_path`/주입 store로(실 `runtime/`·`ui_settings.json`·`secrets.local.json` 미변형). [Source: project-context.md(82·92), memory/dev-env-quirks]
  - [x] 누출 grep: 신규/수정 코드·테스트에 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·한국 휴대폰 평문이 없는지 확인. store/설정 직렬화 출력에도 평문 secret이 남지 않는지 한 번 더 확인. [Source: epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2). [Source: epic-1-retro 액션 A2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **추가/강등만** 한다: (1) 로컬 `SecretStore` seam + 파일 백엔드 1개, (2) secret 3분류 매핑, (3) `UiSettings`에 `*_ref` 3종 추가 + 평문 secret 3종을 비영속(transient) in-memory로 강등, (4) 직렬화/로드 choke point에서 평문↔ref 변환. 변경 표면: `ui_settings.py`(필드+직렬화/로드 분기+store 주입), 신규 `secret_store.py`(seam+백엔드+분류), + 테스트. (`config.py`는 분류 매핑을 거기 둘 경우에만, 그 외 무변경.)
- **건드리지 않는다:** 정식 도메인 `SecretRef`/`PlatformAccount`(2.5), 오케스트레이션 마이그레이션 러너(2.7), 실제 DPAPI/Credential Manager 백엔드(Epic 4), AWS Secrets Manager·DB `*_ref` 컬럼·Alembic(Epic 5), `AppConfig`/`.env` env 경로, `telegram_chat_id`/`thread_id`(라우팅 식별자 — secret 아님), `redaction` 코드(약화 금지), `app._message_scope_key`(dedup 정본 — resolved 값 바이트 동일 유지), 30+ 소비자 재배선, 2.1 ID 발급·2.2 atomic write/state_subdir/로그 rotation·2.3 중립 접근자(전부 보존). [Source: implementation-contract.md(41), project-context.md(92), epics.md Story 2.5·2.7(440-503)]

### 핵심 설계 — 왜 "비영속 평문 필드 + `*_ref` 직렬화 + 주입 store seam"인가 (AC1·AC3, 반드시 읽을 것)

- **현 상태(평문이 어디에 있나):** `UiSettings`는 `telegram_bot_token`(35), `coupang_login_id`(49), `coupang_login_password`(50)를 **평문 dataclass 필드**로 갖고, `_to_jsonable`(291-295)의 `asdict`가 이들을 그대로 `ui_settings.json`에 쓴다. 흐름: UI form → `_settings_from_values`(ui.py 140-173) → `UiSettings` → `save_all`(JSON 평문) / `to_app_config`(130-163) → `AppConfig`(52·75-76) → sender·app dedup·coupang 2FA. **`ui_settings.json`이 P1-06가 겨냥하는 "설정 파일"** 이다(고객/탭별로 백업·diff됨). [Source: ui_settings.py(35·49-50·291-295), ui.py(140-173)]
- **이미 분리된 것(따라할 선례):** Gmail OAuth **token**은 `ui_settings.json`에 값이 없다 — config는 **파일 경로**(`gmail_token_path` → `secrets/google/token.gmail.json`, config.py 22·66-67)만 갖고 값은 gitignore된 별도 파일에 있다. 이는 **"경로가 ref, 값은 설정 밖 파일"** 패턴으로 P1-06의 모범이다. 본 스토리는 token/password에 같은 분리를 일반화한다(경로 대신 store 핸들). [Source: config.py(21-22·66-67), .gitignore(11-13)]
- **왜 평문 필드를 "비영속"으로 강등하나(삭제가 아니라):** 소비자(sender·app dedup·coupang 2FA·UI StringVar)는 모두 **평문 값**을 원한다. 평문 필드를 삭제하면 30+ 호출부가 깨진다. 대신 평문 필드는 **in-memory에만** 살리고(로드 시 store.resolve로 채움) **직렬화에서만 뺀다**. in-memory 평문은 NFR-8이 금지하지 않는다(NFR은 **영속/로그/스크린샷/config 평문**만 금지). 이로써 `to_app_config()`·소비자·dedup scope key가 전부 무변경(회귀 0)이고 디스크에는 ref만 남는다. [Source: app.py(98-118), operations-security-test-contract.md(15·92-93)]
- **왜 직렬화/로드 choke point에 후킹하나:** `_to_jsonable`/`save`/`save_all`은 **유일한 쓰기 경로**, `_settings_from_mapping`/`load`/`load_all`은 **유일한 읽기 경로**다. 여기서 변환하면 **모든 persist가 자동으로 secret-safe**가 된다 — 2.7의 마이그레이션 러너가 나중에 같은 `save_all`을 부르면 **공짜로** 평문 미복사 보장을 상속한다(별도 secret 처리 불필요). 2.1/2.2/2.3는 이 choke point의 **형식**(ensure_ascii·atomic·9탭)을 잠갔는데, 본 스토리는 그 형식을 **전부 보존**하면서 secret 키만 ref로 바꾸는 권한을 가진 유일한 스토리다(P1-06 소유). [Source: ui_settings.py(221-230·291-312), epics.md Story 2.7(487-503)]
- **왜 정식 `SecretRef` dataclass가 아니라 평문 `*_ref` 문자열인가:** 정식 도메인 `SecretRef`(필드·관계, data-api-contract 19)는 **Story 2.5**(ADD-7) 소유다. 지금 만들면 2.5와 표면이 겹쳐 재작업/충돌이 난다. 2.3이 enum(`CENTER_MISMATCH`) 대신 `(bool, str)`을 쓴 것과 **동일한 규율**로, 본 스토리는 단순 `*_ref` 문자열 + store seam만 쓰고 2.5가 정식 모델로 승격하게 한다. [Source: epics.md Story 2.5(440-461·450), 2-3 스토리(102)]

### secret 저장 위치 분류 (AC2 — NFR-8, 3분류)

| secret 종류 | 오늘 위치 | 2.4 동작 | 분류(NFR-8) | 최종 백엔드 |
|---|---|---|---|---|
| 텔레그램 봇 토큰 | `ui_settings.json` 평문 | → `telegram_bot_token_ref`, 값→로컬 store | **central**(중앙 secret store) | AWS Secrets Manager (Epic 5) |
| 쿠팡 로그인 비밀번호 | `ui_settings.json` 평문 | → `coupang_login_password_ref`, 값→로컬 store | **agent_local**(DPAPI/Cred Mgr) | DPAPI (Epic 4) |
| 쿠팡 로그인 아이디(username) | `ui_settings.json` 평문 | → `coupang_login_id_ref`, 값→로컬 store | **agent_local** | DPAPI (Epic 4) |
| Gmail OAuth token | 설정 밖 파일(`secrets/google/token.gmail.json`), 설정엔 경로만 | 동작 없음(이미 ref-by-path) — 분류만 명문화 | **agent_local** | DPAPI (Epic 4) |
| OTP / 2FA 코드 | 저장 안 함(Gmail에서 읽어 입력 후 폐기) | 동작 없음 — store가 다루지 않음 확인 | **not_stored**(비저장) | — |
| telegram_chat_id / thread_id | `ui_settings.json` 평문(라우팅 식별자) | 동작 없음(secret 아님 — token/password 대상 밖) | 운영 식별자(redaction 관리) | — |

- **왜 username(login-id)도 ref화하나:** data-api-contract `platform_accounts`는 `username_ref`+`password_ref` 쌍을 계약한다(27). username은 비밀번호보다 낮은 민감도지만, 쿠팡 계정 식별자가 평문으로 남으면 계정 enumeration 단서가 된다. 계약과 정렬해 쌍으로 분리한다. (판단 호출 — 계약이 `username_ref`를 명시하므로 분리 채택.) [Source: data-api-contract.md(27)]
- **왜 central/agent_local이 MVP에선 같은 로컬 store로 가나:** AWS Secrets Manager(중앙)·DPAPI(agent-local) **백엔드가 아직 없다**(Epic 5/4). 그래서 **분류(정책/메타데이터)는 secret별로 기록**하되 **백엔드는 단일 로컬 파일 seam**으로 둔다. 분류는 코드 매핑으로 "적용"돼 있고(AC2 "분류된다"), 백엔드 교체 시 분류값이 어느 store로 라우팅할지 결정한다. enum 아님(2.5) — 단순 문자열 3종. [Source: architecture.md(146-147·181-184), operations-security-test-contract.md(7-9)]

### 보존해야 할 공개 동작 (깨면 regression)

- (a) **런타임 평문 무회귀** — `to_app_config()`의 `AppConfig.telegram_bot_token`/`coupang_login_password`/`coupang_login_id`가 store resolve로 **이전과 바이트 동일**(sender 전송·쿠팡 2FA·UI StringVar 무변경). (b) **dedup scope key 불변** — `app._message_scope_key`(98-118)는 resolved token을 쓰므로 값이 같으면 scope 정본 무변경(project-context §92). (c) **직렬화 형식 불변** — `ensure_ascii=False, indent=2`·`{"crawlings":[...]}`·9탭·atomic write·`browser_user_data_dir`/`log_dir` str화는 그대로, secret 키만 ref로. (d) **legacy kakao/coupang 추론 무변경** — `_is_legacy_kakao_mapping`(326-340)·`_infer_platform_name`(315-323)이 raw 기준으로 동일 판정(secret 이관이 추론을 흔들지 않게 순서 보존). (e) **redaction 무약화** — `*_ref` 보존·secret 어간 마스킹(194-198) 그대로. (f) **fail-closed** — store에 값 없으면 빈 평문(전송 비활성), 절대 예외로 평문 노출/로그 없음. [Source: project-context.md(47·82·92), ui_settings.py(298-340), redaction.py(194-198)]

### 코드 앵커 (변경 대상 정밀 위치)

- `src/rider_crawl/ui_settings.py`
  - `UiSettings` dataclass 23-62: **`*_ref` 필드 3종 추가**(꼬리, 기본값 `""`). 평문 `telegram_bot_token`(35)·`coupang_login_id`(49)·`coupang_login_password`(50)는 **유지하되 비영속화**(직렬화서 제외).
  - `to_app_config()` 130-163: **무변경**(in-memory 평문을 그대로 `AppConfig`에 넘김 — 회귀 0의 핵심).
  - `UiSettingsStore.__init__` 166-168: **store 주입**(기본값 = 설정 파일 옆 `secrets.local.json` 백엔드, 시그니처 호환 위해 기본 인자).
  - `load`/`load_all` 170-219: legacy 평문→store 이관 + ref 발급 + persist-on-first-issue(2.1 패턴). `resolve`로 in-memory 평문 복원.
  - `save`/`save_all` 221-230, `_to_jsonable` 291-295: 직렬화 직전 평문→store, ref만 기록(평문 키 제거). 형식 전부 보존.
  - `_atomic_write_text` 233-263: **재사용**(store 백엔드도 이걸로 atomic write — wheel 재발명 금지).
  - `_settings_from_mapping` 298-312: legacy 평문 키 감지/이관 분기. `_is_legacy_kakao_mapping` 326-340: raw 기준 추론 보존(이관 순서 주의).
  - `_issue_missing_ids` 266-288: ref 발급도 **같은 persist-on-first-issue 정신**으로(idempotent, 안정 ref).
- `src/rider_crawl/secret_store.py` (**신규**): `SecretStore` seam(`put`/`resolve`), `LocalFileSecretStore`, 분류 매핑 3종.
- **참조만(변경 금지):** `config.py` `AppConfig`(52·75-76, 평문 유지 — 런타임 resolved view), `from_env`(78-106, env 경로 무변경), gmail paths(21-22·66-67); `app._message_scope_key`(98-118, dedup 정본); `sender.py`(288-289); `ui.py`(140-173·240·247-248·185·522); `telegram_commands.py`(606); `auth/coupang_email_2fa.py`(203-204); `redaction.py`(161-198). [Source: 위 grep 결과]

### 이전 스토리 인텔리전스 (Epic 1 → 2.1 → 2.2 → 2.3 → 2.4 이월 교훈)

- **A1(secret 게이트):** 신규 테스트 값은 명백한 가짜값만(`"tok-fake"`·`"pw-fake"`·`"-100123"`). 실제 토큰/전화/이메일 형태 금지. 본 스토리는 secret을 다루므로 **특히** 테스트 출력/파일에 가짜 secret이 남는지까지 단언한다. [Source: epic-1-retro 액션 A1]
- **A2(테스트 수치 stale):** dev 노트에 잠정 pass 수치 박지 말 것 — **리뷰 시점 재측정값 1개만** 정본. 2.1/2.2/2.3 모두 dev-story 수치가 QA 갭 보강 후 stale가 돼 리뷰에서 정정됐다(2.1 M1·2.2 M1·2.3 M1). [Source: epic-1-retro 액션 A2, 2-3 스토리(167·192)]
- **2.1/2.2/2.3 교훈(범위 규율):** 2.1=ID 발급만, 2.2=경로 연결+atomic+rotation, 2.3=중립 이름 alias+위험 분류 토대. 각 스토리가 **직교 보강**을 분리했다. 본 스토리도 "secret 분리 메커니즘 + 분류 정책"만 하고 정식 도메인(2.5)·마이그레이션 러너(2.7)·실제 백엔드(Epic 4/5)를 끌어오지 않는다. **2.1의 `_issue_missing_ids` persist-on-first-issue 패턴을 ref 발급에 그대로 재사용**한다(검증된 패턴). [Source: 2-1·2-2·2-3 스토리 범위 경계]
- **2.3 교훈(choke point 형식 보존):** `asdict`는 `@property`를 직렬화 안 함 → 2.3은 형식 0 영향. 본 스토리는 **형식을 바꿔야 하는**(secret 키 제거) 첫 스토리이므로, **그 외 모든 형식 불변**(ensure_ascii·9탭·atomic·crawlings)을 기존 라운드트립 테스트로 잠근다. [Source: 2-3 스토리(90·42)]
- **dev-env:** pytest는 반드시 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — pytest 미설치). git tree는 CRLF/LF 노이즈 — 범위 확인은 `git diff -w`. [Source: memory/dev-env-quirks]
- **테스트 컨벤션:** `tests/`는 미러 구조 + `tmp_path` + 순수 객체/파일 I/O(외부 브라우저/네트워크/PC앱 미호출). 설정 테스트는 `test_ui_settings.py`/`test_config.py`, redaction은 `test_redaction.py`, 신규 store는 `test_secret_store.py`. [Source: project-context.md(53-54·57), architecture.md(280-282)]

### Project Structure Notes

- 변경: `src/rider_crawl/ui_settings.py` + 신규 `src/rider_crawl/secret_store.py`(제품 코드) + 테스트(`test_ui_settings.py`·`test_secret_store.py`, 필요 시 `test_config.py`·`test_app.py`·`test_redaction.py` 회귀). `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64)]
- store 파일(`secrets.local.json`)은 **`runtime/` 하위**(gitignore 6번 줄에 이미 포함) 또는 설정 파일 옆에 두되 **반드시 gitignore**되고 **`ui_settings.json`과 다른 파일**이어야 한다. Story 1.1 백업 zip이 `runtime/state`를 포함하면 store 파일도 secret 산출물로 취급된다(gitignore 17번 줄 정신 — 백업 zip은 secret 포함이므로 절대 커밋 금지). 본 스토리의 testable 보장은 **`ui_settings.json`에 평문 없음**이다(백업 zip 자체 암호화는 Epic 4 NFR-9). [Source: .gitignore(6·11-17)]
- `*_ref` 이름은 redaction `_is_secret_key`의 `_ref` 예외(196-197) 및 data-api-contract `*_ref` 네이밍(secret은 `*_ref`만)과 정렬한다. 단 본 스토리는 DB 컬럼이 아니라 dataclass 필드 + 로컬 store라 테이블/Alembic은 하지 않는다(Epic 5 P4-02). [Source: redaction.py(196-197), architecture.md(257·343), data-api-contract.md(27)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.4(422-438)] — user story·2개 Given/When/Then AC 원문(secret을 일반 설정에서 분리·설정 JSON엔 ref만, 신규 파일에 원본 token/password 없음, 마이그레이션 평문 미복사(ADD-15), 저장 위치 3분류(중앙/Agent-local DPAPI/비저장), DB/로그/스크린샷/config 평문 금지).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2(353-355)] — Epic 2 목표(탭 번호 대신 안정 ID, 도메인 모델·legacy alias·secret_ref 분리; 도메인 dataclass/Enum은 2.5, 마이그레이션 러너는 2.7, 테이블/Alembic은 Epic 5).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P1-06(41)] — Separate secret values from normal settings; UI JSON keeps only refs. New settings files contain no raw token/password.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Secret-Storage(3-17·80-93)] — secret별 저장 계약(Telegram=Secrets Manager+`secret_ref`, Coupang password=Secrets Manager/DPAPI·저장 후 평문 미표시, Gmail token=Agent-local DPAPI/Credential Manager, Agent token=Agent-local), redaction 금지 목록(password/token/refresh/auth code/OTP/full phone/email), 금지행위(token/password/OTP를 로그·DB text·스크린샷·config·에러에 저장 금지).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(9·19·27)] — PlatformAccount(secret refs, not raw credentials), SecretRef 정의(설정/DB 밖에 저장된 secret 참조 — **정식 모델은 Story 2.5**), `platform_accounts`(username_ref·password_ref) — `*_ref` 네이밍 정렬용.
- [Source: _bmad-output/planning-artifacts/architecture.md(146-147·179-184·254-257·343·492-494)] — Secret 저장(중앙 Secrets Manager ref만 DB / Agent-local DPAPI / 비저장), redaction 정책, `*_ref` 컬럼만(`password_ref`/`username_ref`), DB/로그/스크린샷/config 평문 금지, secret 값은 DB 밖(Secrets Manager/Agent DPAPI) Agent 로컬 디스크 경계.
- [Source: _bmad-output/planning-artifacts/architecture.md(414·418·446-447)] — `secure_store.py`(DPAPI/Credential Manager — **Epic 4 소유**), `settings.py`(Secrets Manager ref 로딩 — **Epic 5 소유**), `secret_ref.py` 도메인(**Epic 5/2.5**). 본 스토리 store는 이 백엔드들이 끼울 로컬 MVP seam.
- [Source: src/rider_crawl/ui_settings.py(35·49-50·130-163·221-230·266-288·291-340)] — 평문 secret 필드(`telegram_bot_token`/`coupang_login_id`/`coupang_login_password`), `to_app_config`(무변경 대상), `save`/`save_all`/`_to_jsonable`(직렬화 choke point), `_issue_missing_ids`(persist-on-first-issue 패턴 재사용), `_settings_from_mapping`/`_is_legacy_kakao_mapping`(로드/추론 보존), `_atomic_write_text`(store 재사용).
- [Source: src/rider_crawl/config.py(21-23·52·66-67·75-76·78-106)] — `AppConfig`(런타임 resolved view, 평문 유지), gmail token **경로**-ref 선례(`secrets/google/`), `from_env`(env 경로 무변경).
- [Source: src/rider_crawl/redaction.py(161-198)] — `_SECRET_KEY_SUFFIXES`(token/password/secret/…), `_is_secret_key`의 **`*_ref` 예외**(196-197 — `*_ref`는 secret 아님, 보존). 본 스토리 약화 금지.
- [Source: src/rider_crawl/app.py(98-118)] — `_message_scope_key`(resolved `telegram_bot_token` 직접 참조, dedup 정본 — resolved 값 바이트 동일 유지로 무변경).
- [Source: src/rider_crawl/sender.py(288-289)·ui.py(140-173·240·247-248·185·522)·telegram_commands.py(606)·auth/coupang_email_2fa.py(203-204)] — 평문 secret 소비자(전부 in-memory 평문 읽음 → 무변경, 재배선 금지).
- [Source: _bmad-output/implementation-artifacts/2-1-…(266-288)·2-2-…·2-3-…(90·102·167·192)] — persist-on-first-issue 패턴, choke point 형식 보존, enum 대신 단순 타입 규율, A1/A2 교훈.
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-06-13.md] — 액션 A1(secret 게이트)·A2(테스트 수치 단일 정본).
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: P1-06(secret 분리·설정엔 ref만), NFR-8(secret 저장 위치 분류), NFR-5(redaction), ADD-15(평문 secret 저장 금지행위), NFR-20(회귀). 정식 `SecretRef`/PlatformAccount=Story 2.5(ADD-7), 마이그레이션 러너=Story 2.7(ADD-16), DPAPI 백엔드=Epic 4, AWS Secrets Manager·DB `*_ref` 컬럼·Alembic=Epic 5(P4-02).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code dev-story workflow)

### Debug Log References

- 전체 스위트: `.venv/Scripts/python.exe -m pytest -q` → **670 passed** (리뷰 시점 재측정 단일 정본; 기준선 642 + 신규 28 케이스, 기존 통과 회귀 0 — A2 규율). ⚠️ dev-story가 기록한 663/신규 21은 stale였음 — 리뷰에서 재측정값 670/28로 정정(test_secret_store 11 + test_ui_settings +15 + test_app +1 + test_redaction +1 = 28).
- 범위: `git diff -w --stat`은 `ui_settings.py` + 신규 `secret_store.py` + 테스트만. `config.py`/`ui.py`/`sender.py`/`app.py`/`telegram_commands.py`/`auth/coupang_email_2fa.py`/`redaction.py` 의 `-w` diff = 0줄(소비자 재배선 0, dedup scope key 무변경).
- 누출 grep: 실 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·KR 휴대폰 패턴 0건. 신규 테스트는 가짜값(`tok-fake`/`pw-fake`/`id-fake`/`tok-fresh`)만 사용.

### Completion Notes List

- **AC1 (설정 JSON엔 ref만·마이그레이션 평문 미복사):** `UiSettings`에 `telegram_bot_token_ref`/`coupang_login_password_ref`/`coupang_login_id_ref` 3종을 꼬리 필드로 추가하고, 평문 secret 3종은 dataclass 필드로 유지하되 `_to_jsonable`에서 값을 비워 **비영속(transient)** 으로 강등. 직렬화 choke point(`save`/`save_all`→`_absorb_secrets`)가 평문을 store로 빼고 `*_ref`만 기록, 로드 choke point(`load`/`load_all`→`_resolve_secrets`)가 legacy 평문을 감지하면 persist-on-first-issue로 한 번 영속화해 신규 파일엔 ref만 남긴다(2.1 패턴 재사용). 반쪽 마이그레이션(평문+ref 동시) 시 평문을 정본으로 재이관(precedence)해 평문 잔존 0을 보장.
- **AC2/AC5 (3분류 + 무약화):** `secret_store.py`에 `SECRET_STORAGE_CLASSIFICATION` dict + `classify_secret_storage()`로 5종을 `central`/`agent_local`/`not_stored` 3분류(enum 아님 — 2.5 소유). redaction은 무수정, `*_ref` 보존·secret 어간 마스킹 회귀 케이스 추가. Gmail OAuth token은 설정 JSON에 경로(ref)만 있음을 회귀로 명문화.
- **AC3 (주입 가능 seam + MVP 한계):** `SecretStore` Protocol(`put`/`resolve`) + `LocalFileSecretStore`(설정 파일과 **다른** `secrets.local.json`, atomic write는 `_atomic_write_text` 공유). store 미보유 ref는 `resolve→None→""`(fail-closed). 기본 store는 생성자 기본 인자라 `ui.py` 무변경. 테스트는 `tmp_path` store 주입.
- **무회귀 핵심:** `to_app_config()` 무변경 — 로드 시 ref를 in-memory 평문으로 resolve하므로 `AppConfig.telegram_bot_token`/`coupang_login_*`이 바이트 동일하게 채워져 sender·쿠팡 2FA·`app._message_scope_key` dedup 정본이 그대로 동작(scope key 무변경 테스트로 잠금).
- **ref 형식 판단(redaction 정합):** 권장 예시 `local:{id}:{field}`는 `<digits>:<word>`가 redaction의 Telegram 토큰 정규식에 걸려 ref가 로그에서 마스킹된다. 추적성 보존(스토리 "redaction이 ref를 보존")을 위해 필드 구분자를 `/`로 바꿔 `local:{id}/{field}` 사용(vault://… 선례와 동일하게 redaction이 보존).
- **기존 테스트 정합 업데이트:** atomic-write 누출 테스트 3건의 잔여물 검사를 "`.tmp` 잔여물 없음"으로 정정(secret store가 정상 sibling `secrets.local.json`을 만드므로) — 테스트 본래 의도(atomic temp 정리) 보존.

### File List

- `src/rider_crawl/secret_store.py` (신규) — `SecretStore` seam, `LocalFileSecretStore`, 3분류 매핑/`classify_secret_storage`.
- `src/rider_crawl/ui_settings.py` (수정) — `*_ref` 필드 3종, store 주입, `_absorb_secrets`/`_resolve_secrets`/`_secret_ref`, `_to_jsonable` 평문 제외, load/load_all 마이그레이션·persist-on-first-issue.
- `tests/test_secret_store.py` (신규) — 분류 5종·store seam(put/resolve·결정적 ref·fail-closed·별도 경로·atomic).
- `tests/test_ui_settings.py` (수정) — 평문 0·마이그레이션 미복사·resolve 바이트 동일·fail-closed·precedence·gmail ref·멱등 로드; atomic 누출 검사 `.tmp` 기준으로 정정.
- `tests/test_app.py` (수정) — secret store 왕복 후 `_message_scope_key` 무변경 회귀.
- `tests/test_redaction.py` (수정) — 신규 `*_ref` 키 보존 + 평문 secret 어간 마스킹 회귀.

## Senior Developer Review (AI)

**리뷰어:** Noah Lee · **일자:** 2026-06-13 · **결과:** Approve (자동수정 1건 적용 후)

**범위/방법:** `secret_store.py`(신규)·`ui_settings.py`(수정) 전체 정독 + 4개 테스트 파일 diff 검수 + 전체 스위트 재측정 + 누출 grep + redaction 정합 프로브. `_bmad/`·`_bmad-output/`은 리뷰 대상 제외.

**AC 검증 (전부 IMPLEMENTED):**
- **AC1** — `_absorb_secrets`가 직렬화 직전 평문을 store로 빼고 `_to_jsonable`이 평문 키를 비움(평문 0건). `load`/`load_all`의 `_resolve_secrets`가 legacy 평문을 persist-on-first-issue로 1회 이관 → 신규 파일 ref-only. 반쪽 마이그레이션 precedence(평문 우선) 동작 확인. (`test_save_strips_…`, `test_load_all_migrates_…`, `test_migration_precedence_…`)
- **AC2** — `SECRET_STORAGE_CLASSIFICATION` 5종→3분류, redaction 무수정(`*_ref` 보존·secret 어간 마스킹 회귀 통과), Gmail OAuth는 경로(ref)만 직렬화.
- **AC3** — `SecretStore` Protocol(`put`/`resolve`) + `LocalFileSecretStore`(설정 파일과 분리된 `secrets.local.json`, `_atomic_write_text` 공유), 기본 인자 wiring으로 `ui.py` 무변경, store-miss → `""` fail-closed. MVP 한계(평문 JSON·암호화 Epic 4) 명시.

**Task 감사:** [x] 8개 전부 코드 근거로 실제 완료 확인(허위 [x] 0).

**Git vs File List:** 불일치 0 — git 변경(src/tests) 6개가 File List와 정확히 일치, 미문서화 변경 없음.

**검증한 무회귀 핵심:** `to_app_config()` 무변경 → resolve 평문 바이트 동일 → `app._message_scope_key` dedup 정본 불변(`test_message_scope_key_unchanged_…` 통과). 소비자 재배선 0(`-w` diff = config/app/sender/redaction 0줄). 직렬화 형식(ensure_ascii·9탭·crawlings·atomic) 보존.

**검증한 보안 속성:** 실 봇토큰/`chat_id`/KR전화 패턴 grep 0건. redaction `*_ref` 보존이 uuid-hex target에도 견고(슬래시 구분자 + 필드 접미사가 토큰/전화 정규식 모두 무력화 — 프로브로 PRESERVED 확인). 기본 store 경로 `runtime/state/secrets.local.json` → `.gitignore` `runtime/`(6줄) 커버.

**Findings:**
- **M1 (MEDIUM, A2 — 자동수정 완료):** Dev Agent Record/Change Log의 `663 passed`·`신규 21`이 stale. 리뷰 재측정 단일 정본 = **670 passed / 신규 28**(642 + 28). 기록 정정함.
- **L1 (LOW, 무수정):** `test_resolve_missing_store_value_…`·`test_migration_precedence_…` fixture가 legacy 콜론형 ref(`local:mt-1:telegram_bot_token`)를 입력으로 쓴다 — 코드가 발급하는 슬래시형과 표기 불일치이나, 두 테스트는 입력 ref의 resolve/덮어쓰기 동작을 검증하므로 결함 아님(오히려 비정규 ref도 안전 처리됨을 보장). 변경 불필요.

**결론:** CRITICAL 0, 코드·테스트·범위 모두 계약 준수. M1 정정 반영, Status → `done`.

## Change Log

| 날짜 | 변경 | 비고 |
|---|---|---|
| 2026-06-13 | Story 2.4 구현: secret 값 분리(설정 JSON엔 `*_ref`만), 주입 가능한 로컬 `SecretStore` seam + 파일 백엔드, secret 저장 위치 3분류 매핑 | 670 passed, 회귀 0. `ui_settings.py` 직렬화/로드 choke point만 secret-safe화(2.7 마이그레이션 러너가 자동 상속), 소비자 재배선·dedup scope key 무변경. 정식 `SecretRef`(2.5)·실제 DPAPI(Epic 4)·AWS Secrets Manager(Epic 5)는 범위 밖. |
| 2026-06-13 | 자동 코드 리뷰(story-automator) — 1건 자동수정 | M1(A2): dev 기록 pass 수치 663/신규 21 stale → 재측정 정본 **670 passed / 신규 28**로 정정. CRITICAL 0 → Status `done`. AC1~7 전부 구현 확인, File List=git 일치, 평문 누출 0, redaction `*_ref` 보존·fail-closed·gitignore 커버리지·소비자 무변경 검증. |
