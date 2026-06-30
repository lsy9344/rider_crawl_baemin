# Investigation: EC2 접속 끊김 및 서버 상태 확인

## Hand-off Brief

1. **What happened.** Confirmed: 2026-06-29 19:15 KST GitHub Actions가 SSM 배포 명령을 실행하면서 backend/scheduler/queue/telegram 컨테이너가 재생성되어 짧은 연결 끊김이 발생했다.
2. **Where the case stands.** Concluded; EC2 상태검사, 커널 로그, 메모리/swap/CPU 지표는 host 장애를 지지하지 않는다.
3. **What's needed next.** 무중단이 필요하면 배포 전략을 rolling/blue-green 또는 health-gated proxy 방식으로 바꾼다.

## Case Info

| Field            | Value                                      |
| ---------------- | ------------------------------------------ |
| Ticket           | N/A                                        |
| Date opened      | 2026-06-29                                 |
| Status           | Concluded                                  |
| System           | EC2 `i-0e6a710a505e6b3c4`, `ap-northeast-2`, Docker Compose |
| Evidence sources | User report, AWS CLI, SSM command logs, Docker events, server logs |

## Problem Statement

사용자 보고: "방금 ec2 서버상태확인하세요. 접속이 끊겼습니다. 에러로그 분석하여 원인 파악하세요 브리핑하세요."

## Evidence Inventory

| Source               | Status    | Notes                              |
| -------------------- | --------- | ---------------------------------- |
| User report          | Available | EC2 접속 끊김이 발생했다는 보고    |
| Repo deployment docs | Partial   | 운영 접속/로그 단서 확인 예정      |
| EC2 live status      | Available | running, AWS instance/system/EBS checks ok |
| Runtime logs         | Available | SSM deploy output, Docker events, kernel journal |

## Investigation Backlog

| # | Path to Explore                        | Priority | Status      | Notes                         |
| - | -------------------------------------- | -------- | ----------- | ----------------------------- |
| 1 | 운영 접속 정보와 배포 구조 찾기        | High     | Done        | repo docs/env/scripts 확인     |
| 2 | EC2 접근성과 시스템 상태 확인          | High     | Done        | AWS checks, SSH, health ok     |
| 3 | 최근 앱/시스템 에러 로그 분석          | High     | Done        | SSM/Docker/kernel logs         |
| 4 | 로그 타임라인으로 원인 가설 검증       | High     | Done        | deployment restart confirmed   |

## Timeline of Events

| Time       | Event                      | Source      | Confidence |
| ---------- | -------------------------- | ----------- | ---------- |
| 2026-06-29 | EC2 접속 끊김 사용자 보고  | User report | Confirmed  |
| 2026-06-29 18:57:27 KST | GitHubActions SSM 배포 `00f695e...` 성공 | AWS SSM | Confirmed |
| 2026-06-29 19:15:10 KST | GitHubActions SSM 배포 `9352130...` 시작 | AWS SSM | Confirmed |
| 2026-06-29 19:15:24-35 KST | 기존 앱 컨테이너 4개 stop/die/destroy | Docker events | Confirmed |
| 2026-06-29 19:15:37-38 KST | 새 이미지 `9352130...` 앱 컨테이너 시작 | Docker events | Confirmed |
| 2026-06-29 19:15:43 KST | 배포 health check ok, SSM 명령 성공 종료 | AWS SSM | Confirmed |

## Confirmed Findings

### Finding 1: EC2 자체는 정상이다

**Evidence:** AWS `describe-instance-status` at 2026-06-29 19:17 KST.

**Detail:** Instance state is `running`; instance, system, and attached EBS reachability checks are all `ok/passed`.

### Finding 2: 앱 컨테이너는 배포로 재생성됐다

**Evidence:** Docker events at 2026-06-29 10:15:24-10:15:38 UTC.

**Detail:** old image `00f695e...` containers were killed/stopped/destroyed, then new image `9352130...` containers started. DB container stayed up.

### Finding 3: 배포 명령 자체에 connection reset이 기록됐다

**Evidence:** SSM command `1500efc1-f1d2-47ec-b258-a4ccf35740fb`.

