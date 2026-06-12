import base64
from datetime import datetime, timedelta, timezone

import pytest

from rider_crawl.auth import gmail
from rider_crawl.auth.gmail import (
    Gmail2faError,
    extract_message_text,
    extract_verification_code,
    fetch_latest_verification_code,
)


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _epoch_ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


# --- 본문 추출 ---------------------------------------------------------------


def test_extract_message_text_decodes_single_plain_part():
    message = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _b64url("인증번호는 123456 입니다")},
        }
    }
    assert extract_message_text(message) == "인증번호는 123456 입니다"


def test_extract_message_text_prefers_plain_over_html_in_multipart():
    message = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64url("코드 111111")}},
                {"mimeType": "text/html", "body": {"data": _b64url("<p>코드 999999</p>")}},
            ],
        }
    }
    assert extract_message_text(message) == "코드 111111"


def test_extract_message_text_strips_html_when_no_plain_part():
    message = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<div>인증번호: <b>654321</b></div>")}},
            ],
        }
    }
    assert extract_message_text(message) == "인증번호: 654321"


def test_extract_message_text_handles_nested_multipart():
    message = {
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url("verification code 246802")}},
                    ],
                }
            ],
        }
    }
    assert extract_message_text(message) == "verification code 246802"


# --- 인증번호 파싱 -----------------------------------------------------------


def test_extract_verification_code_prefers_context_keyword():
    text = "주문번호 100200 입니다. 인증번호: 123456 을 입력하세요. 고객센터 987654"
    assert extract_verification_code(text, code_digits=6) == "123456"


def test_extract_verification_code_uses_english_keyword():
    text = "Your verification code is 778899."
    assert extract_verification_code(text, code_digits=6) == "778899"


def test_extract_verification_code_fallback_when_single_number():
    text = "코드를 입력하세요 555444"
    assert extract_verification_code(text, code_digits=6) == "555444"


def test_extract_verification_code_fallback_rejects_multiple_numbers():
    # 같은 자리수 숫자가 여럿이고 주변 단어 매칭이 없으면 잘못된 추측을 하지 않는다.
    text = "주문 100200, 결제 300400 안내"
    assert extract_verification_code(text, code_digits=6) is None


def test_extract_verification_code_fallback_requires_context_keyword():
    # 인증 관련 단어가 본문에 전혀 없으면, 유일한 6자리 숫자라도 코드로 쓰지 않는다.
    # (넓은 Gmail 쿼리에 섞여 들어온 비인증 쿠팡 메일의 숫자 오인 방지)
    text = "주문번호 778899 가 접수되었습니다. 감사합니다."
    assert extract_verification_code(text, code_digits=6) is None


def test_extract_verification_code_fallback_accepts_with_distant_keyword():
    # 인증 단어가 숫자와 떨어져 있어도(주변 매칭 실패), 본문에 인증 단어가 있고 유일한
    # 6자리 숫자면 fallback으로 채택한다.
    text = "이메일 인증 안내입니다. 아래 값을 입력하세요. 778899"
    assert extract_verification_code(text, code_digits=6) == "778899"


def test_extract_verification_code_respects_code_digits():
    text = "인증번호 1234"
    assert extract_verification_code(text, code_digits=4) == "1234"
    assert extract_verification_code(text, code_digits=6) is None


# --- 폴링/필터링/최신 선택 ---------------------------------------------------


class _FakeGmailService:
    """Minimal stand-in for the Gmail API client surface used by gmail.py."""

    def __init__(self, messages: dict[str, dict]) -> None:
        # messages: {id: full message dict}
        self._messages = messages
        self.list_calls = 0

    # users().messages().list/get(...).execute() 체인을 흉내 낸다.
    def users(self):
        return self

    def messages(self):
        return self

    def list(self, *, userId, q):
        self.list_calls += 1
        return _FakeExecute({"messages": [{"id": mid} for mid in self._messages]})

    def get(self, *, userId, id, format):
        return _FakeExecute(self._messages[id])


class _FakeExecute:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _ExternalApiError(Exception):
    """Stand-in for googleapiclient.errors.HttpError / transport errors."""


class _RaisingExecute:
    def __init__(self, error: Exception):
        self._error = error

    def execute(self):
        raise self._error


class _ListRaisesService(_FakeGmailService):
    def __init__(self, error: Exception):
        super().__init__({})
        self._error = error

    def list(self, *, userId, q):
        self.list_calls += 1
        return _RaisingExecute(self._error)


class _GetRaisesService(_FakeGmailService):
    def __init__(self, messages: dict[str, dict], error: Exception):
        super().__init__(messages)
        self._error = error

    def get(self, *, userId, id, format):
        return _RaisingExecute(self._error)


def _plain_message(*, code_text: str, internal: datetime) -> dict:
    return {
        "internalDate": _epoch_ms(internal),
        "payload": {"mimeType": "text/plain", "body": {"data": _b64url(code_text)}},
    }


