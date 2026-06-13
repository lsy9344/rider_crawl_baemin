# 네이버/Gmail 이메일 2차 인증 자동복구 병합 설계 및 지시서 (UI 탭별 자격증명 버전)

대상 레포: `lsy9344/rider_crawl_baemin` (main 브랜치)

## 0. 적용 가능성 검토 결과 (2026-06-13 기준)

검토 대상: GitHub `lsy9344/rider_crawl_baemin` `main` 최신 커밋 `7a6a143`
(LFS 파일은 checkout 시 smudge 비활성화, 소스/문서/테스트 기준 검토).

**결론: 이 문서를 그대로 적용하면 일부 회귀 위험이 있다.** 아래 보완을 포함한 버전으로
적용해야 안전하다.

### 0.1 그대로 적용하면 위험한 지점

1. **`coupang_credentials_path` 제거 지시가 불완전하다.**
   현재 `auth/coupang_email_2fa.py`의 `_load_coupang_credentials()`는 UI 아이디/비밀번호가
   비어 있으면 `config.coupang_credentials_path` JSON으로 폴백한다. 이 필드를 `config.py`에서만
   삭제하면 런타임 `AttributeError` 또는 테스트 회귀가 난다. 본 개정안은 JSON 폴백을 완전히
   폐기하고 `_load_coupang_credentials()`를 UI 입력 전용으로 단순화하도록 명시한다.
2. **IMAP 전환 시 Gmail 검색식의 `from:` 필터가 사라진다.**
   기존 Gmail API 경로는 `from:(donotreply@coupang.com) subject:(...)`로 후보를 좁혔다.
   제목 키워드만 보면 같은 시간대의 다른 인증메일을 오인할 수 있다. 본 개정안은 UI에는
   노출하지 않는 `verification_email_sender_keyword` 기본값(`coupang`)을 추가해 `FROM` 헤더도
   클라이언트 필터링한다.
3. **UI 저장 단계 검증이 빠져 있다.**
   현재 `validate_active_tab_isolation()`은 쿠팡 URL/센터/포트/메신저만 검증한다. 자동복구가 켜진
   쿠팡 탭에서 인증 이메일 주소·앱 비밀번호가 비어 있어도 저장된다. 본 개정안은 자동복구 on인
   활성 쿠팡 탭의 4개 자격증명과 지원 도메인을 저장 시점에 검증하도록 추가한다.
4. **기존 테스트/문서와 충돌한다.**
   `tests/test_config.py`, `tests/test_ui_settings.py`, `tests/test_coupang_email_2fa.py`,
   `tests/test_ui_helpers.py`, `README.md`, `.env.example`, `docs/coupang-gmail-2fa-implementation.md`는
   아직 Gmail OAuth/env/JSON 경로를 기대한다. 본 개정안은 어떤 테스트를 삭제/교체/갱신할지와
   README/문서 갱신 범위를 명시한다.
5. **배포 exe 검증이 빠져 있다.**
   레포에는 `rider_crawl_onefile.spec`가 있으며 `dist/rider_crawl_onefile.exe`를 LFS로 추적한다.
   `IMAPClient`가 새 런타임 의존성이므로, 테스트뿐 아니라 PyInstaller 빌드/임포트 smoke 검증도
   필요하다.

### 0.2 적용 성공 기준

- Gmail OAuth/token/Google API 의존 경로가 런타임에서 제거된다.
- 쿠팡 로그인 자격증명과 인증 이메일 자격증명은 탭별 UI 설정에서만 온다.
- 자동복구 off인 기존 탭은 동작 변화가 없다.
- 자동복구 on인 쿠팡 탭은 저장 시점에 필수 자격증명 누락과 미지원 도메인을 막는다.
- 네이버/Gmail 모두 같은 IMAP fetcher를 쓰며, 호스트만 이메일 도메인으로 분기한다.
- 테스트/README/.env.example/배포 spec까지 새 정책과 일치한다.

## 이번 개정의 방침 (사용자 지시 반영)

1. **`.env` 및 OAuth 토큰 파일을 쓰지 않는다.** 모든 자격증명은 **UI에 직접 노출·입력**한다.
2. **위험(평문 저장)을 감수**하고 UI 탭별 설정에 저장한다(기존 `coupang_login_password`와 동일 방식).
3. **각 크롤링 탭마다** 아래 4개 자격증명을 입력한다.
   - 쿠팡 아이디 / 쿠팡 비밀번호
   - **인증 이메일 아이디(전체 주소) / 인증 이메일 비밀번호(앱 비밀번호)**
4. 인증 이메일은 **Gmail/Naver를 IMAP(아이디+앱 비밀번호)으로 통일**한다.
   → 기존 Gmail **OAuth/토큰 방식은 폐기**하고, 도메인으로 IMAP 호스트만 분기한다.
5. 로그아웃 감지 후 **화면에 표시된 인증 이메일 도메인**과 **탭에 입력한 인증 이메일 도메인**을
   대조해 공급자(naver/gmail)를 정하고 알맞은 IMAP 로직을 선택한다.

---

## 1. 기존 구조 분석 (요약)

### 1.1 자동복구 데이터 흐름

```
platforms/coupang/crawler.py
  _wait_for_target_page_ready(...)              # 로그인 만료 감지 → BrowserActionRequiredError
  _try_recover_coupang_session(page, config)    # 자동복구 on이면 1회 시도
    └─ auth/coupang_email_2fa.recover_coupang_session_with_email_2fa(page, config)
         1) 화면 판별(CAPTCHA/비번화면이면 False/로그인 제출)
         2) "이메일로 인증" 클릭
         3) "인증코드 전송" 클릭 → requested_after 확정
         4) _fetch_code(config, requested_after)   ★ 공급자 종속 (유일 지점)
         5) 코드 입력 → "확인/제출"
```

