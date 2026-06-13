# 쿠팡이츠 Gmail 2차 인증 자동 입력 구현 문서

> ⚠️ **구 OAuth 방식 — 현행 아님(역사 문서).** 이 문서는 Gmail API(OAuth/토큰 파일) 기반의
> 초기 구현을 기록한 것입니다. 현재는 **Gmail/Naver를 IMAP(주소+앱 비밀번호)로 통일**하고,
> 자격증명을 UI 각 크롤링 탭에서 직접 입력받습니다. Gmail OAuth/토큰 파일과 관련 환경변수는
> 더 이상 사용하지 않습니다. 현행 설계·지시서는 `docs/imap/MERGE_PLAN_naver_2fa.md`,
> 사용법은 루트 `README.md`를 참고하세요.

## 목적

쿠팡이츠 파트너 웹사이트가 약 6시간마다 로그아웃되는 상황을 감지한 뒤, 이메일 2차 인증을 선택하고 Gmail에서 인증번호를 읽어 입력한다. 인증이 끝나면 기존 쿠팡이츠 수집 흐름(`rider-performance` + `peak-dashboard`)을 다시 실행한다.

이 문서는 구현 범위, 기술스택, 파일 배치, 보안 규칙을 정리한다. 실제 Google 인증 파일은 프로젝트 안의 `secrets/google/` 폴더에 따로 둔다.

## 현재 코드 확인 결과

기존 로그아웃 감지 코드는 이미 있다.

- `src/rider_crawl/platforms/coupang/crawler.py`
  - `_page_looks_like_coupang_login_required(page)`: 현재 URL과 HTML을 보고 로그인 필요 상태를 판단한다.
  - `_url_looks_like_coupang_login_required(url)`: `xauth.coupang.com/auth/realms/eats-partner` 또는 `partner.coupangeats.com`의 `login`, `signin`, `auth` 경로를 감지한다.
  - `_html_looks_like_coupang_login_required(html)`: `세션이 만료`, `다시 로그인`, `로그인이 필요`, `sign in to eats-partner`, vendor portal 로그인 폼 구조를 감지한다.
  - `_wait_for_target_page_ready(...)`: 대상 텍스트 대기 실패 시 로그인 화면이면 `BrowserActionRequiredError`를 발생시킨다.
  - `_raise_coupang_page_action_required(...)`: 대상 탭을 못 찾았을 때 로그인 페이지가 열려 있으면 로그인 필요 오류를 발생시킨다.
- `src/rider_crawl/ui.py`
  - 현재는 `BrowserActionRequiredError`가 발생하면 해당 탭 반복 실행을 중지한다.
- `README.md`
  - 현재 운영 정책은 "쿠팡이츠 로그인 만료 시 자동 로그인이나 2차 인증 처리를 하지 않고 중지"다.

이번 구현은 이 정책을 바꿔, 쿠팡이츠 탭에서만 "로그인 필요 감지 후 자동 이메일 인증 복구"를 추가한다.

## 구현 범위

### 포함

- 쿠팡이츠 로그인 만료 감지 재사용
- 로그인/인증 화면에서 이메일 인증 방식 선택
- 인증번호 발송 버튼 클릭
- Gmail API로 새 인증번호 메일 조회
- 인증번호 파싱 후 쿠팡이츠 화면에 입력
- 인증 성공 뒤 `rider-performance`와 `peak-dashboard` 페이지 준비 상태 확인
- 인증 실패 시 기존처럼 운영자 조치가 필요한 오류로 중지

### 제외

- CAPTCHA 자동 해결
- 쿠팡 계정 보안 정책 우회
- Google/Gmail 비밀번호 저장
- 쿠팡 비밀번호를 평문 파일에 저장
- 인증번호나 토큰 값을 로그에 출력

쿠팡 로그인 폼에서 아이디/비밀번호 재입력이 꼭 필요한 경우는 별도 결정이 필요하다. 1차 구현은 "기존 Chrome 프로필 세션이 살아 있고 이메일 인증만 요구되는 상태"를 우선 지원한다. 완전 로그아웃으로 아이디/비밀번호가 필요한 경우에는 Windows Credential Manager 같은 OS 보안 저장소 사용을 별도 구현으로 둔다.

