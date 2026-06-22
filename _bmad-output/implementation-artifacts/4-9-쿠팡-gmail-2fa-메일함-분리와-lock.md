---
baseline_commit: 92e4a64
---

# Story 4.9: 쿠팡 Gmail 2FA 메일함 분리와 lock

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 보안 담당 운영자,
I want **쿠팡 Gmail 2FA token 을 고객/`mailbox_id` 단위로 분리 저장(Agent-local DPAPI, 서버는 ref 만)하고 고객 간 token 을 공유하지 않으며**, **같은 `mailbox_id` 에 동시에 들어온 두 쿠팡 인증 요청을 mailbox lock 으로 직렬화하고(메일 검색은 인증 요청 시각 이후 수신 메일만 from/subject/query/customer 필터로 조회해 최신 메일 오인식을 막으며)**, **인증번호(OTP)·OAuth token·refresh token·쿠팡 비밀번호가 로그·예외·결과에 남지 않게 하면서 CAPTCHA/이상 로그인은 복구를 멈추고 `USER_ACTION_REQUIRED`, refresh 실패/grant 취소는 `GMAIL_REAUTH_REQUIRED` 로 조치 가능 유형으로 분류**하고, **자동복구가 반복 실패하면 인증 요청을 계속 보내지 않고 bounded 상한에서 멈춰 탭/작업을 중지하는 기존 정책을 유지**하는 것을, **OS·Gmail·시계·lock 부작용을 전부 주입 가능하게 한 순수 동기 primitive 신규 모듈 `src/rider_agent/auth/coupang_gmail_2fa.py`(mailbox 별 token 분리 helper + `MailboxLockRegistry` + 실패 분류기 + bounded 복구 orchestrator)** 로 갖고 싶다,
so that 다른 고객의 인증번호를 잘못 읽거나(최신 메일 오인식·교차 mailbox) 민감값을 노출하지 않고(NFR-5), 동시 같은 메일함 읽기 충돌을 막으며(FR-19), 반복 인증 요청 루프로 운영을 망가뜨리지 않고(NFR-4) 안전하게 2FA 를 복구한다 (P3, FR-19, NFR-4·5·8·16, ADD-15).

