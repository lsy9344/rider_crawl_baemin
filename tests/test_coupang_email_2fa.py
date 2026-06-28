from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from rider_crawl.auth import coupang_email_2fa
from rider_crawl.auth.coupang_email_2fa import (
    Coupang2faError,
    recover_coupang_session_with_email_2fa,
)
from rider_crawl.auth.imap_2fa import Imap2faError
from rider_crawl.config import AppConfig


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        peak_dashboard_url="https://partner.coupangeats.com/page/peak-dashboard",
        platform_name="coupang",
        baemin_center_name="제이앤에이치플러스 의정부남부",
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
        page_timeout_seconds=1000,
        verification_email_address="rider@naver.com",
        coupang_2fa_code_digits=6,
    )


class _FakePage:
    """Records clicks/fills and serves page text, mimicking the Playwright surface."""

    def __init__(
        self,
        html: str = "",
        *,
        clickable: tuple[str, ...] = (),
        role_clickable: tuple[tuple[str, str], ...] = (),
        role_click_updates: dict[tuple[str, str], str] | None = None,
        role_click_input_updates: dict[tuple[str, str], tuple[str, ...]] | None = None,
        role_min_timeout: dict[tuple[str, str], int] | None = None,
        input_selectors: tuple[str, ...] = (),
    ):
        self.html = html
        self._clickable = clickable
        self._role_clickable = role_clickable
        self._role_click_updates = role_click_updates or {}
        self._role_click_input_updates = role_click_input_updates or {}
        self._role_min_timeout = role_min_timeout or {}
        self._input_selectors = input_selectors
        self.clicked_texts: list[str] = []
        self.clicked_roles: list[tuple[str, str]] = []
        self.filled: list[tuple[str, str]] = []

    def content(self) -> str:
        return self.html

    def get_by_text(self, text: str, *, exact: bool = False):
        return _FakeTextLocator(self, text)

    def get_by_role(self, role: str, *, name: str, exact: bool = False):
        return _FakeRoleLocator(self, role, name)

    def locator(self, selector: str):
        return _FakeInputLocator(self, selector)


class _FakeTextLocator:
    def __init__(self, page: _FakePage, text: str):
        self._page = page
        self._text = text

    @property
    def first(self):
        return self

    def click(self, **_kwargs):
        if any(self._text.casefold() in candidate.casefold() for candidate in self._page._clickable):
            self._page.clicked_texts.append(self._text)
            return
        raise RuntimeError(f"no clickable element: {self._text}")


class _FakeRoleLocator:
    def __init__(self, page: _FakePage, role: str, name: str):
        self._page = page
        self._role = role
        self._name = name

    @property
    def first(self):
        return self

    def click(self, **_kwargs):
        if (self._role, self._name) in self._page._role_clickable:
            min_timeout = self._page._role_min_timeout.get((self._role, self._name), 0)
            if int(_kwargs.get("timeout") or 0) < min_timeout:
                raise RuntimeError(f"not actionable yet: {self._role} {self._name}")
            self._page.clicked_roles.append((self._role, self._name))
            updated_html = self._page._role_click_updates.get((self._role, self._name))
            if updated_html is not None:
                self._page.html = updated_html
            updated_inputs = self._page._role_click_input_updates.get((self._role, self._name))
            if updated_inputs is not None:
                self._page._input_selectors = updated_inputs
            return
        raise RuntimeError(f"no clickable role: {self._role} {self._name}")


class _FakeInputLocator:
    def __init__(self, page: _FakePage, selector: str):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def fill(self, value: str, **_kwargs):
        if self._selector in self._page._input_selectors:
            self._page.filled.append((self._selector, value))
            return
        raise RuntimeError(f"no input for selector: {self._selector}")

    def count(self) -> int:
        return 1 if self._selector in self._page._input_selectors else 0


_NOW = lambda: datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ok_fetch(code: str = "123456"):
    def _fetch(**_kwargs):
        return code

    return _fetch