### 1.2 이미 UI 탭별로 저장 중인 것 (그대로 활용)

`ui_settings.py`의 `UiSettings`는 **이미** 2FA 관련 값을 `.env`가 아니라 탭별 UI로 저장한다
(주석에도 ".env 사용 안 함" 명시):

- `coupang_auto_email_2fa_enabled`, `coupang_login_id`, `coupang_login_password`
- (구) `gmail_2fa_query`, `gmail_credentials_path`, `gmail_token_path` ← **이번에 폐기/대체**

→ 우리는 이 구조를 **인증 이메일 IMAP 자격증명으로 확장**하기만 하면 된다.

### 1.3 공급자 종속 지점

`coupang_email_2fa.py`의 `_fetch_code()` **한 곳**만 Gmail에 종속. 화면 조작·보안·폴링은
공급자 무관. → 여기를 IMAP 통일 + 도메인 분기로 바꾸면 끝.

---

## 2. 설계 원칙

1. **오케스트레이터 시그니처 보존**:
   `recover_coupang_session_with_email_2fa(page, config, *, fetch_code=None, now=...)`와
   `crawler.py` 호출부는 유지(회귀 최소화).
2. **단일 IMAP fetcher**: Gmail/Naver를 하나의 `auth/imap_2fa.py`로 처리. **호스트만 도메인으로 분기.**
3. **코드 파싱 단일화**: 인증번호 추출 규칙은 `auth/codes.py`로 승격해 공용.
4. **자격증명은 탭별 UI 입력**: `.env`/토큰 파일/`secrets/google/` 미사용.
5. **발신자 필터 유지**: 기존 Gmail 검색식의 `from:` 안전장치를 IMAP `FROM` 헤더 필터로 대체한다.
6. **저장 시점 검증**: 자동복구 on인 활성 쿠팡 탭은 UI 저장 단계에서 4개 자격증명과 지원 도메인을 검증한다.
7. **테스트 가능성**: IMAP `connect` 콜러블 주입으로 네트워크 없이 단위 테스트.
8. **기본 비활성**: 자동복구는 기본 off. 켜기 전 동작 불변.
9. **보안 로깅 규칙 유지**: 인증번호·비밀번호·앱 비밀번호는 예외/로그에 절대 미출력.
   읽기 전용(`BODY.PEEK[]` + readonly SELECT)으로 메일 미변경.

---

## 3. 탭별 자격증명 모델 (신규)

각 크롤링 탭(UiSettings) 1개당 인증 1세트:

| UI 입력 항목 | 필드명 | 비고 |
| --- | --- | --- |
| 쿠팡 아이디 | `coupang_login_id` | 기존 |
| 쿠팡 비밀번호 | `coupang_login_password` | 기존, 마스킹 입력 |
| **인증 이메일 주소** | `verification_email_address` | 신규. 예: `silverrainnoah@naver.com` |
| **인증 이메일 비밀번호** | `verification_email_app_password` | 신규, 마스킹 입력(앱 비밀번호) |
| 인증 메일 제목 키워드 | `verification_email_subject_keyword` | 신규, 기본 `인증번호` |
| 자동복구 사용 | `coupang_auto_email_2fa_enabled` | 기존 체크박스 |

> 공급자(naver/gmail)는 **별도 입력하지 않는다.** `verification_email_address`의 도메인으로
> 자동 결정한다(§4).

폴링 시간/코드 자릿수/발신자 키워드처럼 자주 안 바뀌는 값은 UI에 노출하지 않고 `AppConfig`
기본값을 쓴다(`coupang_2fa_code_digits=6`, 폴링 120초/5초,
`verification_email_sender_keyword="coupang"`). 필요 시 후속에서 탭별 노출.

---

## 4. 공급자(호스트) 선택 + 화면 교차검증

```python
# auth/imap_2fa.py
IMAP_HOST_BY_DOMAIN = {
    "naver.com": "imap.naver.com",
    "mail.naver.com": "imap.naver.com",
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
}

def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""

def imap_host_for_email(address: str) -> str:
    host = IMAP_HOST_BY_DOMAIN.get(domain_of(address))
    if not host:
        raise Imap2faError(
            "지원하지 않는 인증 이메일 도메인입니다. naver.com 또는 gmail.com 주소를 입력하세요."
        )
    return host
```

**선택 로직(2단계):**

1. **주 결정**: 탭에 입력한 `verification_email_address`의 도메인 → IMAP 호스트 결정.
2. **교차검증(안전장치)**: 로그인 만료 화면에 마스킹돼 노출되는 인증 이메일 도메인
   (`...@naver.com`)을 읽어, 탭에 입력한 주소의 도메인과 **다르면** 자동복구를 중단(False)한다.
   → 이 탭이 보는 메일함으로 인증번호가 오지 않는 오설정을 조기에 잡는다(코드/비번 미노출 로깅).

```python
# coupang_email_2fa.py
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%*+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_SUPPORTED_SCREEN_DOMAINS = set(IMAP_HOST_BY_DOMAIN)

def _onscreen_domains(page: Any) -> set[str]:
    text = _safe_page_text(page)            # 이미 casefold 된 page.content()
    # 화면이 도메인을 일부 마스킹(예: na***.com)하면 안전하게 교차검증을 생략한다.
    # 완전한 지원 도메인만 hard block 기준으로 삼는다.
    return {
        domain
        for m in _EMAIL_RE.finditer(text)
        if (domain := m.group(1).casefold()) in _SUPPORTED_SCREEN_DOMAINS
    }

def _account_matches_screen(page: Any, account_address: str) -> bool:
    screen = _onscreen_domains(page)
    if not screen:
        return True                          # 화면에 도메인이 없으면 교차검증 생략(주 결정만 신뢰)
    return domain_of(account_address) in screen
```

