"""쿠팡이츠 이메일 2차 인증 자동 복구."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from rider_crawl.auth.imap_2fa import (
    IMAP_HOST_BY_DOMAIN,
    Imap2faError,
    ImapAuthError,
    domain_of,
)
from rider_crawl.config import AppConfig

_REQUESTED_AFTER_SAFETY_SECONDS = 30
# 화면 조작(버튼 클릭/입력칸 탐색) 시도당 대기 상한. selector 후보를 순차로
# 시도하므로 너무 길게 잡지는 않되, 쿠팡 antd 화면에서 이메일 방식 선택 직후
# "인증코드 전송" 버튼이 늦게 actionable 되는 경우가 있어 2초까지 기다린다.
_INTERACTION_TIMEOUT_MS = 2_000

_EMAIL_METHOD_TEXTS = ("이메일로 인증", "이메일 인증", "이메일", "email")
_SEND_CODE_TEXTS = (
    "인증코드 전송",
    "인증번호 발송",
    "인증번호 받기",
    "인증코드 받기",
    "인증 코드 받기",
    "인증번호 전송",
    "인증코드 발송",
    "코드 받기",
    "인증 재요청",
    "인증코드 재전송",
    "인증번호 재전송",
    "인증 재전송",
    "send code",
    "send",
    "resend",
)
_CODE_INPUT_SELECTORS = (
    "input[name='code']",
    "input[name='verificationCode']",
    "input[placeholder*='인증번호']",
    "input[placeholder*='코드']",
    "input[type='tel']",
    "input[type='number']",
)
_SUBMIT_TEXTS = ("인증 완료", "확인", "인증", "제출", "로그인", "submit", "verify")
_CAPTCHA_SIGNALS = ("captcha", "보안문자", "자동입력 방지", "로봇이 아닙니다", "recaptcha")
_PASSWORD_SIGNALS = ("비밀번호 입력",)
_EMAIL_2FA_SCREEN_SIGNALS = (
    "2단계 인증",
    "이메일로 인증",
    "휴대전화로 인증",
    "인증코드 전송",
    "인증번호 발송",
)
_USERNAME_LOGIN_SIGNALS = (
    "아이디",
    "username",
    "login",
    "vendor portal",
    "login-actions/authenticate",
)
_USERNAME_INPUT_SELECTORS = (
    "input[placeholder*='아이디']",
    "input[name='username']",
    "input[name='email']",
    "input[name='loginId']",
    "input[name='login_id']",
    "input[name='id']",
    "input[id*='username']",
    "input[id*='email']",
    "input[id*='login']",
    "input[autocomplete='username']",
    "input[type='email']",
)
_PASSWORD_INPUT_SELECTORS = (
    "input[placeholder*='비밀번호']",
    "input[type='password']",
    "input[name='password']",
    "input[id*='password']",
    "input[autocomplete='current-password']",
)
_LOGIN_BUTTON_TEXTS = ("로그인", "login")
_USERNAME_STEP_BUTTON_TEXTS = ("다음", "계속", "로그인", "login", "next", "continue")
_EMAIL_RE = re.compile(r"(?P<email>[A-Za-z0-9._%*+\-]+@[A-Za-z0-9.*\-]+\.[A-Za-z]{2,})")
_SUPPORTED_SCREEN_DOMAINS = set(IMAP_HOST_BY_DOMAIN)


def recover_coupang_session_with_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    fetch_code: Callable[..., str] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    """Attempt to recover an expired Coupang session via email 2FA."""

    page_text = _safe_page_text(page)
    if _contains_any(page_text, _CAPTCHA_SIGNALS):
        return False

    if _is_primary_login_screen(page_text, page):
        if not _submit_primary_login(page, config):
            return False
        _wait_after_action(page, config)

    _click_first_by_text(page, _EMAIL_METHOD_TEXTS, config, roles=("tab", "button"))
    refreshed_text = _safe_page_text(page)
    if _is_primary_login_screen(refreshed_text, page):
        return False

    requested_after = now() - timedelta(seconds=_REQUESTED_AFTER_SAFETY_SECONDS)
    if not _click_first_by_text(page, _SEND_CODE_TEXTS, config, roles=("button",)):
        return False

    if not _account_matches_screen(page, config.verification_email_address):
        return False

    code = _fetch_code(config, requested_after=requested_after, fetch_code=fetch_code)
    _fill_code_input(page, code, config)
    _click_first_by_text(page, _SUBMIT_TEXTS, config, roles=("button",))
    return True


class Coupang2faError(RuntimeError):
    """이메일 2FA 복구 중 운영자 조치가 필요한 실패. 메시지에 코드/앱 비밀번호를 넣지 않는다.

    ``email_auth_required`` 는 IMAP 로그인/이메일 설정 실패(``ImapAuthError``)에서 비롯됐는지
    표시한다 — 상위 인증 job 분류기가 이걸 보고 ``EMAIL_AUTH_REQUIRED`` 로 표면화해, 메일 지연
    같은 일시적 실패와 운영자 조치형 실패를 구분한다(검토 Medium).
    """

    def __init__(self, *args: object, email_auth_required: bool = False) -> None:
        super().__init__(*args)
        self.email_auth_required = email_auth_required


def _fetch_code(
    config: AppConfig,
    *,
    requested_after: datetime,
    fetch_code: Callable[..., str] | None,
) -> str:
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
        raise Coupang2faError(
            str(exc), email_auth_required=isinstance(exc, ImapAuthError)
        ) from exc

    if not code:
        raise Coupang2faError("이메일에서 인증번호를 받지 못했습니다.")
    return code


def _imap_fetch(**kwargs: Any) -> str:
    from rider_crawl.auth.imap_2fa import fetch_latest_verification_code

    return fetch_latest_verification_code(**kwargs)


def _onscreen_recipients(page: Any) -> set[str]:
    text = _safe_page_text(page)
    return {
        email
        for match in _EMAIL_RE.finditer(text)
        if (email := match.group("email").casefold()) and domain_of(email).replace("*", "") in _SUPPORTED_SCREEN_DOMAINS
    }


def _account_matches_screen(page: Any, account_address: str) -> bool:
    account = str(account_address or "").strip().casefold()
    if "@" not in account:
        return False
    recipients = _onscreen_recipients(page)
    if not recipients:
        return True
    return any(_recipient_matches_account(recipient, account) for recipient in recipients)


def _recipient_matches_account(recipient: str, account: str) -> bool:
    if recipient == account:
        return True
    recipient_local, _, recipient_domain = recipient.partition("@")
    account_local, _, account_domain = account.partition("@")
    if "*" in recipient_domain:
        return False
    if recipient_domain != account_domain:
        return False
    if "*" not in recipient_local:
        return False
    return _masked_local_part_matches(recipient_local, account_local)


def _masked_local_part_matches(mask: str, value: str) -> bool:
    pieces = [piece for piece in re.split(r"\*+", mask) if piece]
    if not pieces:
        return False
    if not value.startswith(pieces[0]):
        return False
    if len(pieces) == 1:
        return True
    if not value.endswith(pieces[-1]):
        return False
    position = 0
    for piece in pieces:
        found = value.find(piece, position)
        if found == -1:
            return False
        position = found + len(piece)
    return True


def _interaction_timeout(config: AppConfig) -> int:
    return min(config.page_timeout_seconds, _INTERACTION_TIMEOUT_MS)


def _safe_page_text(page: Any) -> str:
    try:
        return str(page.content() or "").casefold()
    except Exception:
        return ""


def _contains_any(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal.casefold() in text for signal in signals)


def _is_password_login_screen(text: str, page: Any | None = None) -> bool:
    if page is None:
        return _contains_any(text, _PASSWORD_SIGNALS)
    return _has_visible_input(page, _PASSWORD_INPUT_SELECTORS)


def _is_primary_login_screen(text: str, page: Any | None = None) -> bool:
    if _contains_any(text, _EMAIL_2FA_SCREEN_SIGNALS):
        return False
    if _is_password_login_screen(text, page):
        return True
    if page is None:
        return _contains_any(text, _USERNAME_LOGIN_SIGNALS)
    return _has_visible_input(page, _USERNAME_INPUT_SELECTORS) and _contains_any(
        text, _USERNAME_LOGIN_SIGNALS
    )


def _submit_primary_login(page: Any, config: AppConfig) -> bool:
    credentials = _load_coupang_credentials(config)
    if credentials is None:
        return False

    username, password = credentials
    if not _fill_first_input(page, _USERNAME_INPUT_SELECTORS, username, config):
        return False
    if not _fill_first_input(page, _PASSWORD_INPUT_SELECTORS, password, config):
        if not _click_first_by_text(page, _USERNAME_STEP_BUTTON_TEXTS, config, roles=("button",)):
            _press_enter_first(page, _USERNAME_INPUT_SELECTORS, config)
        if not _wait_for_visible_input(page, _PASSWORD_INPUT_SELECTORS, config):
            return False
        if not _fill_first_input(page, _PASSWORD_INPUT_SELECTORS, password, config):
            return False
    _press_enter_first(page, _PASSWORD_INPUT_SELECTORS, config)
    _click_first_by_text(page, _LOGIN_BUTTON_TEXTS, config, roles=("button",))
    return True


def _load_coupang_credentials(config: AppConfig) -> tuple[str, str] | None:
    ui_username = str(getattr(config, "coupang_login_id", "") or "").strip()
    ui_password = str(getattr(config, "coupang_login_password", "") or "")
    if ui_username and ui_password:
        return ui_username, ui_password
    return None


def _fill_first_input(
    page: Any,
    selectors: tuple[str, ...],
    value: str,
    config: AppConfig,
) -> bool:
    timeout = _interaction_timeout(config)
    for selector in selectors:
        try:
            _enter_text(page.locator(selector).first, value, timeout)
            return True
        except Exception:
            continue
    return False


def _enter_text(locator: Any, value: str, timeout: int) -> None:
    try:
        locator.click(timeout=timeout)
        try:
            locator.press("Control+a", timeout=timeout)
            locator.press("Delete", timeout=timeout)
        except Exception:
            pass
        locator.press_sequentially(value, timeout=timeout, delay=30)
        return
    except (AttributeError, TypeError):
        pass
    locator.fill(value, timeout=timeout)


def _press_enter_first(page: Any, selectors: tuple[str, ...], config: AppConfig) -> bool:
    timeout = _interaction_timeout(config)
    for selector in selectors:
        try:
            page.locator(selector).first.press("Enter", timeout=timeout)
            return True
        except (AttributeError, TypeError):
            return False
        except Exception:
            continue
    return False


def _has_visible_input(page: Any, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            visible_count = page.locator(selector).evaluate_all(
                """els => els.filter((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.visibility !== 'hidden'
                        && style.display !== 'none';
                }).length"""
            )
            if visible_count > 0:
                return True
        except AttributeError:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        except Exception:
            continue
    return False


def _wait_for_visible_input(page: Any, selectors: tuple[str, ...], config: AppConfig) -> bool:
    if _has_visible_input(page, selectors):
        return True
    try:
        page.locator(", ".join(selectors)).first.wait_for(
            state="visible",
            timeout=min(config.page_timeout_seconds, _EMAIL_METHOD_READY_TIMEOUT_MS),
        )
    except Exception:
        pass
    return _has_visible_input(page, selectors)


# 로그인 제출 → 2FA 화면 전환 대기 상한. networkidle 은 쿠팡 페이지에서 안 떠 항상
# timeout 까지 헛대기하므로(라이브 측정 3.0s 통째), '이메일로 인증' 요소가 보일 때까지
# 대기하는 방식으로 바꾼다. 요소 기반이라 전환이 빠르면 즉시 진행하고(라이브 0.01s로
# 등장→최대 3s 단축), 느려도 이 상한까지 기다려 아직 안 뜬 버튼을 성급히 클릭해 실패하던
# 위험을 없앤다. 상한은 구 networkidle(3s)과 비슷한 4s — 빠른 케이스 이득은 살리되 느린
# 케이스에서 구버전보다 크게 느려지지 않게 한다(8s 는 과해 평시 손해라 낮춤).
_EMAIL_METHOD_READY_TIMEOUT_MS = 4_000


def _wait_after_action(page: Any, config: AppConfig) -> None:
    """로그인 제출 후 '이메일로 인증' 요소가 보일 때까지 대기(없으면 상한까지).

    고정 networkidle(항상 상한 헛대기) 대신 다음 단계 요소를 직접 기다린다 — 빠르면
    즉시 진행하고, 전환이 느려도 요소가 뜰 때까지 기다려 성급한 클릭 실패를 막는다.
    어떤 신호도 못 잡으면 예외를 삼켜 호출자가 그대로 진행한다(무회귀)."""

    timeout_ms = min(config.page_timeout_seconds, _EMAIL_METHOD_READY_TIMEOUT_MS)
    if _wait_for_any_text_visible(page, _EMAIL_METHOD_TEXTS, timeout_ms):
        return
    # 폴백: 요소 신호를 못 잡았으면 과거처럼 짧게 한 번 더 settle 을 시도한다.
    try:
        page.wait_for_load_state("networkidle", timeout=min(config.page_timeout_seconds, 1_000))
    except Exception:
        pass


def _wait_for_any_text_visible(page: Any, texts: tuple[str, ...], timeout_ms: int) -> bool:
    """texts 중 하나라도 (tab/button role 또는 본문 text 로) visible 해질 때까지 대기. 보이면 True.

    주의: Playwright 의 wait_for_selector 는 서로 다른 엔진(text=/role=)을 쉼표로 섞은
    selector 를 OR 로 처리하지 못한다(라이브 검증: 혼합 쉼표는 요소가 있어도 timeout).
    그래서 get_by_role(...).or_(...) 체인으로 합쳐 단일 locator 를 한 번만 기다린다."""
    try:
        candidate = None
        for t in texts:
            for loc in (
                page.get_by_role("tab", name=t),
                page.get_by_role("button", name=t),
                page.get_by_text(t, exact=False),
            ):
                candidate = loc if candidate is None else candidate.or_(loc)
        if candidate is None:
            return False
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _click_first_by_text(
    page: Any,
    texts: tuple[str, ...],
    config: AppConfig,
    *,
    roles: tuple[str, ...] = ("tab", "button"),
) -> bool:
    timeout = _interaction_timeout(config)
    for text in texts:
        for role in roles:
            try:
                locator = page.get_by_role(role, name=text, exact=False)
            except TypeError:
                try:
                    locator = page.get_by_role(role, name=text)
                except Exception:
                    continue
            except Exception:
                continue
            if _click_first_visible(locator, timeout):
                return True
        try:
            locator = page.get_by_text(text, exact=False)
        except TypeError:
            locator = page.get_by_text(text)
        except Exception:
            continue
        if _click_first_visible(locator, timeout):
            return True
    return False


def _click_first_visible(locator: Any, timeout: int) -> bool:
    try:
        visible = locator.filter(visible=True)
    except (AttributeError, TypeError):
        try:
            locator.first.click(timeout=timeout)
            return True
        except Exception:
            return False

    try:
        visible.first.click(timeout=timeout)
        return True
    except Exception:
        return False


def _fill_code_input(page: Any, code: str, config: AppConfig) -> None:
    timeout = _interaction_timeout(config)
    for selector in _CODE_INPUT_SELECTORS:
        try:
            _enter_text(page.locator(selector).first, code, timeout)
            return
        except Exception:
            continue
    raise Coupang2faError(
        "쿠팡 인증번호 입력칸을 찾지 못했습니다. 인증 화면 selector 보정이 필요합니다."
    )