def test_recover_clicks_email_method_send_and_fills_code(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증을 선택하세요 인증코드를 rider@naver.com 으로 보냅니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    captured = {}

    def _fetch(**kwargs):
        captured.update(kwargs)
        return "246802"

    config = replace(
        _config(tmp_path),
        verification_email_address="rider@naver.com",
        verification_email_app_password="app-pass",
    )

    result = recover_coupang_session_with_email_2fa(page, config, fetch_code=_fetch, now=_NOW)

    assert result is True
    assert "이메일" in page.clicked_texts
    assert "인증번호 발송" in page.clicked_texts
    assert "확인" in page.clicked_texts
    assert page.filled == [("input[name='code']", "246802")]
    assert captured["email_address"] == "rider@naver.com"
    assert captured["app_password"] == "app-pass"
    assert captured["subject_keyword"] == "인증번호"
    assert captured["sender_keyword"] == "coupang"
    assert captured["requested_after"] < _NOW()
    assert captured["requested_after"] >= _NOW() - timedelta(minutes=2)
    assert captured["code_digits"] == 6


def test_recover_returns_false_on_captcha(tmp_path):
    page = _FakePage(html="<html>보안문자(CAPTCHA)를 입력하세요</html>")

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
    )

    assert result is False
    assert page.clicked_texts == []


def test_recover_returns_false_on_password_login_screen(tmp_path):
    page = _FakePage(
        html="<html><input placeholder='아이디 입력'><input placeholder='비밀번호 입력'></html>",
        clickable=("이메일",),
        input_selectors=("input[placeholder*='비밀번호']",),
    )

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
    )

    assert result is False


def test_recover_ignores_hidden_password_field_on_email_2fa_screen(tmp_path):
    page = _FakePage(
        html=(
            "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
            " 인증코드를 rider@naver.com 으로 보냅니다"
            "<input name='password' type='hidden'>"
            "<input placeholder='인증코드'></html>"
        ),
        clickable=("이메일로 인증", "인증코드 전송", "로그인"),
        input_selectors=("input[placeholder*='코드']",),
    )

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch("654321"), now=_NOW
    )

    assert result is True
    assert "이메일로 인증" in page.clicked_texts
    assert "인증코드 전송" in page.clicked_texts
    assert page.filled == [("input[placeholder*='코드']", "654321")]


def test_recover_prefers_tab_and_button_roles_over_broad_text_matches(tmp_path):
    page = _FakePage(
        html=(
            "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
            " 인증코드를 rider@naver.com 으로 보냅니다<input placeholder='인증코드'></html>"
        ),
        clickable=("2단계 인증 로그인 이메일로 인증 인증코드 전송",),
        role_clickable=(("tab", "이메일로 인증"), ("button", "인증코드 전송"), ("button", "인증 완료")),
        input_selectors=("input[placeholder*='코드']",),
    )

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch("135790"), now=_NOW
    )

    assert result is True
    assert ("tab", "이메일로 인증") in page.clicked_roles
    assert ("button", "인증코드 전송") in page.clicked_roles
    assert ("button", "인증 완료") in page.clicked_roles
    assert page.clicked_texts == []
    assert page.filled == [("input[placeholder*='코드']", "135790")]


def test_recover_waits_long_enough_for_send_code_button_to_become_actionable(tmp_path):
    page = _FakePage(
        html=(
            "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
            " 인증코드를 rider@naver.com 으로 보냅니다<input placeholder='인증코드'></html>"
        ),
        role_clickable=(
            ("tab", "이메일로 인증"),
            ("button", "인증코드 전송"),
            ("button", "인증 완료"),
        ),
        role_min_timeout={("button", "인증코드 전송"): 1500},
        input_selectors=("input[placeholder*='코드']",),
    )
    config = replace(_config(tmp_path), page_timeout_seconds=5000)

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("135790"), now=_NOW
    )

    assert result is True
    assert ("button", "인증코드 전송") in page.clicked_roles
    assert page.filled == [("input[placeholder*='코드']", "135790")]


def test_recover_does_not_treat_2fa_screen_with_visible_username_as_primary_login(tmp_path):
    page = _FakePage(
        html=(
            "<html>2단계 인증 로그인 아이디 이메일로 인증 인증코드 전송"
            " 인증코드를 rider@naver.com 으로 보냅니다<input placeholder='인증코드'></html>"
        ),
        clickable=("이메일로 인증", "인증코드 전송", "인증 완료"),
        input_selectors=("input[placeholder*='아이디']", "input[placeholder*='코드']"),
    )

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch("135790"), now=_NOW
    )

    assert result is True
    assert "인증코드 전송" in page.clicked_texts
    assert page.filled == [("input[placeholder*='코드']", "135790")]


