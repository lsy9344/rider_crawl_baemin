# Runbook: Agent offline (Story 5.9 / AC3, NFR-15·17)

> 지표: `agent_last_heartbeat`. 알림: `agent_offline`. 장애 분류(`FailureCategory`): `CRAWL_FAILURE`
> (Agent 가 멈추면 수집 job 이 진행되지 않아 수집 실패로 번진다).

## 증상

- `/metrics/operational` 의 `agents_offline >= 1` 또는 `oldest_heartbeat_age_seconds` 가 크게
  증가. Admin 대시보드에서 Agent 가 offline 표시.
- 판정 정본: heartbeat 가 **2분 초과**(`AGENT_OFFLINE_AFTER = 2분`, `severity.is_agent_online`)
  누락이면 offline. 정확히 2분 경과는 online(초과 `>` 일 때만 offline).

## 원인

- Agent PC 종료/절전/네트워크 단절.
- Agent 프로세스 비정상 종료(크래시) 또는 outbound HTTPS 차단.
- 재부팅 후 자동 시작(autostart) 미동작(Story 4.7).

## 조치

1. 해당 PC 전원/네트워크 상태 확인 — 켜져 있고 인터넷 연결이 살아있는지.
2. Agent 프로세스 생존 확인. 죽었으면 재시작, 재부팅 후 autostart 가 heartbeat 를 복구하는지
   확인(Story 4.7 자동 시작 연계).
3. 복구 후 `agent_last_heartbeat` 가 2분 이내로 돌아오고 `agents_offline` 이 0 으로 떨어지는지
   확인.
4. Agent 가 장시간 죽어 있던 동안 누락된 수집은 scheduler 가 다음 due 윈도에 멱등 enqueue 로
   따라잡는다 — 수동 중복 enqueue 금지(`CRAWL_FAILURE` 누적·중복 방지).

## 에스컬레이션

- 30분 내 heartbeat 미복구이거나 동일 PC 가 반복 offline → 인프라/현장 담당 에스컬레이션.
- 다수 Agent 동시 offline → 중앙 서버/네트워크 공통 원인 의심, 운영 책임자 호출.
