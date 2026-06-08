# Rider Crawl

배민 `배달현황` 페이지를 읽어 텔레그램 그룹방에 보낼 텍스트 메시지를 생성하는 Python 봇입니다.
기본 실행은 UI 앱이며, `시작`을 누르면 즉시 1회 실행 후 설정한 메세지 전송 간격(분)마다 페이지의 `새로고침` 버튼을 누르고 다시 수집합니다.

## 준비

- Windows PC 또는 macOS
- Python 3.10 이상
- Chrome 설치
- 배민 배달현황 페이지 로그인 완료
- 텔레그램 봇 토큰
- 텔레그램 그룹방 chat_id
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
start "" chrome.exe --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%CD%\runtime\browser-profile" "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
```

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD/runtime/browser-profile" "https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
```

UI의 `Chrome 준비하기` 버튼으로도 선택된 탭의 CDP 포트와 브라우저 프로필 경로를 사용해 전용 Chrome 창을 열 수 있습니다.

## 실행

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl
```

macOS:

```bash
.venv/bin/python -m rider_crawl
```

UI에서 필요한 값을 입력하고 `설정 저장`을 누릅니다. 처음에는 `텔레그램 전송` 체크를 끄고 `1회 실행`으로 메시지 미리보기를 확인하세요.

## UI 설정

- 배달현황 URL: 기본 `https://deliverycenter.baemin.com/delivery/history?page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus=`
- 브라우저 연결 방식: 기본 `cdp`, 예비 모드 `persistent`
- CDP 주소: 기본 `http://127.0.0.1:9222`
- 앱 전용 브라우저 프로필 경로: `persistent` 모드에서 사용
- 텔레그램 봇 토큰
- 텔레그램 채팅방 ID
- 로그 경로
- 카카오톡 채팅방명: 기존 카카오톡 PC 앱 자동화 방식용으로 남아 있는 값입니다.
- 메세지 전송 간격: 기본 35분
- 페이지 타임아웃
- 락 타임아웃
- Headless 여부
- 텔레그램 전송 여부
- 변경 시에만 전송 여부

UI에는 `크롤링1`부터 `크롤링9`까지 9개 탭이 있습니다. 각 탭은 독립된 배달현황 URL, CDP 주소, 브라우저 프로필 경로, 전송 설정을 저장합니다. 여러 배민 계정을 쓰려면 탭마다 서로 다른 Chrome 프로필과 서로 다른 원격 디버깅 포트를 사용하세요. 예를 들어 `크롤링1`은 `9222`, `크롤링2`는 `9223`, ..., `크롤링9`는 `9230`을 사용합니다.

설정은 환경변수가 아니라 `runtime/state/ui_settings.json`에 저장됩니다. 토큰과 chat_id를 파일에 저장하고 싶지 않다면 CLI 실행용으로 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 환경변수를 사용할 수 있습니다.

## 텔레그램 봇 준비

1. 텔레그램에서 `@BotFather`를 검색합니다.
2. `/newbot`을 입력하고 안내에 따라 봇 이름과 username을 만듭니다.
3. BotFather가 알려주는 토큰을 복사합니다. 예: `123456789:ABC...`
4. 실적을 받을 텔레그램 그룹방에 봇을 초대합니다.
5. 그룹방에서 봇이 일반 텍스트 `!홍길동1234` 같은 메시지를 보려면 BotFather에서 `/setprivacy`를 실행해 해당 봇의 privacy mode를 `Disable`로 바꾸세요. 또는 그룹방에서 봇을 관리자 권한으로 넣으세요.
6. UI의 `텔레그램 봇 토큰`에 봇 토큰을 입력합니다.

## chat_id 확인

가장 쉬운 방법은 봇을 그룹방에 초대한 뒤 그룹방에 아무 메시지나 보내고 브라우저에서 아래 주소를 여는 것입니다.

```text
https://api.telegram.org/bot<봇토큰>/getUpdates
```

응답 JSON에서 `"chat":{"id":...}` 값을 찾습니다. 그룹방 chat_id는 보통 `-100`으로 시작하는 음수입니다. 그 값을 UI의 `텔레그램 채팅방 ID`에 입력합니다.

이미 getUpdates를 여러 번 봤거나 결과가 비어 있으면 그룹방에 새 메시지를 한 번 더 보낸 뒤 다시 열어 보세요.

## 텔레그램 명령

그룹방에서 아래 형식으로 입력하면 봇이 다시 크롤링해서 라이더 취소 데이터를 답장합니다.

```text
!홍길동1234
```

- `홍길동`: 라이더 이름
- `1234`: 휴대폰번호 뒤 4자리

봇은 활성 탭 전체를 순서대로 크롤링합니다. 처음 찾은 결과를 답장하고, 여러 탭에서 같은 이름과 번호가 발견되면 중복 발견을 함께 알려줍니다. 찾지 못하면 `해당 라이더를 찾지 못했습니다.`라고 답장합니다.

취소율 계산식은 `총 취소 수 / (완료 + 거절 + 총 취소 수) * 100`입니다. 총 취소 수는 `배차취소 + 배달취소(라이더귀책)`입니다. 취소율이 4% 이상이면 `위험합니다.`, 아니면 `정상 범위입니다.`라고 표시합니다.

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
