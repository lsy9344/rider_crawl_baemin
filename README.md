# Rider Crawl

쿠팡이츠 파트너 라이더 실적 페이지를 읽어 카카오톡에 보낼 텍스트 메시지를 생성하는 Python 봇입니다.
기본 실행은 UI 앱이며, `시작`을 누르면 즉시 1회 실행 후 설정한 간격으로 반복합니다.

## 준비

- macOS 또는 Windows PC
- Python 3.10 이상
- Chrome 로그인 완료
- Windows 운영 전송 시 Windows용 카카오톡 PC 앱 로그인 완료
- 고유한 카카오톡 단체 채팅방명
- 쿠팡이츠 실적 페이지가 원격 디버깅 포트로 실행한 Chrome에 로그인된 상태로 열려 있음

## 설치(Windows)

```bat
cd /d C:\rider_crawl
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\pip.exe install -e ".[dev]"
```

앱 전용 브라우저 예비 모드(`persistent`)를 쓸 때만 Chromium 설치가 필요합니다.

```bat
.venv\Scripts\playwright.exe install chromium
```

## 설치(macOS)

```bash
cd /Users/sooyeol/Desktop/dev_busi/rider_crawl
uv sync --extra dev --python 3.10
```

앱 전용 브라우저 예비 모드(`persistent`)를 쓸 때만 Chromium 설치가 필요합니다.

```bash
uv run --python 3.10 playwright install chromium
```

## Chrome 준비

기본 브라우저 연결 방식은 이미 로그인한 Chrome 탭에 CDP로 붙는 방식입니다.

macOS에서는 UI의 `앱 실행 준비하기(mac)` 버튼을 누르면 전용 Chrome 창이 `http://127.0.0.1:9222`로 열리고 쿠팡이츠 실적 페이지까지 실행됩니다. 열린 Chrome 창에서 한 번 로그인한 뒤, 그 창을 닫지 않고 사용하세요.

터미널에서 직접 실행할 때는 아래 명령을 사용할 수 있습니다.

Windows:

```bat
start "" chrome.exe --remote-debugging-port=9222
```

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

## 실행

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl
```

macOS:

```bash
uv run --python 3.10 python -m rider_crawl
```

UI에서 필요한 값을 입력하고 `설정 저장`을 누릅니다. macOS에서는 먼저 `앱 실행 준비하기(mac)`를 눌러 Chrome을 열고 로그인하세요. 처음에는 `카카오톡 전송` 체크를 끄고 `1회 실행`으로 메시지 미리보기를 확인하세요.

## UI 설정

- 실적 URL
- 피크 대시보드 URL
- 브라우저 연결 방식: 기본 `cdp`, 예비 모드 `persistent`
- CDP 주소: 기본 `http://127.0.0.1:9222`
- 앱 전용 브라우저 프로필 경로: `persistent` 모드에서 사용
- 로그 경로
- 카카오톡 채팅방명
- 실행 간격, 기본 35분
- 페이지 타임아웃
- 락 타임아웃
- Headless 여부
- 카카오톡 전송 여부
- 변경 시에만 전송 여부

설정은 환경변수가 아니라 `runtime/state/ui_settings.json`에 저장됩니다.

## 1회 CLI 실행

UI 없이 저장된 설정으로 1회만 실행하려면 다음 명령을 사용합니다.

```bat
.venv\Scripts\python.exe -m rider_crawl --once
```

macOS:

```bash
uv run --python 3.10 python -m rider_crawl --once
```