def test_recover_logs_in_with_ui_credentials_before_email_2fa(tmp_path):
    config = replace(
        _config(tmp_path),
        coupang_login_id="worker-id",
        coupang_login_password="worker-password",
    )
    page = _FakePage(
        html="<html>Vendor Portal 아이디 입력 비밀번호 입력 로그인</html>",
        role_clickable=(
            ("button", "로그인"),
            ("tab", "이메일로 인증"),
            ("button", "인증코드 전송"),
            ("button", "인증 완료"),
        ),
        role_click_updates={
                ("button", "로그인"): (
                    "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
                    " 인증코드를 rider@naver.com 으로 보냅니다"
                    "<input placeholder='인증코드'></html>"
                ),
        },
        role_click_input_updates={
            ("button", "로그인"): ("input[placeholder*='코드']",),
        },
        input_selectors=(
            "input[name='username']",
            "input[name='password']",
            "input[placeholder*='코드']",
        ),
    )

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("112233"), now=_NOW
    )

    assert result is True
    assert ("button", "로그인") in page.clicked_roles
    assert ("tab", "이메일로 인증") in page.clicked_roles
    assert ("button", "인증코드 전송") in page.clicked_roles
    assert ("button", "인증 완료") in page.clicked_roles
    assert page.filled == [
        ("input[name='username']", "worker-id"),
        ("input[name='password']", "worker-password"),
        ("input[placeholder*='코드']", "112233"),
    ]


def test_recover_handles_split_username_then_password_login(tmp_path):
    config = replace(
        _config(tmp_path),
        coupang_login_id="worker-id",
        coupang_login_password="worker-password",
    )
    page = _FakePage(
        html="<html>Vendor Portal 아이디 입력 다음</html>",
        role_clickable=(
            ("button", "다음"),
            ("button", "로그인"),
            ("tab", "이메일로 인증"),
            ("button", "인증코드 전송"),
            ("button", "인증 완료"),
        ),
        role_click_updates={
            ("button", "다음"): "<html>Vendor Portal 비밀번호 입력 로그인</html>",
            ("button", "로그인"): (
                "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
                " 인증코드를 rider@naver.com 으로 보냅니다"
                "<input placeholder='인증코드'></html>"
            ),
        },
        role_click_input_updates={
            ("button", "다음"): ("input[placeholder*='비밀번호']",),
            ("button", "로그인"): ("input[placeholder*='코드']",),
        },
        input_selectors=("input[placeholder*='아이디']",),
    )

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("112233"), now=_NOW
    )

    assert result is True
    assert ("button", "다음") in page.clicked_roles
    assert ("button", "로그인") in page.clicked_roles
    assert ("tab", "이메일로 인증") in page.clicked_roles
    assert ("button", "인증코드 전송") in page.clicked_roles
    assert ("button", "인증 완료") in page.clicked_roles
    assert page.filled == [
        ("input[placeholder*='아이디']", "worker-id"),
        ("input[placeholder*='비밀번호']", "worker-password"),
        ("input[placeholder*='코드']", "112233"),
    ]


def test_recover_detects_primary_login_by_password_input_when_body_label_is_short(tmp_path):
    config = replace(
        _config(tmp_path),
        coupang_login_id="worker-id",
        coupang_login_password="worker-password",
    )
    page = _FakePage(
        html="<html>Vendor Portal 아이디 비밀번호 로그인</html>",
        role_clickable=(
            ("button", "로그인"),
            ("tab", "이메일로 인증"),
            ("button", "인증코드 전송"),
            ("button", "인증 완료"),
        ),
        role_click_updates={
                ("button", "로그인"): (
                    "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
                    " 인증코드를 rider@naver.com 으로 보냅니다"
                    "<input placeholder='인증코드'></html>"
                ),
        },
        role_click_input_updates={
            ("button", "로그인"): ("input[placeholder*='코드']",),
        },
        input_selectors=(
            "input[placeholder*='아이디']",
            "input[placeholder*='비밀번호']",
            "input[placeholder*='코드']",
        ),
    )

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("445566"), now=_NOW
    )

    assert result is True
    assert ("button", "로그인") in page.clicked_roles
    assert page.filled == [
        ("input[placeholder*='아이디']", "worker-id"),
        ("input[placeholder*='비밀번호']", "worker-password"),
        ("input[placeholder*='코드']", "445566"),
    ]


