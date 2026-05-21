# 2026-05-21 작업 히스토리 및 인수인계

이 문서는 새 세션에서 바로 이어가기 위한 상황 기록이다.

## 1. 프로젝트 목적

쿠팡이츠 파트너 실적 페이지에서 실시간 라이더 실적을 수집해 카카오톡 단체방에 텍스트 메시지로 전송하는 로컬 PC 자동화 프로그램을 만든다.

대상 URL:

- 실적 페이지: `https://partner.coupangeats.com/page/rider-performance`
- 피크 대시보드: `https://partner.coupangeats.com/page/peak-dashboard`

초기 요청은 카카오톡 이미지 메시지였지만, 이후 사양이 바뀌어 현재 기본 범위는 **텍스트 메시지 전송**이다.

## 2. 현재 사양 요약

- 쿠팡이츠 사이트는 로그인 시 2차 인증이 있다.
- 자동 로그인은 하지 않는다.
- 사용자가 이미 로그인해 둔 페이지를 활용해야 한다.
- 프로그램은 UI를 제공해야 한다.
- UI에서 필요한 설정을 입력하고 `시작`을 누르면 즉시 1회 실행 후 기본 35분 간격으로 반복한다.
- Windows 작업 스케줄러는 필수로 쓰지 않는다.
- 환경변수나 `.env`가 아니라 UI에서 설정을 저장해야 한다.
- 카카오톡 PC 앱에 텍스트를 붙여넣고 Enter로 보내는 방식이 기본안이다.

## 3. 현재 구현 상태

생성된 주요 파일:

- `pyproject.toml`: Python 패키지/의존성 정의
- `src/rider_crawl/config.py`: 기존 환경변수 기반 설정 모델
- `src/rider_crawl/ui_settings.py`: UI 설정 JSON 저장/로드
- `src/rider_crawl/ui.py`: Tkinter UI
- `src/rider_crawl/scheduler.py`: 시작 후 반복 실행 루프
- `src/rider_crawl/crawler.py`: Playwright 기반 크롤러
- `src/rider_crawl/parser.py`: HTML/텍스트 파서
- `src/rider_crawl/message.py`: 카카오톡 텍스트 메시지 생성
- `src/rider_crawl/sender.py`: Windows 카카오톡 UI 전송 골격
- `src/rider_crawl/app.py`: 1회 실행 흐름
- `docs/rider-performance-bot-spec.md`: 최신 사양 문서
- `docs/plans/2026-05-21-rider-crawl-implementation.md`: 초기 구현 계획
- `docs/plans/2026-05-21-ui-runner-implementation.md`: UI 실행기 구현 계획

테스트 파일:

- `tests/test_parser.py`
- `tests/test_message.py`
- `tests/test_config.py`
- `tests/test_lock.py`
- `tests/test_app.py`
- `tests/test_crawler.py`
- `tests/test_sender.py`
- `tests/test_ui_settings.py`
- `tests/test_scheduler.py`
- `tests/test_ui_helpers.py`

최근 전체 테스트 결과:

```text
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.10 --extra dev pytest -q
25 passed in 0.13s
```

## 4. 현재 UI 상태

Mac에서 UI 실행까지 확인했다.

실행 명령:

```bash
cd /Users/sooyeol/Desktop/dev_busi/rider_crawl
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.10 python -m rider_crawl
```

현재 UI 설정 파일:

```json
{
  "performance_url": "https://partner.coupangeats.com/page/rider-performance",
  "peak_dashboard_url": "https://partner.coupangeats.com/page/peak-dashboard",
  "browser_user_data_dir": "runtime/browser-profile",
  "headless": false,
  "kakao_chat_name": "라이더 auto",
  "log_dir": "logs",
  "send_enabled": false,
  "send_only_on_change": false,
  "interval_minutes": 1,
  "timezone": "Asia/Seoul",
  "run_lock_timeout_seconds": 900,
  "page_timeout_seconds": 60000
}
```

주의:

- Mac 테스트라 `send_enabled=false`로 맞췄다.
- `interval_minutes=1`은 테스트용 값이다. 운영 기본값은 35분이다.
- 카카오톡 자동 전송은 현재 Windows 전용 가드가 있다.

현재 프로세스 확인 당시:

```text
uv run --python 3.10 python -m rider_crawl
.venv/bin/python3 -m rider_crawl
```

## 5. 시도했던 방식

### 5.1 사양 문서 작성

처음에는 `docs/rider-performance-bot-spec.md`를 만들었다.

초기 문서는 PNG 이미지 생성 후 카카오톡 전송 기준이었다. 이후 사용자가 문서를 업데이트했고, 현재는 텍스트 메시지 전송과 UI 설정 방식으로 바뀌었다.

### 5.2 실제 열린 페이지 확인

Mac Chrome에서 `https://partner.coupangeats.com/page/rider-performance` 페이지가 로그인 상태로 열리는 것을 확인했다.

화면에서 읽힌 예시 데이터:

