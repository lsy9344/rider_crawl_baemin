# Rider Crawl

배민 `달성현황(beta)` 또는 쿠팡이츠 실적 페이지를 읽어 텔레그램 또는 카카오톡으로 보낼 텍스트 메시지를 생성하는 Python 봇입니다.
플랫폼은 탭마다 `배민` 또는 `쿠팡이츠`로 선택합니다.
기본 실행은 UI 앱이며, `시작`을 누르면 즉시 1회 실행 후 설정한 메세지 전송 간격(분)마다 다시 수집합니다.

## 준비

- Windows PC 또는 macOS
- Python 3.10 이상
- Chrome 설치
- 배민 탭: 배민 달성현황(beta) 페이지 로그인 완료
- 쿠팡이츠 탭: 쿠팡이츠 `rider-performance`(실적)와 `peak-dashboard`(피크 대시보드) 로그인 완료
- 쿠팡이츠 탭은 실적 URL과 피크 대시보드 URL 두 가지가 모두 필요합니다.
- 텔레그램 전송 시: 텔레그램 봇 토큰과 그룹방 chat_id
- 카카오톡 전송 시: 카카오톡 PC 앱 로그인과 채팅방명
- 대상 페이지가 원격 디버깅 포트로 실행한 Chrome에 로그인된 상태로 열려 있음

## 설치(Windows)

```bat
cd /d C:\rider_crawl
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\pip.exe install -e ".[dev]"
.venv\Scripts\crawl4ai-setup.exe
.venv\Scripts\crawl4ai-doctor.exe
```

> `crawl4ai`는 `pyproject.toml`에 `0.8.7`로 고정되어 있어 `pip install -e ".[dev]"`로 검증된 버전이 설치됩니다. `pip install -U crawl4ai`로 따로 올리지 마세요. 고정 버전을 벗어나면 파서/크롤러 동작이 달라질 수 있습니다. `crawl4ai-setup`/`crawl4ai-doctor`는 버전을 올리지 않고 브라우저 의존성만 설치·점검합니다.

## 설치(macOS)

```bash
cd /Users/sooyeol/Desktop/dev_busi/rider_crawl
python3.10 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/crawl4ai-setup
.venv/bin/crawl4ai-doctor
```

> macOS도 동일합니다. `crawl4ai`는 `pyproject.toml`에 `0.8.7`로 고정되어 있으므로 `pip install -U crawl4ai`로 올리지 마세요.

## Chrome 준비

기본 브라우저 연결 방식은 이미 로그인한 Chrome에 CDP로 붙는 방식입니다.
자동 로그인은 하지 않습니다. 사용자가 Chrome 창에서 한 번 로그인해야 합니다.

Windows:

```bat
start "" chrome.exe --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%CD%\runtime\browser-profile" "https://deliverycenter.baemin.com/delivery/report"
```

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD/runtime/browser-profile" "https://deliverycenter.baemin.com/delivery/report"
```

UI의 `Chrome 준비하기` 버튼으로도 선택된 탭의 CDP 포트와 브라우저 프로필 경로를 사용해 전용 Chrome 창을 열 수 있습니다.

배민 달성현황은 Google/Looker 보고서가 배민 페이지 안에 들어간 구조입니다. 그래서 최초에 Google 로그인이 뜰 수 있지만, 이 앱은 Google 계정 API로 데이터를 직접 가져오지 않습니다. 보고서 소유자 권한과 별도 API 자격증명이 없는 일반 운영 환경에서는 로그인된 Chrome 화면을 CDP로 읽는 방식이 가장 현실적입니다.

## 실행

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl
```

macOS:

```bash
.venv/bin/python -m rider_crawl
```

UI에서 필요한 값을 입력하고 `설정 저장`을 누릅니다. 처음에는 `메시지 전송` 체크를 끄고 `1회 실행`으로 메시지 미리보기를 확인하세요.

## UI 설정