> 사용자가 처음 요청한 "화면에서 naver/gmail 확인 → 로직 선택"은, 이제 **탭 입력 주소가
> 주 결정**이고 **화면 도메인은 교차검증**으로 들어간다. 둘이 일치할 때만 진행한다.

---

## 5. 파일별 변경 지시서

### 5.1 [신규] `src/rider_crawl/auth/codes.py` — 코드 파싱 공용 승격

`gmail.py`의 `extract_verification_code`와 보조 정규식(`_CODE_CONTEXT_KEYWORDS`,
`_VERIFICATION_INTENT_RE`)을 그대로 이 모듈로 이동. Gmail/Naver(=IMAP) 공용.

```python
# auth/codes.py
import re
_CODE_CONTEXT_KEYWORDS = r"(?:인증번호|인증\s*코드|코드|verification\s*code|code|otp)"
_VERIFICATION_INTENT_RE = re.compile(
    r"(?:인증|코드|verification|verify|\bcode\b|otp|일회용\s*비밀번호|one[-\s]*time)",
    flags=re.IGNORECASE,
)
def extract_verification_code(text: str, *, code_digits: int) -> str | None:
    ...  # 기존 gmail.py 구현 그대로
```

> **전달(Fwd)·인용 메일 중복 안전 (실측 확인).** Gmail 테스트에서 전달된 인증 메일은
> 본문에 `인증번호630873`이 원문+전달본으로 **2번** 들어 있었다. 이 함수는
> (1) `인증번호` 컨텍스트 패턴이 **첫 매치**를 반환하고, (2) fallback도 "같은 자리수 숫자가
> 유일할 때만" 채택하는데 두 값이 동일(`630873`)하므로 집합 크기가 1 → **둘 다 단일
> `630873`을 반환**한다. (참고: 테스트용 `extract.py`는 `re.findall`이라 `630873, 630873`
> 처럼 중복을 그대로 보여줬지만, 운영용 이 함수는 단일값을 준다.)

### 5.2 [신규] `src/rider_crawl/auth/imap_2fa.py` — 단일 IMAP fetcher (Gmail/Naver 공용)

> 운영 검증 메모(네이버·Gmail 양쪽 실측 확인 — 자세한 로그는 부록 §12):
> - **앱 비밀번호의 공백 제거 필수.** Gmail은 `nuda vmiy gtfr ggeg`처럼 4자리씩 공백으로
>   보여준다. 공백 포함 입력은 로그인 실패, 제거 시 성공 → `_imap_connect`에서 strip.
> - **시각 컷오프는 `Date`(발신자값)가 아니라 서버 수신시각 `INTERNALDATE`** 로 비교.
> - **2단계 인증 계정은 애플리케이션 비밀번호** 필요(네이버·Gmail 모두).
> - **이 fetcher는 두 공급자 모두 "바운드 폴링"으로 통일한다(IDLE 사용 안 함).**
>   2FA 조회는 "코드 전송 직후 ~120초 동안만 새 코드를 기다리는" 짧고 끝이 있는 작업이라
>   폴링이 단순·충분하다. *Gmail은 IDLE을 지원*하지만(실측 확인), Naver는 미지원이므로
>   양쪽을 같은 코드로 다루기 위해 폴링으로 맞춘다. (참고: 장시간 상시 감시라면 Gmail은
>   IDLE이 유리하지만, 그건 이 2FA 복구의 범위가 아니다.)
> - **네이버는 선택된 세션이 `NOOP`로 새 메일을 갱신하지 않는다** → 폴링마다 INBOX
>   **재-SELECT**(이 fetcher는 `_find_code_once`마다 `select_folder`). Gmail도 무해.
> - **INBOX만 본다.** 인증 메일은 INBOX로 도착하므로(실측: Gmail UID 44가 INBOX 수신),
>   Gmail의 한글 특수폴더(`[Gmail]/전체보관함`, `[Gmail]/스팸함` 등 localized 이름)를
>   다룰 필요가 없다. 폴더명 로캘 차이로 인한 오류를 원천 회피한다.
> - 한글 SUBJECT 서버검색 불안정 → `SINCE`로 후보만 줄이고 **제목은 클라이언트 필터**.
> - 기존 Gmail API 검색식의 `from:` 안전장치는 **FROM 헤더 클라이언트 필터**로 유지한다
>   (기본 `verification_email_sender_keyword="coupang"`).

