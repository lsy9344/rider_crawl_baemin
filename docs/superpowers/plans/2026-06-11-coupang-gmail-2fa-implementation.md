# Coupang Gmail 2FA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 쿠팡이츠 탭이 이메일 2차 인증 화면으로 이동했을 때 Gmail에서 인증번호를 읽어 한 번 자동 복구하고, 실패하면 기존처럼 운영자 조치가 필요한 상태로 멈춘다.

**Architecture:** 새 `rider_crawl.auth` 패키지에 Gmail 읽기와 쿠팡 이메일 2FA 화면 조작을 분리한다. 쿠팡 크롤러는 기존 로그인 감지 지점에서 새 복구 함수를 한 번 호출하고, 성공하면 원래 대상 URL을 다시 준비시킨다. 설정은 UI 입력칸을 만들지 않고 환경변수로만 켜며, UI 실행 경로도 같은 환경변수 값을 `AppConfig`에 주입한다.

**Tech Stack:** Python 3.10+, Playwright sync API, Gmail API (`google-api-python-client`), Google OAuth (`google-auth-oauthlib`, `google-auth-httplib2`), `pytest`, `uv`.

---

## Current State And Safety

- Work branch: `google_verification`.
- Existing user-owned changes must be preserved:
  - `.gitignore` already ignores `secrets/google/*.json`, `secrets/google/*.pickle`, `secrets/google/token*`, `secrets/google/credentials*`.
  - `docs/coupang-gmail-2fa-implementation.md` is the source spec.
  - `secrets/google/README.md` documents local Google OAuth files.
- Do not add real `credentials.gmail.json`, `token.gmail.json`, Gmail message contents, OAuth tokens, 쿠팡 계정값, or 인증번호 values to git or logs.
- `uv run pytest ...` currently fails before tests start with a Windows Korean-path `UnicodeDecodeError: cp949`. Treat that as an environment verification blocker, not as an application test failure.

## File Structure

- Modify `pyproject.toml`
  - Add Gmail API/OAuth runtime dependencies.
- Modify `uv.lock`
  - Refresh after dependency changes with `uv lock`.
- Modify `src/rider_crawl/config.py`
  - Add 2FA config fields to `AppConfig`.
  - Add one helper that reads env values for both CLI and UI paths.
- Modify `src/rider_crawl/ui_settings.py`
  - Pass env-only 2FA settings into `AppConfig` from `UiSettings.to_app_config`.
- Create `src/rider_crawl/auth/__init__.py`
  - Mark auth package.
- Create `src/rider_crawl/auth/gmail.py`
  - Own Gmail OAuth, polling, body extraction, and code parsing.
- Create `src/rider_crawl/auth/coupang_email_2fa.py`
  - Own browser interaction for the Coupang email 2FA screen.
- Modify `src/rider_crawl/platforms/coupang/crawler.py`
  - Try email 2FA recovery before raising the existing login-required error.
- Modify `README.md`
  - Replace old "2차 인증 처리 안 함" policy with env-gated email 2FA policy.
- Modify `.env.example`
  - Add commented 2FA env settings.
- Modify `src/rider_crawl/ui.py`
  - Update the checklist text so it no longer says the app never handles 2FA.
- Create `tests/test_gmail_2fa.py`
  - Unit tests for email body parsing and code selection.
- Create `tests/test_coupang_email_2fa.py`
  - Unit tests for fake Playwright 2FA screen interactions.
- Modify `tests/test_config.py`
  - Defaults and env parsing.
- Modify `tests/test_coupang_crawler.py`
  - Integration behavior around login-required recovery.

---

## Task 1: Dependencies And Config Tests

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add failing config tests**

Append these tests to `tests/test_config.py` near the existing Coupang config tests:

```python
def test_app_config_defaults_disable_coupang_email_2fa(monkeypatch):
    for key in (
        "COUPANG_AUTO_EMAIL_2FA_ENABLED",
        "GMAIL_CREDENTIALS_PATH",
        "GMAIL_TOKEN_PATH",
        "GMAIL_2FA_QUERY",
        "GMAIL_2FA_POLL_SECONDS",
        "GMAIL_2FA_POLL_INTERVAL_SECONDS",
        "COUPANG_2FA_CODE_DIGITS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("BAEMIN_CENTER_NAME", "쿠팡강남센터")

    config = AppConfig.from_env()

    assert config.coupang_auto_email_2fa_enabled is False
    assert config.gmail_credentials_path == Path("secrets/google/credentials.gmail.json")
    assert config.gmail_token_path == Path("secrets/google/token.gmail.json")
    assert config.gmail_2fa_query == "from:(coupang.com) newer_than:10m"
    assert config.gmail_2fa_poll_seconds == 120
    assert config.gmail_2fa_poll_interval_seconds == 5
    assert config.coupang_2fa_code_digits == 6


def test_app_config_reads_coupang_email_2fa_environment(monkeypatch):
    monkeypatch.setenv("PERFORMANCE_PLATFORM", "coupang")
    monkeypatch.setenv("BAEMIN_CENTER_NAME", "쿠팡강남센터")
    monkeypatch.setenv("COUPANG_AUTO_EMAIL_2FA_ENABLED", "true")
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "C:/safe/credentials.gmail.json")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", "C:/safe/token.gmail.json")
    monkeypatch.setenv("GMAIL_2FA_QUERY", "from:(no-reply@example.test) subject:(인증)")
    monkeypatch.setenv("GMAIL_2FA_POLL_SECONDS", "90")
    monkeypatch.setenv("GMAIL_2FA_POLL_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("COUPANG_2FA_CODE_DIGITS", "8")

    config = AppConfig.from_env()

    assert config.coupang_auto_email_2fa_enabled is True
    assert config.gmail_credentials_path == Path("C:/safe/credentials.gmail.json")
    assert config.gmail_token_path == Path("C:/safe/token.gmail.json")
    assert config.gmail_2fa_query == "from:(no-reply@example.test) subject:(인증)"
    assert config.gmail_2fa_poll_seconds == 90
    assert config.gmail_2fa_poll_interval_seconds == 3
    assert config.coupang_2fa_code_digits == 8
```