- 플랫폼: `배민` 또는 `쿠팡이츠`. 플랫폼별로 입력 항목과 검증 규칙이 다릅니다.
- 실적/달성현황 URL(주 URL): 배민 기본값 `https://deliverycenter.baemin.com/delivery/report`, 쿠팡이츠는 `https://partner.coupangeats.com/page/rider-performance`. 이 URL이 비어 있으면 탭은 비활성으로 간주합니다.
- 보조 URL(쿠팡 피크 대시보드): 쿠팡이츠 탭에서만 사용하며 활성 쿠팡이츠 탭은 반드시 입력해야 합니다. 예: `https://partner.coupangeats.com/page/peak-dashboard`
- 배민 센터명·배민 센터 ID: 배민 탭에서는 센터 검증 항목입니다. 쿠팡이츠 탭에서는 **배민 센터명 칸을 기대 센터/상점명으로 재사용**하며 활성 쿠팡 탭은 반드시 실제 쿠팡 센터/상점명을 입력해야 합니다(배민 기본값 그대로 두면 저장이 거부됩니다). 포트/프로필이 잘못 연결돼 다른 쿠팡 계정 실적을 보내는 일을 막기 위한 값입니다. 배민 센터 ID 칸은 쿠팡 탭에서 사용하지 않습니다.
- 브라우저 연결 방식: 기본 `cdp`, 예비 모드 `persistent`
- CDP 주소: 기본 `http://127.0.0.1:9222`
- 앱 전용 브라우저 프로필 경로: `cdp`와 `persistent` 모드 모두에서 선택한 플랫폼/계정 로그인 세션을 분리하는 데 사용
- 텔레그램 봇 토큰
- 텔레그램 채팅방 ID
- 텔레그램 토픽 ID: 토픽이 있는 그룹방에서만 입력합니다. 일반 그룹방은 비워둡니다.
- 로그 경로
- 카카오톡 채팅방명: 카카오톡 전송 방식을 선택할 때 사용합니다.
- 메세지 전송 간격: 기본 35분
- 페이지 타임아웃
- 락 타임아웃
- Headless 여부
- 전송 방식: 텔레그램 또는 카카오톡
- 메시지 전송 여부
- 변경 시에만 전송 여부

UI에는 `크롤링1`부터 `크롤링9`까지 9개 탭이 있습니다. 각 탭은 독립된 플랫폼, 주 URL, 보조 URL, CDP 주소, 브라우저 프로필 경로, 전송 설정을 저장합니다. 탭마다 `배민` 또는 `쿠팡이츠`를 자유롭게 섞어 쓸 수 있습니다. 여러 계정을 쓰려면 탭마다 서로 다른 Chrome 프로필과 서로 다른 원격 디버깅 포트를 사용하세요. 예를 들어 `크롤링1`은 `9222`, `크롤링2`는 `9223`, ..., `크롤링9`는 `9230`을 사용합니다. 전송 방식(텔레그램/카카오톡)은 플랫폼 선택과 무관하게 탭마다 따로 고릅니다.

UI 설정은 `runtime/state/ui_settings.json`에 저장됩니다. 토큰과 chat_id를 파일에 저장하고 싶지 않다면 CLI `--once` 실행용으로 `.env` 또는 환경변수에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MESSAGE_THREAD_ID`를 넣어 사용할 수 있습니다.

## 여러 배민 아이디로 실행

배민 아이디마다 탭 하나를 사용합니다. 같은 달성현황 URL을 써도 되지만, CDP 포트와 Chrome 프로필 경로는 반드시 달라야 합니다. `Chrome 준비하기`는 이미 사용 중인 CDP 포트를 감지하면 중단합니다. 이 경우 기존 Chrome을 닫거나 다른 포트를 입력하세요.

1. `크롤링1`에 첫 번째 배민 아이디용 설정을 입력합니다.
2. `CDP 주소`는 `http://127.0.0.1:9222`, `앱 전용 브라우저 프로필 경로`는 `runtime/browser-profile`처럼 둡니다.
3. `Chrome 준비하기`를 눌러 열린 Chrome에서 첫 번째 배민 아이디로 로그인합니다.
4. `크롤링2`로 이동해 두 번째 배민 아이디용 설정을 입력합니다.
5. `CDP 주소`는 `http://127.0.0.1:9223`, `앱 전용 브라우저 프로필 경로`는 `runtime/browser-profile-2`처럼 다르게 둡니다.
6. `Chrome 준비하기`를 눌러 열린 다른 Chrome에서 두 번째 배민 아이디로 로그인합니다.
7. 필요한 탭마다 같은 방식으로 다른 포트와 다른 프로필 경로를 사용합니다.
8. 각 탭의 전송 방식과 전송 여부를 선택한 뒤 `시작`을 누릅니다.