```python
"""이메일 2차 인증번호 조회 (IMAP, Gmail/Naver 공용).

도메인으로 IMAP 호스트만 분기한다. requested_after 이후(INTERNALDATE 기준) 도착한
가장 최신 메일에서 인증번호를 추출한다.

보안: 인증번호·앱 비밀번호를 예외 메시지/로그에 넣지 않는다. BODY.PEEK + readonly로
메일을 읽음 처리하지 않는다.
"""
from __future__ import annotations
import email, re, time
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Any, Callable
from rider_crawl.auth.codes import extract_verification_code

IMAP_HOST_BY_DOMAIN = {
    "naver.com": "imap.naver.com", "mail.naver.com": "imap.naver.com",
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
}

class Imap2faError(RuntimeError):
    """이메일 인증번호 조회 실패. 메시지에 코드/앱 비밀번호를 넣지 않는다."""

def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""

def imap_host_for_email(address: str) -> str:
    host = IMAP_HOST_BY_DOMAIN.get(domain_of(address))
    if not host:
        raise Imap2faError("지원하지 않는 인증 이메일 도메인입니다. naver.com/gmail.com만 지원합니다.")
    return host

def fetch_latest_verification_code(
    *,
    email_address: str,
    app_password: str,
    subject_keyword: str,
    sender_keyword: str,
    requested_after: datetime,
    poll_seconds: int,
    poll_interval_seconds: int,
    code_digits: int,
    host: str | None = None,
    port: int = 993,
    connect: Callable[[str, int, str, str], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    imap_host = host or imap_host_for_email(email_address)
    requested_after_utc = _to_utc(requested_after)
    server = (connect or _imap_connect)(imap_host, port, email_address, app_password)

    deadline = now() + timedelta(seconds=max(0, poll_seconds))
    interval = max(0.0, float(poll_interval_seconds))
    last_error: Imap2faError | None = None
    try:
        while True:
            try:
                code = _find_code_once(
                    server, subject_keyword=subject_keyword,
                    sender_keyword=sender_keyword,
                    requested_after=requested_after_utc, code_digits=code_digits,
                )
            except Imap2faError as exc:
                last_error, code = exc, None
            if code is not None:
                return code
            if now() >= deadline:
                break
            sleep(interval)
    finally:
        _safe_logout(server)

    if last_error is not None:
        raise Imap2faError(str(last_error)) from (last_error.__cause__ or last_error)
    raise Imap2faError(
        "요청 시각 이후 도착한 인증 메일을 찾지 못했습니다. 제목 키워드/메일 도착 여부를 확인하세요."
    )

def _find_code_once(server, *, subject_keyword, sender_keyword, requested_after, code_digits):
    # 네이버는 선택 세션에 새 메일이 반영 안 되므로 매 폴링 재-SELECT. readonly로 읽음 방지.
    server.select_folder("INBOX", readonly=True)
    try:
        uids = server.search(["SINCE", requested_after.date()])  # SINCE는 '일' 단위까지만
    except Exception as exc:
        raise Imap2faError("메일 검색에 실패했습니다.") from exc
    if not uids:
        return None
    meta = server.fetch(uids, ["INTERNALDATE", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)]"])
    newest_uid, newest_dt = None, None
    for uid, data in meta.items():
        internal = _to_utc(data.get(b"INTERNALDATE"))
        if internal is None or internal < requested_after:
            continue
        headers = data.get(b"BODY[HEADER.FIELDS (SUBJECT FROM)]", b"")
        subject = _decode_header_value(headers, "Subject")
        sender = _decode_header_value(headers, "From")
        if subject_keyword and subject_keyword.casefold() not in subject.casefold():
            continue
        if sender_keyword and sender_keyword.casefold() not in sender.casefold():
            continue
        if newest_dt is None or internal > newest_dt:
            newest_uid, newest_dt = uid, internal
    if newest_uid is None:
        return None
    raw = server.fetch([newest_uid], ["BODY.PEEK[]"])[newest_uid][b"BODY[]"]
    body = _message_text(email.message_from_bytes(raw))
    code = extract_verification_code(body, code_digits=code_digits)
    if code is not None:
        return code
    raise Imap2faError("최신 인증 메일에서 인증번호를 추출하지 못했습니다(자리수/형식 확인).")

def _message_text(msg: Message) -> str:
    plain, html = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            plain.append(text)
        elif part.get_content_type() == "text/html":
            html.append(text)
    return "\n".join(plain) if plain else _strip_html("\n".join(html))

def _strip_html(html): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()

def _decode_subject(raw: bytes) -> str:
    return _decode_header_value(raw, "Subject")

def _decode_header_value(raw: bytes, header_name: str) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(email.message_from_bytes(raw).get(header_name, ""))))
    except Exception:
        return ""

def _imap_connect(host, port, email_address, app_password):
    from imapclient import IMAPClient
    # Gmail은 앱 비밀번호를 4자리씩 공백으로 끊어 보여준다("nuda vmiy gtfr ggeg").
    # 사용자가 화면 그대로 붙여넣어도 로그인되도록 공백을 제거한다(네이버도 무해).
    # ★ 실측 검증: 공백 포함 입력은 로그인 실패, 공백 제거 시 성공.
    app_password = re.sub(r"\s+", "", app_password or "")
    server = IMAPClient(host, port=port, ssl=True, use_uid=True)
    try:
        server.login(email_address, app_password)
    except Exception as exc:
        raise Imap2faError(
            "IMAP 로그인 실패. 메일의 IMAP 사용 설정과 앱 비밀번호를 확인하세요."
        ) from exc  # 앱 비밀번호 값은 메시지에 넣지 않는다.
    return server

def _safe_logout(server):
    try: server.logout()
    except Exception: pass

def _to_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None
```

### 5.3 [수정] `src/rider_crawl/auth/coupang_email_2fa.py`

- 상단 import: `from rider_crawl.auth.imap_2fa import Imap2faError, IMAP_HOST_BY_DOMAIN, domain_of` 추가,
  기존 `from rider_crawl.auth.gmail import ...` 제거.
- `json` import와 `config.coupang_credentials_path` 기반 JSON 폴백은 폐기한다. `_load_coupang_credentials()`
  는 UI 입력(`coupang_login_id`, `coupang_login_password`)만 보고, 둘 중 하나라도 비면 `None`을 반환한다.
- "인증코드 전송" 클릭 직후 **교차검증** 후 `_fetch_code` 호출:

