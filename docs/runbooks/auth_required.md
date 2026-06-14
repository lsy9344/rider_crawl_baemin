# Runbook: 인증 필요 / Gmail 재인증 (Story 5.9 / AC3, NFR-15·17)

> 지표: `auth_required_count`, `gmail_reauth_required_count`. 알림: `auth_required`.
> 장애 분류(`FailureCategory`): `AUTH_REQUIRED`.

## 증상

- `/metrics/operational` 의 `auth_required_count >= 1` 또는 `gmail_reauth_required_count >= 1`
  → `auth_required` 알림(`AUTH_REQUIRED_ALERT_MIN=1` / `GMAIL_REAUTH_ALERT_MIN=1`).
- `auth_required_count` = 인증 필요(`AUTH_REQUIRED`) 계정 fleet 카운트.
- `gmail_reauth_required_count` = 쿠팡 Gmail 재인증 **근사**(아래 한계 참조).

## 원인

- 배민: 세션 만료/추가 인증 요구 → 사람 개입형 재인증 필요(Story 4.8).
- 쿠팡: Gmail 2FA 메일함 접근 재인증 필요(Story 4.9).

## 조치

1. 배민 인증 필요: Story 4.8 사람 개입 재인증 절차로 해당 계정 재로그인. 완료 후 계정
   `auth_state` 가 `AUTH_REQUIRED` 에서 벗어나 카운트가 줄어드는지 확인.
2. 쿠팡 Gmail 재인증: Story 4.9 절차로 Gmail 2FA 메일함 접근을 복구. 완료 후 해당 쿠팡 계정의
   미해결 `auth_session` 이 해소(`resolved_at` 기록)되는지 확인.
3. **무한 재인증 요청 금지** 정책 재확인 — 재인증은 사람 개입 한 번으로 끝내고, 자동 루프가
   반복 알림을 만들지 않게 한다(`AUTH_REQUIRED` 는 무한 재시도 금지 카테고리, fail-closed 보류).

## gmail_reauth 근사의 한계 (정직성 명시)

- 중앙 서버에는 **Gmail 전용 상태가 없다**(`Platform` 은 BAEMIN/COUPANG 둘뿐, Gmail reauth 는
  쿠팡 전용·agent-side 개념의 spec plain-string). 따라서 `gmail_reauth_required_count` 는
  **쿠팡 계정의 미해결(`resolved_at IS NULL`) `auth_sessions` 카운트로 근사**한다.
- 이 근사는 "쿠팡 인증 흐름이 사람 개입 대기 중"을 대표할 뿐, Gmail 2FA 단계만 정밀 분리하지
  않는다. 임의 enum/컬럼을 신설해 정밀 수치를 **위조하지 않는다** — 한계를 안 채로 운영한다.

## 에스컬레이션

- 동일 계정이 짧은 주기로 반복 `AUTH_REQUIRED` → 계정 차단/패턴 변화 의심, 운영 책임자 보고.
- 다수 계정 동시 인증 필요 → 플랫폼 정책 변경 가능성, 개발/운영 합동 점검.