Add this import at the top if it is not already present:

```python
from pathlib import Path
```

- [ ] **Step 2: Run the new tests and confirm they fail for missing fields**

Run:

```powershell
uv run pytest tests/test_config.py -q
```

Expected before implementation:

```text
AttributeError: 'AppConfig' object has no attribute 'coupang_auto_email_2fa_enabled'
```

If Python exits with `UnicodeDecodeError: cp949` before pytest collection, record that blocker and continue implementation. Do not change application code to work around that environment issue in this task.

- [ ] **Step 3: Add Gmail dependencies**

Modify `pyproject.toml` dependencies:

```toml
dependencies = [
  "crawl4ai==0.8.7",
  "playwright==1.60.0",
  "python-dotenv>=1.1.0",
  "pyperclip>=1.9.0",
  "pyautogui>=0.9.54",
  "pywinauto>=0.6.8; platform_system == 'Windows'",
  "google-api-python-client>=2.0.0",
  "google-auth-oauthlib>=1.0.0",
  "google-auth-httplib2>=0.2.0",
]
```

- [ ] **Step 4: Refresh lockfile**

Run:

```powershell
uv lock
```

Expected:

```text
Resolved ... packages
```

- [ ] **Step 5: Commit if this task is implemented as an isolated commit**

```powershell
git add pyproject.toml uv.lock tests/test_config.py
git commit -m "test: cover coupang gmail 2fa config"
```

---

## Task 2: AppConfig 2FA Settings

**Files:**
- Modify: `src/rider_crawl/config.py`
- Modify: `src/rider_crawl/ui_settings.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add config fields and env helper**

In `src/rider_crawl/config.py`, add these fields after `page_timeout_seconds` and before the existing optional string fields:

```python
    coupang_auto_email_2fa_enabled: bool = False
    gmail_credentials_path: Path = Path("secrets/google/credentials.gmail.json")
    gmail_token_path: Path = Path("secrets/google/token.gmail.json")
    gmail_2fa_query: str = "from:(coupang.com) newer_than:10m"
    gmail_2fa_poll_seconds: int = 120
    gmail_2fa_poll_interval_seconds: int = 5
    coupang_2fa_code_digits: int = 6
```

Add this helper near `_env_bool`:

```python
def gmail_2fa_settings_from_env() -> dict[str, object]:
    load_dotenv()
    return {
        "coupang_auto_email_2fa_enabled": _env_bool("COUPANG_AUTO_EMAIL_2FA_ENABLED", default=False),
        "gmail_credentials_path": Path(os.getenv("GMAIL_CREDENTIALS_PATH", "secrets/google/credentials.gmail.json")),
        "gmail_token_path": Path(os.getenv("GMAIL_TOKEN_PATH", "secrets/google/token.gmail.json")),
        "gmail_2fa_query": os.getenv("GMAIL_2FA_QUERY", "from:(coupang.com) newer_than:10m"),
        "gmail_2fa_poll_seconds": int(os.getenv("GMAIL_2FA_POLL_SECONDS", "120")),
        "gmail_2fa_poll_interval_seconds": int(os.getenv("GMAIL_2FA_POLL_INTERVAL_SECONDS", "5")),
        "coupang_2fa_code_digits": int(os.getenv("COUPANG_2FA_CODE_DIGITS", "6")),
    }
