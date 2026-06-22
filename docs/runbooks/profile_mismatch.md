# Runbook: 기대 센터/상점명 불일치 (Story 5.9 / AC3, NFR-15·17)

> 장애 분류(`FailureCategory`): `TARGET_VALIDATION_FAILURE`. 관련 계정 상태:
> `BaeminAuthState.CENTER_MISMATCH`. fail-closed 신호(`severity.failclosed_signals_from`).

## 증상

- 수집/검증 단계에서 화면의 센터/상점명이 대상에 등록된 **기대 센터/상점명**(`center_name`,
  FR-20)과 다름 → `TARGET_VALIDATION_FAILURE` 로 분류, 해당 대상 **fail-closed 미발송**.
- Admin 대시보드에서 해당 대상이 STOPPED(중지) 우선 표시(fail-closed > 시간 경과).

## 원인

- 브라우저 프로필이 **다른 계정/지점**으로 로그인되어 있음(프로필 격리 깨짐, Story 4.5).
- 계정에 연결된 센터/상점이 변경되었는데 대상 등록값이 갱신되지 않음.
- 플랫폼 측 계정-지점 매핑 변경.

## 조치

1. 다른 계정의 실적이 엉뚱한 고객에게 나갈 위험이 가장 크다 — **미발송(fail-closed) 유지**가
   정답이다. 의심스러우면 보내지 않는다.
2. 해당 대상의 브라우저 프로필이 올바른 계정으로 로그인되어 있는지 확인(Story 4.5 프로필 격리).
3. 기대 센터/상점명(`center_name`)이 실제 계정 지점과 일치하도록 정정(등록값 또는 계정 매핑).
4. 일치 확인 후에야 재개 — 검증 통과 시 `TARGET_VALIDATION_FAILURE` 가 해소되고 정상 전송 재개.

## 에스컬레이션

- 정정해도 반복 불일치 → 플랫폼 계정-지점 매핑 문제 의심, 계정 담당/운영 책임자 보고.
- 오발송이 실제 발생했을 가능성 → 즉시 운영 책임자 통지 및 영향 범위 점검(고객 신뢰 직결).
