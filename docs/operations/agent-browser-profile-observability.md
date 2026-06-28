# Agent Browser Profile Observability

작성일: 2026-06-28
근거: agent-auth-observability-work-order-2026-06-28 Task 2·3A·4

## 목적

운영자가 Agents 관리자 화면과 인증 필요 화면에서 "어떤 target 이 어디에 배정됐는지"와
"지금 그 Agent PC 의 Chrome/CDP 가 어떤 상태인지"를 헷갈리지 않게 한다. 같은 이름
`browser_profiles` 가 두 의미로 쓰여 생기는 혼란을 줄인다.

## 두 가지 `browser_profiles` 의 역할

| 대상 | 의미 | 수명 | 정본 | 화면 라벨 |
| --- | --- | --- | --- | --- |
| DB `BrowserProfile` table | 어떤 target 이 어떤 agent/profile/port 에 **배정**됐는지 (agent/target FK 관계) | 비교적 정적 | 배정 정보 정본 | `DB 배정 정보` |
| heartbeat `capacity_json.browser_profiles` | 지금 에이전트 PC 에서 Chrome/CDP 가 **어떤 상태**인지 | 휘발성 | 런타임 상태 정본 | `heartbeat runtime` / `Chrome 현재 상태` |

- Agents 화면의 "Chrome 현재 상태" 열은 heartbeat 런타임 상태다(`heartbeat runtime` 라벨).
- 인증 필요 화면의 "DB 배정 정보" 열은 DB `BrowserProfile` 기반 배정 정보다.

## 강제 동기화하지 않는다

DB `BrowserProfile`(배정 정보)과 heartbeat `browser_profiles`(런타임 상태)는 **자동으로
강제 동기화하지 않는다**. 둘은 의미와 수명이 다르다.

- 배정은 비교적 정적이고, 런타임 상태는 heartbeat 주기마다 갱신된다.
- 인증 필요 화면에서 DB 배정값과 heartbeat 현재값이 **달라 보이는 것은 정상**일 수 있다
  (예: 배정은 남아 있지만 그 순간 Chrome 이 `AUTH_REQUIRED` 이거나 `UNKNOWN`).
- 두 값을 맞추는 배치를 만들지 않는다. 운영자는 "배정"과 "현재 상태"를 각각 그대로 읽는다.

## heartbeat 진단 필드(Task 3A)

heartbeat `browser_profiles[]` 의 각 항목은 다음 optional 진단 필드를 가질 수 있다.

| field | 예 | 설명 |
| --- | --- | --- |
| `auth_state` | `ACTIVE`, `AUTH_REQUIRED`, `CENTER_MISMATCH`, `UNKNOWN` | 마지막으로 관측된 인증 상태 요약 |
| `last_error_code` | `CDP_UNREACHABLE`, `PARSER_MISSING_DATA`, `CRAWL_TIMEOUT` | 실패/`UNKNOWN` 원인 힌트 |
| `last_probe_at` | `2026-06-28T10:20:30Z` | 이 값을 확인한 시각(ISO-8601 UTC) |

판독 가이드:

- `UNKNOWN` 은 인증 만료 단정이 아니라 fail-safe 다. parser/CDP/timeout 오류는
  `AUTH_REQUIRED` 로 바꾸지 않고 `UNKNOWN` + 원인 `last_error_code` 로 남긴다.
- `last_error_code` 가 원인 파악의 1차 힌트다(`CDP_UNREACHABLE` → CDP/Chrome,
  `PARSER_MISSING_DATA` → 페이지 구조/로그인, `CRAWL_TIMEOUT` → 느림/멈춤).

## 보안 경계

- heartbeat `browser_profiles` 에는 비밀값/원시 경로/URL/HTML/screenshot 을 절대 넣지 않는다.
- 서버 저장 단계(`agent_registry.heartbeat_capacity`)에서 allowlist + 타입/길이/`cdp_port`
  범위(`1..65535`)를 검증한다. 허용 밖 키와 잘못된 값은 저장되지 않는다.
- 화면에 보여주는 값은 반드시 서버에서 allowlist 와 길이 제한을 통과한 값만이다
  (`dashboard_repository_postgres._browser_profile_rows` 가 화면용으로 한 번 더 정제).
- `profile_path_ref` 는 서버 저장 allowlist 에 남아 있어도 런타임 상태 화면에는 렌더하지 않는다.

## 후속(Future Work 3B, 이번 작업 범위 아님)

`page_kind`, redacted URL 같은 페이지 문맥 수집은 별도 작업으로 설계한다. 이번 Phase 1 에는
구현하지 않으며, 구현 시에도 query/fragment 를 제거한 redacted URL 만 허용한다.