## 기술스택

| 영역 | 기술 | 용도 |
| --- | --- | --- |
| 언어 | Python 3.10+ | 기존 프로젝트 기준 |
| 브라우저 자동화 | Playwright `sync_api` | 기존 CDP Chrome 연결, 쿠팡이츠 인증 화면 조작 |
| 환경변수 | `python-dotenv` | 기존 `.env` 설정 로딩 |
| Gmail 연동 | `google-api-python-client` | Gmail API 호출 |
| Google OAuth | `google-auth-oauthlib`, `google-auth-httplib2` | 로컬 OAuth 승인과 토큰 갱신 |
| 메일 파싱 | Python 표준 `base64`, `email`, `re` | Gmail 메시지 본문에서 인증번호 추출 |
| 테스트 | `pytest` | 로그인 감지, Gmail 파싱, 재인증 흐름 단위 테스트 |

추가 의존성 후보:

```toml
dependencies = [
  "google-api-python-client>=2.0.0",
  "google-auth-oauthlib>=1.0.0",
  "google-auth-httplib2>=0.2.0",
]
```

## Google 인증 파일 보관 위치

프로젝트 안에 별도 폴더를 둔다.

```text
secrets/
  google/
    README.md
    credentials.gmail.json  # Git 제외
    token.gmail.json        # Git 제외
```

`.gitignore`에서 `secrets/google/*.json`, `secrets/google/token*`, `secrets/google/credentials*`를 제외한다. 실제 인증 파일은 운영 PC에만 둔다.

Gmail OAuth scope는 최소 권한만 사용한다.

```text
https://www.googleapis.com/auth/gmail.readonly
```

## 설정값

추가할 환경변수 또는 UI 설정 후보:

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `COUPANG_AUTO_EMAIL_2FA_ENABLED` | `false` | 쿠팡이츠 이메일 2차 인증 자동 입력 사용 여부 |
| `GMAIL_CREDENTIALS_PATH` | `secrets/google/credentials.gmail.json` | OAuth 클라이언트 파일 경로 |
| `GMAIL_TOKEN_PATH` | `secrets/google/token.gmail.json` | OAuth 토큰 저장 경로 |
| `GMAIL_2FA_QUERY` | `from:(coupang.com) newer_than:10m` | 인증번호 메일 검색 쿼리 |
| `GMAIL_2FA_POLL_SECONDS` | `120` | 인증번호 메일을 기다리는 최대 시간 |
| `GMAIL_2FA_POLL_INTERVAL_SECONDS` | `5` | Gmail 재조회 간격 |
| `COUPANG_2FA_CODE_DIGITS` | `6` | 인증번호 자리수 |

쿼리는 운영 메일의 실제 발신자와 제목을 확인한 뒤 더 좁게 잡는다. 예를 들면 `from:(no-reply@...) subject:(인증)`처럼 좁힐 수 있다.

## 모듈 설계

### `src/rider_crawl/auth/gmail.py`

역할:

- Google OAuth 인증 파일과 토큰 파일을 읽는다.
- 토큰이 만료되면 refresh 한다.
- Gmail API로 인증번호 메일을 검색한다.
- 메일 본문에서 인증번호를 추출한다.

주요 함수:

```python
def fetch_latest_verification_code(
    *,
    credentials_path: Path,
    token_path: Path,
    query: str,
    requested_after: datetime,
    poll_seconds: int,
    poll_interval_seconds: int,
    code_digits: int,
) -> str:
    ...
```

중요 규칙:

- `requested_after` 이후에 도착한 메일만 사용한다.
- 여러 코드가 있으면 가장 최신 메일만 사용한다.
- 인증번호 원문은 로그에 남기지 않는다.

### `src/rider_crawl/auth/coupang_email_2fa.py`

역할:

- 현재 Playwright page가 쿠팡 인증 화면인지 확인한다.
- 이메일 인증 방식을 선택한다.
- 인증번호 발송 버튼을 누른다.
- Gmail에서 받은 코드를 인증번호 입력칸에 넣고 제출한다.
- 성공하면 대상 페이지가 다시 준비됐는지 확인한다.