```

Update `AppConfig.from_env()` so the constructor receives these fields:

```python
            page_timeout_seconds=int(os.getenv("PAGE_TIMEOUT_SECONDS", "60000")),
            **gmail_2fa_settings_from_env(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
```

- [ ] **Step 2: Make UI-created AppConfig read env-only 2FA settings**

In `src/rider_crawl/ui_settings.py`, import the helper:

```python
    gmail_2fa_settings_from_env,
```

Then pass it in `UiSettings.to_app_config()`:

```python
            page_timeout_seconds=self.page_timeout_seconds,
            **gmail_2fa_settings_from_env(),
            crawl_name=crawl_name,
            state_subdir=state_subdir,
```

This is required because UI tabs do not call `AppConfig.from_env()`.

- [ ] **Step 3: Run config tests**

Run:

```powershell
uv run pytest tests/test_config.py -q
```

Expected after implementation:

```text
passed
```

If `uv run` still fails before pytest starts with the known `cp949` error, try:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

If both fail before pytest starts, record the environment blocker in the final implementation notes.

- [ ] **Step 4: Commit if isolated**

```powershell
git add src/rider_crawl/config.py src/rider_crawl/ui_settings.py tests/test_config.py
git commit -m "feat: add coupang email 2fa config"
```

---

## Task 3: Gmail 2FA Parsing Tests

**Files:**
- Create: `tests/test_gmail_2fa.py`
- Create: `src/rider_crawl/auth/__init__.py`
- Create: `src/rider_crawl/auth/gmail.py`

- [ ] **Step 1: Add the auth package stub**

Create `src/rider_crawl/auth/__init__.py`:

```python
"""Authentication helpers for external services."""
```

- [ ] **Step 2: Add failing Gmail parser tests**

Create `tests/test_gmail_2fa.py`:

```python
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from rider_crawl.auth.gmail import (
    GmailVerificationError,
    extract_message_text,
    extract_verification_code,
    select_latest_verification_code,
)


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def test_extract_message_text_reads_plain_text_payload():
    message = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _encoded("쿠팡이츠 인증번호는 123456 입니다.")},
        }
    }

    assert extract_message_text(message) == "쿠팡이츠 인증번호는 123456 입니다."


def test_extract_message_text_reads_nested_parts_and_html():
    message = {
        "payload": {
            "parts": [
                {"mimeType": "text/html", "body": {"data": _encoded("<p>verification code: <b>654321</b></p>")}},
            ]
        }
    }

    assert "verification code:" in extract_message_text(message)
    assert "654321" in extract_message_text(message)


def test_extract_verification_code_prefers_nearby_keyword():
    text = "주문번호 111111 / 인증번호: 222222"

    assert extract_verification_code(text, code_digits=6) == "222222"


def test_extract_verification_code_allows_single_fallback_number():
    assert extract_verification_code("코드 안내: 333333", code_digits=6) == "333333"


def test_extract_verification_code_rejects_ambiguous_fallback_numbers():
    with pytest.raises(GmailVerificationError, match="인증번호를 찾지 못했습니다"):
        extract_verification_code("주문 111111, 금액 222222", code_digits=6)


def test_select_latest_verification_code_ignores_messages_before_request():
    requested_after = datetime.fromtimestamp(2_000, tz=timezone.utc)
    messages = [
        {
            "id": "old",
            "internalDate": "1000000",
            "payload": {"mimeType": "text/plain", "body": {"data": _encoded("인증번호 111111")}},
        },
        {
            "id": "new",
            "internalDate": "3000000",
            "payload": {"mimeType": "text/plain", "body": {"data": _encoded("인증번호 999999")}},
        },
    ]

    assert select_latest_verification_code(messages, requested_after=requested_after, code_digits=6) == "999999"


def test_select_latest_verification_code_uses_newest_matching_message():
    requested_after = datetime.fromtimestamp(1_000, tz=timezone.utc)
    messages = [
        {
            "id": "first",
            "internalDate": "3000000",
            "payload": {"mimeType": "text/plain", "body": {"data": _encoded("인증번호 111111")}},
        },
        {
            "id": "second",
            "internalDate": "5000000",
            "payload": {"mimeType": "text/plain", "body": {"data": _encoded("인증번호 222222")}},
        },
    ]

    assert select_latest_verification_code(messages, requested_after=requested_after, code_digits=6) == "222222"
```

- [ ] **Step 3: Add a minimal failing module**

Create `src/rider_crawl/auth/gmail.py` with only the exception class so imports fail on missing functions, not missing module:

```python
from __future__ import annotations


class GmailVerificationError(RuntimeError):
    """Safe error for Gmail verification failures."""
```

- [ ] **Step 4: Run parser tests and confirm failure**

Run:

```powershell
uv run pytest tests/test_gmail_2fa.py -q
```

Expected before full implementation:

```text
ImportError: cannot import name 'extract_message_text'
```

- [ ] **Step 5: Commit if isolated**

```powershell
git add src/rider_crawl/auth/__init__.py src/rider_crawl/auth/gmail.py tests/test_gmail_2fa.py
git commit -m "test: cover gmail verification parsing"
```

---

## Task 4: Gmail 2FA Implementation

**Files:**
- Modify: `src/rider_crawl/auth/gmail.py`
- Test: `tests/test_gmail_2fa.py`

- [ ] **Step 1: Implement message decoding and code selection**

Replace `src/rider_crawl/auth/gmail.py` with this structure:

```python
from __future__ import annotations

import base64
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailVerificationError(RuntimeError):
    """Safe error for Gmail verification failures."""


