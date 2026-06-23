# Runbook: 인증 필요 / 쿠팡 인증 이메일 재확인 (Story 5.9 / AC3, NFR-15·17)

> 지표: `auth_required_count`, `gmail_reauth_required_count`(레거시 지표명). 알림: `auth_required`.
> 장애 분류(`FailureCategory`): `AUTH_REQUIRED`.

## 증상

- `/metrics/operational` 의 `auth_required_count >= 1` 또는 `gmail_reauth_required_count >= 1`
  → `auth_required` 알림(`AUTH_REQUIRED_ALERT_MIN=1` / `GMAIL_REAUTH_ALERT_MIN=1`).
- `auth_required_count` = 인증 필요(`AUTH_REQUIRED`) 계정 fleet 카운트.
- `gmail_reauth_required_count` = 쿠팡 인증 이메일 재확인 **근사**(레거시 지표명, 아래 한계 참조).

## 원인

- 배민: 세션 만료/추가 인증 요구 → 사람 개입형 재인증 필요(Story 4.8).
- 쿠팡: Gmail/Naver IMAP 인증 이메일 접근 재확인 필요(Story 4.9).

## 조치

1. 배민 인증 필요: Story 4.8 사람 개입 재인증 절차로 해당 계정 재로그인. 완료 후 계정
   `auth_state` 가 `AUTH_REQUIRED` 에서 벗어나 카운트가 줄어드는지 확인.
2. 쿠팡 인증 이메일 재확인: Story 4.9 절차로 IMAP 메일함 접근을 복구. 완료 후 해당 쿠팡 계정의
   미해결 `auth_session` 이 해소(`resolved_at` 기록)되는지 확인.
3. **무한 재인증 요청 금지** 정책 재확인 — 재인증은 사람 개입 한 번으로 끝내고, 자동 루프가
   반복 알림을 만들지 않게 한다(`AUTH_REQUIRED` 는 무한 재시도 금지 카테고리, fail-closed 보류).

## gmail_reauth 레거시 지표명의 한계 (정직성 명시)

- 중앙 서버에는 **인증 이메일 전용 상태가 없다**(`Platform` 은 BAEMIN/COUPANG 둘뿐,
  `gmail_reauth_required_count` 는 과거 Gmail 전용 설계에서 남은 레거시 이름). 따라서 이 지표는
  **쿠팡 계정의 미해결(`resolved_at IS NULL`) `auth_sessions` 카운트로 근사**한다.
- 이 근사는 "쿠팡 인증 흐름이 사람 개입 대기 중"을 대표할 뿐, 인증 이메일 단계만 정밀 분리하지
  않는다. 임의 enum/컬럼을 신설해 정밀 수치를 **위조하지 않는다** — 한계를 안 채로 운영한다.

## 크롤/인증 책임 분리 (crawl-coupang-auth-separation, 2026-06-23)

- **`CRAWL_COUPANG` 은 더 이상 자동 2FA 를 하지 않는다.** 크롤 job 은 세션이 유효하다는 전제로
  데이터만 읽고, 로그인 화면을 만나면 즉시 `AUTH_REQUIRED` 를 반환한다(IMAP/OTP 접근 0).
- **자동 email 2FA 복구 job 은 `AUTH_COUPANG_2FA` 다.** 쿠팡 `AUTH_REQUIRED` 계정에 자동 2FA
  설정(로그인 ID/PW + 인증 이메일 주소/앱비번)이 완전하고 cooldown 이 없으면 scheduler 가
  **crawl 대신 `AUTH_COUPANG_2FA` 인증 job** 을 만든다(`인증 시작` 도 동일). 자동 복구가 실패하면
  계정에 cooldown 이 설정돼 그 동안 새 인증 job 이 만들어지지 않는다(반복 OTP 요청 차단).
- **`OPEN_AUTH_BROWSER` 는 사람이 직접 조치하는 수동 전용** 이다. 자동 OTP 를 호출하지 않는다.
  자동 2FA 설정이 불완전한 쿠팡 계정의 `인증 시작` 은 이 수동 경로로 폴백한다.
- 인증 job 결과의 세부 상태(`auth_recovery_state`)와 운영자 조치:
  - `EMAIL_AUTH_REQUIRED`(메일 인증 필요) → 운영자는 **메일함 앱 비밀번호/IMAP 인증** 을 점검·갱신한다.
  - `USER_ACTION_REQUIRED`(캡차/이상 로그인) → 운영자는 **`OPEN_AUTH_BROWSER`(인증 브라우저 열기)**
    로 직접 캡차/이상 로그인을 푼다.
  - `RECOVERY_FAILED`(메일 지연/반복 실패) → cooldown 동안 자동 재시도하지 않는다.
- **반복 자동 2FA 를 맹목적으로 재실행하지 않는다.** 한 번 실패하면 상태와 reason 을 남기고 멈춘다.
- `인증 시작`/`AUTH_COUPANG_2FA` job 은 짧은 TTL 을 가진다. 서버/Agent 재시작 뒤 만료된 인증
  job 은 재실행되지 않고 `stale_auth_job_expired`(수동) / `stale_auth_recovery_abandoned`(자동)
  로 terminal 종료된다 — 중복 OTP 요청을 막는다.
- 상세 정책: `docs/operations/queue-backlog-handling-policy.md`,
  `docs/goal/crawl-coupang-auth-separation-work-order-2026-06-23.md` 참조.

## 에스컬레이션

- 동일 계정이 짧은 주기로 반복 `AUTH_REQUIRED` → 계정 차단/패턴 변화 의심, 운영 책임자 보고.
- 다수 계정 동시 인증 필요 → 플랫폼 정책 변경 가능성, 개발/운영 합동 점검.
