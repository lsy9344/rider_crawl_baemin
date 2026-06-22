# Runbook: 중복 전송 차단 (Story 5.9 / AC3, NFR-15·17)

> 장애 분류(`FailureCategory`): `DUPLICATE_BLOCKED`. 관련 상태: `DeliveryStatus.DUPLICATE_BLOCKED`
> (같은 값·다른 레이어 — error_code 분류 vs 전송 상태).

## 증상

- 같은 dedup key 의 메시지가 이미 성공 전송으로 확보되어 재전송이 **차단**됨 →
  `DUPLICATE_BLOCKED` 로 기록(audit). 이는 **정상 idempotency 동작**인 경우가 대부분이다.

## 원인

- 재시도/재기동/crash-after-send 후 동일 메시지를 다시 보내려다 dedup 이 차단(Story 3.5 —
  insert-then-send 로 유니크 제약 선확보).
- 스케줄 중복 enqueue 가 멱등 차단됨(정상).
- (의심 케이스) 동일 dedup key 가 서로 다른 내용을 가리키도록 키 생성이 잘못된 경우.

## 조치

1. 먼저 **정상 idempotency vs 의심 케이스**를 구분한다:
   - 정상: 재시도/재기동/crash-after-send 직후의 차단 — 중복 발송을 막은 **올바른 동작**이라
     추가 조치 불필요. crash-after-send 안전성(보냈는데 기록 전 죽음)도 이 차단이 보장한다.
   - 의심: 서로 다른 내용이 같은 dedup key 로 묶여 정작 보내야 할 메시지가 차단되는 경우.
2. 의심 케이스면 dedup key 생성 로직(대상/스냅샷/기간 구성요소)이 올바른지 점검 —
   서로 다른 실적이 같은 키로 충돌하지 않는지 확인.
3. `DUPLICATE_BLOCKED` 빈도가 비정상적으로 높으면 상류(스케줄 중복/재시도 폭주) 원인을 본다
   (`api_error_rate.md`/`queue_lag.md` 연계 가능).

## 에스컬레이션

- dedup key 충돌로 **정상 메시지가 누락**되는 정황 → 즉시 개발 에스컬레이션(데이터 정합 영향).
- 정상 idempotency 차단이면 에스컬레이션 불필요(기록만 유지).