def extract_message_text(message: dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    chunks = list(_payload_text_chunks(payload))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def extract_verification_code(text: str, *, code_digits: int) -> str:
    digit_pattern = rf"\d{{{code_digits}}}"
    keyword_pattern = re.compile(
        rf"(?:인증번호|verification code|verify code|code)[^\d]{{0,30}}({digit_pattern})",
        re.IGNORECASE,
    )
    if match := keyword_pattern.search(text or ""):
        return match.group(1)

    fallback_matches = re.findall(rf"\b({digit_pattern})\b", text or "")
    unique_matches = list(dict.fromkeys(fallback_matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    raise GmailVerificationError("인증번호를 찾지 못했습니다.")


def select_latest_verification_code(
    messages: Iterable[dict[str, Any]],
    *,
    requested_after: datetime,
    code_digits: int,
) -> str:
    requested_after_ms = int(requested_after.astimezone(timezone.utc).timestamp() * 1000)
    newest_first = sorted(
        (message for message in messages if _message_internal_date_ms(message) >= requested_after_ms),
        key=_message_internal_date_ms,
        reverse=True,
    )
    for message in newest_first:
        text = extract_message_text(message)
        try:
            return extract_verification_code(text, code_digits=code_digits)
        except GmailVerificationError:
            continue
    raise GmailVerificationError("요청 시각 이후 인증번호 메일을 찾지 못했습니다.")
```

Add the private helpers below it:

```python
def _payload_text_chunks(payload: dict[str, Any]) -> Iterable[str]:
    mime_type = str(payload.get("mimeType") or "").casefold()
    body_data = ((payload.get("body") or {}).get("data")) or ""
    if body_data and mime_type in {"text/plain", "text/html"}:
        decoded = _decode_gmail_body(body_data)
        yield _html_to_text(decoded) if mime_type == "text/html" else decoded

    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            yield from _payload_text_chunks(part)


def _decode_gmail_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _message_internal_date_ms(message: dict[str, Any]) -> int:
    try:
        return int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        return 0


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts)


def _html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(html or "")
    return parser.text()
```

- [ ] **Step 2: Implement OAuth and polling**

Add these functions to the same module:

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
    service = _build_gmail_service(credentials_path=credentials_path, token_path=token_path)
    deadline = time.monotonic() + poll_seconds
    last_error: GmailVerificationError | None = None

    while True:
        messages = _fetch_recent_messages(service, query=query)
        try:
            return select_latest_verification_code(
                messages,
                requested_after=requested_after,
                code_digits=code_digits,
            )
        except GmailVerificationError as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            raise GmailVerificationError("인증 메일을 제한 시간 안에 찾지 못했습니다.") from last_error
        time.sleep(max(1, poll_interval_seconds))


def _build_gmail_service(*, credentials_path: Path, token_path: Path) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GmailVerificationError("Gmail API 의존성이 설치되지 않았습니다.") from exc

    if not credentials_path.exists():
        raise GmailVerificationError(f"Gmail OAuth 클라이언트 파일을 찾지 못했습니다: {credentials_path}")

    credentials = None
    scopes = [GMAIL_READONLY_SCOPE]
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=credentials)


def _fetch_recent_messages(service: Any, *, query: str) -> list[dict[str, Any]]:
    listed = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
    refs = listed.get("messages") or []
    messages: list[dict[str, Any]] = []
    for ref in refs:
        message_id = ref.get("id")
        if not message_id:
            continue
        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        messages.append(message)
    return messages
```

- [ ] **Step 3: Run Gmail parser tests**

Run:

```powershell
uv run pytest tests/test_gmail_2fa.py -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Commit if isolated**

```powershell
git add src/rider_crawl/auth/gmail.py tests/test_gmail_2fa.py
git commit -m "feat: read gmail verification codes"
```

---

## Task 5: Coupang Email 2FA Browser Tests

**Files:**
- Create: `tests/test_coupang_email_2fa.py`
- Create/Modify: `src/rider_crawl/auth/coupang_email_2fa.py`

- [ ] **Step 1: Add failing fake-page tests**

Create `tests/test_coupang_email_2fa.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rider_crawl.auth.coupang_email_2fa import recover_coupang_session_with_email_2fa
from rider_crawl.config import AppConfig


def test_recover_coupang_email_2fa_clicks_sends_fills_and_submits(tmp_path):
    config = _config(tmp_path, enabled=True)
    page = _Fake2FAPage("<html><body>이메일 인증 인증번호</body></html>")
    calls = []

    def fake_fetch_code(**kwargs):
        calls.append(kwargs)
        return "123456"

    assert recover_coupang_session_with_email_2fa(page, config, fetch_code=fake_fetch_code) is True

    assert page.clicked_texts == ["이메일", "인증번호 발송", "확인"]
    assert page.filled_values == ["123456"]
    assert calls[0]["credentials_path"] == config.gmail_credentials_path
    assert calls[0]["token_path"] == config.gmail_token_path
    assert calls[0]["query"] == config.gmail_2fa_query
    assert calls[0]["code_digits"] == 6


def test_recover_coupang_email_2fa_returns_false_when_disabled(tmp_path):
    config = _config(tmp_path, enabled=False)
    page = _Fake2FAPage("<html><body>이메일 인증 인증번호</body></html>")

    assert recover_coupang_session_with_email_2fa(page, config, fetch_code=lambda **_: "123456") is False
    assert page.clicked_texts == []


def test_recover_coupang_email_2fa_returns_false_for_captcha(tmp_path):
    config = _config(tmp_path, enabled=True)
    page = _Fake2FAPage("<html><body>captcha 보안문자 이메일 인증</body></html>")

    assert recover_coupang_session_with_email_2fa(page, config, fetch_code=lambda **_: "123456") is False


def test_recover_coupang_email_2fa_returns_false_for_password_login(tmp_path):
    config = _config(tmp_path, enabled=True)
    page = _Fake2FAPage("<html><body>아이디 입력 비밀번호 입력 로그인</body></html>")

    assert recover_coupang_session_with_email_2fa(page, config, fetch_code=lambda **_: "123456") is False
```

Add fake classes in the same test file:

```python
class _Fake2FAPage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.clicked_texts: list[str] = []
        self.filled_values: list[str] = []
        self.waited_states: list[str] = []

    def content(self) -> str:
        return self.html

    def get_by_text(self, text, **_kwargs):
        return _FakeLocator(self, str(text))

    def get_by_placeholder(self, text, **_kwargs):
        return _FakeLocator(self, "인증번호")

    def get_by_role(self, _role, *, name=None, **_kwargs):
        return _FakeLocator(self, str(name.pattern if hasattr(name, "pattern") else name))

    def locator(self, selector):
        return _FakeLocator(self, str(selector))

    def wait_for_load_state(self, state, **_kwargs):
        self.waited_states.append(state)


class _FakeLocator:
    def __init__(self, page: _Fake2FAPage, label: str) -> None:
        self.page = page
        self.label = label

    @property
    def first(self):
        return self

    def filter(self, *, has_text):
        return _FakeLocator(self.page, str(has_text))

    def click(self, **_kwargs):
        if "이메일" in self.label:
            self.page.clicked_texts.append("이메일")
        elif "발송" in self.label or "받기" in self.label or "send" in self.label.casefold():
            self.page.clicked_texts.append("인증번호 발송")
        else:
            self.page.clicked_texts.append("확인")

    def fill(self, value, **_kwargs):
        self.page.filled_values.append(value)
```

Add config helper:

```python
def _config(tmp_path: Path, *, enabled: bool) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
        platform_name="coupang",
        baemin_center_name="쿠팡강남센터",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        coupang_auto_email_2fa_enabled=enabled,
        gmail_credentials_path=tmp_path / "credentials.gmail.json",
        gmail_token_path=tmp_path / "token.gmail.json",
        gmail_2fa_query="from:(coupang.com) newer_than:10m",
        gmail_2fa_poll_seconds=120,
        gmail_2fa_poll_interval_seconds=5,
        coupang_2fa_code_digits=6,
    )
```

- [ ] **Step 2: Add a minimal failing module**

Create `src/rider_crawl/auth/coupang_email_2fa.py`:

```python
from __future__ import annotations

from typing import Any, Callable

from rider_crawl.config import AppConfig


def recover_coupang_session_with_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    fetch_code: Callable[..., str] | None = None,
) -> bool:
    return False
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```powershell
uv run pytest tests/test_coupang_email_2fa.py -q
```

