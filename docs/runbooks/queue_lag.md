# Runbook: KakaoTalk queue lag (Story 5.9 / AC3, NFR-15·17)

> 지표: `kakao_queue_lag_seconds`. 알림: `queue_lag`. 장애 분류(`FailureCategory`): `KAKAO_FAILURE`
> (Kakao 전송 적체·창 검증 실패는 오발송 위험과 함께 Kakao 실패로 분류된다).

## 증상

- `/metrics/operational` 의 `kakao_queue_lag_seconds` 가 **120초 초과**(`QUEUE_LAG_ALERT_SECONDS`,
  NFR-14) 로 지속/반복 → `queue_lag` 알림. 대기 `KAKAO_SEND` job 의 가장 오래된 `run_after`
  기준 지연이다(fleet 최댓값).

## 원인

- Kakao 는 **FIFO 단일 세션 직렬 전송**(Story 4.6)이라 한 건이 막히면 뒤가 모두 적체된다.
- 모호한 채팅방 검증 실패로 특정 건이 보류되어 큐가 진행되지 못함(`kakao_ambiguous_room.md`).
- Agent Kakao 세션 만료/로그아웃으로 전송이 멈춤.

## 조치

1. Agent 의 Kakao 세션 상태 확인 — 로그인/세션이 살아있는지, 만료면 재로그인.
2. 큐 선두 건이 **모호한 방명 검증 실패**로 막혀 있는지 확인. 모호하면 임의 창 전송 금지 —
   미발송 유지(오발송이 적체보다 위험, `KAKAO_FAILURE` 분류). `kakao_ambiguous_room.md` 참조.
3. 세션 복구/모호건 해소 후 lag 이 120초 아래로 회복되는지 모니터링.

## 에스컬레이션

- 세션 정상인데 lag 이 계속 증가 → 전송량/세션 처리율 한계 의심, 운영 책임자에게 보고.
- 적체로 SLA(전송 지연) 초과 위험 시 고객 커뮤니케이션 담당 통지.