def test_recover_logs_in_with_common_coupang_email_login_selector(tmp_path):
    config = replace(
        _config(tmp_path),
        coupang_login_id="worker-id",
        coupang_login_password="worker-password",
    )
    page = _FakePage(
        html=(
            "<html>Vendor Portal login-actions/authenticate realms/eats-partner"
            " username password 로그인</html>"
        ),
        role_clickable=(
            ("button", "로그인"),
            ("tab", "이메일로 인증"),
            ("button", "인증코드 전송"),
            ("button", "인증 완료"),
        ),
        role_click_updates={
            ("button", "로그인"): (
                "<html>2단계 인증 로그인 이메일로 인증 인증코드 전송"
                " 인증코드를 rider@naver.com 으로 보냅니다"
                "<input placeholder='인증코드'></html>"
            ),
        },
        role_click_input_updates={
            ("button", "로그인"): ("input[placeholder*='코드']",),
        },
        input_selectors=(
            "input[name='email']",
            "input[id*='password']",
            "input[placeholder*='코드']",
        ),
    )

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("667788"), now=_NOW
    )

    assert result is True
    assert page.filled == [
        ("input[name='email']", "worker-id"),
        ("input[id*='password']", "worker-password"),
        ("input[placeholder*='코드']", "667788"),
    ]


def test_recover_uses_resend_button_when_code_already_sent(tmp_path):
    page = _FakePage(
        html=(
            "<html>2단계 인증 로그인 이메일로 인증 인증 재요청"
            " 인증코드를 rider@naver.com 으로 보냈습니다<input placeholder='인증코드'></html>"
        ),
        clickable=("이메일로 인증", "인증 재요청", "인증 완료"),
        input_selectors=("input[placeholder*='코드']",),
    )

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch("778899"), now=_NOW
    )

    assert result is True
    assert "인증 재요청" in page.clicked_texts
    assert page.filled == [("input[placeholder*='코드']", "778899")]


def test_recover_returns_false_when_send_button_missing(tmp_path):
    page = _FakePage(html="<html>알 수 없는 화면</html>", clickable=())

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
    )

    assert result is False


def test_coupang_email_2fa_requests_code_when_recipient_is_not_visible_yet(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드 받기<input placeholder='인증코드'></html>",
        clickable=("이메일", "인증코드 받기", "확인"),
        input_selectors=("input[name='code']",),
    )
    called = {"hit": False}

    def _fetch(**_kwargs):
        called["hit"] = True
        return "246802"

    result = recover_coupang_session_with_email_2fa(
        page,
        replace(_config(tmp_path), verification_email_address="rider@naver.com"),
        fetch_code=_fetch,
        now=_NOW,
    )

    assert result is True
    assert called["hit"] is True
    assert "인증코드 받기" in page.clicked_texts
    assert page.filled == [("input[name='code']", "246802")]