주요 함수:

```python
def recover_coupang_session_with_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    fetch_code: Callable[..., str] | None = None,
) -> bool:
    ...
```

성공하면 `True`, 자동 복구할 수 없는 화면이면 `False`를 반환한다. 예외가 필요한 경우에도 인증번호와 토큰 값은 메시지에 넣지 않는다.

### `src/rider_crawl/platforms/coupang/crawler.py`

변경 지점:

- `_fetch_target_page_content(...)`에서 로그인 필요 상태를 감지하면 바로 `BrowserActionRequiredError`를 내기 전에 자동 복구를 시도한다.
- 복구가 성공하면 대상 URL을 다시 열거나 기존 탭을 새로고침한 뒤 `_wait_for_target_page_ready(...)`를 다시 실행한다.
- 복구가 실패하면 기존처럼 `BrowserActionRequiredError`를 발생시킨다.

흐름:

```text
대상 탭 선택
  -> 로그인 필요 감지
  -> COUPANG_AUTO_EMAIL_2FA_ENABLED 확인
  -> 이메일 2FA 복구 시도
  -> 성공: 원래 페이지 준비 확인 후 HTML 반환
  -> 실패: BrowserActionRequiredError
```

### `src/rider_crawl/config.py`

`AppConfig`에 Gmail/2FA 설정을 추가한다. 기본값은 자동 인증 비활성화다. 기존 운영 환경이 갑자기 로그인 자동화를 시도하지 않게 하기 위해서다.

### `src/rider_crawl/ui.py`

1차 구현에서는 환경변수만으로 켤 수 있게 두는 편이 안전하다. UI 항목은 운영 검증 뒤 추가한다.

자동 인증이 켜져 있고 복구가 성공하면 탭 반복 실행은 계속된다. 복구 실패, Gmail 인증 실패, CAPTCHA, 알 수 없는 화면은 기존과 같이 오류를 표시하고 해당 탭을 중지한다.

## 인증 흐름

```text
1. 스케줄러가 쿠팡이츠 탭을 실행한다.
2. 기존 쿠팡 크롤러가 rider-performance 또는 peak-dashboard를 읽는다.
3. URL/HTML/대상 텍스트 대기로 로그인 만료를 감지한다.
4. 자동 이메일 2FA가 꺼져 있으면 기존처럼 중지한다.
5. 자동 이메일 2FA가 켜져 있으면 쿠팡 인증 화면을 찾는다.
6. 이메일 인증 방식을 선택한다.
7. 인증번호 발송 버튼을 클릭하고 요청 시각을 기록한다.
8. Gmail API가 요청 시각 이후 도착한 인증 메일을 polling 한다.
9. 메일 본문에서 인증번호를 추출한다.
10. 쿠팡 화면 인증번호 입력칸에 코드를 입력하고 제출한다.
11. 인증 성공 후 rider-performance와 peak-dashboard를 다시 준비시킨다.
12. 기존 파서와 메시지 전송 흐름을 그대로 실행한다.
```

## Gmail 인증번호 조회 규칙

잘못된 과거 인증번호를 쓰지 않기 위해 아래 조건을 모두 적용한다.

- 인증번호 발송 버튼을 누른 뒤의 시각을 `requested_after`로 기록한다.
- Gmail 검색 쿼리는 `after`, `newer_than`, 발신자, 제목을 함께 사용해 좁힌다.
- 메일 `internalDate`가 `requested_after`보다 이른 메일은 버린다.
- 인증번호 정규식은 주변 단어를 함께 본다.

예시 정규식:

```python
r"(?:인증번호|verification code|code)[^\d]{0,20}(\d{6})"
```

주변 단어 기반 추출이 실패할 때만 `\b\d{6}\b` fallback을 쓴다.

## 오류 처리