활성 탭의 CDP 포트와 Chrome 프로필 경로가 중복되면 설정 저장이 막힙니다. 텔레그램은 같은 봇 토큰을 여러 탭에서 공유할 수 있지만, 활성 탭마다 `chat_id + 토픽 ID` 조합은 달라야 합니다.

새 탭의 기본 전송 방식은 텔레그램입니다. 카카오톡으로 보내려면 탭마다 `전송 방식`을 `카카오톡`으로 바꾸고 `카카오톡 채팅방명`을 입력해야 합니다. 메시지 전송이 켜진 활성 카카오톡 탭은 `카카오톡 채팅방명`이 비어 있으면 설정 저장이 막히고, 메시지 전송이 켜진 활성 카카오톡 탭끼리 같은 채팅방명을 쓸 수도 없습니다. 카카오톡 전송 시에는 입력한 채팅방명과 제목이 정확히 일치하는 카카오톡 채팅방 창 하나만 골라 보냅니다. 같은 이름의 창이 여러 개 열려 있거나 카카오톡 창 목록 자체를 조회하지 못하면 임의의 창으로 보내지 않고 즉시 실패합니다. 일치하는 창이 아직 없으면 카카오톡 메인창에서 채팅방을 검색해 연 뒤 다시 정확히 일치하는 창인지 확인하고 보냅니다.

이전 버전에서 저장한 설정에 `messenger_name`이 없고 텔레그램 봇 토큰·채팅방 ID도 없이 `카카오톡 채팅방명`만 있으면, 메시지 전송 여부와 상관없이 카카오톡으로 불러옵니다. 이렇게 해야 기존 카카오톡 설정이 텔레그램으로 잘못 인식되지 않습니다.

앱은 탭마다 다른 Chrome 프로필과 CDP 포트를 사용해 여러 활성 탭을 동시에 크롤링합니다. 같은 탭에서 중복 실행이 들어오면 건너뜁니다. 카카오톡 전송은 공통 잠금으로, 텔레그램 전송과 명령 답장은 봇 토큰별 잠금으로 순서대로 처리합니다.

텔레그램 `getUpdates`는 봇 토큰 하나가 하나의 업데이트 큐를 공유합니다. 같은 봇 토큰을 여러 앱 프로세스에서 나눠 쓰지 마세요. 한 앱 안의 여러 탭에서 같은 봇 토큰을 공유하는 것은 지원하며, 이때 활성 탭마다 `chat_id + 토픽 ID` 조합은 달라야 합니다.

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

봇은 명령이 들어온 채팅방 또는 토픽에 연결된 탭만 다시 크롤링합니다. 탭마다 배민 계정과 전송 대상이 독립적으로 동작하므로, 다른 탭의 라이더 정보는 함께 조회하지 않습니다. 찾지 못하면 `해당 라이더를 찾지 못했습니다.`라고 답장합니다.

라이더 조회 명령은 배민 배달현황 테이블을 읽어 동작하므로 **배민 탭에서만** 지원합니다. 쿠팡이츠 탭에 연결된 채팅방에서 명령을 보내면 `라이더 조회 명령은 배민 탭에서만 지원합니다.`라고 답장합니다.

취소율 계산식은 `총 취소 수 / (완료 + 거절 + 총 취소 수) * 100`입니다. 총 취소 수는 `배차취소 + 배달취소(라이더귀책)`입니다. 취소율이 4% 이상이면 `위험합니다.`, 아니면 `정상 범위입니다.`라고 표시합니다.

## 1회 CLI 실행

UI 없이 `.env` 또는 환경변수 설정으로 1회만 실행하려면 다음 명령을 사용합니다. 여러 계정을 CLI로 따로 실행할 때는 계정마다 `CDP_URL`, `BROWSER_USER_DATA_DIR`, `CRAWL_NAME`, `STATE_SUBDIR`를 다르게 지정하세요.