def test_fetch_ignores_messages_before_requested_after():
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    old = _plain_message(code_text="인증번호 111111", internal=requested_after - timedelta(minutes=5))
    service = _FakeGmailService({"old": old})

    with pytest.raises(Gmail2faError):
        fetch_latest_verification_code(
            credentials_path=None,
            token_path=None,
            query="q",
            requested_after=requested_after,
            poll_seconds=0,
            poll_interval_seconds=0,
            code_digits=6,
            build_service=lambda _c, _t: service,
            sleep=lambda _s: None,
            now=lambda: requested_after,
        )


def test_fetch_picks_newest_message_after_requested_after():
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    older = _plain_message(code_text="인증번호 111111", internal=requested_after + timedelta(seconds=10))
    newer = _plain_message(code_text="인증번호 222222", internal=requested_after + timedelta(seconds=40))
    service = _FakeGmailService({"older": older, "newer": newer})

    code = fetch_latest_verification_code(
        credentials_path=None,
        token_path=None,
        query="q",
        requested_after=requested_after,
        poll_seconds=0,
        poll_interval_seconds=0,
        code_digits=6,
        build_service=lambda _c, _t: service,
        sleep=lambda _s: None,
        now=lambda: requested_after,
    )

    assert code == "222222"


def test_fetch_does_not_use_older_mail_when_newest_unparseable():
    # 인증번호가 여러 번 발송돼 최신 메일이 본문 파싱에 실패하면, 더 오래된(이미 무효일
    # 수 있는) 코드로 내려가지 않는다. poll_seconds=0이면 그대로 실패한다.
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    older = _plain_message(code_text="인증번호 111111", internal=requested_after + timedelta(seconds=10))
    newest_unparseable = _plain_message(
        code_text="인증 메일입니다. 본문에 코드 숫자가 없습니다.",
        internal=requested_after + timedelta(seconds=40),
    )
    service = _FakeGmailService({"older": older, "newest": newest_unparseable})

    with pytest.raises(Gmail2faError):
        fetch_latest_verification_code(
            credentials_path=None,
            token_path=None,
            query="q",
            requested_after=requested_after,
            poll_seconds=0,
            poll_interval_seconds=0,
            code_digits=6,
            build_service=lambda _c, _t: service,
            sleep=lambda _s: None,
            now=lambda: requested_after,
        )


def test_fetch_polls_until_mail_arrives():
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    arrived = _plain_message(code_text="인증번호 333333", internal=requested_after + timedelta(seconds=5))

    # 첫 조회에는 메일이 없고, 두 번째 조회에서 도착하는 상황을 흉내 낸다.
    state = {"messages": {}}

    class _DelayedService(_FakeGmailService):
        def list(self, *, userId, q):
            self.list_calls += 1
            if self.list_calls >= 2:
                state["messages"] = {"arrived": arrived}
            self._messages = state["messages"]
            return _FakeExecute({"messages": [{"id": mid} for mid in self._messages]})

    service = _DelayedService({})

    # now()가 호출될 때마다 시간이 흐르게 해서 deadline 안에서 두 번째 폴링이 일어나게 한다.
    clock = {"t": requested_after}

    def fake_now():
        return clock["t"]

    def fake_sleep(_seconds):
        clock["t"] = clock["t"] + timedelta(seconds=5)

    code = fetch_latest_verification_code(
        credentials_path=None,
        token_path=None,
        query="q",
        requested_after=requested_after,
        poll_seconds=60,
        poll_interval_seconds=5,
        code_digits=6,
        build_service=lambda _c, _t: service,
        sleep=fake_sleep,
        now=fake_now,
    )

    assert code == "333333"
    assert service.list_calls >= 2


def test_fetch_wraps_list_api_error_as_gmail2faerror():
    # Gmail list 호출의 외부 예외(HttpError 등)는 raw로 새지 않고 Gmail2faError로 감싸
    # 원인을 보존한다(from exc). 메시지에 Google 클라이언트 세부 정보를 담지 않는다.
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    external = _ExternalApiError("HttpError 401: invalid_grant token detail")
    service = _ListRaisesService(external)

    with pytest.raises(Gmail2faError) as exc_info:
        fetch_latest_verification_code(
            credentials_path=None,
            token_path=None,
            query="q",
            requested_after=requested_after,
            poll_seconds=0,
            poll_interval_seconds=0,
            code_digits=6,
            build_service=lambda _c, _t: service,
            sleep=lambda _s: None,
            now=lambda: requested_after,
        )

    assert exc_info.value.__cause__ is external
    assert "HttpError" not in str(exc_info.value)


def test_fetch_wraps_get_api_error_as_gmail2faerror():
    requested_after = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    message = _plain_message(code_text="인증번호 123456", internal=requested_after + timedelta(seconds=10))
    external = _ExternalApiError("transport boom")
    service = _GetRaisesService({"m1": message}, external)

    with pytest.raises(Gmail2faError) as exc_info:
        fetch_latest_verification_code(
            credentials_path=None,
            token_path=None,
            query="q",
            requested_after=requested_after,
            poll_seconds=0,
            poll_interval_seconds=0,
            code_digits=6,
            build_service=lambda _c, _t: service,
            sleep=lambda _s: None,
            now=lambda: requested_after,
        )

    assert exc_info.value.__cause__ is external
