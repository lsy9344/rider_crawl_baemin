# Investigation: 해운대플러스 수영중앙 수집 지연

## Hand-off Brief

1. **What happened.** 원격 Admin 기준 `해운대플러스 수영중앙`은 ACTIVE/NORMAL이며, 마지막 수집은 "1시간 전"으로 표시된다.
2. **Where the case stands.** 수집이 멈춘 확정 증거는 없고, 60분 주기에 target별 jitter 53분 27초가 붙어 실제 다음 스케줄 간격이 약 113분 27초다.
3. **What's needed next.** 수집 결과 알림이 안 오는 문제라면 delivery rule과 고객 전송 토글을 복구해야 한다.

## Case Info

| Field | Value |
| --- | --- |
| Ticket | N/A |
| Date opened | 2026-06-29 |
| Status | Active |
| System | 원격 `http://54.116.103.149:8000` Admin, 로컬 조사 PC Windows |
| Evidence sources | 원격 Admin HTML fragments, local code trace, scheduler jitter calculation |

## Problem Statement

사용자 보고: "`해운대플러스 수영중앙`의 수집이 1시간 전에 멈춘 이유는? 현재 active 상태인데 왜 수집이 안되나요?"

## Evidence Inventory

| Source | Status | Notes |
| --- | --- | --- |
| `runtime/remote_ce2d__admin_targets_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html` | Available | 대상 카드: NORMAL, interval 60, 수집 1시간 전, 전송 3시간 전 |
| `runtime/remote_ce2d__admin_jobs_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html` | Available | active/failed/stuck 모두 0, 큐 비어 있음 |
| `runtime/remote_ce2d__admin_registered-settings_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html` | Available | 수집 ON, 고객 전송 OFF, 대상 연결 OFF |
| `runtime/remote_ce2d__admin_delivery-rules_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html` | Available | DeliveryRule 0건 |
| `src/rider_server/scheduler/policy.py` | Available | next_run_at = now + interval + jitter |

## Confirmed Findings

### Finding 1: 대상 자체는 ACTIVE/NORMAL이다

**Evidence:** `runtime/remote_ce2d__admin_targets_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html:31`

**Detail:** 대상 행은 `data-severity="NORMAL"`, `data-interval="60"`, `data-success="1시간 전"`으로 표시된다.

### Finding 2: 현재 큐에는 이 tenant의 처리 중 job이 없다

**Evidence:** `runtime/remote_ce2d__admin_jobs_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html:7`

**Detail:** `data-queue-active="0"`, `data-queue-failed="0"`, `data-queue-stuck="0"`이며 본문도 "처리 중인 작업이 없습니다"다.

### Finding 3: 전송은 설정상 꺼져 있고 대상 연결 rule도 없다

**Evidence:** `runtime/remote_ce2d__admin_registered-settings_tenant_ce2d114d-5b06-4e15-824d-f3e2c16ae937.html:21`

**Detail:** 등록 설정 표는 수집 ON, 고객 전송 OFF, 대상 연결 OFF를 보여준다. DeliveryRule 목록은 0건이다.

### Finding 4: 이 target의 스케줄 간격은 60분이 아니라 약 113분 27초다

**Evidence:** `src/rider_server/scheduler/policy.py:76`

**Detail:** 스케줄러는 `next_run_at = now + interval + jitter`를 사용한다. target id `a300835d-903d-4b8c-af4b-92764296f767`의 jitter는 3207초(53분 27초)로 계산됐다.

## Deduced Conclusions

### Deduction 1: "1시간 전" 표시만으로는 수집 중단이라고 볼 수 없다

**Based on:** Finding 1, Finding 2, Finding 4

**Reasoning:** 대상 주기는 60분이지만 scheduler는 고정 jitter를 더해 이 대상은 약 113분 27초마다 다음 job을 만든다. 그러므로 마지막 수집이 "1시간 전"으로 표시되는 동안 큐가 비어 있는 것은 정상 가능한 상태다.

**Conclusion:** 현재 증거로는 수집이 멈춘 것이 아니라 아직 다음 due 시간이 오지 않았을 가능성이 높다.

### Deduction 2: "수집 결과가 안 온다"는 문제라면 원인은 전송 설정이다

**Based on:** Finding 3

**Reasoning:** 수집은 ON이지만 고객 전송이 OFF이고 활성 DeliveryRule이 0건이므로, 새 snapshot이 생겨도 고객 채널로 fan-out될 수 없다.

**Conclusion:** 알림 미수신은 수집 중단보다 전송 비활성/대상 연결 누락으로 설명된다.

## Hypothesized Paths

### Hypothesis 1: next_run_at이 아직 미래다

**Status:** Open

**Theory:** last_enqueued_at 기준 113분 27초가 지나지 않아 scheduler가 아직 새 job을 만들지 않았다.

**Would confirm:** 원격 DB에서 `monitoring_targets.next_run_at`이 현재 server time보다 미래임을 확인.

**Would refute:** `next_run_at <= now`인데도 scheduler tick이 job을 만들지 않는 증거.

## Conclusion

**Confidence:** Medium

현재 확인 가능한 증거로는 `ACTIVE인데 수집이 멈춤`이 아니라, 60분 주기 + 53분 27초 jitter 때문에 다음 수집 job이 아직 안 만들어진 상태로 보는 것이 가장 타당하다. 별도로 결과 전송은 고객 전송 OFF와 DeliveryRule 0건 때문에 막혀 있다.