Expected:

```text
FAILED test_recover_coupang_email_2fa_clicks_sends_fills_and_submits
```

- [ ] **Step 4: Commit if isolated**

```powershell
git add src/rider_crawl/auth/coupang_email_2fa.py tests/test_coupang_email_2fa.py
git commit -m "test: cover coupang email 2fa browser flow"
```

---

## Task 6: Coupang Email 2FA Browser Implementation

**Files:**
- Modify: `src/rider_crawl/auth/coupang_email_2fa.py`
- Test: `tests/test_coupang_email_2fa.py`

- [ ] **Step 1: Implement safe screen detection and browser actions**

Replace `src/rider_crawl/auth/coupang_email_2fa.py` with this structure:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from rider_crawl.config import AppConfig

from .gmail import fetch_latest_verification_code


_EMAIL_AUTH_TEXTS = ("이메일", "메일", "Email", "E-mail")
_SEND_CODE_TEXTS = ("인증번호 발송", "인증번호 받기", "인증번호 전송", "코드 받기", "Send code")
_SUBMIT_TEXTS = ("확인", "인증", "제출", "다음", "Verify")


def recover_coupang_session_with_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    fetch_code: Callable[..., str] | None = None,
) -> bool:
    if not config.coupang_auto_email_2fa_enabled:
        return False

    html = _safe_content(page)
    if not _looks_like_email_2fa_screen(html):
        return False

    if not _click_first_text(page, _EMAIL_AUTH_TEXTS, timeout=config.page_timeout_seconds):
        return False
    if not _click_first_text(page, _SEND_CODE_TEXTS, timeout=config.page_timeout_seconds):
        return False

    requested_after = datetime.now(timezone.utc)
    code_fetcher = fetch_code or fetch_latest_verification_code
    code = code_fetcher(
        credentials_path=config.gmail_credentials_path,
        token_path=config.gmail_token_path,
        query=config.gmail_2fa_query,
        requested_after=requested_after,
        poll_seconds=config.gmail_2fa_poll_seconds,
        poll_interval_seconds=config.gmail_2fa_poll_interval_seconds,
        code_digits=config.coupang_2fa_code_digits,
    )

    if not _fill_verification_code(page, code, timeout=config.page_timeout_seconds):
        return False
    if not _click_first_text(page, _SUBMIT_TEXTS, timeout=config.page_timeout_seconds):
        return False

    _safe_wait_networkidle(page)
    return not _looks_like_verification_failure(_safe_content(page))