먼저 `PERFORMANCE_PLATFORM`으로 플랫폼을 고르고, 플랫폼별로 아래 변수를 설정합니다.

### 공통 변수

| 변수 | 설명 |
| --- | --- |
| `PERFORMANCE_PLATFORM` | `baemin`(기본) 또는 `coupang` |
| `BROWSER_MODE` | `cdp`(기본) 또는 `persistent` |
| `CDP_URL` | CDP 주소(기본 `http://127.0.0.1:9222`). 로컬 주소만 허용 |
| `BROWSER_USER_DATA_DIR` | 계정별 분리 브라우저 프로필 경로 |
| `MESSENGER_NAME` | `telegram`(기본) 또는 `kakao` |
| `SEND_ENABLED` / `SEND_ONLY_ON_CHANGE` | 전송 여부 / 변경 시에만 전송 |
| `CRAWL_NAME` / `STATE_SUBDIR` | 메시지 라벨 / 탭별 상태 분리 디렉터리 |

### 배민(`PERFORMANCE_PLATFORM=baemin`)

| 변수 | 설명 |
| --- | --- |
| `BAEMIN_DELIVERY_HISTORY_URL` | 배민 달성현황 주 URL(미설정 시 기본값 `https://deliverycenter.baemin.com/delivery/report`; 변수명은 이전 버전 호환용) |
| `BAEMIN_CENTER_NAME` / `BAEMIN_CENTER_ID` | 센터 검증용 센터명/센터 ID |

> 배민에서는 `PEAK_DASHBOARD_URL`을 설정해도 무시되고 빈 값으로 둡니다(쿠팡 전용 보조 URL). 이렇게 해야 UI 배민 설정과 중복 감지(scope hash)가 일치합니다.

### 쿠팡이츠(`PERFORMANCE_PLATFORM=coupang`)

| 변수 | 설명 |
| --- | --- |
| `COUPANG_EATS_URL` | 쿠팡 실적 주 URL(기본 `https://partner.coupangeats.com/page/rider-performance`) |
| `PEAK_DASHBOARD_URL` | 쿠팡 피크 대시보드 보조 URL(기본 `https://partner.coupangeats.com/page/peak-dashboard`) |
| `BAEMIN_CENTER_NAME` | 쿠팡 탭에서는 **기대 센터/상점명**으로 재사용됩니다(exact match, `;`·줄바꿈으로 alias 나열 가능). 실적·피크 화면 센터명이 모두 일치하는지 검증합니다. **쿠팡에서는 필수**이며, 미설정이거나 배민 기본값(`표준서울마포...`)이면 실행 시 설정 오류가 납니다(`--once` CLI 포함). 배민 기본값을 기본으로 넣지 않습니다 |
| `BAEMIN_CENTER_ID` | 쿠팡 탭에서는 사용하지 않습니다(빈 값) |

> `PERFORMANCE_URL`을 직접 설정하면 플랫폼과 무관하게 주 URL로 우선 사용됩니다.

상태 파일 루트 정책: run lock과 마지막 메시지 해시는 `LOG_DIR`의 형제 디렉터리(`<LOG_DIR>/../runtime/`)에 두어 탭/스코프별로 분리·격리합니다. 따라서 계정을 커스텀 로그 경로로 나눌 때(`LOG_DIR=C:\acct1\logs`, `LOG_DIR=C:\acct2\logs`)는 lock/해시도 계정별로 자동 분리됩니다. CLI로 여러 계정을 따로 실행한다면 계정마다 `LOG_DIR`를 서로 다른 상위 폴더로 지정하세요(예: `C:\acct1\logs`, `C:\acct2\logs`). 반면 텔레그램 offset/lock은 "봇 토큰별 단일·탭 독립"이라 작업 디렉터리(cwd)와 무관한 고정 루트에 둡니다. 고정 루트는 `RIDER_CRAWL_STATE_ROOT` 환경변수로 바꿀 수 있으며, 미설정 시 프로젝트 루트(개발용) 또는 `~/.rider_crawl`을 씁니다. 두 상태군의 루트가 다른 것은 의도된 설계입니다.

Windows:

