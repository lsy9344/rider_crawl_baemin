import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rider_crawl.auth import coupang_email_2fa
from rider_crawl.auth.coupang_email_2fa import (
    Coupang2faError,
    recover_coupang_session_with_email_2fa,
)
from rider_crawl.auth.gmail import Gmail2faError
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
        input_selectors: tuple[str, ...] = (),
    ):
        self.html = html
        # 클릭 가능한 텍스트(부분 일치)와 채울 수 있는 input selector를 화이트리스트로 둔다.
        self._clickable = clickable
        self._role_clickable = role_clickable
        self._role_click_updates = role_click_updates or {}
        self._role_click_input_updates = role_click_input_updates or {}
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
        html="<html>이메일 인증을 선택하세요</html>",
        clickable=("이메일", "인증번호 발송", "확인"),
        input_selectors=("input[name='code']",),
    )

    captured = {}

    def _fetch(**kwargs):
        captured.update(kwargs)
        return "246802"

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_fetch, now=_NOW
    )

    assert result is True
    assert "이메일" in page.clicked_texts
    assert "인증번호 발송" in page.clicked_texts
    assert "확인" in page.clicked_texts
    assert page.filled == [("input[name='code']", "246802")]
    # 발송 클릭 직전 시각에서 안전 여유를 뺀 시각을 requested_after로 넘긴다. 클릭과
    # 동시에 도착한 메일이 컷오프에서 잘리지 않도록 now()보다 약간 과거여야 한다.
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
        html="<html>2단계 인증 로그인 이메일로 인증 인증코드 전송<input placeholder='인증코드'></html>",
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


def test_recover_logs_in_with_saved_credentials_before_email_2fa(tmp_path):
    credentials_path = tmp_path / "coupang.credentials.json"
    credentials_path.write_text(
        json.dumps({"username": "worker-id", "password": "worker-password"}),
        encoding="utf-8",
    )
    config = replace(_config(tmp_path), coupang_credentials_path=credentials_path)
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


def test_recover_detects_primary_login_by_password_input_when_body_label_is_short(tmp_path):
    credentials_path = tmp_path / "coupang.credentials.json"
    credentials_path.write_text(
        json.dumps({"username": "worker-id", "password": "worker-password"}),
        encoding="utf-8",
    )
    config = replace(_config(tmp_path), coupang_credentials_path=credentials_path)
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


def test_recover_returns_false_when_send_button_missing(tmp_path):
    # 이메일 인증 화면이 아니어서 발송 버튼이 없으면 자동 복구를 포기한다.
    page = _FakePage(html="<html>알 수 없는 화면</html>", clickable=())

    result = recover_coupang_session_with_email_2fa(
        page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
    )

    assert result is False


def test_recover_raises_when_gmail_fetch_fails(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증</html>",
        clickable=("이메일", "인증번호 발송"),
        input_selectors=("input[name='code']",),
    )

    def _fetch(**_kwargs):
        raise Gmail2faError("인증 메일 미도착")

    with pytest.raises(Coupang2faError):
        recover_coupang_session_with_email_2fa(
            page, _config(tmp_path), fetch_code=_fetch, now=_NOW
        )


def test_recover_raises_when_code_input_missing(tmp_path):
    # 발송까지는 됐지만 코드 입력칸 selector를 못 찾으면 복구 불가 오류를 올린다.
    page = _FakePage(
        html="<html>이메일 인증</html>",
        clickable=("이메일", "인증번호 발송"),
        input_selectors=(),
    )

    with pytest.raises(Coupang2faError, match="입력칸"):
        recover_coupang_session_with_email_2fa(
            page, _config(tmp_path), fetch_code=_ok_fetch(), now=_NOW
        )


def test_recover_does_not_leak_code_in_errors(tmp_path):
    page = _FakePage(
        html="<html>이메일 인증</html>",
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
            # Playwright는 숨은 요소 클릭 시 actionability 대기 끝에 타임아웃을 던진다.
            raise RuntimeError("element is not visible")
        self._log.append(self._label)


class _FakeFilterLocator:
    """``filter(visible=True)``를 지원하는 locator. 쿠팡 2FA 화면처럼 같은 이름의
    버튼이 [숨김(앞), 보임(뒤)]로 둘 있는 상황을 모사한다."""

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
    # 숨은 휴대폰 버튼이 DOM 앞에 있고, 보이는 이메일 버튼이 뒤에 있을 때
    # 보이는 쪽을 눌러야 한다.
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
    # .filter 미지원(fake/구버전) locator는 기존 .first.click() 폴백.
    page = _FakePage(html="", clickable=("인증코드 전송",))
    locator = page.get_by_text("인증코드 전송", exact=False)

    assert coupang_email_2fa._click_first_visible(locator, timeout=1000) is True
    assert page.clicked_texts == ["인증코드 전송"]


def test_click_first_visible_returns_false_when_no_visible_match():
    # 보이는 매칭이 없으면 숨은 요소로 폴백하지 않고 False.
    log: list[str] = []
    locator = _FakeFilterLocator([_FakeMatch(visible=False, log=log, label="hidden")])

    assert coupang_email_2fa._click_first_visible(locator, timeout=1000) is False
    assert log == []
