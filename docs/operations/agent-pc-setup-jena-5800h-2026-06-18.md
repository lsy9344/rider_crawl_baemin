# Agent PC setup guide - jena-5800h - 2026-06-18

이 문서는 `jena-5800h` Agent PC에서 Rider Agent를 등록하고 실행하기 위한 작업 순서입니다.

## 현재 등록 값

- Backend base URL: `http://54.116.103.149:8000`
- Backend health check: `http://54.116.103.149:8000/health`
- Admin URL: `http://54.116.103.149:8000/admin`
- Agent UUID: `b781de75-1386-4d91-be00-67381ecca828`
- Agent name: `jena-5800h`
- Current server status: `PENDING_REGISTRATION`
- Registration code: `agreg_FVeX8BulB1IPFsLyIhh6dRgC`

등록 코드는 일회용 값입니다. 등록이 끝나면 같은 코드로 다시 등록할 수 없을 수 있습니다.

## 전제 조건

- Windows 10/11 PC
- PowerShell
- Git
- Python `3.10` 이상 권장
- Chrome 설치 및 로그인 세션 준비

Agent는 서버로 outbound HTTP 요청을 보냅니다. Agent PC에 inbound port를 열 필요는 없습니다.

## 1. 저장소 받기

새 PC에서 작업 폴더를 정한 뒤 저장소를 받습니다.

```powershell
cd "$env:USERPROFILE\Desktop"
git clone https://github.com/lsy9344/rider_crawl_baemin.git rider_result_mornitoring
cd rider_result_mornitoring
git checkout design_develop
git pull origin design_develop
```

이미 저장소가 있다면 아래처럼 최신화합니다.

```powershell
cd C:\path\to\rider_result_mornitoring
git checkout design_develop
git pull origin design_develop
```

## 2. Python 가상환경 만들기

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -e ".[dev,server]"
```

`py -3.11`이 없으면 설치된 Python 경로로 바꿉니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -e ".[dev,server]"
```

## 3. 서버 URL 환경변수 설정

등록 명령은 `RIDER_AGENT_SERVER_URL`을 사용합니다. 현재 운영 서버는 아래 값입니다.

```powershell
$env:RIDER_AGENT_SERVER_URL="http://54.116.103.149:8000"
```

PowerShell 창을 새로 열 때마다 다시 설정하기 싫다면 사용자 환경변수로 저장합니다.

```powershell
[Environment]::SetEnvironmentVariable(
  "RIDER_AGENT_SERVER_URL",
  "http://54.116.103.149:8000",
  "User"
)
```

저장 후 새 PowerShell을 열고 확인합니다.

```powershell
echo $env:RIDER_AGENT_SERVER_URL
```

## 4. 서버 연결 확인

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "http://54.116.103.149:8000/health"
```

정상이라면 응답 본문에 아래 값이 보입니다.

```json
{"status":"ok"}
```

## 5. Agent 등록

아래 명령을 Agent PC에서 한 번 실행합니다.

```powershell
$env:RIDER_AGENT_SERVER_URL="http://54.116.103.149:8000"
.venv\Scripts\python.exe -m rider_agent register --code "agreg_FVeX8BulB1IPFsLyIhh6dRgC"
```

성공 예시는 아래와 비슷합니다.

```text
agent registered: agent_id=b781de75-1386-4d91-be00-67381ecca828 config_version=v1
```

이미 등록된 PC에서 다시 실행하면 서버에 다시 등록 요청을 보내지 않고 로컬 등록 정보를 재사용합니다.

```text
agent already-registered: agent_id=b781de75-1386-4d91-be00-67381ecca828 config_version=v1
```

로컬 등록 정보는 기본적으로 앱 상태 루트 아래 `runtime/state/agent`에 저장됩니다. 토큰은 별도 보안 저장소에 분리 저장되고, `agent_config.json`에는 토큰 평문이 들어가지 않습니다.

## 6. Agent 실행

등록 후 아래 명령으로 Agent loop를 실행합니다.

```powershell
.venv\Scripts\python.exe -m rider_agent run --server-url "http://54.116.103.149:8000"
```

이 프로세스는 서버에서 job을 polling하고 heartbeat를 보냅니다. 운영 중에는 PowerShell 창을 닫지 마세요.

환경변수를 저장해 둔 경우에는 아래처럼 실행해도 됩니다.

```powershell
.venv\Scripts\python.exe -m rider_agent run
```

## 7. 재부팅 후 자동 실행 등록

사용자 로그인 후 자동으로 Agent를 시작하려면 startup 방식으로 등록합니다. 관리자 권한이 필요하지 않은 기본 방식입니다.

```powershell
.venv\Scripts\python.exe -m rider_agent autostart --register --server-url "http://54.116.103.149:8000"
```

등록 상태 확인:

```powershell
.venv\Scripts\python.exe -m rider_agent autostart --status
```

해제:

```powershell
.venv\Scripts\python.exe -m rider_agent autostart --unregister
```

## 8. 등록 후 서버에서 확인할 것

서버/Admin 쪽에서 아래를 확인합니다.

- Agent `jena-5800h` 상태가 `PENDING_REGISTRATION`에서 `REGISTERED` 또는 `ONLINE` 계열 상태로 바뀌는지 확인합니다.
- `last_heartbeat_at`이 갱신되는지 확인합니다.
- CloudWatch `rider-server-heartbeat-stale` 알람이 정상으로 돌아오는지 확인합니다.

운영 PC에서 Agent가 실행 중인데도 heartbeat가 갱신되지 않으면 다음을 먼저 확인합니다.

```powershell
echo $env:RIDER_AGENT_SERVER_URL
Invoke-WebRequest -UseBasicParsing -Uri "http://54.116.103.149:8000/health"
.venv\Scripts\python.exe -m rider_agent register --code "agreg_FVeX8BulB1IPFsLyIhh6dRgC"
.venv\Scripts\python.exe -m rider_agent run --server-url "http://54.116.103.149:8000"
```

## 주의 사항

- Registration code는 일회용입니다. 등록이 끝난 뒤에는 새 Agent PC에 같은 코드를 쓰지 마세요.
- Admin URL은 현재 공개 접근 모드입니다. 주소를 불필요하게 공유하지 마세요.
- Agent PC의 Chrome 프로필과 CDP 포트는 계정별로 분리해야 합니다.
- Telegram bot token, Coupang password, email app password 같은 값은 문서나 커밋에 남기지 말고 Admin 웹앱이나 로컬 secret store에만 입력합니다.