```

Add private helpers below:

```python
def _safe_content(page: Any) -> str:
    try:
        return str(page.content())
    except Exception:
        return ""


def _looks_like_email_2fa_screen(html: str) -> bool:
    text = re.sub(r"\s+", " ", html or "").casefold()
    if any(token in text for token in ("captcha", "보안문자", "자동입력")):
        return False
    if ("아이디 입력" in text and "비밀번호 입력" in text) or ("username" in text and "password" in text):
        return False
    has_email = any(token.casefold() in text for token in _EMAIL_AUTH_TEXTS)
    has_verification = any(token in text for token in ("인증", "verification", "verify", "code", "코드"))
    return has_email and has_verification


def _looks_like_verification_failure(html: str) -> bool:
    text = re.sub(r"\s+", " ", html or "").casefold()
    return any(
        token in text
        for token in (
            "인증번호가 일치하지",
            "인증번호를 다시",
            "verification code is invalid",
            "invalid code",
        )
    )


def _click_first_text(page: Any, labels: tuple[str, ...], *, timeout: int) -> bool:
    for label in labels:
        locators = [
            lambda label=label: page.get_by_text(label).first,
            lambda label=label: page.get_by_role("button", name=re.compile(re.escape(label), re.IGNORECASE)).first,
            lambda label=label: page.locator("button, label, div, span").filter(has_text=label).first,
        ]
        for make_locator in locators:
            try:
                make_locator().click(timeout=timeout)
                return True
            except Exception:
                continue
    return False


def _fill_verification_code(page: Any, code: str, *, timeout: int) -> bool:
    locators = [
        lambda: page.get_by_placeholder(re.compile("인증번호|코드|verification|code", re.IGNORECASE)).first,
        lambda: page.locator("input[name*='otp' i], input[name*='code' i], input[type='tel'], input[type='text']").first,
    ]
    for make_locator in locators:
        try:
            make_locator().fill(code, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _safe_wait_networkidle(page: Any) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
```

- [ ] **Step 2: Run browser-flow tests**

Run:

```powershell
uv run pytest tests/test_coupang_email_2fa.py -q
```

Expected:

```text
passed
```

- [ ] **Step 3: Commit if isolated**

```powershell
git add src/rider_crawl/auth/coupang_email_2fa.py tests/test_coupang_email_2fa.py
git commit -m "feat: automate coupang email 2fa screen"
```

---

## Task 7: Coupang Crawler Recovery Tests

**Files:**
- Modify: `tests/test_coupang_crawler.py`
- Modify: `src/rider_crawl/platforms/coupang/crawler.py`

- [ ] **Step 1: Extend fake page for navigation**

In `tests/test_coupang_crawler.py`, extend `_FakePage.__init__`:

```python
        self.goto_calls: list[str] = []
```

Add this method to `_FakePage`:

```python
    def goto(self, url: str, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        return None
```

- [ ] **Step 2: Add disabled/default behavior test**

Add this test near the existing login-required tests:

```python
def test_coupang_fetch_target_page_content_keeps_existing_error_when_email_2fa_disabled(tmp_path, monkeypatch):
    config = _config(tmp_path)
    page = _FakePage(
        config.coupang_eats_url,
        html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
        wait_error=FakeTimeout("locator timeout"),
    )
    browser = _FakeBrowser([page])
    calls = []

    monkeypatch.setattr(crawler, "recover_coupang_session_with_email_2fa", lambda *_args, **_kwargs: calls.append(True) or True)

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))

    assert calls == []
```

- [ ] **Step 3: Add success recovery test**

Add:

```python
def test_coupang_fetch_target_page_content_recovers_email_2fa_once_when_enabled(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config = config.__class__(
        **{**config.__dict__, "coupang_auto_email_2fa_enabled": True}
    )
    page = _FakePage(
        config.coupang_eats_url,
        html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
        wait_error=FakeTimeout("locator timeout"),
    )
    browser = _FakeBrowser([page])
    calls = []

    def fake_recover(received_page, received_config):
        calls.append((received_page, received_config))
        page.wait_error = None
        page.html = "<html>라이더 현황</html>"
        return True

    monkeypatch.setattr(crawler, "recover_coupang_session_with_email_2fa", fake_recover)

    html = crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))

    assert html == "<html>라이더 현황</html>"
    assert calls == [(page, config)]
    assert page.goto_calls == [config.coupang_eats_url]