```bat
.venv\Scripts\python.exe -m rider_crawl --once
```

macOS:

```bash
.venv/bin/python -m rider_crawl --once
```

## 실행 파일(exe) 재빌드

배포용 단일 실행 파일은 `dist/rider_crawl_onefile.exe`로 제공됩니다. 깨끗한 환경에서 다시 빌드하려면 PyInstaller로 루트의 `rider_crawl_onefile.spec`을 실행합니다. 진입점은 루트의 `rider_crawl_exe_entry.py`이며 spec이 이 파일을 참조합니다(`pathex=['src']`로 `rider_crawl` 패키지를 임포트).

Windows:

```bat
.venv\Scripts\pip.exe install pyinstaller
.venv\Scripts\pyinstaller.exe rider_crawl_onefile.spec
```

빌드 결과는 `dist/rider_crawl_onefile.exe`에 생성됩니다. PyInstaller가 만드는 `build/` 디렉터리는 재생성되는 임시 산출물이라 버전 관리에 포함하지 않습니다.

## 수집 방식

- CDP Chrome에 연결해 대상 페이지의 HTML을 읽습니다.
- 배민 탭: 달성현황(beta) 페이지의 Looker/Google 보고서 프레임을 읽고, `주간 배달 현황`에서 설정한 센터 ID의 날짜 행을 찾습니다. 오늘 행에 실적이 있으면 오늘 행을 쓰고, 오늘 행이 아직 0이면 오늘 이전의 가장 최근 실적 행을 사용합니다. 수락률은 `100 - 수락률`로 거절율을 계산합니다.
- 쿠팡이츠 탭: `rider-performance`(실적)와 `peak-dashboard`(피크 대시보드) 두 페이지를 모두 읽어 실시간 실적 메시지를 만듭니다. 실적 페이지에서 수행 중인 인원을, 피크 대시보드에서 업데이트 시각·배정/처리 물량·거절률·피크타임별 목표/완료를 읽습니다.
- CDP 연결이 실패하면 새 로그인 창을 만들지 않고 오류를 표시합니다. CDP로 붙은 Chrome은 사용자가 직접 띄운 창이므로 수집 후 닫지 않습니다.
- 쿠팡이츠 로그인이 만료되면 기본적으로는 자동 로그인이나 2차 인증 처리를 하지 않고 해당 크롤링 탭의 반복 실행을 중지합니다. Chrome에서 다시 로그인한 뒤 `rider-performance`와 `peak-dashboard` 두 페이지를 로그인된 상태로 열어두고 `시작`을 다시 누르세요.
- (선택) `COUPANG_AUTO_EMAIL_2FA_ENABLED=true`로 켜면 쿠팡이츠 로그인 만료를 감지했을 때 자동 복구를 한 번 시도합니다. 1차 로그인 화면이면 `COUPANG_CREDENTIALS_PATH`의 계정 파일로 로그인하고, 이어서 이메일 인증을 선택하고 인증번호 발송 후, Gmail API(`gmail.readonly`)로 발송 시각 이후 도착한 인증번호 메일을 읽어 입력합니다. 인증에 성공하면 대상 페이지를 다시 준비시켜 수집을 이어가고, 실패하거나 CAPTCHA 화면이면 기존처럼 탭을 중지합니다.
  - 사전 준비: Google Cloud Console에서 OAuth Desktop 클라이언트와 Gmail API를 만든 뒤 클라이언트 JSON을 `secrets/google/credentials.gmail.json`에 두고, 최초 1회 로컬에서 Gmail 승인을 실행해 `secrets/google/token.gmail.json`을 만듭니다. 이 인증 파일은 Git에 올리지 않습니다.
  - 관련 환경변수: `COUPANG_CREDENTIALS_PATH`, `GMAIL_CREDENTIALS_PATH`, `GMAIL_TOKEN_PATH`, `GMAIL_2FA_QUERY`(실제 발신자/제목에 맞게 좁히기), `GMAIL_2FA_POLL_SECONDS`, `GMAIL_2FA_POLL_INTERVAL_SECONDS`, `COUPANG_2FA_CODE_DIGITS`. 인증번호와 토큰 값은 로그에 남기지 않습니다.