| 상황 | 처리 |
| --- | --- |
| Gmail OAuth 파일 없음 | 자동 복구 중단, 설정 오류 메시지 표시 |
| Gmail 토큰 만료 + refresh 실패 | 자동 복구 중단, Google 재승인 요청 |
| 인증 메일 미도착 | 자동 복구 중단, 기존 로그인 필요 오류 표시 |
| 인증번호 파싱 실패 | 자동 복구 중단, 메일 검색 조건/본문 변경 필요 로그 |
| 쿠팡 화면에 CAPTCHA 표시 | 자동 복구 중단, 운영자 직접 처리 |
| 아이디/비밀번호 입력 화면 | 1차 구현에서는 자동 복구 중단 |
| 인증번호 입력 후 실패 | 한 번만 재시도하거나 운영자 조치로 중지 |

실패 시 5초마다 빠르게 재시도하지 않는다. Gmail과 쿠팡 인증 요청이 반복 발송될 수 있기 때문이다. 실패하면 현재처럼 `BrowserActionRequiredError`로 탭을 중지한다.

## 테스트 계획

- `tests/test_coupang_crawler.py`
  - 로그인 필요 감지 시 자동 2FA가 꺼져 있으면 기존처럼 `BrowserActionRequiredError`
  - 자동 2FA가 켜져 있고 복구 함수가 성공하면 대상 페이지를 다시 읽음
  - 복구 함수가 실패하면 `BrowserActionRequiredError`
- `tests/test_gmail_2fa.py`
  - Gmail message payload에서 plain text/body/base64url 본문 추출
  - 요청 시각 이전 메일 제외
  - 여러 코드 중 최신 코드 선택
  - 인증번호 주변 단어 우선, 숫자 fallback은 제한적으로 사용
- `tests/test_coupang_email_2fa.py`
  - 이메일 인증 선택 버튼 클릭
  - 인증번호 발송 버튼 클릭
  - 인증번호 입력/제출
  - CAPTCHA 또는 아이디/비밀번호 화면에서는 실패 반환

## 운영 준비 순서

1. Google Cloud Console에서 OAuth Desktop Client를 만든다.
2. Gmail API를 활성화한다.
3. OAuth 클라이언트 JSON을 `secrets/google/credentials.gmail.json`에 둔다.
4. 최초 1회 로컬에서 Gmail OAuth 승인을 실행해 `secrets/google/token.gmail.json`을 만든다.
5. 쿠팡 인증번호 메일의 실제 발신자/제목/본문을 확인한다.
6. `GMAIL_2FA_QUERY`를 실제 메일에 맞게 좁힌다.
7. `COUPANG_AUTO_EMAIL_2FA_ENABLED=true`로 테스트 탭 하나에서만 검증한다.
8. 인증 성공/실패 로그에 인증번호와 토큰이 남지 않는지 확인한다.
9. 장시간 실행으로 6시간 로그아웃 복구가 1회 이상 되는지 확인한다.

## 보안 주의사항

- Google OAuth 파일과 토큰 파일은 Git에 올리지 않는다.
- Gmail scope는 `gmail.readonly`만 사용한다.
- 인증번호, OAuth token, 쿠팡 계정 정보는 로그에 쓰지 않는다.
- Gmail 검색 쿼리는 가능한 좁게 잡아 다른 메일의 숫자를 인증번호로 잘못 쓰지 않게 한다.
- 여러 쿠팡 계정을 쓰는 경우 탭마다 Chrome 프로필, CDP 포트, Gmail 대상 메일 계정을 명확히 분리한다.
- 자동 인증이 반복 실패하면 인증 요청을 계속 보내지 말고 탭을 중지한다.

## 구현 순서 제안

1. `pyproject.toml`에 Gmail API 의존성을 추가한다.
2. `AppConfig`에 Gmail/2FA 설정을 추가한다.
3. `auth/gmail.py`를 만들고 Gmail 코드 조회를 단위 테스트한다.
4. `auth/coupang_email_2fa.py`를 만들고 Playwright page 조작을 fake page로 테스트한다.
5. 쿠팡 crawler 로그인 필요 분기에서 자동 복구를 한 번만 시도한다.
6. README의 기존 "2차 인증 처리 안 함" 문구를 새 정책으로 갱신한다.
7. 운영 PC에서 실제 쿠팡 인증 메일 기준으로 selector와 Gmail query를 보정한다.