def test_coupang_email_2fa_rejects_domain_only_match(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 other@naver.com 으로 보냅니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    called = {"hit": False}

    def _fetch(**_kwargs):
        called["hit"] = True
        return "246802"

    result = recover_coupang_session_with_email_2fa(
        page,
        replace(_config(tmp_path), verification_email_address="rider@naver.com"),
        fetch_code=_fetch,
        now=_NOW,
    )

    assert result is False
    assert called["hit"] is False
    assert page.filled == []


def test_recover_raises_when_imap_fetch_fails(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 rider@naver.com 으로 보냅니다</html>",
        clickable=("이메일", "인증번호 발송"),
        input_selectors=("input[name='code']",),
    )

    def _fetch(**_kwargs):
        raise Imap2faError("인증 메일 미도착")

    with pytest.raises(Coupang2faError):
        recover_coupang_session_with_email_2fa(
            page, _config(tmp_path), fetch_code=_fetch, now=_NOW
        )


def test_recover_proceeds_when_screen_recipient_matches_tab_address(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 rider@naver.com 으로 보냈습니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    config = replace(_config(tmp_path), verification_email_address="rider@naver.com")
    called = {"hit": False}

    def _fetch(**_kwargs):
        called["hit"] = True
        return "246802"

    result = recover_coupang_session_with_email_2fa(page, config, fetch_code=_fetch, now=_NOW)

    assert result is True
    assert called["hit"] is True
    assert page.filled == [("input[name='code']", "246802")]


def test_recover_stops_when_screen_domain_differs_from_tab_address(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 abc@naver.com 으로 보냈습니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    config = replace(_config(tmp_path), verification_email_address="rider@gmail.com")
    called = {"hit": False}

    def _fetch(**_kwargs):
        called["hit"] = True
        return "246802"

    result = recover_coupang_session_with_email_2fa(page, config, fetch_code=_fetch, now=_NOW)

    assert result is False
    assert called["hit"] is False
    assert page.filled == []


def test_recover_proceeds_when_screen_local_part_is_comparably_masked(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 ri***@naver.com 으로 보냈습니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    config = replace(_config(tmp_path), verification_email_address="rider@naver.com")

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("111222"), now=_NOW
    )

    assert result is True
    assert page.filled == [("input[name='code']", "111222")]


def test_recover_skips_cross_check_when_screen_domain_is_masked(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 ri***@na***.com 으로 보냈습니다</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )
    config = replace(_config(tmp_path), verification_email_address="rider@naver.com")

    result = recover_coupang_session_with_email_2fa(
        page, config, fetch_code=_ok_fetch("111222"), now=_NOW
    )

    assert result is True
    assert page.filled == [("input[name='code']", "111222")]


def test_recover_raises_when_code_input_missing(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 rider@naver.com 으로 보냅니다</html>",
        clickable=("이메일", "인증번호 발송"),
        input_selectors=(),
    )

    with pytest.raises(Coupang2faError, match="입력칸"):
        recover_coupang_session_with_email_2fa(
            page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
        )


def test_recover_does_not_leak_code_in_errors(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증 인증코드를 rider@naver.com 으로 보냅니다</html>",
        clickable=("이메일", "인증번호 발송"),
        input_selectors=(),
    )

    try:
        recover_coupang_session_with_email_2fa(
            page, _config(tmp_path), fetch_code=_ok_fetch("999888"), now=_NOW
        )
    except Coupang2faError as exc:
        assert "999888" not in str(exc)


class _FakeMatch:
    def __init__(self, visible: bool, log: list[str], label: str):
        self._visible = visible
        self._log = log
        self._label = label

    def click(self, **_kwargs):
        if not self._visible:
            raise RuntimeError("element is not visible")
        self._log.append(self._label)


class _FakeFilterLocator:
    def __init__(self, matches: list[_FakeMatch], *, visible_only: bool = False):
        self._matches = matches
        self._visible_only = visible_only

    def filter(self, *, visible: bool):
        if not visible:
            return self
        return _FakeFilterLocator([m for m in self._matches if m._visible], visible_only=True)

    @property
    def first(self):
        return self._matches[0]


def test_click_first_visible_skips_hidden_duplicate_button():
    log: list[str] = []
    locator = _FakeFilterLocator(
        [
            _FakeMatch(visible=False, log=log, label="hidden-phone"),
            _FakeMatch(visible=True, log=log, label="visible-email"),
        ]
    )

    assert coupang_email_2fa._click_first_visible(locator, timeout=1000) is True
    assert log == ["visible-email"]


def test_click_first_visible_falls_back_when_filter_unsupported():
    page = _FakePage(html="", clickable=("인증코드 전송",))
    locator = page.get_by_text("인증코드 전송", exact=False)

    assert coupang_email_2fa._click_first_visible(locator, timeout=1000) is True
    assert page.clicked_texts == ["인증코드 전송"]


def test_click_first_visible_returns_false_when_no_visible_match():
    log: list[str] = []
    locator = _FakeFilterLocator([_FakeMatch(visible=False, log=log, label="hidden")])

    assert coupang_email_2fa._click_first_visible(locator, timeout=1000) is False
    assert log == []