> **이 스토리의 성격 — "쿠팡 Gmail 2FA 메일함 분리·lock·분류·bounded-stop primitive"만. Epic 4 플랫폼-인증 시리즈(4.8 배민 사람 개입형 / 4.9 쿠팡 Gmail 2FA)의 두 번째이자 배민과 정반대 정책 스토리.** 4.8 과 **같은 `src/rider_agent/auth/` 서브패키지**(`__init__.py`·`baemin_auth.py` 이미 존재)에 **세 번째 모듈 `coupang_gmail_2fa.py`** 를 추가한다. 4.1~4.8 이 만든 토대(reuse seam·DpapiSecretStore·job_loop 헬퍼·heartbeat capability) 위에 **(a) mailbox 별 token 분리**(`DpapiSecretStore` 재사용, ref 를 `mailbox_id` 로 keying, 서버는 ref 만), **(b) `MailboxLockRegistry`**(`mailbox_id` 별 `threading.Lock` — 같은 mailbox 직렬·다른 mailbox 병렬), **(c) 실패 분류기**(`recovered`/CAPTCHA/reauth → 평문 상수 `ACTIVE`/`USER_ACTION_REQUIRED`/`GMAIL_REAUTH_REQUIRED`), **(d) bounded 복구 orchestrator**(reuse `recover_coupang_session_with_email_2fa` 를 lock 아래에서 호출, 반복 실패 시 상한에서 멈춤)를 얹는다. **실제 Gmail API·실 쿠팡 인증 화면·실 시계·실 token 파일은 본 스토리가 "테스트에서" 한 줄도 호출하지 않는다** — 전부 주입 fake(`recover`/`fetch_code`/`store`/`now`/`sleep`)로 결정적 검증한다. [Source: data-api-contract.md Coupang-Gmail-2FA(135-144), operations-security-test-contract.md(8-9·15·31·80·92-93), implementation-contract.md(11), epics.md Story 4.9(877-902)]
>
> **배민(4.8)과 정반대 정책 — 쿠팡은 OTP 자동 복구를 "한다"(reuse 소비).** 4.8 배민 auth 는 OTP 취득·우회를 **절대 금지**(`fetch_latest_verification_code`/`recover_coupang_session_with_email_2fa`/`pyautogui` import 0, AST 부정 가드)했다. **4.9 쿠팡은 정반대로 그 reuse seam 을 적극 소비한다** — `reuse.py(46-48)` 가 `recover_coupang_session_with_email_2fa`·`fetch_latest_verification_code` 를 **이미 4.9 용으로 pre-commit** 해 두었다("Gmail 2FA — 쿠팡 ... 4.9 가 이 seam 으로 import"). 본 스토리는 OTP **조회·입력·제출**을 reuse 코드에 맡기고, 그 위에 **mailbox 분리·lock·분류·bounded-stop** 만 얹는다(OTP 파싱 로직 재구현 0 — 이미 `gmail.py` 가 "최신 메일만·요청시각 이후만·유일 N자리만"을 검증된 형태로 가짐). [Source: src/rider_agent/reuse.py(46-48·77-78), src/rider_crawl/auth/gmail.py(97-138·238-264), src/rider_crawl/auth/coupang_email_2fa.py(76-124), src/rider_agent/auth/baemin_auth.py(27-31)]
>
> **mailbox 분리 = 고객 간 token 비공유(핵심 가드 #1, ADD-15·NFR-8).** Gmail OAuth token 은 `mailbox_id` 단위로 **분리 저장**한다 — `DpapiSecretStore.put(token, ref=mailbox_token_ref(mailbox_id))` 로 mailbox 마다 **다른 ref**(예: `gmail:{mailbox_id}`)를 쓰고, 서버에는 **ref(불투명 핸들)만** 올린다. 두 고객이 같은 token/같은 ref 를 공유하면 안 된다(ops:92 "Share Gmail token between customers" = 금지). DPAPI 암호화·atomic write·결정적 ref·fail-closed `resolve→None` 는 4.2 `DpapiSecretStore` 가 이미 보장하므로 **새 crypto/store 재발명 0**. [Source: src/rider_agent/secure_store.py(141-207), src/rider_crawl/secret_store.py(23-39·42-52), operations-security-test-contract.md(8-9·92), data-api-contract.md(139-140)]
>
> **mailbox lock = 같은 메일함 동시 읽기 차단(핵심 가드 #2, FR-19·NFR-16).** 같은 `mailbox_id` 로 두 인증 요청이 동시에 들어오면 **lock 으로 직렬화**한다 — `MailboxLockRegistry` 가 `mailbox_id` 별 `threading.Lock` 을 발급(같은 id→같은 lock 객체, 다른 id→독립 lock 으로 병렬 허용). 직렬화 + reuse 의 `requested_after`(요청 시각 − 30s 안전여유) 컷오프 + `gmail_2fa_query`(from/subject) + customer 필터가 함께 **"최신 메일 오인식"(다른 요청의 코드를 잘못 읽음)** 을 막는다. lock scope 는 `mailbox_id` 단위 — 이는 4.5 CDP lock(`cdp_url` scope)·4.6 Kakao 전역 lock·browser_profile 등록부 lock(`threading.Lock`)과 동형 패턴. [Source: epics.md AC(890-893), data-api-contract.md(141-142), src/rider_agent/browser_profile.py(208), src/rider_agent/workers/kakao_sender.py(182·213), src/rider_crawl/auth/coupang_email_2fa.py(110-114)]
>
> **민감값 0 노출(핵심 가드 #3, NFR-5·ops:15·93).** **OTP·OAuth token·refresh token·쿠팡 비밀번호·full email/phone 이 로그·예외·결과·metrics·event 어디에도 들어가지 않는다.** `Gmail2faError`/`Coupang2faError` 는 설계상 메시지에 코드/토큰을 넣지 않고(gmail.py:29-30·coupang_email_2fa.py:128), `make_failure_result`(`redacted_error_event`)·`redact()` 가 자유 텍스트를 마스킹한다. **단, `redact()` 는 운영 식별자(mailbox/customer/center 명)를 못 가리므로(memory: redact-skips-operational-ids) reuse-레이어 예외 본문을 결과/로그에 통째로 forwarding 하지 않고 고정 사유 상수 + `mailbox_id` **ref**(평문 mailbox 주소 아님) 만 쓴다**(4.8 `auth_check`·4.6 `_failure` 선례). token bytes 는 store(DPAPI)에만, result_json 엔 ref 만. [Source: operations-security-test-contract.md(15·93), src/rider_crawl/auth/gmail.py(10·29-30·142-160), src/rider_agent/job_loop.py(187-228), memory/redact-skips-operational-ids]
>
> **조치 가능 분류 + bounded-stop(핵심 가드 #4, NFR-16·NFR-4).** 실패는 **조치 가능 유형**으로 분류한다 — CAPTCHA/이상 로그인(=reuse `recover` 가 `False` 반환) → `USER_ACTION_REQUIRED`; Gmail refresh 실패/grant 취소(=reauth 신호) → `GMAIL_REAUTH_REQUIRED`; 인증메일 지연/코드 미추출(=transient) → bounded 재시도 후 멈춤. **자동복구가 반복 실패하면 인증 요청을 계속 보내지 않는다** — `recover` 호출 횟수에 상한(`max_attempts`, 기존 정책처럼 작게)을 두고, 상한 소진 시 마지막 분류 상태로 **멈춰**(탭/작업 중지 신호) `make_failure_result(error_code, ...)` 로 표면화한다(반복 인증 요청 루프 0). 이는 기존 crawler 정책(`_try_recover_coupang_session` = 1회 시도 후 실패면 `BrowserActionRequiredError` 로 탭 중지·`config.py:20` "로그인 만료 시 탭 중지")·4.5 `recover_profile` bounded·4.8 bounded 대기와 동형. [Source: epics.md AC(900-902), data-api-contract.md(143-144), src/rider_crawl/platforms/coupang/crawler.py(518-546), src/rider_crawl/config.py(20·65), src/rider_agent/browser_profile.py(71-73·345-392), project-context.md(89)]
>
> **상태/오류 어휘는 평문 문자열 — enum/"정확히 N개" lock 금지(핵심 가드 #5, memory: enum-member-count-locks).** `USER_ACTION_REQUIRED`·`GMAIL_REAUTH_REQUIRED` 는 **spec data-api-contract(143-144)의 정본 평문 문자열**이며 `rider_server.domain.states` 의 어떤 enum(`BaeminAuthState` 7·`FailureCategory` 7)에도 **없다**(배민 전용 `USER_ACTION_PENDING` 과 혼동 금지 — 쿠팡은 `USER_ACTION_REQUIRED`). 따라서 `rider_server` 를 import 하지 않고(단방향 가드) **평문 상수**로 두며(4.5 `STATE_AUTH_REQUIRED`·4.6 `ERROR_KAKAO_FAILURE`·4.8 `AUTH_STATE_*` 선례) 어떤 count-lock 도 두지 않는다. [Source: data-api-contract.md(143-144), src/rider_server/domain/states.py(45-59·165-187), src/rider_agent/auth/baemin_auth.py(67-86), memory/enum-member-count-locks]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리 산출물: **신규 `src/rider_agent/auth/coupang_gmail_2fa.py` + 신규 `tests/agent/test_coupang_gmail_2fa.py`** 뿐이다. **기존 `rider_crawl/`·`rider_server/` 소스는 0줄 변경, `pyproject.toml` 0줄 변경.** 아래는 **다른 스토리/에픽 소유 — 본 스토리에서 손대지 않는다:**
> - **`src/rider_crawl/` 의 auth/crawl/Gmail/config 소스 — 0줄 변경(무회귀 안전 마진).** Gmail OTP 조회·세션 복구·요청시각 컷오프·query 필터·코드 파싱은 **이미 `gmail.py`/`coupang_email_2fa.py` 에 검증된 형태로 존재**한다 — 4.9 는 reuse seam 으로 **소비만** 하고 새 OTP/Gmail 로직을 `rider_crawl` 에 추가하지 않는다(epic-3-retro(158): "`rider_crawl` 0줄이 안전 마진"). [Source: project-context.md(64·82), epic-3-retro-2026-06-13.md(158)]
> - **`reuse.py` 변경 여부 = 열린 질문(아래 참조).** 권장은 **error 타입 분류를 주입 seam**(`is_reauth`)으로 흡수해 `reuse.py` 도 0줄. 정 concrete 타입이 필요하면 `Gmail2faError`/`Coupang2faError` 의 **additive re-export 2줄**만(reuse 는 4.1 이 "후속 워커 auth 4.8·4.9" 용으로 설계한 seam — re-export-only·비행위적). 둘 중 하나로 일관. [Source: src/rider_agent/reuse.py(1-7·46-48), tests/agent/test_agent_package.py(316-319)]
> - **`pyproject.toml` `[project].dependencies` — 0줄.** stdlib(`threading`/`time`/`pathlib`) + `rider_crawl`(reuse) + 자기 패키지만. 새 third-party 도입 시 4.1 가드 `test_pyproject_dependencies_unchanged_pins`(deps **정확히 9개**)·`test_rider_agent_only_third_party_root_is_rider_crawl`(third-party root == `{rider_crawl}`)가 **둘 다** 깨진다. [Source: tests/agent/test_agent_package.py(206-225)]
> - **`job_loop.py`·`__main__.py`·`heartbeat.py`·`browser_profile.py`·`secure_store.py`·`baemin_auth.py`·`auth/__init__.py` — 0줄(reuse only).** 4.9 primitive 는 `make_*_result`/`DpapiSecretStore`/`CAPABILITY_*` 를 **import 만** 한다. 새 heartbeat capability 신설 안 함(쿠팡 2FA 복구는 별도 job type 이 아니라 `CRAWL_COUPANG` 흐름 안에서 일어남 — 아래 "설계 결정"). [Source: src/rider_agent/heartbeat.py(62-77), src/rider_agent/auth/__init__.py(10-13)]
> - **실제 `CRAWL_COUPANG` 수집 워커(`workers/crawl_worker.py`)·서버 측 OAuth onboarding·`gmail_reauth_required_count` 알림·Admin 인증 화면** → 미존재/Epic 5. 본 스토리는 그 워커가 소비할 **mailbox 분리·lock·분류·bounded-stop primitive** 를 제공할 뿐, crawl 워커나 서버 알림을 만들지 않는다. [Source: architecture.md(453-454), operations-security-test-contract.md(31), src/rider_agent/job_loop.py(234-249)]
>
> **sync 런타임 + 단방향 import + Windows-gated import-safety(4.1 규약 계승 — 자동 검증됨).** 신규 `coupang_gmail_2fa.py` 는 **순수 동기**(no `async def`/`await`/직접 `import asyncio`)이고 stdlib + `rider_crawl`(reuse·redact·secret_store) + 자기 패키지만 import 한다(역방향 0, `rider_server` 0). 실 Gmail/실 DPAPI/실 시계는 **함수 내부 lazy + 주입 가능**이라 `import rider_agent.auth.coupang_gmail_2fa` 가 비-Windows(WSL/CI)에서도 import-safe 하다(reuse seam eager import 도 `googleapiclient` 미로드 — gmail.py 가 google 을 함수 내부 import). 4.1 가드가 `src/rider_agent/` 를 `rglob("*.py")`(재귀)로 검사하므로 **신규 모듈도 자동 적용**된다. [Source: tests/agent/test_agent_package.py(188-245·277-287), src/rider_crawl/auth/gmail.py(275-300), memory/negative-guard-tests-use-ast]

## Acceptance Criteria

**AC1 — Gmail token 고객/mailbox 분리 저장·서버 ref 만·고객 간 비공유 (FR-19, ADD-15, NFR-8)**

1. **Given** 여러 고객의 쿠팡 Gmail 2FA 가 있을 때 **When** Gmail OAuth token 을 저장하면 **Then** token 은 `mailbox_id` 단위로 **분리 저장**되고(Agent-local DPAPI) 서버는 **ref 만** 저장한다: `coupang_gmail_2fa.py` 가 `mailbox_token_ref(mailbox_id) -> str`(예: `f"gmail:{mailbox_id}"` — `mailbox_id` 별 결정적 고유 ref), `store_mailbox_token(store, mailbox_id, token) -> str`(=`store.put(token, ref=mailbox_token_ref(mailbox_id))`), `resolve_mailbox_token(store, mailbox_id) -> str | None`(=`store.resolve(...)`, 없으면 `None` fail-closed)을 제공한다. `store` 기본은 `DpapiSecretStore`(주입 가능 — 테스트는 fake codec/`tmp_path`). token bytes 는 store(DPAPI 암호화 blob)에만, 반환·result_json·로그엔 **ref 만**(평문 token 0). [Source: src/rider_agent/secure_store.py(141-207·44-48), src/rider_crawl/secret_store.py(42-52), data-api-contract.md(139-140)]
2. **And** **고객 간 Gmail token 을 공유하지 않는다(ADD-15·ops:92)**: 두 다른 `mailbox_id` 는 **다른 ref** 로 저장되고 한 mailbox 의 `resolve` 가 다른 mailbox 의 token 을 돌려주지 않는다. `gmail_oauth_token` 은 `classify_secret_storage` 상 `agent_local`(영속·DPAPI), `otp` 는 `not_stored`(읽어 입력 후 폐기 — store 에 넣지 않음) 정책을 따른다. [Source: src/rider_crawl/secret_store.py(27-33), operations-security-test-contract.md(92)]
3. **And** token 분리/해소는 **순수 동기·주입 가능**(테스트에서 실 DPAPI 미호출 — fake codec)하고 **평문 token 비노출**(`AgentIdentity.__repr__` 류 보장은 store 가, 4.9 는 ref 만 surfacing). [Source: src/rider_agent/secure_store.py(164-191·213-230), project-context.md(81)]

**AC2 — 같은 mailbox 동시 읽기 lock 직렬화 + 요청시각·필터 메일 검색 (FR-19, NFR-16)**

4. **Given** 같은 메일함을 동시에 읽으면 안 될 때 **When** 두 쿠팡 인증 요청이 같은 `mailbox_id` 에 들어오면 **Then** **mailbox lock 으로 동시 처리를 막는다**: `MailboxLockRegistry` 가 `lock_for(mailbox_id) -> threading.Lock`(같은 id→**같은 lock 객체**, 다른 id→독립 lock) 또는 context-manager `acquire(mailbox_id)` 를 제공해, 같은 `mailbox_id` 의 두 복구가 **직렬화**(겹쳐 실행 0)되고 다른 `mailbox_id` 는 병렬 허용된다. 등록부 내부 dict 는 `threading.Lock` 으로 보호(4.5/4.6 등록부 선례). [Source: epics.md AC(890-892), data-api-contract.md(142), src/rider_agent/browser_profile.py(208), src/rider_agent/workers/kakao_sender.py(182·213)]
5. **And** **메일 검색은 인증 요청 시각 이후 수신 메일만 from/subject/query/customer 필터로 조회한다**: 복구 orchestrator 는 reuse `recover_coupang_session_with_email_2fa`(내부적으로 `fetch_latest_verification_code(requested_after=요청시각−안전여유, query=gmail_2fa_query, ...)` 호출 — "최신 메일만·요청시각 이후만·유일 N자리만" 이미 검증됨)를 **lock 아래에서** 호출하고, customer 필터(`gmail_2fa_query` 또는 mailbox 별 query)를 적용한다. 4.9 는 이 컷오프/필터 로직을 **재구현하지 않고** reuse 가 보장하게 하며, lock 직렬화로 동시 요청 간 코드 교차(최신 메일 오인식)를 막는다. [Source: src/rider_crawl/auth/gmail.py(97-138·56-94), src/rider_crawl/auth/coupang_email_2fa.py(110-124), data-api-contract.md(141)]
6. **And** lock 획득/해제는 **결정적·재진입 안전**(예외/finally 로 항상 해제, 같은 thread 가 hang 하지 않음)하고, lock 동작은 주입 fake `recover`/짧은 스레드/순서 단언으로 **실 Gmail 없이** 검증한다. [Source: src/rider_agent/workers/kakao_sender.py(213·직렬 패턴), 4-6 스토리]

**AC3 — 민감값 비노출 + CAPTCHA/이상→USER_ACTION_REQUIRED, refresh 실패/grant 취소→GMAIL_REAUTH_REQUIRED (NFR-5·16)**

7. **Given** 민감값을 보호해야 할 때 **When** 2FA 를 처리하면 **Then** **인증번호(OTP)·OAuth token·refresh token·쿠팡 비밀번호가 로그·예외 메시지·result_json·metrics·event 어디에도 남지 않는다**: 결과는 `make_success_result`/`make_failure_result`(`redacted_error_event` 자동 마스킹)로 만들고 자유 텍스트는 `redact()` 통과, raw reuse 예외 본문은 forwarding 하지 않고 **고정 사유 상수 + `mailbox_id` ref** 만 싣는다. fixture 는 가짜 mailbox_id/token-ref 만(실제 OTP·token·email·비밀번호 금지). [Source: operations-security-test-contract.md(15·93), src/rider_agent/job_loop.py(187-228·210-228), memory/redact-skips-operational-ids]
8. **And** **실패는 조치 가능 유형으로 분류된다(NFR-16)**: `classify_coupang_2fa_outcome(*, recovered=None, error=None, is_reauth=None) -> str` 가 — `recovered is True` → `STATE_RECOVERED="ACTIVE"`; `recovered is False`(CAPTCHA/이상 로그인 = reuse `recover` 가 자동 복구 불가로 `False`) → `STATE_USER_ACTION_REQUIRED="USER_ACTION_REQUIRED"`; reauth 신호(`is_reauth is True` 또는 reauth 분류된 `error` = Gmail refresh 실패/grant 취소) → `STATE_GMAIL_REAUTH_REQUIRED="GMAIL_REAUTH_REQUIRED"`; 그 외(인증메일 지연/코드 미추출 transient `error`) → `STATE_RECOVERY_FAILED`(또는 transient 사유) 로 매핑한다. 상태 어휘는 **평문 상수**(spec data-api-contract 값 정합·`rider_server` import 0·count-lock 0). [Source: data-api-contract.md(143-144), src/rider_crawl/auth/coupang_email_2fa.py(92-94·148-153), src/rider_crawl/auth/gmail.py(287-298), src/rider_server/domain/states.py(45-59)]

**AC4 — bounded 자동복구·반복 인증 요청 금지·탭 중지 정책 유지 (NFR-4, project-context)**

9. **Given** 자동복구가 실패할 때 **When** 인증이 반복 실패하면 **Then** **반복 인증 요청을 계속 보내지 않고 탭/작업을 중지하는 기존 정책을 유지한다**: bounded 복구 orchestrator `recover_coupang_mailbox(*, mailbox_id, recover, ..., max_attempts=DEFAULT_MAX_RECOVERY_ATTEMPTS, now, sleep) -> JobResult` 가 `recover` 호출 횟수에 **상한**(`max_attempts`, 기본은 기존 정책처럼 작게 — 1~3)을 두고, 상한 내 미복구면 `STATE_RECOVERED` 로 가지 않고 분류 상태(`USER_ACTION_REQUIRED`/`GMAIL_REAUTH_REQUIRED`/recovery_failed)로 **멈춰** `make_failure_result(error_code, "<고정 사유>", result_json={mailbox 식별 ref, state, reason}, metrics={...})` 를 돌린다(반복 인증 요청·무한 polling 0). 주입 `now`/`sleep` 로 backoff·상한을 결정적 검증. [Source: epics.md AC(900-902), src/rider_crawl/platforms/coupang/crawler.py(518-540), src/rider_crawl/config.py(20), src/rider_agent/browser_profile.py(71-73·345-392), project-context.md(89)]
10. **And** **복구 실패/중지가 운영 상태에 남는다**: 결과는 `JobResult`(`error_code` ∈ {`USER_ACTION_REQUIRED`/`GMAIL_REAUTH_REQUIRED`/recovery_failed} + `result_json.state` + `metrics.reason`)로 표면화돼 서버(Epic 5)·`gmail_reauth_required_count` 알림(Epic 5) 경로로 관측 가능하다. **secret 0**(사유·식별은 평문 상수·ref 만). 복구 성공 시 `make_success_result(result_json={mailbox ref, state: ACTIVE})`. [Source: operations-security-test-contract.md(31), src/rider_agent/job_loop.py(175-207), data-api-contract.md(143-144)]

## Tasks / Subtasks

- [x] **Task 1 — 신규 모듈 `coupang_gmail_2fa.py` + 평문 상태/오류 상수 (AC: 1, 3, 4)**
  - [x] `src/rider_agent/auth/coupang_gmail_2fa.py` 신설(`auth/__init__.py`·`baemin_auth.py` 와 같은 서브패키지). 모듈 docstring 에 범위(mailbox 분리·lock·분류·bounded-stop; 실 Gmail/DPAPI/시계는 lazy·주입 가능; **OTP/token 비노출**; 배민과 정반대로 reuse OTP 복구를 **소비**)와 sync/단방향/import-safety 규약을 4.8 `baemin_auth.py` 와 동형으로 명시. [Source: src/rider_agent/auth/baemin_auth.py(1-45), src/rider_agent/auth/__init__.py(1-14)]
  - [x] 상태 어휘(**평문 상수**, spec data-api-contract 값 정합·`rider_server` import 0·enum/"정확히 N" lock 금지): `STATE_RECOVERED="ACTIVE"`, `STATE_USER_ACTION_REQUIRED="USER_ACTION_REQUIRED"`, `STATE_GMAIL_REAUTH_REQUIRED="GMAIL_REAUTH_REQUIRED"`, (선택) `STATE_RECOVERY_FAILED="RECOVERY_FAILED"`. error_code 는 상태명과 동일 UPPER_SNAKE 평문(`ERROR_USER_ACTION_REQUIRED`/`ERROR_GMAIL_REAUTH_REQUIRED`/`ERROR_RECOVERY_FAILED`). 사유 평문(`REASON_CAPTCHA_OR_ABNORMAL="captcha_or_abnormal_login"`, `REASON_GMAIL_REAUTH="gmail_reauth_required"`, `REASON_MAIL_DELAY="verification_mail_delayed"`, `REASON_REPEATED_FAILURE="repeated_recovery_failure"`). 상한 상수 `DEFAULT_MAX_RECOVERY_ATTEMPTS`(기존 1회 정책 본떠 작게)·`DEFAULT_RECOVERY_BACKOFF_SECONDS`. [Source: data-api-contract.md(143-144), src/rider_agent/auth/baemin_auth.py(67-96), src/rider_server/domain/states.py(180), memory/enum-member-count-locks]
  - [x] **순수 동기 + stdlib(`threading`/`time`/`pathlib`/`typing`) + `rider_crawl`(reuse·redact·secret_store) + 자기 패키지만 import** — async 0, `rider_server` 0, `import asyncio` 0. 4.1 AST 가드(`rglob`)가 자동 검사. [Source: tests/agent/test_agent_package.py(188-215)]
- [x] **Task 2 — mailbox 별 token 분리 helper(DpapiSecretStore 재사용) (AC: 1)**
  - [x] `mailbox_token_ref(mailbox_id: str) -> str`: `mailbox_id` 별 결정적 고유 ref(예: `f"gmail:{mailbox_id}"`). 두 다른 mailbox→다른 ref(고객 간 비공유 보장). ref 는 secret 아닌 불투명 핸들(redaction 이 `*_ref` 보존). **⚠️ ref 안의 `mailbox_id` 는 opaque/hashed 핸들이어야 하고 평문 이메일 주소면 안 된다** — `redact()` 는 운영 식별자(이메일/mailbox 명)를 못 가리므로(memory: redact-skips-operational-ids) ref 가 로그/result_json/store keyspace 로 새면 평문 mailbox 가 노출된다. `mailbox_id` 가 평문 이메일일 수 있으면 `mailbox_token_ref` 안에서 hash/opaque 화한다(예: `f"gmail:{sha256(mailbox_id)[:16]}"`). [Source: src/rider_agent/secure_store.py(44-48·166-168), src/rider_crawl/secret_store.py(70-80), memory/redact-skips-operational-ids]
  - [x] `store_mailbox_token(store: SecretStore, mailbox_id: str, token: str) -> str` = `store.put(token, ref=mailbox_token_ref(mailbox_id))`; `resolve_mailbox_token(store, mailbox_id) -> str | None` = `store.resolve(mailbox_token_ref(mailbox_id))`(없으면 `None` fail-closed). `store` 기본 `DpapiSecretStore(default_secret_store_path())`(주입 가능). **새 store/crypto 재발명 0** — 4.2 백엔드를 그대로 끼운다. token bytes 는 store 에만, 반환·result 엔 ref 만. [Source: src/rider_agent/secure_store.py(141-207·74-75), src/rider_crawl/secret_store.py(42-52)]
  - [x] (선택) `classify_secret_storage("gmail_oauth_token")==agent_local`·`("otp")==not_stored` 정책을 docstring/주석에 명시(저장 분류 정합). OTP 는 store 에 넣지 않는다(읽어 입력 후 폐기 — reuse 가 담당). [Source: src/rider_crawl/secret_store.py(27-39)]
- [x] **Task 3 — `MailboxLockRegistry`(mailbox_id 별 직렬화) (AC: 2)**
  - [x] `class MailboxLockRegistry`: 내부 `dict[str, threading.Lock]` + 등록부 `threading.Lock` 으로 보호. `lock_for(mailbox_id) -> threading.Lock`(같은 id→같은 객체, 다른 id→독립) + context-manager `acquire(mailbox_id)`(획득·`finally` 해제). 같은 mailbox 직렬·다른 mailbox 병렬. 4.5/4.6 등록부 lock 패턴 동형(재발명 0 — `threading` stdlib). [Source: src/rider_agent/browser_profile.py(208), src/rider_agent/workers/kakao_sender.py(213)]
  - [x] lock 직렬화를 **실 Gmail 없이** 검증할 수 있게: orchestrator 가 lock 을 잡은 채 `recover` 를 호출하는 구조라, 같은 mailbox 두 호출이 겹치지 않음(주입 `recover` 가 진입/이탈을 기록 → 순서 단언)을 짧은 thread/배리어로 확인. [Source: src/rider_agent/workers/kakao_sender.py(FIFO 직렬 테스트 패턴)]
- [x] **Task 4 — 실패 분류기 `classify_coupang_2fa_outcome` (AC: 3)**
  - [x] `classify_coupang_2fa_outcome(*, recovered: bool | None = None, error: BaseException | None = None, is_reauth: bool | None = None) -> str`: `recovered is True`→`STATE_RECOVERED`; `recovered is False`→`STATE_USER_ACTION_REQUIRED`(CAPTCHA/이상 로그인 — reuse 가 자동 복구 불가); `is_reauth is True`(또는 reauth 분류된 `error`)→`STATE_GMAIL_REAUTH_REQUIRED`; 그 외 `error`(transient)→`STATE_RECOVERY_FAILED`. **reauth 판별은 주입 seam**(`is_reauth`)으로 흡수 — 메시지 텍스트 파싱 금지(아래 "열린 질문" — concrete 타입 분류는 reuse re-export 또는 주입 predicate 로 일관). [Source: data-api-contract.md(143-144), src/rider_crawl/auth/coupang_email_2fa.py(92-94), src/rider_crawl/auth/gmail.py(287-298), memory/redact-skips-operational-ids]
  - [x] 분류기는 **순수 함수**(부작용 0)이고 secret 을 읽지 않는다(boolean/예외 타입만 본다). 비-`recovered`/비-`error` 모호 입력은 fail-closed(`STATE_RECOVERY_FAILED`). [Source: src/rider_agent/auth/baemin_auth.py(102-124)]
- [x] **Task 5 — bounded 복구 orchestrator `recover_coupang_mailbox` (AC: 2, 3, 4)**
  - [x] `recover_coupang_mailbox(*, mailbox_id, recover, locks=MailboxLockRegistry(), store=None, now=time.time, sleep=time.sleep, max_attempts=DEFAULT_MAX_RECOVERY_ATTEMPTS, backoff_seconds=DEFAULT_RECOVERY_BACKOFF_SECONDS, is_reauth=None, log=None) -> JobResult`: (a) `with locks.acquire(mailbox_id):` 아래에서 (b) `recover()` 를 **bounded** 호출(주입 `now`/`sleep` + `max_attempts` 상한·backoff), (c) `recover` 가 `True`→`make_success_result(result_json={mailbox_ref, state: ACTIVE})`; `False`/예외→`classify_coupang_2fa_outcome` 로 분류해 `make_failure_result(error_code, "<고정 사유>", result_json={mailbox_ref, state, reason}, metrics={reason})`. 상한 소진 시 **반복 인증 요청 0**(마지막 분류 상태로 멈춤). [Source: src/rider_agent/job_loop.py(175-228), src/rider_crawl/platforms/coupang/crawler.py(518-540), src/rider_agent/browser_profile.py(345-392)]
  - [x] `recover` 기본은 reuse `recover_coupang_session_with_email_2fa`(주입·Windows/실 Gmail 게이트) 를 mailbox token/`requested_after`/query 와 함께 부르는 thin wrapper — **OTP 조회·입력·요청시각 컷오프·query 필터는 reuse 가 수행**(4.9 재구현 0). 테스트는 항상 fake `recover` 주입(실 Gmail/실 화면 0). [Source: src/rider_crawl/auth/coupang_email_2fa.py(76-124), src/rider_agent/reuse.py(47·77-78)]
  - [x] **🚨 회귀 트랩(반드시 처리 — 가장 위험한 숨은 버그) — per-mailbox token 경로 미배선 = token 공유 회귀.** reuse 기본 fetch 는 **단일 공유 파일** `config.gmail_token_path`(기본 `secrets/google/token.gmail.json`, config.py:67·22)를 읽는다. `recover` 를 미배선된 reuse 기본으로 두면 **모든 고객이 같은 token 파일을 공유**해 AC1/ADD-15(고객 간 token 비공유)를 **운영에서 위반**한다 — 그런데 **fake 주입 테스트는 전부 통과**해 회귀가 숨는다. 반드시 `mailbox_id` 로 token 경로/credentials 를 파생(예: `dataclasses.replace(config, gmail_token_path=Path(f"...token.{mailbox_id}.gmail.json"))` 또는 주입 token-path-resolver/`build_service`)해 두 mailbox 가 다른 파일을 읽게 한다. `AppConfig` 는 `@dataclass(frozen=True)` 라 `dataclasses.replace` 로 0줄-소스-변경 배선 가능. **테스트 통과는 격리 증명이 아니다 — 두 mailbox 의 token 경로 분기(서로 다른 path)를 명시 단언하라.** [Source: src/rider_crawl/config.py(22·67), src/rider_crawl/auth/gmail.py(33-58·278-282), operations-security-test-contract.md(92), data-api-contract.md(139-141)]
  - [x] **민감값 0**: result_json·metrics·log 에 `mailbox_id` **ref**·평문 상태·고정 사유만 — OTP/token/refresh/비밀번호/full email 0. reuse 예외 본문 통째 forwarding 금지(`make_failure_result` 가 `redacted_error_event` 로 마스킹하되, raw 식별자는 처음부터 안 넣음). [Source: operations-security-test-contract.md(15·93), src/rider_agent/job_loop.py(187-228), memory/redact-skips-operational-ids]
- [x] **Task 6 — 테스트: `tests/agent/test_coupang_gmail_2fa.py` (AC: 1~10)** — 외부 호출 없음(fake `recover`/`store`(fake codec)/`now`/`sleep`), 가짜 mailbox_id/token-ref 만:
  - [x] **위치/네이밍:** `tests/agent/test_coupang_gmail_2fa.py`(평면, `__init__.py` 미추가 — 4.1~4.8 미러). `rider_agent.__main__` 을 **모듈 top 에서 import 금지**(필요 시 함수 내부 defer — runpy 경고 회피). [Source: memory/agent-main-runpy-warning, tests/agent/ 목록]
  - [x] **(AC1 — token 분리):** `store_mailbox_token`/`resolve_mailbox_token` round-trip(fake codec `DpapiSecretStore` + `tmp_path`); 두 다른 mailbox_id→다른 ref·교차 resolve 시 다른 token; 미저장 mailbox→`None`(fail-closed); 반환/캡처에 평문 token 0(ref 만); ref 가 평문 이메일을 담지 않음(opaque/hashed). [Source: tests/agent/test_secure_store.py 패턴, src/rider_agent/secure_store.py(164-191)]
  - [x] **(AC1 — 회귀 트랩 명시 단언):** 기본 `recover` 배선이 두 다른 mailbox_id 에 **서로 다른 token 경로/credentials** 를 쓰는지 단언한다(예: 주입 spy `fetch_code`/token-path-resolver 가 받은 path 가 mailbox 간 분기). 같은 공유 `token.gmail.json` 으로 떨어지면 실패해야 한다 — token 공유 회귀를 fake 통과 뒤에 숨기지 않는다. [Source: src/rider_crawl/config.py(67), data-api-contract.md(139-141), operations-security-test-contract.md(92)]
  - [x] **(AC2 — lock 직렬화):** 같은 mailbox_id 두 동시 복구가 겹치지 않음(주입 `recover` 가 진입/이탈 타임스탬프·active-count 기록 → 최대 동시 1 단언, 짧은 thread/barrier); 다른 mailbox_id 는 병렬 허용; `lock_for(id)` 가 같은 id 에 같은 객체 반환. [Source: src/rider_agent/workers/kakao_sender.py 직렬 테스트]
  - [x] **(AC3 — 분류기):** `recovered=True`→`ACTIVE`; `recovered=False`→`USER_ACTION_REQUIRED`; `is_reauth=True`(또는 reauth `error`)→`GMAIL_REAUTH_REQUIRED`; transient `error`→`RECOVERY_FAILED`. 분류기가 secret 미접근(boolean/타입만). [Source: data-api-contract.md(143-144)]
  - [x] **(AC3·AC4 — 누출 가드):** fake `recover` 가 OTP/token/refresh/비밀번호를 예외/반환에 실어도 result_json·metrics·error_message_redacted·log 캡처에 **0건**(orchestrator 는 ref/상태/고정 사유만 싣음). fixture 는 가짜 `mailbox-fake-1`/`gmail:mailbox-fake-1`/`otp-fake-…`. [Source: operations-security-test-contract.md(15·93), project-context.md(81), 4-8 스토리(93)]
  - [x] **(AC4 — bounded·반복 인증 요청 0):** `recover` 항상 `False`/예외 + 상한 → `recover` 호출 횟수 ≤ `max_attempts` 단언(무한 재시도 0), 결과가 분류 상태 + `error_code` + `metrics.reason` 로 운영 표면에 남음; 주입 `now`/`sleep` 로 backoff 결정적. `recover` 가 N회째 `True` → 성공·이후 호출 0. [Source: src/rider_agent/browser_profile.py(71-73·365-392)]
  - [x] **(import-safety·단방향):** `import rider_agent.auth.coupang_gmail_2fa` 가 비-Windows 에서 성공(실 DPAPI/실 Gmail 미로드); 모듈 third-party root == `rider_crawl` 만; `rider_server` import 0; async 0(4.1 가드가 자동으로 잡지만 명시 케이스 1개). [Source: tests/agent/test_agent_package.py(188-245·277-287)]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~10)**
  - [x] 운영 venv 로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기존 통과가 **하나도** 안 깨지고(특히 `tests/agent/test_agent_package.py`·`test_secure_store.py`·`test_baemin_auth.py`·`test_job_loop.py`·`tests/test_gmail_2fa.py`·`test_coupang_email_2fa.py`), 신규 케이스만큼만 증가가 정상. [Source: project-context.md(53·75), memory/dev-env-quirks]
  - [x] **4.1 가드 green 재확인:** `pytest tests/agent/test_agent_package.py -q` 의 (a) third-party root == `{rider_crawl}`, (b) sync, (c) 단방향(`rider_server` 0), (d) pyproject deps **정확히 9개·핀 불변**, (e) reuse seam import-safe(`googleapiclient` 미로드)가 **신규 모듈 추가 후에도 통과**. `reuse.py` 에 error 타입 additive re-export 를 택했다면 `test_reuse_all_names_are_resolvable`·`test_reuse_seam_is_import_safe_no_heavy_deps` 가 새 이름 자동 커버. [Source: tests/agent/test_agent_package.py(206-225·277-319)]
  - [x] **enum/lock 무회귀:** 쿠팡 2FA 상태·error_code·사유는 평문 상수(새 enum/"정확히 N" lock 0). `rider_server` 도메인 enum(`BaeminAuthState` 7·`FailureCategory` 7) 무회귀(import 0). `USER_ACTION_REQUIRED`(쿠팡) 를 `USER_ACTION_PENDING`(배민) 과 혼동하지 않음. [Source: memory/enum-member-count-locks, src/rider_server/domain/states.py(45-59·165-187)]
  - [x] **무변경 확인:** `git diff -w --stat` 에 **신규 `src/rider_agent/auth/coupang_gmail_2fa.py` + 신규 `tests/agent/test_coupang_gmail_2fa.py` + sprint-status/스토리**(+택1: `reuse.py` re-export 2줄)만 보이고 **기존 `rider_crawl/`·`rider_server/` 소스·`pyproject.toml`·`job_loop.py`·`secure_store.py`·`baemin_auth.py` 0줄 변경**임을 확인(CRLF/LF 노이즈 무시 — `git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 raw OTP/token/refresh/비밀번호/full email/평문 mailbox 주소 0건(fake·ref 만), `coupang_gmail_2fa.py` 에 `rider_server` import 0·async 0, `src/rider_crawl/` 에 `rider_agent` import 신규 0. [Source: project-context.md(64·81), operations-security-test-contract.md(92-93)]
  - [x] 변경 파일을 File List 에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record 에 적는다(dev 노트에 잠정 수치 박지 말 것 — 4.1~4.8 에서 stale 수치 MEDIUM 재발: qa-e2e 가 dev 노트 뒤에 케이스를 append). [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리 산출물: **신규 `src/rider_agent/auth/coupang_gmail_2fa.py` + 신규 `tests/agent/test_coupang_gmail_2fa.py`** 뿐. **기존 `rider_crawl/`·`rider_server/` 소스·`pyproject.toml`·`job_loop.py`·`secure_store.py`·`heartbeat.py`·`browser_profile.py`·`baemin_auth.py`·`auth/__init__.py` 0줄 변경.** (단 하나의 허용 예외 = 아래 "열린 질문"의 `reuse.py` error-타입 additive re-export 2줄을 택할 경우 — 그 외에는 0줄.)
- **건드리지 않는다:** 배민 auth(4.8 — 정반대 정책), 실제 `CRAWL_COUPANG` 수집 워커(`workers/crawl_worker.py` — 미존재), 서버 측 OAuth onboarding/`gmail_reauth_required_count` 알림/Admin 인증 화면(Epic 5), Gmail OTP 조회·세션 복구·요청시각 컷오프·query 필터·코드 파싱의 `rider_crawl` 신규 추가(reuse 가 이미 가짐). **빈 stub 워커/빈 GUI 파일도 만들지 않는다.** [Source: epics.md Story 4.9(877-902), architecture.md(453-454·456), operations-security-test-contract.md(31)]

### 열린 질문 / 의도된 부분 구현 (반드시 읽을 것)

- **reauth(Gmail refresh 실패/grant 취소) 판별 = 주입 seam 권장, concrete 타입은 reuse re-export(택1·일관).** reuse `recover_coupang_session_with_email_2fa` 는 CAPTCHA/이상 로그인을 **`False` 반환**(→`USER_ACTION_REQUIRED`)으로, Gmail 조회 불가를 **`Coupang2faError`(←`Gmail2faError`)** 로 표면화한다. 그런데 `Gmail2faError` 는 "refresh 실패/grant 취소"(→`GMAIL_REAUTH_REQUIRED`)와 "인증메일 지연/코드 미추출"(transient)을 **같은 타입·다른 고정 메시지**로 낸다(gmail.py:91-94·135-138·287-298) — **메시지 텍스트 파싱은 금지**(fragile·운영 식별자 한계). 권장: 분류기에 **주입 predicate `is_reauth: Callable[[BaseException], bool] | None`** 을 두고, 실 binding(어떤 예외가 reauth 인지)은 운영/Epic 5(실 OAuth/credentials 소유)가 주입한다(4.8 가 real probe 를 주입 placeholder 로 이월한 선례와 동형). 정 concrete 타입이 필요하면 **`reuse.py` 에 `Gmail2faError`/`Coupang2faError` re-export 2줄**(reuse 는 4.1 이 "후속 워커 auth 4.8·4.9" 용으로 설계한 seam, line 1-7·46-48; re-export-only·비행위적·`test_reuse_all_names_are_resolvable` 자동 커버)만 추가하고 `isinstance` 분류한다 — **둘 중 하나로 일관**하고 메시지 파싱은 어느 쪽도 안 한다. [Source: src/rider_crawl/auth/gmail.py(287-298·135-138), src/rider_agent/reuse.py(1-7·46-48), tests/agent/test_agent_package.py(316-319), src/rider_agent/auth/baemin_auth.py(127-156, placeholder 이월 선례)]
- **per-mailbox token → reuse `fetch_latest_verification_code(token_path=...)` 배선.** reuse fetch 는 **파일 경로**(`credentials_path`/`token_path`)를 받는데, 4.9 의 mailbox 분리는 **DPAPI store 의 ref** 다. MVP 권장: store 는 **ref(서버가 보는 불투명 핸들)** 을 보유하고, 실제 OAuth token **파일 경로는 `mailbox_id` 로 파생**(예: `secrets/google/token.{mailbox_id}.gmail.json`)해 두 고객이 `token.gmail.json` 을 **공유하지 않게** 한다 — 또는 store-resolved token 으로 `build_service`/`fetch_code` 를 주입한다. 정밀 OAuth onboarding 배선(token 파일 생성/갱신 위치)은 **Epic 5/운영 소유** — 본 스토리는 `recover`/`fetch_code`/token-path-resolver 를 **주입 가능**하게 두고 테스트는 fake 로 검증한다(실 token 파일 0). `rider_crawl` 0줄 유지. [Source: src/rider_crawl/auth/gmail.py(33-58·267-302), src/rider_crawl/auth/coupang_email_2fa.py(131-158), data-api-contract.md(139-140)]
- **`recover` 호출 단위 = job 1건 안의 bounded 시도(반복 인증 요청 금지의 정본).** 기존 crawler 정책은 **crawl 1회당 복구 1회**(`_try_recover_coupang_session` = 1회 시도, 실패면 `BrowserActionRequiredError` 로 탭 중지 — crawler.py:518-540·config.py:20). 4.9 는 이 "1회(또는 작은 N회) 후 멈춤" 을 Agent-job 레이어에서 `max_attempts`(기본 `DEFAULT_MAX_RECOVERY_ATTEMPTS`, 작게)로 재현한다. **job 재시도(서버 재스케줄) 자체의 상한은 lease/scheduler(4.4·Epic 5) 소유** — 본 스토리는 한 job 안에서 반복 인증 요청을 안 하는 것까지 보장한다. [Source: src/rider_crawl/platforms/coupang/crawler.py(518-540), src/rider_crawl/config.py(20·65), src/rider_agent/browser_profile.py(71-73)]
- **새 heartbeat capability/job type 불필요.** 쿠팡 2FA 복구는 **별도 job type 이 아니라 `CRAWL_COUPANG` 수집 흐름 안에서** 세션 만료 시 일어난다(기존 crawler 가 crawl 중 recover 호출 — crawler.py:543-546). 따라서 4.9 는 `build_execute_job` 라우터(4.6/4.8)를 **만들지 않고** mailbox 분리·lock·분류·bounded-stop **primitive** 만 제공한다 — 미래 `CRAWL_COUPANG` 워커(`crawl_worker.py`, 미존재)가 이를 소비한다(4.8 이 `CRAWL_BAEMIN` 워커 없이 분류기/실행자 primitive 만 낸 것과 동형). [Source: src/rider_agent/heartbeat.py(62-77), src/rider_crawl/platforms/coupang/crawler.py(543-546), src/rider_agent/job_loop.py(234-249)]

### 설계 결정 — 무엇을 재사용하고 무엇이 신규인가 (반드시 읽을 것)

- **OTP 조회/세션 복구 = reuse 소비(재구현 0, 배민과 정반대).** `gmail.py` 는 "요청시각 이후만·최신 메일만·유일 N자리만·코드/토큰 미로깅"(gmail.py:97-138·238-264·29-30)을, `coupang_email_2fa.py` 는 "이메일 인증 선택→발송→코드 입력→제출, CAPTCHA/비번화면 시 `False`, 발송 직전 −30s 안전여유"(coupang_email_2fa.py:76-124·110-114)를 **이미 검증된 형태로** 가진다. 4.9 는 이 reuse 표면(이미 `reuse.py:47-48` 노출·4.9 용 pre-commit)을 **호출만** 하고 그 위에 mailbox 분리·lock·분류·bounded-stop 을 얹는다. [Source: src/rider_agent/reuse.py(46-48·77-78), src/rider_crawl/auth/gmail.py(97-138), src/rider_crawl/auth/coupang_email_2fa.py(76-124)]
- **mailbox token store = 4.2 DpapiSecretStore 백엔드 재사용(새 crypto 0).** `DpapiSecretStore.put/resolve` 는 DPAPI 암호화·atomic write·결정적 ref·멱등 쓰기·fail-closed `resolve→None`·손상 blob 무시를 이미 보장한다(secure_store.py:164-207). 4.9 는 ref 를 `mailbox_id` 로 keying 하는 thin helper 만 추가한다 — `LocalFileSecretStore`/`SecretStore` Protocol 도 그대로(2.4 seam). 서버는 ref 만(NFR-8). [Source: src/rider_agent/secure_store.py(141-207·44-48), src/rider_crawl/secret_store.py(42-52)]
- **mailbox lock = `threading.Lock` 등록부(4.5/4.6 패턴 동형).** `MailboxLockRegistry` 는 `browser_profile` 등록부(`threading.Lock` 보호 dict, browser_profile.py:208)·`kakao_sender` worker lock(kakao_sender.py:213)과 같은 구조다 — `mailbox_id` 별 lock 으로 같은 메일함 직렬·다른 메일함 병렬. lock scope 가 `mailbox_id` 인 점은 4.5 CDP lock scope(`cdp_url`)·project-context 의 "scope key 를 줄이면 다른 탭/계정 혼선"(92) 규율과 정합. [Source: src/rider_agent/browser_profile.py(208), src/rider_agent/workers/kakao_sender.py(182·213), project-context.md(92)]
- **결과/이벤트 = `job_loop` 헬퍼 재사용(redact 자동).** `make_success_result`/`make_failure_result`(`redacted_error_event` 로 raw/OTP/secret 마스킹)/`make_job_event`(`redact` 통과)를 그대로 쓴다 — 중복 마스킹 로직 신설 0. error_code 는 평문 분류 상태명. [Source: src/rider_agent/job_loop.py(175-228)]
- **상태 어휘 = spec data-api-contract 정본 평문(rider_server import 0).** `USER_ACTION_REQUIRED`/`GMAIL_REAUTH_REQUIRED` 는 data-api-contract(143-144)의 정본 문자열이고 `rider_server` enum 에 없다(배민 `USER_ACTION_PENDING` 과 다름). 4.5 `STATE_AUTH_REQUIRED`·4.8 `AUTH_STATE_*` 처럼 **값만 평문 상수로** 두고 단방향 가드를 지킨다. [Source: data-api-contract.md(143-144), src/rider_server/domain/states.py(45-59·56), src/rider_agent/auth/baemin_auth.py(67-86)]

### 재사용 대상 공개 표면 (재구현 금지 — import/주입만)

| 도메인 | 공개 심볼 | 파일/행 | 4.9 사용 |
|---|---|---|---|
| 쿠팡 세션 복구(OTP 자동) | `recover_coupang_session_with_email_2fa` | rider_agent/reuse.py(47·77)←coupang_email_2fa.py(76) | bounded orchestrator 의 기본 `recover`(주입·Gmail 게이트) — **소비** |
| Gmail 인증번호 조회 | `fetch_latest_verification_code` | rider_agent/reuse.py(48·77)←gmail.py(33) | 요청시각/query/최신메일 필터 — reuse 가 수행(4.9 재구현 0) |
| Gmail/복구 예외 타입 | `Gmail2faError`, `Coupang2faError` | gmail.py(29)·coupang_email_2fa.py(127) | reauth 분류용(**주입 predicate 권장**, 또는 reuse re-export 2줄 — 열린 질문) |
| Agent-local 암호화 store | `DpapiSecretStore(put/resolve)`, `default_secret_store_path`, `SecretStore` | rider_agent/secure_store.py(141-207·74), rider_crawl/secret_store.py(42) | mailbox 별 token 분리 저장(재발명 0) |
| secret 저장 분류 | `classify_secret_storage`, `SECRET_STORAGE_AGENT_LOCAL`, `SECRET_STORAGE_NOT_STORED` | rider_crawl/secret_store.py(23-39) | gmail_oauth_token=agent_local·otp=not_stored 정책 정합 |
| job 결과 | `make_success_result`, `make_failure_result`, `ClaimedJob`, `JobResult` | rider_agent/job_loop.py(103-228) | orchestrator 결과(redact 자동·무변경) |
| redaction | `redact`, `redacted_error_event` | rider_crawl/redaction.py(130·248) | 자유 텍스트 마스킹(직접 import — secure_store/job_loop 선례) |
| 직렬 lock | `threading.Lock` (stdlib) | — | `MailboxLockRegistry`(4.5/4.6 등록부 패턴) |
| 상태 값(정합용) | `BaeminAuthState`(7)·`FailureCategory`(7) | rider_server/domain/states.py(45-59·165-187) | **값 비교만, import 0** — 평문 상수 정합/혼동 회피 |

- **주의 — 단방향 import:** `rider_server` 를 `coupang_gmail_2fa.py` 가 import 하면 `test_rider_agent_never_imports_rider_server` 가 깨진다. 상태/오류는 `rider_agent` 안 **평문 상수**로 두고 `rider_server` 를 import 하지 않는다. [Source: tests/agent/test_agent_package.py(240-245), memory/enum-member-count-locks]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **기존 `rider_crawl`/`rider_server` 소스·`pyproject.toml` 무변경** — `git diff -w` = 신규 `coupang_gmail_2fa.py` + 신규 테스트 + sprint-status/스토리 (+택1 reuse re-export 2줄).
- (b) **OTP/Gmail 로직 reuse 소비, 재구현 0** — 요청시각 컷오프·query 필터·최신 메일·코드 파싱은 reuse 가 수행(4.9 가 다시 짜지 않음).
- (c) **고객 간 token 비공유** — mailbox 마다 다른 ref/token, 교차 resolve 0(ops:92).
- (d) **같은 mailbox 직렬·다른 mailbox 병렬** — `MailboxLockRegistry` scope = `mailbox_id`.
- (e) **민감값 0 노출** — OTP/token/refresh/비밀번호/full email 이 로그·예외·result·metrics·event 0(NFR-5·ops:93). ref·평문 상태·고정 사유만.
- (f) **조치 가능 분류** — CAPTCHA/이상→`USER_ACTION_REQUIRED`, refresh/grant→`GMAIL_REAUTH_REQUIRED`, transient→recovery_failed(NFR-16).
- (g) **bounded·반복 인증 요청 금지** — `max_attempts` 상한, 상한 소진 시 멈춤(NFR-4·기존 탭 중지 정책).
- (h) **단방향·sync·새 의존 0** — stdlib(+`rider_crawl`)만, async 0, `rider_server` 0, deps 정확히 9개 → 4.1 가드 green(`rglob` 자동 검사).
- (i) **import-safety** — 실 Gmail/실 DPAPI/실 시계는 함수 내부 lazy·주입. `import rider_agent.auth.coupang_gmail_2fa` 가 `googleapiclient`/`crypt32` 를 끌지 않는다.
- (j) **enum/상태 lock 무회귀** — 평문 상수("정확히 N" lock 0), `rider_server` enum(`BaeminAuthState` 7·`FailureCategory` 7) 무회귀, `USER_ACTION_REQUIRED`≠`USER_ACTION_PENDING`.
[Source: project-context.md(64·81·82·89·92), operations-security-test-contract.md(15·92-93), data-api-contract.md(139-144), tests/agent/test_agent_package.py(188-318)]

### 이전 스토리/회고 인텔리전스 (4.1~4.8 → 4.9 이월 교훈)

- **4.8 과 같은 `auth/` 패키지·정반대 정책(직접 대조):** 4.8 `baemin_auth.py` 는 OTP 자동화를 **금지**(AST 부정 가드로 `fetch_latest_verification_code`/`pyautogui` import 0)했다. 4.9 `coupang_gmail_2fa.py` 는 **정반대로 그 reuse 를 소비**한다 — 같은 패키지 안에서 두 모듈이 정반대 정책을 갖는다(배민=사람 개입, 쿠팡=자동 복구). 4.9 는 AST 부정 가드를 **두지 않는다**(쿠팡은 OTP 조회가 정상). 대신 mailbox 분리·lock·분류·bounded-stop 으로 안전을 보장한다. [Source: src/rider_agent/auth/baemin_auth.py(27-31), data-api-contract.md(133·135-144)]
- **4.2 DpapiSecretStore 위에 빌드(직접 계승):** 4.2 가 `put`/`resolve`/결정적 ref/atomic/fail-closed 를 이미 구현했다(secure_store.py:141-207). 4.9 는 ref 를 `mailbox_id` 로 keying 하는 helper 만 추가 — 새 store/crypto 발명 0. `test_secret_store.py`/`test_secure_store.py` 의 fake codec·`tmp_path` 패턴을 그대로 쓴다. [Source: src/rider_agent/secure_store.py(141-207), tests/agent/test_secure_store.py]
- **enum/lock 전수 점검(memory):** 쿠팡 2FA 상태/오류/사유를 enum 이나 "정확히 N개" 테스트로 잠그면 `rider_server` 도메인 lock·후속이 깨질 수 있다 → **평문 상수**로 두고 count-lock 0. `USER_ACTION_REQUIRED`(쿠팡)를 `USER_ACTION_PENDING`(배민 enum)과 **혼동 금지** — 둘은 다른 상태머신이다. [Source: src/rider_server/domain/states.py(45-59·56), memory/enum-member-count-locks]
- **redact 는 운영 식별자를 못 가린다(4.5·4.6·4.8 핵심 교훈):** 자유 텍스트 `redact()` 는 mailbox/customer/center 명을 마스킹 못 한다 → 결과/로그/사유에 raw 식별자·reuse 예외 본문을 **처음부터 넣지 않고** 고정 사유 상수 + `mailbox_id` **ref** 만 쓴다. [Source: src/rider_agent/workers/kakao_sender.py(357-375), src/rider_agent/auth/baemin_auth.py(216-227), memory/redact-skips-operational-ids]
- **테스트 수치/File List 단일 정본(A2″ 계승):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. 4.1~4.8 모두 qa-e2e append 후 stale 로 MEDIUM 이 났다. [Source: epic-3-retro-2026-06-13.md(59), memory/stale-test-count-a2]
- **runpy 경고(memory):** 테스트가 `rider_agent.__main__` 을 모듈 top 에서 import 하면 runpy RuntimeWarning. `test_coupang_gmail_2fa.py` 는 `__main__` top-import 하지 말고 `auth.coupang_gmail_2fa`/`job_loop`/`secure_store` 심볼만 import. [Source: memory/agent-main-runpy-warning]
- **agent job-type 어휘(memory):** heartbeat 의 capability/job-type 은 평문 문자열 상수. 4.9 는 새 capability 를 안 만들지만, 쿠팡 복구가 `CRAWL_COUPANG`(heartbeat.py:63) 흐름에 속함을 인지한다. [Source: memory/agent-job-type-vocab, src/rider_agent/heartbeat.py(63)]

### 개발 환경 / 실행 (memory: dev-env-quirks)

- pytest 는 **WSL `python3` 가 아니라 운영 venv `.venv/Scripts/python.exe -m pytest`** 로 돌린다(WSL python 엔 pytest 미설치). 설정은 `pyproject.toml`(`pythonpath=["src"]`, `testpaths=["tests"]`). [Source: project-context.md(53·75), memory/dev-env-quirks]
- git 트리는 **CRLF/LF 노이즈**가 있으므로 범위 점검은 `git diff -w` 로 하고 무관한 EOL flip 을 되돌리지 않는다. [Source: memory/dev-env-quirks, project-context.md(82)]
- **2FA 테스트 주의:** 실제 Gmail API/실 쿠팡 인증 화면/실 DPAPI/실 token 파일/실 시계를 쓰지 말고 **주입 fake `recover`/`fetch_code`/`store`(fake codec)/`now`/`sleep` + 호출 인자·횟수·동시성 캡처**로 분리·lock·분류·bounded-stop 을 결정적으로 검증한다(OS-오염/flaky/hang 방지). 비-Windows CI 에서도 통과해야 한다(import-safety). lock 직렬화 테스트는 짧은 thread/barrier + active-count 단언으로 결정적이게 만든다. [Source: src/rider_agent/secure_store.py(80-99 codec 주입), tests/test_gmail_2fa.py(fake service), tests/test_coupang_email_2fa.py(fake page), 4-8 스토리(170)]

### Project Structure Notes

- 신규 파일은 architecture.md(456) 트리와 정렬: `src/rider_agent/auth/`(= `# 배민 auth open, Gmail mailbox lock`) — `auth/__init__.py` docstring(7) 이 이미 "4.9 가 같은 패키지에 쿠팡 Gmail mailbox lock 을 추가한다"고 forward-commit 했다. **계획된 신설이지 이탈이 아니다.** [Source: architecture.md(456), src/rider_agent/auth/__init__.py(7)]
- 테스트는 `tests/agent/test_coupang_gmail_2fa.py`(평면, `__init__.py` 미추가). 기존 `tests/agent/test_{agent_package,registration,secure_store,heartbeat,job_loop,browser_profile,kakao_sender,autostart,baemin_auth}.py` 와 별 basename. [Source: architecture.md(461), tests/agent/ 목록]
- **변이/충돌:** `project-context.md` 의 `rider_agent` 진전 반영(쿠팡 Gmail 2FA mailbox 분리·lock)은 **Epic 4 retro** 에서 한다(rider_server 를 Epic 2 retro 에서 반영한 선례). 본 스토리에서 project-context.md 는 수정하지 않는다. [Source: 4-8 스토리(176), project-context.md(114)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.9(877-902)] — user story + AC(token 고객/mailbox 분리·서버 ref·고객 간 비공유; mailbox lock 동시 처리 차단·요청시각 이후·from/subject/query/customer 필터; OTP/token/refresh/비밀번호 미로깅·CAPTCHA→USER_ACTION_REQUIRED·refresh/grant→GMAIL_REAUTH_REQUIRED; 반복 실패 시 탭 중지 기존 정책 유지).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-19·NFR-4·NFR-5·NFR-16·ADD-15] — Gmail 2FA 고객/메일함/token 분리 + mailbox lock; 무한 재시도 금지; secret 비노출; 인증 실패 조치 가능 분류; 우회/공유 금지.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md#Coupang-Gmail-2FA(135-144)] — OAuth onboarding(mailbox_id 단위 token)·Token storage(Agent-local DPAPI, 서버 ref 만)·Mail search(요청시각 이후 + from/subject/query/customer)·Mailbox lock(같은 mailbox_id 동시 처리 금지)·CAPTCHA/abnormal→USER_ACTION_REQUIRED·Refresh failure/revoked→GMAIL_REAUTH_REQUIRED.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#(8-9·15·31·80·92-93)] — Gmail OAuth token Agent-local DPAPI·customer/mailbox isolation; never log password/token/refresh/OTP/full phone/email; `gmail_reauth_required_count`; Mailbox-level token isolation·lock·query filtering; **forbidden: share Gmail token between customers / store secret in logs/DB/screenshots/config/errors**.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#(11)] — "Coupang Gmail 2FA logic: Add customer/mailbox token isolation, mailbox lock, and restricted-scope handling."
- [Source: src/rider_agent/reuse.py(46-48·77-78)] — `recover_coupang_session_with_email_2fa`·`fetch_latest_verification_code` re-export(4.9 용 pre-commit) — **소비 대상**.
- [Source: src/rider_crawl/auth/gmail.py(26·29-30·33-94·97-138·238-264·267-302)] — `GMAIL_READONLY_SCOPE`·`Gmail2faError`(코드/토큰 미수록)·`fetch_latest_verification_code`(요청시각 이후·최신 메일만)·코드 파싱·OAuth refresh(실패→재승인 예외).
- [Source: src/rider_crawl/auth/coupang_email_2fa.py(76-124·92-94·110-114·127-128·148-153)] — `recover_coupang_session_with_email_2fa`(CAPTCHA/비번화면→False·−30s 안전여유)·`Coupang2faError`(코드/토큰 미수록).
- [Source: src/rider_crawl/platforms/coupang/crawler.py(493·518-546)] — 기존 정책: crawl 1회당 복구 1회, 실패면 탭 중지(`BrowserActionRequiredError`)·`coupang_auto_email_2fa_enabled` 게이트.
- [Source: src/rider_crawl/config.py(20·65-72)] — "로그인 만료 시 탭 중지" 기존 동작; `gmail_credentials_path`/`gmail_token_path`/`gmail_2fa_query`/`gmail_2fa_poll_*`/`coupang_2fa_code_digits`/`coupang_auto_email_2fa_enabled`.
- [Source: src/rider_agent/secure_store.py(44-48·141-207·74-75)] — `DpapiSecretStore`(put/resolve·결정적 ref·atomic·fail-closed)·`default_secret_store_path`·`AGENT_TOKEN_REF` 선례.
- [Source: src/rider_crawl/secret_store.py(23-39·42-52·70-80)] — `SecretStore` Protocol·`classify_secret_storage`(gmail_oauth_token=agent_local·otp=not_stored)·결정적 ref.
- [Source: src/rider_agent/job_loop.py(103-228·234-249)] — `ClaimedJob`/`JobResult`·`make_success_result`/`make_failure_result`(`redacted_error_event`)·`default_execute_job`.
- [Source: src/rider_agent/browser_profile.py(71-73·208·345-392), src/rider_agent/workers/kakao_sender.py(182·213)] — bounded 재시작 상수·`threading.Lock` 등록부 패턴(=`MailboxLockRegistry` 동형).
- [Source: src/rider_agent/auth/baemin_auth.py(1-45·67-96·102-124), src/rider_agent/auth/__init__.py(1-14)] — 4.8 모듈 구조/평문 상수/분류기 패턴·정반대 정책·`auth/` 패키지 forward-commit.
- [Source: src/rider_server/domain/states.py(45-59·56·165-187)] — `BaeminAuthState`(7, `USER_ACTION_PENDING` 포함)·`FailureCategory`(7) — **값 비교만(import 0)**·쿠팡 `USER_ACTION_REQUIRED` 와 혼동 회피.
- [Source: tests/agent/test_agent_package.py(188-245·277-319)] — 4.1 가드(sync·third-party root==rider_crawl·단방향·deps 9핀·reuse import-safe·`__all__` 해석) — 신규 모듈 자동 적용·green 유지.
- [Source: _bmad-output/implementation-artifacts/4-8-...md(19·27-39·160-164), epic-3-retro-2026-06-13.md(59·158)] — 정반대 정책 대조·AST 가드·평문 상수·수치 단일 정본·`rider_crawl` 0줄.
- [Source: _bmad-output/project-context.md(53·64·75·81·82·89·92·114)] — pytest 실행·단방향 import·누출 금지·git diff·쿠팡 자동복구 실패 시 탭 중지·scope key 혼선·retro 반영.
- [Source: memory/dev-env-quirks, memory/stale-test-count-a2, memory/enum-member-count-locks, memory/agent-main-runpy-warning, memory/redact-skips-operational-ids, memory/agent-job-type-vocab] — venv pytest·`git diff -w`, 수치 단일 정본, enum/lock 전수 점검, `__main__` runpy 경고, redact 운영 식별자 한계, 평문 job-type 어휘.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 신규 테스트 단독 실행: `.venv/Scripts/python.exe -m pytest tests/agent/test_coupang_gmail_2fa.py -q` → 38 passed.
- 전체 회귀 스위트: `.venv/Scripts/python.exe -m pytest -q` → 1316 passed, 0 failed (4.1 가드 green: third-party root==`rider_crawl`·sync·단방향 `rider_server` 0·deps 정확히 9개·reuse seam import-safe).
- 범위 점검: `git diff -w --stat` = sprint-status(상태 전이만) 변경 + 신규 `coupang_gmail_2fa.py`·`test_coupang_gmail_2fa.py`·스토리 파일. 기존 `rider_crawl/`·`rider_server/`·`pyproject.toml`·`job_loop.py`·`secure_store.py`·`baemin_auth.py`·`reuse.py` **0줄 변경**.

### Completion Notes List

- **신규 primitive 모듈 `src/rider_agent/auth/coupang_gmail_2fa.py`** (4.8 `baemin_auth.py` 와 같은 `auth/` 서브패키지, 순수 동기·단방향·import-safe). 4종 primitive 제공:
  1. **mailbox 별 token 분리 helper** — `mailbox_token_ref`(opaque sha256 핸들 `gmail:<16hex>`, 평문 이메일 비노출)·`store_mailbox_token`·`resolve_mailbox_token`. 4.2 `DpapiSecretStore` 를 그대로 끼우고(새 crypto/store 재발명 0) ref 만 `mailbox_id` 로 keying — 서버는 ref 만, token bytes 는 store(DPAPI blob)에만. 두 다른 mailbox→다른 ref·교차 resolve 0(AC1, ADD-15·NFR-8).
  2. **`MailboxLockRegistry`** — `mailbox_id` 별 `threading.Lock`(같은 id→같은 객체, 다른 id→독립), `lock_for`/`acquire`(contextmanager·`finally` 해제). 등록부 dict 는 가드 lock 보호(4.5/4.6 패턴 동형). 같은 mailbox 직렬·다른 mailbox 병렬(AC2, FR-19·NFR-16).
  3. **`classify_coupang_2fa_outcome`** — 순수 함수, `recovered=True`→`ACTIVE`/`recovered=False`→`USER_ACTION_REQUIRED`/`is_reauth=True`→`GMAIL_REAUTH_REQUIRED`/그 외→`RECOVERY_FAILED`(fail-closed). bool/예외 타입만 보고 메시지 파싱·secret 접근 0(AC3, NFR-5·16).
  4. **`recover_coupang_mailbox`** bounded orchestrator — lock 아래에서 주입 `recover` 를 `max_attempts`(기본 1, 기존 "crawl 1회당 복구 1회·실패 시 탭 중지" 정책 계승) 상한으로 호출. `False`(CAPTCHA)·reauth 예외는 **즉시 멈춤**(재시도·재요청 0), transient 예외만 backoff 후 상한까지 재시도. 결과는 `make_success_result`/`make_failure_result`(redact 자동) + result_json/metrics 에 **mailbox ref·평문 상태·고정 사유만**(AC4, NFR-4). `build_coupang_recover` 는 reuse `recover_coupang_session_with_email_2fa` 를 `dataclasses.replace(config, gmail_token_path=mailbox_token_path(...))` 로 배선해 **고객 간 token 파일 공유를 막는다**(회귀 트랩).
- **열린 질문 해소 (택1·일관):** reauth 판별은 **주입 predicate `is_reauth: Callable[[BaseException], bool]`** 로 흡수 — `reuse.py` 에 error-타입 re-export 를 **추가하지 않아** `reuse.py` 0줄 변경(둘 중 권장안 채택, 메시지 파싱 어느 쪽도 안 함). 실 binding 은 운영/Epic 5 가 주입(4.8 placeholder 이월 선례 동형).
- **회귀 트랩 명시 단언:** `mailbox_token_path` 가 두 mailbox 에 서로 다른 파일을 주고(공유 `token.gmail.json` 아님), `build_coupang_recover` 가 spy `recover_session` 에 넘기는 `config.gmail_token_path` 가 mailbox 간 분기함을 테스트로 잠금 — token 공유 회귀를 fake 통과 뒤에 숨기지 않음.
- **상태 어휘 = 평문 상수**(spec data-api-contract `USER_ACTION_REQUIRED`/`GMAIL_REAUTH_REQUIRED` 정합). `rider_server` import 0·enum/"정확히 N" lock 0. 쿠팡 `USER_ACTION_REQUIRED` ≠ 배민 `USER_ACTION_PENDING`(테스트로 혼동 방지 확인). `BaeminAuthState`(7)·`FailureCategory`(7) 무회귀.
- **민감값 0 노출:** OTP/token/refresh/비밀번호/평문 mailbox(이메일)가 result_json·metrics·error_message_redacted·log 어디에도 없음(테스트 누출 가드 통과 — reuse 예외 본문 forwarding 0, mailbox 는 해시 ref 만).
- **테스트 수치 (리뷰 시점 재측정 단일 정본):** 신규 `tests/agent/test_coupang_gmail_2fa.py` **38 cases**, 전체 스위트 **1316 passed / 0 failed**. (리뷰 시점 재측정값 — dev 노트의 잠정 30/1308 은 qa-e2e gap pass 가 8건 append 하기 전 수치라 stale 였고, 리뷰에서 38/1316 로 정정.)

### File List

- `src/rider_agent/auth/coupang_gmail_2fa.py` (신규) — mailbox token 분리 helper + `MailboxLockRegistry` + `classify_coupang_2fa_outcome` + bounded `recover_coupang_mailbox` + `build_coupang_recover` primitive.
- `tests/agent/test_coupang_gmail_2fa.py` (신규) — AC1~10 검증(token 분리·회귀 트랩·lock 직렬화/병렬·분류·bounded·누출 가드·import-safety·단방향·sync). 외부 호출 0(전부 주입 fake).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (수정) — 4-9 상태 `ready-for-dev`→`in-progress`→`review`.
- `_bmad-output/implementation-artifacts/4-9-쿠팡-gmail-2fa-메일함-분리와-lock.md` (수정) — Tasks/Subtasks 체크·Dev Agent Record·Change Log·Status.

### Change Log

| 날짜 | 변경 | 작성 |
| --- | --- | --- |
| 2026-06-14 | Story 4.9 구현 — 쿠팡 Gmail 2FA mailbox 분리·lock·분류·bounded 복구 primitive 신설 + 테스트 38건. 기존 `rider_crawl`/`rider_server`/`pyproject.toml`/`reuse.py` 0줄. Status → review. | Dev Agent (claude-opus-4-8) |
| 2026-06-14 | Senior Developer Review (AI) — 적대적 리뷰: AC1~10 전수 검증·구현 정합 확인(CRITICAL/HIGH 0). MEDIUM 1건 자동 수정: dev 노트의 stale 테스트 수치(30 cases/1308) → 리뷰 시점 재측정(38 cases/1316)로 정정. 스코프 경계 무회귀 확인(`git diff -w` = `_bmad-output` 산출물만, tracked 소스 0줄). Status → done. | Review (claude-opus-4-8) |

## Senior Developer Review (AI)

**리뷰어:** Noah Lee · **일자:** 2026-06-14 · **결과:** Approve (CRITICAL 0 / HIGH 0 / MEDIUM 1 자동 수정 / LOW 1 인정)

### 검증 요약

- **AC 전수(1~10):** 모두 IMPLEMENTED. mailbox 별 token 분리(`mailbox_token_ref`/`store_/resolve_mailbox_token`, 해시 핸들 `gmail:<16hex>`), `MailboxLockRegistry`(같은 mailbox 직렬·다른 mailbox 병렬 — 스레드 테스트로 max-active=1·barrier 비-deadlock 검증), `classify_coupang_2fa_outcome`(평문 상태, secret 미접근), bounded `recover_coupang_mailbox`(상한·즉시 멈춤·민감값 0) 모두 동작 확인.
- **Task `[x]` 감사:** 7개 모두 실제 완료. 특히 회귀 트랩(per-mailbox token 경로 미배선 = token 공유) — `build_coupang_recover` 가 `dataclasses.replace(config, gmail_token_path=mailbox_token_path(...))` 로 mailbox 간 경로를 분기하고, `test_build_coupang_recover_wires_distinct_token_path_per_mailbox` 가 spy 로 명시 단언함을 확인(fake 통과 뒤에 회귀가 숨지 않음).
- **계약 정합:** `make_failure_result(error_code, message, *, result_json, metrics)`·`make_success_result(*, result_json, metrics)`·`DpapiSecretStore.put/resolve`·`recover_coupang_session_with_email_2fa(page, config, *, fetch_code=)` 시그니처 모두 실제 소스와 일치(production 경로 깨짐 없음).
- **민감값 0:** orchestrator 가 reuse 예외 본문을 forwarding 하지 않고 고정 사유 상수 + 해시 mailbox ref 만 싣음. 누출 가드 테스트(OTP/oauth/refresh/평문 이메일 0)·성공/실패/log 경로 모두 통과.
- **스코프·단방향·import-safety:** `git diff -w` = `_bmad-output` 산출물만(tracked `rider_crawl`/`rider_server`/`pyproject.toml`/`job_loop`/`secure_store`/`baemin_auth`/`reuse` 0줄). `rider_server` import 0·async 0·third-party root == `rider_crawl`·비-Windows import-safe(`googleapiclient` 미로드) 모두 green. 쿠팡 `USER_ACTION_REQUIRED` ≠ 배민 `USER_ACTION_PENDING` 확인.

### 발견 사항

| # | 심각도 | 내용 | 조치 |
| --- | --- | --- | --- |
| M1 | MEDIUM | Dev Agent Record 테스트 수치 stale — 기록 30 cases/1308, 실측 38 cases/1316(qa-e2e gap pass 가 8건 append). [memory/stale-test-count-a2] | 자동 수정 — Debug Log·Completion Notes·Change Log 정정 완료 |
| L1 | LOW | `recover_coupang_mailbox` 의 `now`/`store` 파라미터가 현재 흐름에서 미사용 | 인정 — docstring 이 Epic 5 배선용 인터페이스 대칭(4.8 `execute_auth_check_job(now=…)` 선례)으로 명시. 미수정 |

### 결론

구현은 견고하다. CRITICAL/HIGH 0, MEDIUM 1건은 문서 수치 정정으로 자동 해소. **Status → done**, sprint-status 동기화.