```python
    if not _click_first_by_text(page, _SEND_CODE_TEXTS, config, roles=("button",)):
        return False

    # 탭에 입력한 인증 이메일과 화면 도메인이 어긋나면 이 메일함엔 코드가 안 온다 → 중단.
    if not _account_matches_screen(page, config.verification_email_address):
        return False

    code = _fetch_code(config, requested_after=requested_after, fetch_code=fetch_code)
    _fill_code_input(page, code, config)
    _click_first_by_text(page, _SUBMIT_TEXTS, config, roles=("button",))
    return True
```

- `_fetch_code`를 IMAP 단일 경로로 교체:

```python
def _fetch_code(config, *, requested_after, fetch_code=None):
    fetcher = fetch_code or _imap_fetch
    try:
        code = fetcher(
            email_address=config.verification_email_address,
            app_password=config.verification_email_app_password,
            subject_keyword=config.verification_email_subject_keyword,
            sender_keyword=config.verification_email_sender_keyword,
            requested_after=requested_after,
            poll_seconds=config.email_2fa_poll_seconds,
            poll_interval_seconds=config.email_2fa_poll_interval_seconds,
            code_digits=config.coupang_2fa_code_digits,
        )
    except Imap2faError as exc:
        raise Coupang2faError(str(exc)) from exc
    if not code:
        raise Coupang2faError("이메일에서 인증번호를 받지 못했습니다.")
    return code

def _imap_fetch(**kw):
    from rider_crawl.auth.imap_2fa import fetch_latest_verification_code
    return fetch_latest_verification_code(**kw)
```

- `_account_matches_screen`/`_onscreen_domains`/`_EMAIL_RE`는 §4 코드 추가. 화면의 완전한 지원 도메인이
  탭 설정 도메인과 다를 때만 `False`를 반환하고, 화면 도메인을 읽지 못하거나 일부 마스킹되어 있으면
  주 결정(탭 입력 주소)을 신뢰한다.

### 5.4 [수정] `src/rider_crawl/config.py`

- **삭제/폐기**: `DEFAULT_GMAIL_*`, `gmail_2fa_query`, `gmail_credentials_path`,
  `gmail_token_path`, `DEFAULT_COUPANG_CREDENTIALS_PATH`, `coupang_credentials_path`,
  `gmail_2fa_settings_from_env()`.
  (`.env`로 읽던 2FA 경로/쿼리와 쿠팡 계정 JSON 폴백 일체 제거)
- **AppConfig 필드 재구성**:

```python
from dataclasses import dataclass, field

DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD = "인증번호"
DEFAULT_EMAIL_2FA_SENDER_KEYWORD = "coupang"
DEFAULT_EMAIL_2FA_POLL_SECONDS = 120
DEFAULT_EMAIL_2FA_POLL_INTERVAL_SECONDS = 5
DEFAULT_COUPANG_2FA_CODE_DIGITS = 6

@dataclass(frozen=True)
class AppConfig:
    ...
    coupang_auto_email_2fa_enabled: bool = False
    coupang_login_id: str = ""
    coupang_login_password: str = field(default="", repr=False)
    verification_email_address: str = ""
    verification_email_app_password: str = field(default="", repr=False)
    verification_email_subject_keyword: str = DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
    verification_email_sender_keyword: str = DEFAULT_EMAIL_2FA_SENDER_KEYWORD
    email_2fa_poll_seconds: int = DEFAULT_EMAIL_2FA_POLL_SECONDS
    email_2fa_poll_interval_seconds: int = DEFAULT_EMAIL_2FA_POLL_INTERVAL_SECONDS
    coupang_2fa_code_digits: int = DEFAULT_COUPANG_2FA_CODE_DIGITS

    # 기존 telegram_bot_token도 민감값이다. 이번 작업에서 손대는 김에 repr 노출을 막는다.
    # telegram_bot_token 필드도 field(default="", repr=False)로 바꾼다.
```

- `from_env()`는 **비밀 항목을 더 이상 읽지 않는다.** 위 필드는 기본값(빈 문자열)으로 두고,
  실제 값은 UI(`UiSettings.to_app_config`)에서만 주입한다. (`**gmail_2fa_settings_from_env()`
  호출부 제거.) `COUPANG_CREDENTIALS_PATH`, `GMAIL_*`, `COUPANG_AUTO_EMAIL_2FA_ENABLED`는
  `from_env()`에서 더 이상 읽지 않는다.

> 결정: `--once` CLI는 이번 병합에서 이메일 자동복구를 지원하지 않는다. `from_env()`는 탭을
> 특정할 수 없으므로 자동복구를 기본 off로 유지한다. CLI에서 자동복구가 필요하면 후속 작업으로
> `--settings-tab N` 같은 명시적 탭 선택 옵션을 추가한다.

### 5.5 [수정] `src/rider_crawl/ui_settings.py`

- import에서 `DEFAULT_GMAIL_*` 제거, `DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD`,
  `DEFAULT_EMAIL_2FA_SENDER_KEYWORD` 추가.
- `UiSettings` 필드 교체:

```python
    coupang_auto_email_2fa_enabled: bool = False
    coupang_login_id: str = ""
    coupang_login_password: str = ""
    verification_email_address: str = ""
    verification_email_app_password: str = ""
    verification_email_subject_keyword: str = DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
    verification_email_sender_keyword: str = DEFAULT_EMAIL_2FA_SENDER_KEYWORD
    # (구) gmail_2fa_query / gmail_credentials_path / gmail_token_path 삭제
```

- `to_app_config()` 매핑 교체:

