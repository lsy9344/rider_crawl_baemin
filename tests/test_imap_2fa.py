from datetime import datetime, timedelta, timezone
from email.header import Header
from email.message import EmailMessage

import pytest

from rider_crawl.auth import imap_2fa
from rider_crawl.auth.imap_2fa import (
    Imap2faError,
    domain_of,
    fetch_latest_verification_code,
    imap_host_for_email,
)


# --- 호스트/도메인 분기 -------------------------------------------------------


def test_imap_host_for_email_maps_naver_and_gmail():
    assert imap_host_for_email("user@naver.com") == "imap.naver.com"
    assert imap_host_for_email("user@mail.naver.com") == "imap.naver.com"
    assert imap_host_for_email("user@gmail.com") == "imap.gmail.com"
    assert imap_host_for_email("user@googlemail.com") == "imap.gmail.com"


def test_imap_host_for_email_rejects_unsupported_domain():
    with pytest.raises(Imap2faError, match="지원하지 않는"):
        imap_host_for_email("user@daum.net")


def test_domain_of_handles_missing_at():
    assert domain_of("not-an-email") == ""
    assert domain_of("USER@Naver.com") == "naver.com"


# --- fake IMAP 서버 ----------------------------------------------------------


def _header_bytes(subject: str, sender: str) -> bytes:
    enc_subject = Header(subject, "utf-8").encode()
    return (f"Subject: {enc_subject}\r\nFrom: {sender}\r\n\r\n").encode("ascii")