**Detail:** command ran 2026-06-29 10:15:10-10:15:43 UTC, status `Success`. Its stderr includes `curl: (56) Recv failure: Connection reset by peer`, followed by stdout `production health ok after 3s`.

### Finding 4: OOM/host resource starvation 증거는 없다

**Evidence:** kernel journal since 2 hours ago; CloudWatch host metrics 09:45-10:30 UTC.

**Detail:** kernel journal had no OOM, killed process, hung task, NVMe, EXT4, or I/O error lines. Host memory stayed around 57-58% available, swap around 1.66% used, and CPU max was about 17.2% in the deploy window.

## Deduced Conclusions

### Deduction 1: 사용자가 본 끊김은 EC2 장애가 아니라 배포 중 backend 재생성 때문이다

**Based on:** Findings 1, 2, 3, 4.

**Reasoning:** AWS health checks stayed normal and DB stayed running. The only matching event is GitHubActions -> SSM -> docker compose deploy, which stopped/recreated the API container and logged connection resets during health retries.

**Conclusion:** Root cause is a short planned deployment interruption from single-instance Docker Compose recreate.

## Hypothesized Paths

### Hypothesis 1: EC2 또는 서비스 프로세스 장애

**Status:** Refuted

**Theory:** EC2 자체, 네트워크, systemd/docker 프로세스, DB 연결, 디스크/메모리 문제 중 하나가 접속 끊김을 유발했을 수 있다.

**Supporting indicators:** 사용자 보고 외 확인 전이다.

**Would confirm:** EC2 상태 이상, 서비스 다운, OOM, 디스크 full, 애플리케이션 fatal log, DB/network 에러.

**Would refute:** EC2와 서비스가 정상이고 로그에 장애 흔적이 없으며 클라이언트 네트워크 문제만 확인되는 경우.

**Resolution:** AWS health checks and kernel logs refute host failure; Docker/SSM logs confirm deployment recreate instead.

### Hypothesis 2: GitHub Actions 배포로 인한 짧은 서비스 끊김

**Status:** Confirmed

**Theory:** GitHub Actions가 SSM으로 production deploy를 실행했고, Docker Compose가 API 관련 컨테이너를 재생성하면서 기존 연결이 reset됐다.

**Supporting indicators:** SSM deploy command, Docker image tag change, old containers exit 137 during Docker kill, deploy log `connection reset by peer`.

**Would confirm:** SSM command success and Docker event timeline matching the disconnect.

**Would refute:** 같은 시각 EC2 status failure, OOM, app fatal exception, network outage.

**Resolution:** Confirmed by SSM and Docker logs.

## Missing Evidence

| Gap                  | Impact                     | How to Obtain              |
| -------------------- | -------------------------- | -------------------------- |
| GitHub Actions run URL | 누가 배포를 눌렀는지 UI 링크까지 확정 | GitHub Actions UI/API |

## Source Code Trace

| Element       | Detail |
| ------------- | ------ |
| Error origin  | Docker Compose deployment via SSM |
| Trigger       | GitHubActions `SendCommand` for image `9352130...` |
| Condition     | Single EC2 / single backend API container is recreated during deploy |
| Related files | `.github/workflows/...`, `deploy/docker-compose.yml`, `scripts/production_health_check.sh` |

## Conclusion

**Confidence:** High

EC2 인스턴스 장애가 아니라 GitHub Actions production deploy가 단일 backend 컨테이너를 재생성하면서 발생한 짧은 연결 끊김이다. 배포는 성공했고 현재 서비스는 정상이다.

## Recommended Next Steps

### Fix direction

무중단 배포가 필요하면 단일 컨테이너 recreate 대신 rolling/blue-green, reverse proxy health switch, 또는 최소 2개 backend replica + 앞단 proxy 구조가 필요하다.

### Diagnostic

GitHub Actions run을 확인해 배포 트리거 주체와 의도 여부를 확인한다. 현재 서버는 `docker compose ps`, `/health`, `/metrics/operational`, CloudWatch alarms로 정상 확인됐다.

## Reproduction Plan

Production deploy를 실행하면 기존 backend 연결이 reset될 수 있다. 같은 단일 인스턴스/단일 API 컨테이너 구성에서 재현 가능하다.

## Side Findings
