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
    """``connect`` 콜러블로 주입하는 fake. IMAPClient 응답 표면을 흉내 낸다.

    폴더별 메일을 담는다(기본 INBOX). ``extra_folders``로 프로모션/스팸 등 다른 폴더에
    메일을 둘 수 있다(네이버 분류함 수신 시나리오 검증용).
    """

    def __init__(self, messages: dict, *, extra_folders: dict | None = None):
        # messages: {uid: {internaldate, subject, sender, body}}
        self._by_folder = {"INBOX": dict(messages)}
        for name, msgs in (extra_folders or {}).items():
            self._by_folder[name] = dict(msgs)
        self._cur = "INBOX"
        self.select_calls = 0
        self.logged_out = False

    def list_folders(self):
        return [((), "/", name) for name in self._by_folder]

    def select_folder(self, folder, readonly=False):
        self.select_calls += 1
        assert readonly is True
        self._cur = folder

    def search(self, criteria):
        # 서버측 SINCE는 '일' 단위라 후보만 줄인다. fake는 현재 폴더의 전체 uid를 돌려주고
        # INTERNALDATE 컷은 _find_code_once가 한다.
        return list(self._by_folder.get(self._cur, {}))

    def fetch(self, uids, data_items):
        msgs = self._by_folder.get(self._cur, {})
        result = {}
        if any("HEADER.FIELDS" in item for item in data_items):
            for uid in uids:
                msg = msgs[uid]
                result[uid] = {
                    b"INTERNALDATE": msg["internaldate"],
                    b"BODY[HEADER.FIELDS (SUBJECT FROM)]": _header_bytes(
                        msg["subject"], msg["sender"]
                    ),
                }
            return result
        for uid in uids:
            msg = msgs[uid]
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
        def select_folder(self, folder, readonly=False):
            super().select_folder(folder, readonly=readonly)
            if self.select_calls >= 2:
                self._by_folder["INBOX"] = {1: arrived}

    server = _DelayedImap({})
    clock = {"t": _REQUESTED_AFTER}

    def fake_now():
        return clock["t"]

    def fake_sleep(_seconds):
        clock["t"] = clock["t"] + timedelta(seconds=5)

    code = _fetch(server, poll_seconds=60, poll_interval_seconds=5, now=fake_now, sleep=fake_sleep)
    assert code == "555555"
    assert server.select_calls >= 2


def test_fetch_finds_code_in_non_inbox_folder():
    # 네이버는 쿠팡 인증 메일을 INBOX가 아니라 '프로모션' 등으로 분류해 넣기도 한다.
    # INBOX가 비어 있어도 분류 폴더에서 코드를 찾아야 한다.
    server = _FakeImap(
        {},
        extra_folders={
            "프로모션": {
                1: _message(code_text="인증번호 313373", internal=_REQUESTED_AFTER + timedelta(seconds=20))
            }
        },
    )
    assert _fetch(server) == "313373"


def test_fetch_picks_newest_across_folders():
    # INBOX와 프로모션 양쪽에 인증 메일이 있으면 폴더와 무관하게 가장 최신을 채택한다.
    server = _FakeImap(
        {1: _message(code_text="인증번호 111111", internal=_REQUESTED_AFTER + timedelta(seconds=10))},
        extra_folders={
            "프로모션": {
                9: _message(code_text="인증번호 222222", internal=_REQUESTED_AFTER + timedelta(seconds=50))
            }
        },
    )
    assert _fetch(server) == "222222"


class _FolderStub:
    def __init__(self, listed):
        self._listed = listed

    def list_folders(self):
        return self._listed


def test_candidate_folders_excludes_sent_drafts_trash_keeps_spam_and_promotions():
    listed = [
        ((b"\\HasNoChildren",), "/", "INBOX"),
        ((b"\\Sent",), "/", "[Gmail]/Sent Mail"),
        ((b"\\Drafts",), "/", "[Gmail]/Drafts"),
        ((b"\\Trash",), "/", "[Gmail]/Trash"),
        ((b"\\All",), "/", "[Gmail]/All Mail"),
        ((b"\\Junk",), "/", "[Gmail]/Spam"),
        ((), "/", "Sent Messages"),
        ((), "/", "Drafts"),
        ((), "/", "Deleted Messages"),
        ((), "/", "프로모션"),
        ((), "/", "Junk"),
    ]
    folders = imap_2fa._candidate_folders(_FolderStub(listed))

    assert folders[0] == "INBOX"
    # 스팸/프로모션/분류 폴더는 검색 대상에 남는다.
    assert "프로모션" in folders
    assert "Junk" in folders
    assert "[Gmail]/Spam" in folders
    # 보낸함/임시/휴지통/전체보관함은 제외한다(플래그·이름 양쪽으로).
    assert "[Gmail]/Sent Mail" not in folders
    assert "[Gmail]/Drafts" not in folders
    assert "[Gmail]/Trash" not in folders
    assert "[Gmail]/All Mail" not in folders
    assert "Sent Messages" not in folders
    assert "Drafts" not in folders
    assert "Deleted Messages" not in folders


def test_candidate_folders_falls_back_to_inbox_when_list_fails():
    class _Boom:
        def list_folders(self):
            raise RuntimeError("not supported")

    assert imap_2fa._candidate_folders(_Boom()) == ["INBOX"]


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


def test_imap_connect_disables_time_normalisation(monkeypatch):
    # INTERNALDATE를 aware로 받기 위해 normalise_times를 끈다. 기본값(True)이면 naive 로컬
    # 시각이 되어 KST(+9) 머신에서 requested_after(UTC) 컷오프가 9시간 어긋난다.
    import imapclient

    class _FakeClient:
        def __init__(self, host, port, ssl, use_uid):
            self.normalise_times = True

        def login(self, email_address, app_password):
            pass

    monkeypatch.setattr(imapclient, "IMAPClient", _FakeClient)

    server = imap_2fa._imap_connect("imap.naver.com", 993, "a@naver.com", "pw")

    assert server.normalise_times is False


def test_to_utc_converts_aware_internaldate_from_kst():
    # IMAPClient가 normalise_times=False로 돌려주는 +0900 aware INTERNALDATE가 UTC로
    # 올바르게 변환되는지 확인한다(12:00 KST == 03:00 UTC).
    from datetime import datetime, timezone

    from imapclient import datetime_util

    aware = datetime_util.parse_to_datetime(b"01-Jul-2026 12:00:00 +0900", normalise=False)
    converted = imap_2fa._to_utc(aware)

    assert converted == datetime(2026, 7, 1, 3, 0, 0, tzinfo=timezone.utc)


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