```python
            coupang_auto_email_2fa_enabled=self.coupang_auto_email_2fa_enabled,
            coupang_login_id=self.coupang_login_id,
            coupang_login_password=self.coupang_login_password,
            verification_email_address=self.verification_email_address.strip(),
            verification_email_app_password=self.verification_email_app_password,
            verification_email_subject_keyword=(
                self.verification_email_subject_keyword or DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
            ),
            verification_email_sender_keyword=(
                self.verification_email_sender_keyword or DEFAULT_EMAIL_2FA_SENDER_KEYWORD
            ),
```

> 마이그레이션: 저장된 탭 JSON에 옛 `gmail_*` 키가 있으면 로드시 무시한다.
> `verification_email_address`/`verification_email_app_password`는 빈 값으로 남기고, 사용자가
> 앱 비밀번호를 새로 입력하게 한다.
> 기존 구현의 `_settings_from_mapping()`은 dataclass 필드에 없는 키를 이미 버리므로,
> `gmail_*` 필드를 dataclass에서 제거하면 옛 저장 파일은 자연스럽게 무시된다. 이 동작을
> 회귀 테스트로 고정한다.

### 5.6 [수정] `src/rider_crawl/ui.py`

탭 구성에 입력칸 추가(쿠팡 자격증명 근처). 비밀번호류는 `show="*"`.

- `tab_vars`에 추가: `verification_email_address`(StringVar),
  `verification_email_app_password`(StringVar), `verification_email_subject_keyword`(StringVar).
- `verification_email_sender_keyword`는 UI 입력칸을 만들지 않고 `UiSettings` 기본값으로 유지한다
  (사용자가 요구한 탭별 자격증명 입력 범위를 늘리지 않기 위함).
- 라벨/엔트리 추가(예):
  - "인증 이메일 주소" → `verification_email_address`
  - "인증 이메일 비밀번호(앱 비밀번호)" → `verification_email_app_password` (`show="*"`)
  - "인증 메일 제목 키워드(기본 인증번호)" → `verification_email_subject_keyword`
- 설정 수집부(`values.get(...)`)와 저장/로드에 위 3개 반영.
- 기존 "쿠팡 로그인 만료 시 자동복구(이메일 2FA)" 체크박스/안내문(라인 ~400, ~450)의
  Gmail 토큰 관련 문구를 "인증 이메일 주소/앱 비밀번호" 안내로 교체.
- `validate_active_tab_isolation()`에 자동복구 on인 활성 쿠팡 탭 검증 추가:
  - `coupang_login_id` 필수
  - `coupang_login_password` 필수
  - `verification_email_address` 필수
  - `verification_email_app_password` 필수
  - `verification_email_address` 도메인이 `naver.com`, `mail.naver.com`, `gmail.com`,
    `googlemail.com` 중 하나인지 확인
  - 실패 메시지에는 실제 비밀번호/앱 비밀번호 값을 절대 넣지 않음
- `coerce_settings()`는 아이디/이메일 주소/제목 키워드는 strip하고, 비밀번호류는 기존 쿠팡
  비밀번호와 동일하게 strip하지 않는다(단, IMAP 로그인 직전에는 앱 비밀번호의 모든 공백 제거).

### 5.7 [수정] `pyproject.toml`

- 추가: `"IMAPClient>=3.0.1",`
- 제거: `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`.
  (현재 레포에서 Gmail OAuth 외 사용처 없음. `uv lock` 재생성.)

### 5.8 [수정] `rider_crawl_onefile.spec`

- `IMAPClient`가 PyInstaller 분석에 누락되지 않는지 확인한다. 일반 import 스캔으로 포함되면
  spec 변경은 필요 없지만, exe smoke에서 누락되면 `hiddenimports`에 `"imapclient"`를 추가한다.
- 배포 검증 명령:

```bash
uv run pyinstaller rider_crawl_onefile.spec
```

생성 exe에서 UI 시작 또는 `--once` import smoke를 확인한다(실제 IMAP 로그인은 수기 검증).

### 5.9 [폐기] 제거 대상

- `src/rider_crawl/auth/gmail.py` 의 OAuth/Gmail API 경로
  (`fetch_latest_verification_code`, `_build_gmail_service` 등). `extract_*`는 `codes.py`로 이동.
- `scripts/gmail_authorize.py`, `secrets/google/` (토큰/크리덴셜 부트스트랩) — 더 이상 불필요.
- `.env.example`의 "쿠팡이츠 이메일 2차 인증" 블록 — 삭제(자격증명은 UI 전용).
- `src/rider_crawl/platforms/coupang/crawler.py::_log_recovery_failure`: token 파일명/query 대신
  **인증 이메일 도메인/마스킹 주소**와 공급자만 기록. 앱 비밀번호/인증번호/전체 이메일 주소는 기록하지 않음.

### 5.10 [수정] 문서

- `README.md`: 쿠팡 이메일 자동복구 설명을 Gmail OAuth/env에서 UI 입력 IMAP 방식으로 교체.
- `.env.example`: Gmail/OAuth/쿠팡 credentials JSON 관련 2FA 블록 제거. CLI `--once`에서는 자동 이메일
  복구가 지원되지 않는다고 명시.
- `docs/coupang-gmail-2fa-implementation.md`: 역사 문서로 남길 경우 상단에 "구 OAuth 방식, 현행 아님"
  경고를 추가하거나, 현행 IMAP 방식 문서로 갱신한다. `docs/superpowers/plans/*`는 과거 계획 기록이므로
  일반 사용자 문서가 아니라면 변경하지 않아도 된다.

### 5.11 [신규/수정] 테스트

