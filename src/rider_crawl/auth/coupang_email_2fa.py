"""쿠팡이츠 이메일 2차 인증 자동 복구.

로그인 만료 화면에서 이메일 인증 방식을 고르고, 인증번호 발송 버튼을 누른 뒤
인증 이메일(IMAP, Gmail/Naver 공용)에서 받은 코드를 입력칸에 넣어 제출한다. 자동 복구가
가능한 화면이면 ``True``, CAPTCHA·아이디/비밀번호 입력 등 1차 구현 범위 밖이면 ``False``를
반환한다.

selector는 운영 PC에서 실제 쿠팡 인증 화면 기준으로 보정한다. 그래서 화면 조작을
얇은 헬퍼로 분리하고, 후보 selector를 여러 개 두어 화면 변화에 견디게 했다.

보안: 인증번호와 앱 비밀번호 값은 예외 메시지/로그에 넣지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from rider_crawl.auth.imap_2fa import IMAP_HOST_BY_DOMAIN, Imap2faError, domain_of
from rider_crawl.config import AppConfig

# 발송 클릭 직전 시각에서 빼 둘 안전 여유(초). 로컬/Gmail 서버 시계 오차와, 클릭과
# 동시에 도착하는 메일을 ``requested_after`` 컷오프에서 잃지 않도록 둔다.
_REQUESTED_AFTER_SAFETY_SECONDS = 30

# 인증 화면 조작(클릭·입력) 1회당 타임아웃(ms). 맞는 요소는 보통 즉시 존재하므로,
# 후보 selector/텍스트마다 페이지 전체 타임아웃(수십 초)을 기다리면 그 사이 인증번호가
# 만료된다. 짧게 잡아 틀린 후보는 빨리 넘기고, 코드 입력칸을 지체 없이 채운다.
_INTERACTION_TIMEOUT_MS = 2_000

# 이메일 인증 방식을 고르는 버튼/링크 후보 텍스트. 화면 문구가 바뀔 수 있어 여러 개를 둔다.
_EMAIL_METHOD_TEXTS = ("이메일로 인증", "이메일 인증", "이메일", "email")

# 인증번호 발송(요청) 버튼 후보 텍스트. 실측 화면의 실제 버튼("인증코드 전송")을 맨 앞에
# 둬, 틀린 후보를 하나씩 타임아웃만큼 기다리는 낭비를 줄인다.
#
# 이미 1차 발송이 끝나 코드 입력칸과 "인증 재요청"만 보이는 상태로 파킹된 화면도 흔하다
# (예: 세션이 2단계 인증 화면에 머물러 있던 경우). 이때는 "전송" 버튼이 없고 "인증 재요청"
# (실측: <span class="right-btn">)으로 새 코드를 받는다. 그래서 재요청/재전송 라벨도 후보에
# 넣되, 첫 발송 라벨("인증코드 전송")보다 뒤에 둬 신규 발송이 있으면 그쪽을 먼저 누른다.
# (요청 직전 requested_after를 새로 찍으므로 재요청으로 받은 새 코드를 정상 채택한다.)
_SEND_CODE_TEXTS = (
    "인증코드 전송",
    "인증번호 발송",
    "인증번호 받기",
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

# 인증번호 입력칸 후보 selector(placeholder/이름/일반 텍스트 입력).
_CODE_INPUT_SELECTORS = (
    "input[name='code']",
    "input[name='verificationCode']",
    "input[placeholder*='인증번호']",
    "input[placeholder*='코드']",
    "input[type='tel']",
    "input[type='number']",
)

# 코드 입력 후 제출(확인) 버튼 후보 텍스트.
_SUBMIT_TEXTS = ("인증 완료", "확인", "인증", "제출", "로그인", "submit", "verify")

# 자동 복구 범위 밖 신호. 이 텍스트가 보이면 운영자 조치가 필요하므로 False를 돌려준다.
_CAPTCHA_SIGNALS = ("captcha", "보안문자", "자동입력 방지", "로봇이 아닙니다", "recaptcha")
_PASSWORD_SIGNALS = ("비밀번호 입력",)

_USERNAME_INPUT_SELECTORS = (
    "input[placeholder*='아이디']",
    "input[name='username']",
)
_PASSWORD_INPUT_SELECTORS = (
    "input[placeholder*='비밀번호']",
    "input[type='password']",
    "input[name='password']",
)
_LOGIN_BUTTON_TEXTS = ("로그인", "login")

# 화면 교차검증용 이메일 주소 패턴. 쿠팡 인증 화면에 노출되는 인증 이메일(일부 마스킹될
# 수 있음)의 도메인을, 탭에 입력한 인증 이메일 도메인과 대조한다. ``*``는 마스킹 문자.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%*+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_SUPPORTED_SCREEN_DOMAINS = set(IMAP_HOST_BY_DOMAIN)


def recover_coupang_session_with_email_2fa(
    page: Any,
    config: AppConfig,
    *,
    fetch_code: Callable[..., str] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    """Attempt to recover an expired Coupang session via email 2FA.

    성공하면 ``True``, 자동 복구할 수 없는 화면(CAPTCHA, 아이디/비밀번호 입력 등)이면
    ``False``를 돌려준다. 이메일(IMAP)/입력 단계의 복구 불가 오류는 ``Coupang2faError``로
    올려, 호출부가 기존 ``BrowserActionRequiredError`` 흐름으로 중단하게 한다.
    """

    page_text = _safe_page_text(page)

    if _contains_any(page_text, _CAPTCHA_SIGNALS):
        # CAPTCHA는 자동으로 풀지 않는다(문서 제외 범위). 운영자 처리로 넘긴다.
        return False

    if _is_password_login_screen(page_text, page):
        if not _submit_primary_login(page, config):
            return False
        _wait_after_action(page, config)

    # 이메일 인증 방식 선택. 이미 코드 입력 단계면 이 클릭은 없어도 된다.
    _click_first_by_text(page, _EMAIL_METHOD_TEXTS, config, roles=("tab", "button"))
    # 인증번호 발송 버튼이 없으면(= 이메일 인증 화면이 아니면) 자동 복구 대상이 아니다.
    # 로그인 제출 뒤에도 비밀번호 화면에 머물러 있으면 계정 정보 오류나 CAPTCHA 가능성이
    # 있으므로 운영자 조치가 필요한 상태로 본다.
    refreshed_text = _safe_page_text(page)
    if _is_password_login_screen(refreshed_text, page):
        return False

    # 인증번호 발송 시각은 "발송 클릭 직전"을 기준으로, 거기서 약간의 안전 여유를 뺀
    # 시각으로 잡는다. 발송 클릭 직후에 시각을 찍으면, 클릭과 동시에 도착한 메일의 Gmail
    # internalDate가 그 시각보다 살짝 앞서 버려질 수 있다(로컬 시계와 Gmail 서버 시계
    # 오차 포함). 이 시각 이전 메일은 Gmail 조회에서 버린다.
    requested_after = now() - timedelta(seconds=_REQUESTED_AFTER_SAFETY_SECONDS)

    if not _click_first_by_text(page, _SEND_CODE_TEXTS, config, roles=("button",)):
        # 발송 버튼을 못 찾으면 이메일 인증 화면이 아니라고 보고 자동 복구를 포기한다.
        return False

    # 탭에 입력한 인증 이메일과 화면에 노출된 도메인이 어긋나면, 이 메일함으로는 인증번호가
    # 오지 않는 오설정이다(다른 메일 계정으로 코드가 감). 폴링을 시작하기 전에 중단한다.
    if not _account_matches_screen(page, config.verification_email_address):
        return False

    code = _fetch_code(config, requested_after=requested_after, fetch_code=fetch_code)

    _fill_code_input(page, code, config)
    _click_first_by_text(page, _SUBMIT_TEXTS, config, roles=("button",))
    return True


class Coupang2faError(RuntimeError):
    """이메일 2FA 복구 중 운영자 조치가 필요한 실패. 메시지에 코드/앱 비밀번호를 넣지 않는다."""


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
        # IMAP 조회 실패는 자동 복구 불가. 코드/앱 비밀번호 값은 애초에 메시지에 없다.
        raise Coupang2faError(str(exc)) from exc

    if not code:
        raise Coupang2faError("이메일에서 인증번호를 받지 못했습니다.")
    return code


def _imap_fetch(**kwargs: Any) -> str:
    from rider_crawl.auth.imap_2fa import fetch_latest_verification_code

    return fetch_latest_verification_code(**kwargs)


# --- 화면 도메인 교차검증 ----------------------------------------------------


def _onscreen_domains(page: Any) -> set[str]:
    text = _safe_page_text(page)  # 이미 casefold 된 page.content()
    # 화면이 도메인을 일부 마스킹(예: na***.com)하면 지원 도메인과 매칭되지 않으므로,
    # 완전한 지원 도메인(naver.com/gmail.com 등)만 hard block 기준으로 삼는다.
    return {
        domain
        for m in _EMAIL_RE.finditer(text)
        if (domain := m.group(1).casefold()) in _SUPPORTED_SCREEN_DOMAINS
    }


def _account_matches_screen(page: Any, account_address: str) -> bool:
    screen = _onscreen_domains(page)
    if not screen:
        return True  # 화면에 (완전한) 지원 도메인이 없으면 교차검증 생략(주 결정만 신뢰).
    return domain_of(account_address) in screen


# --- 화면 조작 헬퍼 ----------------------------------------------------------


def _interaction_timeout(config: AppConfig) -> int:
    # 인증 화면 조작은 짧게 시도하고 빨리 넘긴다(코드 만료 방지). 페이지 타임아웃이
    # 더 작게 설정된 환경에서는 그 값을 따른다.
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


def _submit_primary_login(page: Any, config: AppConfig) -> bool:
    credentials = _load_coupang_credentials(config)
    if credentials is None:
        return False

    username, password = credentials
    if not _fill_first_input(page, _USERNAME_INPUT_SELECTORS, username, config):
        return False
    if not _fill_first_input(page, _PASSWORD_INPUT_SELECTORS, password, config):
        return False
    # 제출. 쿠팡 로그인(antd)은 React 상태로 폼 유효성을 판단해서, '로그인' 버튼이
    # enabled로 보여도 클릭이 no-op이 되는 경우가 있다(라이브에서 클릭 성공으로 잡히는데
    # 화면·URL이 그대로). 그래서 비밀번호칸 Enter를 주 제출 경로로 두고, 버튼 클릭도
    # 함께 시도한다(둘 중 하나만 먹어도 제출된다). 실제 제출 성공 여부는 호출부가 이어서
    # _is_password_login_screen 재확인으로 판정하므로, 여기서는 입력까지 끝났으면 True.
    _press_enter_first(page, _PASSWORD_INPUT_SELECTORS, config)
    _click_first_by_text(page, _LOGIN_BUTTON_TEXTS, config, roles=("button",))
    return True


def _load_coupang_credentials(config: AppConfig) -> tuple[str, str] | None:
    # 쿠팡 로그인 자격증명은 UI 탭 입력에서만 온다(JSON 파일 폴백 폐기). 둘 중 하나라도
    # 비면 1차 로그인 자동 제출을 하지 않는다.
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
    """``value``를 실제 키 입력으로 채워, antd/React 컨트롤드 인풋이 값을 인식하게 한다.

    ``.fill()``은 DOM value만 세팅하고 input 이벤트를 한 번만 던진다. 쿠팡 로그인 화면
    (antd)처럼 React 상태로 폼 유효성을 판단하는 곳에서는 ``.fill()`` 뒤에 '로그인'을
    눌러도 빈 값으로 보고 제출이 일어나지 않는다(라이브 확인). 실제 타이핑은 키별
    이벤트를 던져 React가 상태를 확실히 갱신한다. 타이핑 메서드가 없는 구현(테스트의
    fake page 등)은 ``.fill()``로 폴백한다."""
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
    """첫 매칭 입력칸에서 Enter를 눌러 폼을 제출한다(best-effort).

    antd '로그인' 버튼 클릭이 no-op이 되는 경우를 대비한 주 제출 경로다. press를
    지원하지 않는 구현(fake page 등)은 조용히 건너뛴다."""
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


def _wait_after_action(page: Any, config: AppConfig) -> None:
    # networkidle은 보통 빨리 끝나지만, 안 끝나도 인증 화면 조작을 오래 막지 않도록
    # 상한을 짧게 둔다(idle이면 그 전에 반환된다).
    try:
        page.wait_for_load_state("networkidle", timeout=min(config.page_timeout_seconds, 3_000))
    except Exception:
        pass
    try:
        page.wait_for_timeout(300)
    except Exception:
        pass


def _click_first_by_text(
    page: Any,
    texts: tuple[str, ...],
    config: AppConfig,
    *,
    roles: tuple[str, ...] = ("tab", "button"),
) -> bool:
    """Click the first visible element whose text matches one of ``texts``.

    어떤 후보든 한 번 클릭에 성공하면 ``True``. 화면에 없거나 모두 실패하면 ``False``.
    """

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
            # fake page 등 exact 인자를 안 받는 구현 호환.
            locator = page.get_by_text(text)
        except Exception:
            continue
        if _click_first_visible(locator, timeout):
            return True
    return False


def _click_first_visible(locator: Any, timeout: int) -> bool:
    """주어진 locator의 매칭 중 **화면에 보이는 첫 요소**를 클릭한다.

    쿠팡 2단계 인증 화면은 antd 탭이라, '이메일로 인증'으로 전환해도 비활성(휴대폰)
    탭의 '인증코드 전송' 버튼이 DOM에 *숨은 채* 남는다. 그러면 같은 이름의 버튼이 둘이
    되어, ``get_by_role(...).click()``은 strict-mode 위반으로 실패하고 ``.first``는 DOM
    순서상 앞에 있는 **숨은 휴대폰 버튼**을 집어 클릭이 타임아웃된다(→ 이메일 코드가
    발송되지 않아 2FA 복구가 통째로 실패). 그래서 ``visible`` 필터로 보이는 요소만
    남긴 뒤 첫 요소를 누른다.

    ``visible`` 필터(``Locator.filter``)를 지원하지 않는 구현(테스트의 fake page 등)은
    기존 동작(``.first.click()``)으로 폴백한다.
    """

    try:
        visible = locator.filter(visible=True)
    except (AttributeError, TypeError):
        # .filter 미지원(fake page 등) → 기존 .first.click() 폴백.
        try:
            locator.first.click(timeout=timeout)
            return True
        except Exception:
            return False

    try:
        visible.first.click(timeout=timeout)
        return True
    except Exception:
        # 보이는 매칭이 없거나 클릭 실패. 숨은 요소로 폴백하지 않는다(잘못된 탭의
        # 버튼을 눌러 엉뚱한 수단으로 코드가 나가는 것을 막는다).
        return False


def _fill_code_input(page: Any, code: str, config: AppConfig) -> None:
    # selector마다 페이지 전체 타임아웃(수십 초)을 기다리면, 첫 selector가 안 맞을 때
    # 그 시간 동안 입력이 지연돼 인증번호가 만료된다. 짧은 타임아웃으로 빠르게 넘긴다.
    timeout = _interaction_timeout(config)
    for selector in _CODE_INPUT_SELECTORS:
        try:
            locator = page.locator(selector).first
            _enter_text(locator, code, timeout)
            return
        except Exception:
            continue
    raise Coupang2faError(
        "쿠팡 인증번호 입력칸을 찾지 못했습니다. 인증 화면 selector 보정이 필요합니다."
    )