```json
{
  "센터": "제이앤에이치플러스 의정부남부",
  "날짜": "5월 21일(오늘)",
  "현재구간": "오후논피크(13:00~16:55)",
  "상태": "할당량 소진 중",
  "업데이트시각": "14:02",
  "참여가능": "7 / 25명",
  "대기": "0명",
  "총라이더": "29명",
  "온라인": "7명",
  "거절_무시": "2.4건",
  "취소": "0건",
  "완료": "102.4건",
  "순서미준수": "0건",
  "점심피크": "60.6건",
  "저녁피크": "0건",
  "논피크": "41.8건",
  "비활성라이더": "0명"
}
```

이 화면을 기준으로 현재 메시지 렌더러는 아래 형식을 만든다.

```text
[실시간 실적봇]
⏰ 14:02 기준

오후논피크 : 7명/25명
대기 : 0명

완료 : 102.4건
거절/무시 : 2.4건
취소 : 0건
점심피크 : 60.6건
저녁피크 : 0건
논피크 : 41.8건
수행중인인원 : 5명
```

### 5.3 Python 패키지와 테스트 구성

`uv`로 Python 3.10 가상환경을 만들었고, TDD로 파서/메시지/UI 설정/스케줄러를 구현했다.

중요한 의존성 이슈:

- 문서에는 `scrapling[fetchers]==0.4.8`라고 되어 있었다.
- 하지만 `scrapling[fetchers]==0.4.8`은 내부적으로 `playwright==1.59.0`을 요구한다.
- 프로젝트는 `playwright==1.60.0`을 쓰기 때문에 충돌했다.
- 그래서 구현에서는 `scrapling==0.4.8`만 사용하고, Playwright fetcher extras는 쓰지 않도록 변경했다.

현재 `pyproject.toml` 핵심:

```toml
dependencies = [
  "playwright==1.60.0",
  "scrapling==0.4.8",
  "python-dotenv>=1.1.0",
  "pyperclip>=1.9.0",
  "pyautogui>=0.9.54",
  "pywinauto>=0.6.8; platform_system == 'Windows'",
]
```

### 5.4 UI 구현

Tkinter UI를 구현했다.

UI 항목:

- 실적 URL
- 피크 대시보드 URL
- 브라우저 프로필 경로
- 로그 경로
- 카카오톡 채팅방명
- 실행 간격
- 페이지 타임아웃
- 락 타임아웃
- Headless
- 카카오톡 전송
- 변경 시에만 전송
- 설정 저장
- 1회 실행
- 시작
- 중지
- 메시지 미리보기와 로그

UI 설정은 `runtime/state/ui_settings.json`에 저장된다.

### 5.5 Playwright 브라우저 설치 문제

Mac에서 1회 실행 시 아래 오류가 발생했다.

```text
BrowserType.launch_persistent_context: Executable doesn't exist at ...
Looks like Playwright was just installed or updated.
Please run the following command to download new browsers:

    playwright install
```

원인:

- Playwright Python 패키지는 설치됐지만 실제 Chromium 바이너리가 없었다.

처리:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.10 playwright install chromium
```

검증:

```text
Chrome for Testing 148.0.7778.96 downloaded
```

추가 검증:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.10 python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    print(browser.version)
    browser.close()
PY
```

결과:

```text
148.0.7778.96
```

## 6. 현재 문제

가장 큰 문제는 현재 크롤러 방식이다.

현재 `src/rider_crawl/crawler.py`는 아래 방식이다.

```python
context = playwright.chromium.launch_persistent_context(
    str(config.browser_user_data_dir),
    headless=config.headless,
)
```

즉, 일반 사용자가 이미 로그인해 둔 Chrome이 아니라 **Playwright 전용 Chrome for Testing + 별도 프로필**을 연다.

이 방식의 문제:

- 쿠팡이츠는 로그인 시 2차 인증이 있다.
- Playwright 전용 브라우저는 일반 Chrome과 다르게 인식될 수 있다.
- `runtime/browser-profile`에 세션이 저장되어도 사이트가 매 실행마다 재인증을 요구할 수 있다.
- 실제로 사용자가 1회 실행 실패 후 다시 1회 실행을 누르자 또 2차 로그인을 요구했다.

결론:

**앱 전용 Playwright 프로필 방식은 이 사이트에는 적합하지 않다.**

## 7. 원인 분석

반복 2차 인증의 직접 원인은 “프로필 저장이 안 됨”이라고 단정하기 어렵다.

더 가능성 높은 원인:

- 쿠팡이츠가 Playwright Chrome for Testing 또는 새 브라우저 프로필을 신뢰하지 않는다.
- 사용자가 평소 로그인해 둔 실제 Chrome 프로필이 아니라 별도 프로필을 사용한다.
- 매 실행 시 사이트 보안 정책이 재인증을 요구한다.

따라서 자동 로그인이나 쿠키 저장을 더 만지는 방향은 좋지 않다. 계정/2차 인증/보안 정책을 건드리려는 접근이 되기 쉽고, 사용자가 원한 “이미 로그인된 현재 페이지 기준”과도 다르다.

## 8. 다음 권장 방향

다음 세션에서는 크롤러 방식을 바꾸는 것이 우선이다.

