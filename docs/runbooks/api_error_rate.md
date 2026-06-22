# Runbook: API/수집 오류율 급증 (Story 5.9 / AC3, NFR-15·17)

> 지표: `crawl_error_rate_by_platform`, `telegram_send_error_rate`. 알림: `api_error_rate`.
> 장애 분류(`FailureCategory`): `CRAWL_FAILURE`, `RENDER_FAILURE`, `TELEGRAM_FAILURE`.

## 증상

- crawl: 어느 플랫폼(BAEMIN/COUPANG)이든 **최근 15분 실패율 30% 초과**(표본 ≥ min_samples)
  → `api_error_rate` 알림. 정본 임계는 scheduler circuit breaker 와 동일
  (`DEFAULT_BREAKER_THRESHOLD=0.30`, `DEFAULT_BREAKER_MIN_SAMPLES`, 15분 윈도). 표본 가드가
  `1/1=100%` 소표본 오탐을 막는다.
- telegram: **최근 10분** `TELEGRAM_FAILURE` 전송 오류 급증.

## 원인

- circuit breaker **open** = 그 플랫폼이 반복 실패 중이라는 신호.
- 사이트 구조 변경(셀렉터/페이지 변경)으로 파싱 실패(`CRAWL_FAILURE`) 또는 렌더 실패
  (`RENDER_FAILURE`).
- 플랫폼 로그인 만료/차단 → 수집 실패가 누적(인증은 `auth_required.md` 연계).
- Telegram: 봇 토큰/채널 권한 문제, 외부 API 장애 → `TELEGRAM_FAILURE`.

## 조치

1. 어느 플랫폼/채널이 임계를 넘었는지 지표에서 확인(`crawl_error_rate_by_platform` 플랫폼별,
   `telegram_error_count`).
2. crawl: breaker open 은 **정상 보호 동작**이다 — 무리한 수동 재시도 금지. 사이트 구조 변경
   여부를 진단 산출물로 확인하고, 변경이면 파서/셀렉터 수정 후 배포.
3. 로그인 만료가 원인이면 `auth_required` 흐름으로 재인증(`auth_required.md`).
4. telegram: 봇 토큰/채널 등록 상태 확인. 외부 장애면 복구 후 오류율이 떨어지는지 모니터링.
5. 실 알람(CloudWatch) 임계는 본 정본 임계(15분 30% / 10분 급증)로 deploy/운영 설정에서 건다 —
   테스트는 순수 `evaluate_alerts` 와 엔드포인트 알림 배열로만 검증(외부 알람 위조 금지).

## 에스컬레이션

- 사이트 구조 변경으로 다수 고객 수집 중단 → 개발/운영 합동 대응, 우선순위 상향.
- Telegram 외부 장애 장기화 → 대체 통지 채널 검토 및 고객 공지.