```

- [ ] **Step 4: Add recovery failure test**

Add:

```python
def test_coupang_fetch_target_page_content_stops_when_email_2fa_recovery_fails(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config = config.__class__(
        **{**config.__dict__, "coupang_auto_email_2fa_enabled": True}
    )
    page = _FakePage(
        config.coupang_eats_url,
        html="<html><body>세션이 만료되었습니다. 다시 로그인하세요.</body></html>",
        wait_error=FakeTimeout("locator timeout"),
    )
    browser = _FakeBrowser([page])

    monkeypatch.setattr(crawler, "recover_coupang_session_with_email_2fa", lambda *_args, **_kwargs: False)

    with pytest.raises(BrowserActionRequiredError, match="다시 로그인"):
        crawler._fetch_target_page_content(browser, config, load_timeout_errors=(FakeTimeout,))
```

- [ ] **Step 5: Run crawler tests and confirm new failures**

Run:

```powershell
uv run pytest tests/test_coupang_crawler.py -q
```

Expected before implementation:

```text
FAILED test_coupang_fetch_target_page_content_recovers_email_2fa_once_when_enabled
```

- [ ] **Step 6: Commit if isolated**

```powershell
git add tests/test_coupang_crawler.py
git commit -m "test: cover coupang crawler email 2fa recovery"
```

---

## Task 8: Coupang Crawler Recovery Implementation

**Files:**
- Modify: `src/rider_crawl/platforms/coupang/crawler.py`
- Test: `tests/test_coupang_crawler.py`

- [ ] **Step 1: Import the recovery function**

Add near existing imports:

```python
from rider_crawl.auth.coupang_email_2fa import recover_coupang_session_with_email_2fa
```

- [ ] **Step 2: Add recovery helpers**

Add below `_fetch_target_page_content` or near other private helpers:

```python
def _try_recover_coupang_session(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    timeout_errors: tuple[type[BaseException], ...],
) -> bool:
    if not config.coupang_auto_email_2fa_enabled:
        return False
    if not recover_coupang_session_with_email_2fa(page, config):
        return False

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=config.page_timeout_seconds)
        page.wait_for_load_state("networkidle", timeout=10_000)
    except timeout_errors:
        pass
    _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=timeout_errors)
    return True


def _wait_for_target_page_ready_with_optional_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    target_url: str,
    timeout_errors: tuple[type[BaseException], ...],
) -> None:
    if _page_looks_like_coupang_login_required(page):
        if _try_recover_coupang_session(page, config, target_url=target_url, timeout_errors=timeout_errors):
            return

    try:
        _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=timeout_errors)
    except BrowserActionRequiredError:
        if _try_recover_coupang_session(page, config, target_url=target_url, timeout_errors=timeout_errors):
            return
        raise
```

- [ ] **Step 3: Use the wrapper in CDP target fetch**

In `_fetch_target_page_content`, replace:

```python
    _wait_for_target_page_ready(page, config, target_url=target_url, timeout_errors=load_timeout_errors)
```

with:

```python
    _wait_for_target_page_ready_with_optional_email_2fa(
        page,
        config,
        target_url=target_url,
        timeout_errors=load_timeout_errors,
    )
```

Keep the second `_wait_for_target_page_ready(...)` after center-tab click unchanged.

- [ ] **Step 4: Recover when target tab is missing but login page exists**

In `_fetch_target_page_content`, replace:

```python
    if page is None:
        _raise_coupang_page_action_required(pages, target_url)
```

with:

```python
    if page is None:
        login_page = _login_required_page(pages)
        if login_page is not None and _try_recover_coupang_session(
            login_page,
            config,
            target_url=target_url,
            timeout_errors=load_timeout_errors,
        ):
            page = login_page
        else:
            _raise_coupang_page_action_required(pages, target_url)
```

- [ ] **Step 5: Use the wrapper in persistent mode**

In `fetch_page_html_via_persistent_context`, replace the first `_wait_for_target_page_ready(...)` after `page.goto(...)` with `_wait_for_target_page_ready_with_optional_email_2fa(...)` using the same `target_url` and timeout args.

- [ ] **Step 6: Run crawler tests**

Run:

```powershell
uv run pytest tests/test_coupang_crawler.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit if isolated**

```powershell
git add src/rider_crawl/platforms/coupang/crawler.py tests/test_coupang_crawler.py
git commit -m "feat: recover coupang session with email 2fa"
```

---

## Task 9: Documentation And Operator Guidance

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `src/rider_crawl/ui.py`
- Already present: `.gitignore`
- Already present: `secrets/google/README.md`

- [ ] **Step 1: Update `.env.example`**

Add this commented block near the Coupang variables:

```text
# 쿠팡이츠 Gmail 이메일 2차 인증 자동 복구(기본 꺼짐).
# COUPANG_AUTO_EMAIL_2FA_ENABLED=false
# GMAIL_CREDENTIALS_PATH=secrets/google/credentials.gmail.json
# GMAIL_TOKEN_PATH=secrets/google/token.gmail.json
# GMAIL_2FA_QUERY=from:(coupang.com) newer_than:10m
# GMAIL_2FA_POLL_SECONDS=120
# GMAIL_2FA_POLL_INTERVAL_SECONDS=5
# COUPANG_2FA_CODE_DIGITS=6
```