- `tests/test_imap_2fa.py` (신규): fake IMAP(`connect` 주입)로
  INTERNALDATE 컷 / 제목 키워드 / 발신자 키워드 / 최신 1통 채택 / 코드 추출 / 미도착
  `Imap2faError` / `imap_host_for_email`(naver·gmail·미지원 도메인) / Gmail 앱 비밀번호
  공백 제거 검증.
- `tests/test_coupang_email_2fa.py` (수정): `_FakePage.content()`에
  `...@naver.com` / `...@gmail.com` / 불일치 케이스 추가 →
  교차검증 통과/차단, 일부 마스킹 도메인 교차검증 생략, 주입 fetcher 호출 인자 검증.
  기존 JSON credential fallback 테스트는 UI credential 테스트로 교체.
- `tests/test_ui_settings.py` (수정): 새 필드 직렬화/`to_app_config` 매핑, 옛 `gmail_*` 키 무시,
  기본 `verification_email_sender_keyword`.
- `tests/test_ui_helpers.py` (수정): `coerce_settings()` 새 필드 수집, 자동복구 on인 쿠팡 탭의 필수
  자격증명 누락/미지원 도메인 저장 거부.
- `tests/test_config.py` (수정): `COUPANG_CREDENTIALS_PATH`/`GMAIL_*` env 읽기 기대 제거,
  새 기본값과 `repr=False` 민감정보 미노출 검증.
- `tests/test_coupang_crawler.py` (수정): `_log_recovery_failure`가 token/query를 쓰지 않고 마스킹 이메일
  또는 도메인만 쓰는지 검증.
- 기존 `tests/test_gmail_2fa.py`: `codes.py`로 옮긴 `extract_verification_code` 회귀만 남기고
  OAuth 관련 케이스 삭제(또는 `tests/test_codes.py`로 이전). `auth/gmail.py`를 삭제하면 import도 제거.

---

## 6. 보안 (위험 감수 명시)

- 사용자 지시에 따라 **쿠팡 비밀번호·인증 이메일 앱 비밀번호를 UI 탭 설정에 평문 저장**한다
  (기존 `coupang_login_password`와 동일). 이는 의도된 결정이며 위험을 감수한다.
- 완화 권장: 탭 설정 JSON 파일에 OS 파일 권한 제한, 저장소(VCS) 커밋 금지(`.gitignore`),
  공유 PC 사용 자제.
- **로깅 규칙은 유지**: 인증번호·비밀번호·앱 비밀번호를 로그/예외/`run_errors.log`에 절대 미출력.
- 메일은 `BODY.PEEK[]` + readonly로 읽어 **읽음 처리하지 않음**.

---

## 7. 사전 설정(메일 IMAP) 안내

- **네이버**: 메일 환경설정 → IMAP/SMTP **사용함**. 2단계 인증 시 **애플리케이션 비밀번호** 발급.
  호스트 `imap.naver.com:993(SSL)`.
- **Gmail**: 설정 → 전달/POP/IMAP에서 **IMAP 사용**. 2단계 인증 후 **앱 비밀번호** 발급.
  호스트 `imap.gmail.com:993(SSL)`. (OAuth 동의/토큰 부트스트랩 불필요 — IMAP+앱 비밀번호로 통일)

---

## 8. 작업 순서

1. `auth/codes.py` 승격(+ `gmail.py`에서 추출 로직 제거/대체) → 코드 파싱 테스트 green.
2. `auth/imap_2fa.py` 추가 + `tests/test_imap_2fa.py` green(도메인/subject/from/공백 strip 포함).
3. `config.py` 재구성(필드 교체, env 비밀 읽기 제거, `repr=False` 마스킹).
4. `coupang_email_2fa.py` 교차검증 + `_fetch_code` IMAP 단일 경로 + JSON credential fallback 제거 + 테스트.
5. `ui_settings.py` / `ui.py` 입력칸·매핑·마이그레이션·저장 시점 검증.
6. `platforms/coupang/crawler.py::_log_recovery_failure` 로깅 문구 교체 + 테스트.
7. `pyproject.toml`(IMAPClient 추가, google-* 제거) + `uv lock`.
8. 폐기 파일/블록 정리(gmail OAuth, gmail_authorize, secrets/google, README/.env 문구).
9. `rider_crawl_onefile.spec` exe smoke 확인(필요 시 hiddenimport 추가).
10. 실환경 1회 수기 검증(네이버 탭/지메일 탭 각각).

---

## 9. 검증 체크리스트

- [ ] `pytest` 전체 green(신규 포함).
- [ ] 자동복구 off일 때 동작 변화 없음.
- [ ] 탭에 `@naver.com` 입력 → imap.naver.com으로 코드 조회·입력 성공.
- [ ] 탭에 `@gmail.com` 입력 → imap.gmail.com으로 코드 조회·입력 성공.
- [ ] Gmail 앱 비밀번호를 **공백 포함 그대로 붙여넣어도** 로그인 성공(strip 동작).
- [ ] 탭 주소 도메인 ≠ 화면 도메인 → 자동복구 중단(오설정 차단), 코드/비번 미노출 로깅.
- [ ] 미지원 도메인 입력 → 명확한 설정 오류로 중단.
- [ ] 자동복구 on인데 쿠팡/인증 이메일 자격증명이 비어 있으면 UI 저장 단계에서 차단.
- [ ] 인증 메일 후보는 제목 키워드와 발신자 키워드(`coupang`)를 모두 통과해야 함.
- [ ] 로그/`run_errors.log`에 비밀번호·앱 비밀번호·인증번호 미출력.
- [ ] `repr(AppConfig)`에 쿠팡 비밀번호·인증 이메일 앱 비밀번호·텔레그램 토큰 미노출.
- [ ] 메일 읽음 처리 안 됨(PEEK/readonly).
- [ ] `README.md`/`.env.example`에 Gmail OAuth/token 안내가 남아 있지 않음.
- [ ] PyInstaller exe에서 `imapclient` 누락 없이 시작됨.

