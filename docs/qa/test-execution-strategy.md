# Test Execution Strategy

이 프로젝트의 테스트는 삭제하지 않고 실행 단계를 나눠서 운영한다.

핵심 원칙은 단순하다. 개발 중에는 빠른 테스트로 자주 확인하고, PR 또는 배포 전에는 느린 테스트와 외부 의존 테스트까지 반드시 확인한다. 그래서 `quick`에서 제외되는 테스트는 버리는 테스트가 아니라 나중 단계로 미루는 테스트다.

## Stages

| 단계 | 명령 | 언제 쓰나 | 뒤로 미루는 테스트 |
| --- | --- | --- | --- |
| Focus | `.\scripts\test.ps1 focus` | 작은 코드 수정 중 아주 빠른 확인 | PostgreSQL, 느린 테스트, 문서/아티팩트, 구조 가드 |
| Quick | `.\scripts\test.ps1 quick` | 일반 코드 수정 후 기본 확인 | PostgreSQL, 느린 테스트, 문서/아티팩트 |
| Full local | `.\scripts\test.ps1 full` | 커밋/PR 전 로컬 확인 | PostgreSQL |
| PostgreSQL | `.\scripts\test.ps1 postgres` | DB 저장소, 큐, 동시성, 보안 변경 시 | 없음. `TEST_DATABASE_URL` 필요 |
| Architecture | `.\scripts\test.ps1 architecture` | 패키지 경계, import, async 경계 변경 시 | 해당 없음 |
| Docs | `.\scripts\test.ps1 docs` | runbook, baseline, 문서성 테스트 변경 시 | 해당 없음 |
| Slow | `.\scripts\test.ps1 slow` | 2FA, subprocess, thread, race 쪽 변경 시 | 해당 없음 |
| E2E | `.\scripts\test.ps1 e2e` | 여러 계층을 함께 건드렸을 때 | 해당 없음 |
| All | `.\scripts\test.ps1 all` | 릴리스 전 최종 확인 | 없음 |

## What Is Deferred

`quick`은 아래 테스트를 뒤 단계로 미룬다.

- `postgres`: 실제 PostgreSQL 의미가 필요한 테스트
- `slow`: subprocess, 실제 sleep, thread 같은 비용 큰 테스트
- `docs`: runbook, baseline, 문서/아티팩트 존재 확인
- `local_artifact`: 로컬 git tag 또는 생성된 baseline 파일에 의존하는 테스트

`focus`는 여기에 `architecture`도 추가로 미룬다. 코드를 빠르게 고치는 중에는 좋지만, PR 전에는 최소 `quick` 또는 `full`을 다시 돌려야 한다.

## Suggested Workflow

1. 수정 범위가 좁으면 관련 테스트 파일을 직접 실행한다.
   예: `.\scripts\test.ps1 all tests\server\test_scheduler_policy.py`
2. 기본 확인은 `.\scripts\test.ps1 quick`으로 한다.
3. 커밋 전에는 `.\scripts\test.ps1 full`을 실행한다.
4. DB, 큐, 보안, 동시성 코드를 건드렸다면 `TEST_DATABASE_URL`을 설정하고 `.\scripts\test.ps1 postgres`도 실행한다.
5. 배포 전에는 가능한 환경에서 `.\scripts\test.ps1 all`을 실행한다.

이 방식은 테스트를 건너뛰는 운영이 아니다. 비용이 큰 테스트를 더 적절한 시점에 배치해서 개발 속도와 안전성을 같이 잡는 운영이다.

## GitHub CI

`.github/workflows/test.yml`은 같은 단계를 CI에서도 사용한다.

- PR: GitHub의 `pull_request` 이벤트가 자동으로 `quick`을 실행하고, 이어서 Windows runner에서 실제 PostgreSQL 기반 `postgres`를 실행한다.
- `main`/`develop` push: `full` + `postgres`
- 매일 03:00 KST 스케줄: `full` + `postgres`
- 수동 실행: `focus`, `quick`, `full`, `all`, `architecture`, `docs`, `slow`, `e2e` 중 선택 가능. PostgreSQL job은 수동 입력으로 끌 수 있다.

여기서 `quick`, `full`, `postgres`는 push 메시지나 branch 이름이 아니다. GitHub Actions가 어떤 테스트 묶음을 실행할지 고르는 내부 단계 이름이다.

CI는 `windows-latest` runner에서 실행한다. Python 의존성은 `uv sync --frozen --extra dev --extra server`로 잠금 파일 기반 설치를 사용하고, PostgreSQL은 Windows runner에서 setup action으로 준비한다. 테스트 결과는 JUnit XML로 저장하고 GitHub artifact에 14일 보관한다.

로컬 테스트는 계속 필요하다. 일반 수정 후에는 `.\scripts\test.ps1 quick`, 커밋/PR 전에는 `.\scripts\test.ps1 full`을 먼저 돌리고, DB 관련 변경이면 로컬에서도 `.\scripts\test.ps1 postgres`를 추가로 돌리는 것이 기본 운영이다.