- [ ] **Step 2: Update README policy**

Replace the current line:

```text
- 쿠팡이츠 로그인이 만료되면 자동 로그인이나 2차 인증 처리를 하지 않고 해당 크롤링 탭의 반복 실행을 중지합니다. Chrome에서 다시 로그인한 뒤 `rider-performance`와 `peak-dashboard` 두 페이지를 로그인된 상태로 열어두고 `시작`을 다시 누르세요.
```

with:

```text
- 쿠팡이츠 로그인이 만료되면 기본값은 기존처럼 해당 크롤링 탭의 반복 실행을 중지합니다. `COUPANG_AUTO_EMAIL_2FA_ENABLED=true`를 설정하면 이메일 인증 화면에서 Gmail API로 인증번호를 읽어 한 번 자동 복구를 시도합니다. 복구가 실패하거나 CAPTCHA, 아이디/비밀번호 입력 화면, 알 수 없는 인증 화면이면 반복 인증 요청을 보내지 않고 기존처럼 탭을 중지합니다.
```

Add a short README section under the Coupang CLI variables:

```markdown
### 쿠팡이츠 Gmail 이메일 2차 인증 자동 복구

기본값은 꺼짐입니다. 운영 PC에서 Gmail API OAuth 파일을 준비한 뒤 아래 값을 설정할 때만 동작합니다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `COUPANG_AUTO_EMAIL_2FA_ENABLED` | `false` | 이메일 2차 인증 자동 복구 사용 여부 |
| `GMAIL_CREDENTIALS_PATH` | `secrets/google/credentials.gmail.json` | Google OAuth Desktop Client JSON |
| `GMAIL_TOKEN_PATH` | `secrets/google/token.gmail.json` | 최초 승인 뒤 저장되는 토큰 |
| `GMAIL_2FA_QUERY` | `from:(coupang.com) newer_than:10m` | Gmail 인증 메일 검색 조건 |
| `GMAIL_2FA_POLL_SECONDS` | `120` | 인증 메일을 기다리는 최대 시간 |
| `GMAIL_2FA_POLL_INTERVAL_SECONDS` | `5` | Gmail 재조회 간격 |
| `COUPANG_2FA_CODE_DIGITS` | `6` | 인증번호 자리수 |

Gmail 권한은 `https://www.googleapis.com/auth/gmail.readonly`만 사용합니다. 인증번호, OAuth 토큰, 쿠팡 계정 정보는 로그에 남기지 않습니다.
```

- [ ] **Step 3: Update UI checklist text**

In `src/rider_crawl/ui.py`, replace checklist item 5:

```python
"5. 2차 인증은 앱이 처리하지 않습니다. 로그인 만료 시 직접 다시 로그인하세요.\n"
```

with:

```python
"5. 쿠팡이츠 이메일 2차 인증 자동 복구는 환경변수로 켠 경우에만 한 번 시도합니다. 실패하면 직접 다시 로그인하세요.\n"
```

- [ ] **Step 4: Verify `.gitignore` remains safe**

Run:

```powershell
git diff -- .gitignore
```

Expected diff still includes these ignores:

```text
secrets/google/*.json
secrets/google/*.pickle
secrets/google/token*
secrets/google/credentials*
```

- [ ] **Step 5: Commit if isolated**

```powershell
git add README.md .env.example src/rider_crawl/ui.py .gitignore secrets/google/README.md
git commit -m "docs: document coupang gmail 2fa setup"
```

---

## Task 10: Final Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused tests**

Run:

```powershell
uv run pytest tests/test_config.py tests/test_gmail_2fa.py tests/test_coupang_email_2fa.py tests/test_coupang_crawler.py -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run full test suite**

Run:

```powershell
uv run pytest -q
```

Expected:

```text
passed
```

If the known `UnicodeDecodeError: cp949` stops Python before pytest starts, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_gmail_2fa.py tests/test_coupang_email_2fa.py tests/test_coupang_crawler.py -q
```

If that also stops before pytest starts, document the exact traceback in the final notes and do not claim tests passed.

- [ ] **Step 3: Check no secrets are tracked**

Run:

```powershell
git status --short
git diff --name-only
```

Expected:

```text
No credentials.gmail.json
No token.gmail.json
No raw Gmail message files
```

- [ ] **Step 4: Check for unsafe logging text**

Run:

```powershell
rg "token|credentials|인증번호|verification code" src tests README.md .env.example docs -n
```

Expected:

```text
Only config names, docs, test fake values, and safe error text appear.
No real token, raw email body, or real 인증번호 appears.
```

- [ ] **Step 5: Final implementation summary**

Report:

- Branch name: `google_verification`
- Files changed
- Tests run and exact result
- Whether `uv run pytest` was blocked by the known `cp949` environment issue
- Operator steps still required:
  1. Create Google OAuth Desktop Client.
  2. Enable Gmail API.
  3. Save `secrets/google/credentials.gmail.json`.
  4. Run once to create `secrets/google/token.gmail.json`.
  5. Narrow `GMAIL_2FA_QUERY` after seeing the real Coupang sender/subject.
