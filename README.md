# Rider Crawl

배민 `배달현황` 페이지를 읽어 카카오톡에 보낼 텍스트 메시지를 생성하는 Python 봇입니다.
기본 실행은 UI 앱이며, `시작`을 누르면 즉시 1회 실행 후 기본 20초마다 페이지의 `새로고침` 버튼을 누르고 다시 수집합니다.

## 준비

- Windows PC 또는 macOS
- Python 3.10 이상
- Chrome 설치
- 배민 배달현황 페이지 로그인 완료
- Windows 운영 전송 시 Windows용 카카오톡 PC 앱 로그인 완료
- 고유한 카카오톡 단체 채팅방명
- 배민 배달현황 페이지가 원격 디버깅 포트로 실행한 Chrome에 로그인된 상태로 열려 있음

## 설치(Windows)

```bat
cd /d C:\rider_crawl
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\pip.exe install -e ".[dev]"
.venv\Scripts\pip.exe install -U crawl4ai
.venv\Scripts\crawl4ai-setup.exe
.venv\Scripts\crawl4ai-doctor.exe
```

## 설치(macOS)

```bash
cd /Users/sooyeol/Desktop/dev_busi/rider_crawl
python3.10 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install -U crawl4ai
.venv/bin/crawl4ai-setup
.venv/bin/crawl4ai-doctor
```

## Chrome 준비

기본 브라우저 연결 방식은 이미 로그인한 Chrome에 CDP로 붙는 방식입니다.
자동 로그인은 하지 않습니다. 사용자가 Chrome 창에서 한 번 로그인해야 합니다.

Windows:

```bat
start "" chrome.exe --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%CD%\runtime\chrome-cdp-profile" "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
```

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD/runtime/chrome-cdp-profile" "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
```

macOS에서는 UI의 `앱 실행 준비하기(mac)` 버튼으로도 전용 Chrome 창을 열 수 있습니다.

## 실행

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl
```

macOS:

```bash
.venv/bin/python -m rider_crawl
```

UI에서 필요한 값을 입력하고 `설정 저장`을 누릅니다. 처음에는 `카카오톡 전송` 체크를 끄고 `1회 실행`으로 메시지 미리보기를 확인하세요.

## UI 설정

- 배달현황 URL: 기본 `https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus=`
- 브라우저 연결 방식: 기본 `cdp`, 예비 모드 `persistent`
- CDP 주소: 기본 `http://127.0.0.1:9222`
- 앱 전용 브라우저 프로필 경로: `persistent` 모드에서 사용
- 로그 경로
- 카카오톡 채팅방명
- 새로고침 간격: 기본 20초
- 페이지 타임아웃
- 락 타임아웃
- Headless 여부
- 카카오톡 전송 여부
- 변경 시에만 전송 여부

설정은 환경변수가 아니라 `runtime/state/ui_settings.json`에 저장됩니다.

## 1회 CLI 실행

UI 없이 저장된 설정으로 1회만 실행하려면 다음 명령을 사용합니다.

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl --once
```

macOS:

```bash
.venv/bin/python -m rider_crawl --once
```

## 수집 방식

- CDP Chrome에 Crawl4AI로 연결합니다.
- 대상 페이지에서 `table`, `th`, `td`를 읽고 헤더 텍스트 기준으로 값을 매핑합니다.
- `합계` 행은 요약값으로 사용합니다.
- 일반 라이더 행은 이름과 운행상태 기준으로 수행 중인 인원을 계산하는 데 사용합니다.
- CDP 연결이 실패하면 새 로그인 창을 만들지 않고 오류를 표시합니다.