권장 방식:

**현재 열려 있는 Chrome에 붙어서 읽기**

구체적으로는 Chrome을 원격 디버깅 포트로 실행하고, Playwright가 CDP로 연결한다.

Mac 예시:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222
```

Windows 예시:

```bat
chrome.exe --remote-debugging-port=9222
```

Playwright 연결 방식:

```python
browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
```

그 뒤:

1. 열린 탭 목록을 순회한다.
2. URL이 `partner.coupangeats.com/page/rider-performance`인 탭을 찾는다.
3. 해당 탭의 `page.content()`를 읽는다.
4. 기존 `parse_current_screen_html()`로 파싱한다.
5. 탭이 없으면 UI에 “실적 페이지를 열어주세요” 오류를 보여준다.

이 방식의 장점:

- 사용자가 실제로 로그인해 둔 Chrome 세션을 그대로 사용한다.
- 2차 인증 반복 가능성이 크게 줄어든다.
- “현재 열려있는 크롬창 기준”이라는 사용자 요구와 맞다.
- 자동 로그인/쿠키 저장을 시도하지 않는다.

주의:

- Chrome이 반드시 원격 디버깅 포트로 실행되어 있어야 한다.
- 이미 일반 방식으로 켜진 Chrome에는 CDP로 붙을 수 없다.
- 운영 PC에서는 Chrome 바로가기에 `--remote-debugging-port=9222`를 붙이는 식으로 안내하는 것이 좋다.

## 9. 다음 세션에서 구현할 일

### 9.1 UI 설정 변경

`UiSettings`에 브라우저 연결 방식을 추가한다.

예:

```python
browser_mode: str = "cdp"
cdp_url: str = "http://127.0.0.1:9222"
```

UI 항목 추가:

- 브라우저 연결 방식
  - 현재 Chrome에 연결, 기본값
  - 앱 전용 브라우저, 예비용
- CDP 주소
  - 기본 `http://127.0.0.1:9222`

기존 `브라우저 프로필 경로`는 앱 전용 브라우저 모드에서만 의미가 있다.

### 9.2 크롤러 변경

현재:

```python
fetch_page_html(config)
```

변경:

- `fetch_page_html(config)`가 설정에 따라 분기한다.
- `browser_mode == "cdp"`이면 `connect_over_cdp` 사용.
- `browser_mode == "persistent"`이면 기존 `launch_persistent_context` 사용.

필요한 함수 예:

```python
def fetch_page_html_via_cdp(config: AppConfig) -> str:
    ...

def _find_page_by_url(browser, url_part: str):
    ...
```

### 9.3 테스트 추가

외부 Chrome을 실제로 띄우지 않는 단위 테스트를 먼저 만든다.

테스트 아이디어:

- `UiSettings.defaults()`의 기본 `browser_mode`가 `cdp`인지 확인
- `UiSettings.to_app_config()`가 `cdp_url`과 `browser_mode`를 전달하는지 확인
- `_select_page_by_url()` 같은 순수 함수로 URL 매칭 테스트
- CDP 연결 실패 시 명확한 오류 메시지 테스트

### 9.4 문서 업데이트

`README.md`와 `docs/rider-performance-bot-spec.md`에 아래 내용을 반영한다.

- 기본 권장 방식은 현재 Chrome 연결이다.
- Chrome은 원격 디버깅 포트로 실행해야 한다.
- 앱 전용 브라우저 방식은 보조 옵션이다.
- 2차 인증 때문에 앱 전용 브라우저 방식은 재인증이 반복될 수 있다.

## 10. 현재 남은 리스크

- 카카오톡 자동 전송은 아직 Windows 실제 환경에서 검증하지 않았다.
- Mac에서는 카카오톡 전송이 의도적으로 차단되어 있다.
- 현재 파서는 실제 페이지 구조 전체가 아니라, 확인된 화면 텍스트와 fixture 기반이다.
- 피크 대시보드에서 `배정/처리/거절율`을 합쳐야 하는 요구가 문서에 일부 남아 있으나, 아직 구현되지 않았다.
- 현재 Chrome CDP 연결 방식은 아직 구현되지 않았다. 다음 세션의 최우선 작업이다.

## 11. 새 세션 시작용 요약

새 세션에서 바로 할 일:

1. 이 문서를 읽는다.
2. 현재 테스트를 돌려 기준 상태를 확인한다.

```bash
cd /Users/sooyeol/Desktop/dev_busi/rider_crawl
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.10 --extra dev pytest -q
```

3. 앱 전용 Playwright 프로필 방식을 기본값에서 내린다.
4. UI와 설정에 `현재 Chrome 연결(CDP)` 모드를 추가한다.
5. `connect_over_cdp("http://127.0.0.1:9222")`로 열린 Chrome 탭을 읽도록 크롤러를 바꾼다.
6. Chrome 실행 안내를 README와 사양 문서에 추가한다.

추천 최종 방향:

```text
기본값: 현재 Chrome에 연결(CDP)
예비값: 앱 전용 브라우저 프로필
```