def _full_bytes(subject: str, sender: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg.set_content(body)
    return msg.as_bytes()


class _FakeImap:
    """``connect`` 콜러블로 주입하는 fake. IMAPClient 응답 표면을 흉내 낸다."""

    def __init__(self, messages: dict):
        # messages: {uid: {internaldate, subject, sender, body}}
        self._messages = messages
        self.select_calls = 0
        self.logged_out = False

    def select_folder(self, folder, readonly=False):
        self.select_calls += 1
        assert folder == "INBOX"
        assert readonly is True

    def search(self, criteria):
        # 서버측 SINCE는 '일' 단위라 후보만 줄인다. fake는 전체 uid를 돌려주고
        # INTERNALDATE 컷은 _find_code_once가 한다.
        return list(self._messages)

    def fetch(self, uids, data_items):
        result = {}
        if any("HEADER.FIELDS" in item for item in data_items):
            for uid in uids:
                msg = self._messages[uid]
                result[uid] = {
                    b"INTERNALDATE": msg["internaldate"],
                    b"BODY[HEADER.FIELDS (SUBJECT FROM)]": _header_bytes(
                        msg["subject"], msg["sender"]
                    ),
                }
            return result
        for uid in uids:
            msg = self._messages[uid]
            result[uid] = {
                b"BODY[]": _full_bytes(msg["subject"], msg["sender"], msg["body"])
            }
        return result

    def logout(self):
        self.logged_out = True


_REQUESTED_AFTER = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
_SENDER = "Coupang <donotreply@coupang.com>"


def _message(*, code_text: str, internal: datetime, subject="[쿠팡] 인증번호 안내", sender=_SENDER):
    return {"internaldate": internal, "subject": subject, "sender": sender, "body": code_text}


def _fetch(server, **overrides):
    kwargs = dict(
        email_address="rider@naver.com",
        app_password="app-pass",
        subject_keyword="인증번호",
        sender_keyword="coupang",
        requested_after=_REQUESTED_AFTER,
        poll_seconds=0,
        poll_interval_seconds=0,
        code_digits=6,
        connect=lambda *_args: server,
        sleep=lambda _s: None,
        now=lambda: _REQUESTED_AFTER,
    )
    kwargs.update(overrides)
    return fetch_latest_verification_code(**kwargs)


# --- 동작 검증 ---------------------------------------------------------------


def test_fetch_returns_code_from_qualifying_mail():
    server = _FakeImap(
        {1: _message(code_text="인증번호 246802", internal=_REQUESTED_AFTER + timedelta(seconds=20))}
    )
    assert _fetch(server) == "246802"
    assert server.logged_out is True


def test_fetch_ignores_mail_before_requested_after():
    server = _FakeImap(
        {1: _message(code_text="인증번호 111111", internal=_REQUESTED_AFTER - timedelta(minutes=5))}
    )
    with pytest.raises(Imap2faError, match="요청 시각 이후"):
        _fetch(server)


def test_fetch_picks_newest_after_requested_after():
    server = _FakeImap(
        {
            1: _message(code_text="인증번호 111111", internal=_REQUESTED_AFTER + timedelta(seconds=10)),
            2: _message(code_text="인증번호 222222", internal=_REQUESTED_AFTER + timedelta(seconds=40)),
        }
    )
    assert _fetch(server) == "222222"


def test_fetch_filters_by_subject_keyword():
    server = _FakeImap(
        {
            1: _message(
                code_text="인증번호 333333",
                internal=_REQUESTED_AFTER + timedelta(seconds=20),
                subject="광고 메일입니다",
            )
        }
    )
    with pytest.raises(Imap2faError, match="요청 시각 이후"):
        _fetch(server)


def test_fetch_filters_by_sender_keyword():
    server = _FakeImap(
        {
            1: _message(
                code_text="인증번호 444444",
                internal=_REQUESTED_AFTER + timedelta(seconds=20),
                sender="Someone <noreply@other.com>",
            )
        }
    )
    with pytest.raises(Imap2faError, match="요청 시각 이후"):
        _fetch(server)


def test_fetch_raises_when_newest_mail_has_no_code():
    server = _FakeImap(
        {
            1: _message(
                code_text="인증 메일입니다만 숫자가 없습니다.",
                internal=_REQUESTED_AFTER + timedelta(seconds=20),
            )
        }
    )
    with pytest.raises(Imap2faError):
        _fetch(server)


def test_fetch_re_selects_inbox_each_poll():
    # 네이버 대비 매 폴링 재-SELECT를 검증한다. 첫 폴링엔 메일이 없고, 두 번째에 도착.
    arrived = _message(code_text="인증번호 555555", internal=_REQUESTED_AFTER + timedelta(seconds=5))
    state = {"messages": {}}

    class _DelayedImap(_FakeImap):
        def search(self, criteria):
            self.select_calls  # no-op
            return list(self._messages)

        def select_folder(self, folder, readonly=False):
            super().select_folder(folder, readonly=readonly)
            if self.select_calls >= 2:
                self._messages = {1: arrived}

    server = _DelayedImap({})
    clock = {"t": _REQUESTED_AFTER}

    def fake_now():
        return clock["t"]

    def fake_sleep(_seconds):
        clock["t"] = clock["t"] + timedelta(seconds=5)

    code = _fetch(server, poll_seconds=60, poll_interval_seconds=5, now=fake_now, sleep=fake_sleep)
    assert code == "555555"
    assert server.select_calls >= 2


def test_fetch_logs_out_even_on_failure():
    server = _FakeImap(
        {1: _message(code_text="인증번호 111111", internal=_REQUESTED_AFTER - timedelta(minutes=5))}
    )
    with pytest.raises(Imap2faError):
        _fetch(server)
    assert server.logged_out is True


def test_fetch_does_not_leak_app_password_in_errors():
    server = _FakeImap({})
    try:
        _fetch(server, app_password="super-secret-app-pass")
    except Imap2faError as exc:
        assert "super-secret-app-pass" not in str(exc)


def test_fetch_uses_host_from_email_domain():
    captured = {}

    def _connect(host, port, email_address, app_password):
        captured["host"] = host
        return _FakeImap(
            {1: _message(code_text="인증번호 246802", internal=_REQUESTED_AFTER + timedelta(seconds=20))}
        )

    code = fetch_latest_verification_code(
        email_address="rider@gmail.com",
        app_password="x",
        subject_keyword="인증번호",
        sender_keyword="coupang",
        requested_after=_REQUESTED_AFTER,
        poll_seconds=0,
        poll_interval_seconds=0,
        code_digits=6,
        connect=_connect,
        sleep=lambda _s: None,
        now=lambda: _REQUESTED_AFTER,
    )
    assert code == "246802"
    assert captured["host"] == "imap.gmail.com"


# --- 앱 비밀번호 공백 strip ---------------------------------------------------


def test_imap_connect_strips_app_password_whitespace(monkeypatch):
    import imapclient

    recorded = {}

    class _FakeClient:
        def __init__(self, host, port, ssl, use_uid):
            recorded["host"] = host
            recorded["port"] = port

        def login(self, email_address, app_password):
            recorded["password"] = app_password

    monkeypatch.setattr(imapclient, "IMAPClient", _FakeClient)

    imap_2fa._imap_connect("imap.gmail.com", 993, "a@gmail.com", "nuda vmiy gtfr ggeg")

    # Gmail이 4자리씩 공백으로 보여주는 앱 비밀번호를 그대로 붙여넣어도 strip해 로그인한다.
    assert recorded["password"] == "nudavmiygtfrggeg"


def test_imap_connect_wraps_login_failure_without_password(monkeypatch):
    import imapclient

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, email_address, app_password):
            raise RuntimeError("AUTHENTICATIONFAILED secret-pass detail")

    monkeypatch.setattr(imapclient, "IMAPClient", _FakeClient)

    with pytest.raises(Imap2faError) as exc_info:
        imap_2fa._imap_connect("imap.gmail.com", 993, "a@gmail.com", "secret-pass")

    assert "secret-pass" not in str(exc_info.value)