---

## 10. 네이버/Gmail IMAP 특이사항 (양쪽 실측 검증 완료)

| 항목 | 네이버 | Gmail | 코드 반영 |
| --- | --- | --- | --- |
| 앱 비밀번호 공백 | 없음 | **4자리씩 공백 표시** | 입력값 공백 strip |
| 로그인 | 2FA 시 앱 비번 | 2FA 시 앱 비번 | 앱 비밀번호 사용 |
| IDLE | 미지원 | 지원 | **fetcher는 둘 다 바운드 폴링으로 통일** |
| 새 메일 반영 | NOOP 무효 | 정상 | 폴링마다 재-SELECT(양쪽 무해) |
| 시각 컷 | Date 부정확 | 동일 | INTERNALDATE 비교 |
| 한글 SUBJECT 검색 | 불안정 | 동일 | SINCE 후 클라이언트 필터 |
| 특수폴더명 | 한글 | **한글 localized** | INBOX만 사용(폴더명 무관) |

---

## 11. 병합 결정사항(이번 개정에서 확정)

1. **Gmail OAuth는 완전 폐기한다.** Gmail도 IMAP+앱 비밀번호로 통일한다. 기존 OAuth 사용자는
   앱 비밀번호를 발급해 UI 탭에 입력해야 한다.
2. **CLI(`--once`)는 이번 병합에서 이메일 자동복구를 지원하지 않는다.** CLI는 `AppConfig.from_env()`를
   계속 쓰되, env 비밀/2FA 설정은 읽지 않고 자동복구 기본 off를 유지한다. CLI 자동복구는 후속 작업에서
   `--settings-tab N`처럼 탭을 명시할 수 있을 때 추가한다.
3. **옛 탭 설정의 `gmail_*` 키는 무시한다.** OAuth token/credentials 경로로는 새 IMAP 이메일 주소나
   앱 비밀번호를 안전하게 추론할 수 없으므로 1회 변환하지 않는다.
4. **제목 키워드 기본값은 `인증번호`, 발신자 키워드 기본값은 `coupang`으로 한다.** 제목 키워드는 UI에서
   탭별 조정 가능하게 두고, 발신자 키워드는 우선 UI에 노출하지 않는다.
5. **쿠팡 계정 JSON 폴백은 폐기한다.** `coupang_login_id`/`coupang_login_password`가 UI에 없으면
   1차 로그인 자동 제출은 하지 않는다.

---

## 12. 부록 — 실측 검증 결과 (성공 스토리)

본 설계는 별도 PoC(`imap_test/main.py`, `extract.py`)로 **네이버·Gmail 두 공급자에서
실제 메일로 검증**했다. 핵심 결과:

### 12.1 네이버 (`@naver.com`)

| 항목 | 결과 |
| --- | --- |
| 로그인 | 애플리케이션 비밀번호로 성공 |
| CAPABILITY/IDLE | `IDLE` 없음 → **폴링 fallback** 동작 |
| 새 메일 감지 | UID 기준 신규 메일 감지 성공 |
| 발송 → 감지 지연 | **약 18초** (폴링 10초 + 네이버 배달 ~8초) |
| 인증번호 추출 | `[쿠팡] … 인증번호630873` → **`630873`** 정상 추출 |
| 읽음 처리 | `BODY.PEEK[]` 로 **읽음 처리 안 됨** |
| **발견·수정한 버그** | 선택된 세션이 `NOOP`로 새 메일을 반영하지 않음 → **폴링마다 INBOX 재-SELECT** 로 해결 (→ §5.2 반영) |

### 12.2 Gmail (`@gmail.com`)

| 항목 | 결과 |
| --- | --- |
| 로그인 | 앱 비밀번호 **공백 제거 후** 성공(`nuda vmiy gtfr ggeg` → strip) |
| CAPABILITY/IDLE | `IDLE` **지원** → 실시간 감지 동작 |
| 새 메일 감지 | INBOX UID 44, **IDLE로 즉시** 감지(메일은 INBOX 수신) |
| 발송 → 감지 지연 | **약 22초** (전부 Gmail 배달 시간; IDLE 반응은 <1초) |
| 인증번호 추출 | 전달(Fwd) 메일에서 **`630873`** 추출(중복 안전, §5.1 참고) |
| 폴더명 | 특수폴더가 한글 localized(`[Gmail]/전체보관함` 등) → **INBOX만 사용**해 회피 |

### 12.3 설계에 반영된 교훈

- **앱 비밀번호 공백 strip** 필수 → `_imap_connect`에 반영(§5.2).
- **2FA 조회는 바운드 폴링으로 통일**: Naver는 IDLE 불가, Gmail은 가능하지만 짧은 1회성
  복구라 폴링이 단순·충분(§5.2 메모).
- **INTERNALDATE 컷 + 매 폴링 재-SELECT + INBOX 한정** 으로 두 공급자를 같은 코드로 처리.
- **도메인으로 호스트만 분기**하면 충분함을 양쪽에서 확인(`imap.naver.com`/`imap.gmail.com`).

> PoC 코드 위치(참고용, 운영 병합 대상 아님): `imap_test/main.py`(IDLE+폴링 감시),
> `imap_test/extract.py`(제목 필터 + 코드 추출). 운영 병합 시에는 §5의 `auth/imap_2fa.py`
> + `auth/codes.py` 형태로 재작성한다.
