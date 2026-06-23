"""배민 auth 상태 감지 + 사람 개입형 재인증 실행자 primitive (Story 4.8 / P3, FR-17·18).

이 모듈이 책임지는 것(범위 — 분류기 + 두 실행자 + bounded 대기 + 얇은 라우터 primitive 만):

* :func:`classify_baemin_auth_state` — reuse seam 의 :class:`BrowserActionRequiredError`
  (로그인/휴대폰 인증 필요 신호)와 정상 snapshot 여부를 받아 **평문 auth 상태**
  (``AUTH_REQUIRED``/``ACTIVE``/``UNKNOWN``)로 분류한다. **비-auth 예외**
  (``CdpUnavailableError``/``MissingPerformanceDataError``/``RuntimeError``)는 auth 로
  오분류하지 않는다(파서/연결 문제를 인증 문제로 오인 금지 — fail-closed 는 ``UNKNOWN``).
* :func:`execute_auth_check_job` — ``AUTH_CHECK`` job 실행자. 주입 ``login_probe`` 로 **로그인
  상태만 점검**해 ``ACTIVE``/``AUTH_REQUIRED`` 를 보고한다. **수집/렌더/전송을 호출하지 않는다**
  (fail-closed = 인증으로 막힌 대상에 잘못된 메시지 0, NFR-2).
* :func:`execute_open_auth_browser_job` — ``OPEN_AUTH_BROWSER`` job 실행자. 주입
  ``open_auth_browser`` 로 프로필 브라우저를 **열고**, 주입 ``detect_completion`` 으로 사람이
  완료한 로그인 상태를 **read-only 로 감지**(``AUTH_VERIFIED``)한다. **휴대폰 인증 코드(OTP)를
  취득·자동입력·우회·자동 통과하려 시도하지 않는다(ADD-15 — 이 스토리의 핵심 금지).** 재인증
  대기는 주입 ``now``/``sleep`` + 상한(``max_wait_seconds``/``max_attempts``)으로 **bounded**
  하며, 상한 내 미완료면 ``AUTH_REQUIRED``(사유 ``auth_timeout``)로 **멈춘다**(무한 재시도 금지,
  NFR-4).
* :func:`build_auth_execute_job` — ``AUTH_CHECK``/``OPEN_AUTH_BROWSER`` 를 각 실행자로, 그 외
  type 은 ``fallback`` 으로 보내는 얇은 라우터(4.6 :func:`build_execute_job` 패턴 동형). 기존
  ``execute_job`` 주입점 위에 합성된다 — ``run_agent``/``job_loop`` 0줄(auth 실행자는 stateless
  요청-응답이라 thread 수명 배선이 불필요. 4.6 kakao 와 대조).

소유 분리(스코프 경계):

* **배민 재인증은 "사람이 한다" — 자동화·우회 절대 금지(ADD-15·ops-security-contract 76·90).**
  배민 경로는 휴대폰 인증 코드 취득·입력·우회를 하지 않으며, ``pyautogui`` 같은 GUI 자동화도
  쓰지 않는다. 쿠팡 경로는 기존 수집 코드의
  로그인 탭 선택 흐름과 이메일 2FA 복구 구현(``fetch_latest_verification_code`` 포함)만
  재사용하며, 수집/렌더/전송은 실행하지 않는다.
* **``rider_crawl`` 0줄·서버 측 ``auth_sessions``/job 생성/queue 는 본 스토리 범위가 아니다.**
  배민 login-page 감지 로직을 ``rider_crawl`` 에 새로 넣지 않고, 기본 probe 도 기존
  ``crawl_snapshot``/``BrowserActionRequiredError`` seam 을 재사용한다.

상태/오류 어휘는 **평문 문자열 상수**다(``rider_server.domain.states`` 의 ``BaeminAuthState``·
``FailureCategory.AUTH_REQUIRED`` 값과 정합) — ``rider_server`` 를 직접 import 하면 단방향 가드
위반이라 **값만 베끼고**(4.5 ``STATE_AUTH_REQUIRED``·4.6 ``ERROR_KAKAO_FAILURE`` 선례) 어떤
enum/"정확히 N개" lock 도 두지 않는다(memory: enum-member-count-locks).

자기(own) 코드는 **순수 동기**이고 stdlib(+``rider_crawl.redaction``·``rider_agent`` 자기
패키지)만 import 한다(역방향/``rider_server`` import 0, ``asyncio`` 0). 실 브라우저/실 시계는
함수 내부 lazy + Windows-gated + 주입 가능이라 ``import rider_agent.auth.baemin_auth`` 가
비-Windows(WSL/CI)에서도 import-safe 하다 — 4.1 의 AST 가드가 ``auth/`` 하위까지 자동 검사한다.
"""

from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rider_crawl.redaction import redact

from rider_agent.heartbeat import (
    CAPABILITY_AUTH_CHECK,
    CAPABILITY_OPEN_AUTH_BROWSER,
)
from rider_agent.job_loop import (
    ClaimedJob,
    JobResult,
    default_execute_job,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import BrowserActionRequiredError

# ── auth 상태 어휘 — **평문 상수**(BaeminAuthState 값 정합), enum/"정확히 N개" lock 금지.
# rider_server 를 직접 import 하면 단방향 가드 위반이라 값만 베낀다(browser_profile
# ``STATE_AUTH_REQUIRED``·secure_store ``TOKEN_STATUS_*`` 선례). 후속(4.9)이 상태를 늘려도
# 다른 lock 을 깨지 않는다. [Source: rider_server/domain/states.py(53-59)]
AUTH_STATE_UNKNOWN = "UNKNOWN"
AUTH_STATE_ACTIVE = "ACTIVE"
AUTH_STATE_AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_STATE_AUTH_VERIFIED = "AUTH_VERIFIED"
AUTH_STATE_BLOCKED_OR_CAPTCHA = "BLOCKED_OR_CAPTCHA"

# job-level error_code — FailureCategory.AUTH_REQUIRED 값과 정합(평문 상수).
# [Source: rider_server/domain/states.py(180)]
ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"

# 재인증 미완료 사유(평문 상수) — timeout 결과 metrics/result_json 에 실어 운영에 남긴다.
REASON_AUTH_TIMEOUT = "auth_timeout"

# payload TTL 이 지난 stale OPEN_AUTH_BROWSER job 을 브라우저 열기 전에 거르는 defensive 가드
# (server preflight 가 우회돼도 오래된 인증 브라우저를 열지 않게 — Task 5 defense-in-depth).
ERROR_PAYLOAD_EXPIRED = "PAYLOAD_EXPIRED"
REASON_PAYLOAD_EXPIRED = "payload_expired"


def _payload_expired(job: ClaimedJob, *, now: datetime) -> bool:
    """job payload ``expires_at``(ISO 8601 ``…Z``) 가 지났는가(없거나 파싱 실패면 False)."""

    text = str((job.payload or {}).get("expires_at") or "").strip()
    if not text:
        return False
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now >= parsed

# auth-required/verified 진행 이벤트 type(평문 상수). 실제 emit 은 호출자(loop)가 한다.
EVENT_TYPE_AUTH_REQUIRED = "AUTH_REQUIRED"
EVENT_TYPE_AUTH_VERIFIED = "AUTH_VERIFIED"
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"

# ── 재인증 대기 상한(무한 재시도 금지, NFR-4) — 4.5 recover_profile(attempts 3·backoff 1.0)
# 규모를 본뜬 모듈 상수. 운영 실값은 Epic 5 스케줄/lease 와 정합하게 조정 가능(본 스토리는
# bounded 보장만). 테스트는 항상 작은 값 + 주입 now/sleep 으로 결정적 검증한다.
# [Source: src/rider_agent/browser_profile.py(71-73·345-392)]
DEFAULT_MAX_WAIT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = int(DEFAULT_MAX_WAIT_SECONDS / DEFAULT_POLL_INTERVAL_SECONDS) + 1
_INTERACTION_TIMEOUT_MS = 10_000
# 인증 시작(브라우저 열기) 경로의 networkidle 대기 상한. 쿠팡 partner 페이지는 백그라운드
# 폴링이 잦아 networkidle 이 잘 안 떠 매번 상한까지 헛대기한다. DOM 은 이미 domcontentloaded
# 로 떴으니 여기선 짧게(3초)만 settle 을 기다린다 — 로그인 폼은 그 전에 이미 보인다.
_AUTH_NETWORKIDLE_TIMEOUT_MS = 3_000

_USERNAME_INPUT_SELECTORS = (
    "input[name='username']",
    "input[name='loginId']",
    "input[name='id']",
    "input[placeholder*='아이디']",
    "input[type='text']",
)
_PASSWORD_INPUT_SELECTORS = (
    "input[name='password']",
    "input[placeholder*='비밀번호']",
    "input[type='password']",
)
_LOGIN_BUTTON_TEXTS = ("로그인", "로그인하기", "login")
_PHONE_CODE_REQUEST_TEXTS = (
    "인증번호 요청",
    "인증번호 받기",
    "인증번호 발송",
    "인증코드 전송",
    "문자 인증",
    "휴대폰 인증",
    "코드 받기",
)
_BAEMIN_PHONE_CODE_READY_SELECTORS = (
    "button:has-text('인증번호 받기')",
    "button:has-text('인증번호 요청')",
    "input[name='verificationCode']",
)


# ── 분류기(auth-required 신호 → 평문 상태) ─────────────────────────────────────


def classify_baemin_auth_state(
    *, snapshot_ok: bool | None = None, error: BaseException | None = None
) -> str:
    """배민 auth 상태를 평문 상수로 분류한다(reuse 신호 소비 — 재구현 0, fail-closed).

    * ``error`` 가 :class:`BrowserActionRequiredError`(reuse seam·로그인/휴대폰 인증 필요
      신호) → :data:`AUTH_STATE_AUTH_REQUIRED`.
    * 그 외 ``snapshot_ok is True``(정상 달성현황 snapshot) → :data:`AUTH_STATE_ACTIVE`.
    * 그 외/모호 → :data:`AUTH_STATE_UNKNOWN`.

    **비-auth 예외는 auth 로 오분류하지 않는다.** 배민 크롤러는 ``BrowserActionRequiredError`` 를
    어디서도 raise 하지 않고(쿠팡만 raise) 로그인 안 됨이 ``MissingPerformanceDataError``/
    ``RuntimeError`` 로 표면화하므로, ``MissingPerformanceDataError``→``AUTH_REQUIRED`` 같은
    광범위 매핑은 금지한다(파서/연결 문제를 인증 문제로 오인 → 잘못된 운영 신호). 그런 예외는
    ``UNKNOWN`` 으로 두고 auth-required 판정은 주입 probe 가 결정한다.
    [Source: src/rider_agent/reuse.py(69), memory/baemin-no-action-required-signal]
    """

    if error is not None and isinstance(error, BrowserActionRequiredError):
        return AUTH_STATE_AUTH_REQUIRED
    if snapshot_ok is True:
        return AUTH_STATE_ACTIVE
    return AUTH_STATE_UNKNOWN


# ── 기본 probe/open/detect (reuse seam 기반 실제 배선) ─────────────────────────
# 실 브라우저/실 crawl import 는 함수 내부 lazy 로 유지한다(import-safety). 새 크롤러/인증
# 우회는 만들지 않고, crawl worker 가 이미 쓰는 job payload → AppConfig 변환과 reuse seam 을
# 그대로 재사용한다.


def _config_from_auth_job(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> Any:
    from rider_agent.workers.crawl_worker import _build_config, payload_from_job

    payload = payload_from_job(job)
    raw_payload = job.payload or {}
    cdp_url = str(raw_payload.get("cdp_url") or "http://127.0.0.1:9222")
    user_data_dir = Path(
        str(raw_payload.get("browser_user_data_dir") or "")
        or str(Path("runtime") / "agent-browser-profiles" / payload.target_id)
    )
    return _build_config(
        payload,
        cdp_url=cdp_url,
        user_data_dir=user_data_dir,
        secret_resolver=secret_resolver,
    )


def default_login_probe(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> str:
    """기본 배민 로그인 상태 probe(reuse ``crawl_snapshot`` 기반 read-only 판정).

    정상 snapshot 을 얻으면 ``ACTIVE``, ``BrowserActionRequiredError`` 는 ``AUTH_REQUIRED``,
    그 외 파서/연결류 예외는 인증 문제로 단정하지 않고 ``UNKNOWN`` 으로 둔다.
    """

    from rider_agent import reuse

    config = _config_from_auth_job(job, secret_resolver=secret_resolver)
    platform = str((job.payload or {}).get("platform") or "baemin").strip().casefold()
    try:
        reuse.crawl_snapshot(config, platform_name=platform or "baemin")
    except BrowserActionRequiredError as exc:
        return classify_baemin_auth_state(error=exc)
    except Exception:
        return AUTH_STATE_UNKNOWN
    return classify_baemin_auth_state(snapshot_ok=True)


def default_open_auth_browser(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> bool | None:
    """기본 프로필 브라우저 열기.

    ``prepare_chrome`` 를 재사용한다. Baemin 은 프로필을 열고 로그인 화면 조치만 수행하며,
    Coupang 은 기존 이메일 2FA 복구 seam 만 실행한다. 어느 경로도 수집 job 을 실행하지 않는다.
    """

    from rider_agent import reuse

    config = _config_from_auth_job(job, secret_resolver=secret_resolver)
    platform = str(getattr(config, "platform_name", "") or "").strip().casefold()
    try:
        reuse.prepare_chrome(config, platform_name="Windows")
    except Exception:
        return
    if platform == "coupang":
        try:
            return _drive_coupang_email_2fa_flow(config)
        except Exception:
            return False
    if platform != "baemin":
        return None
    try:
        _drive_baemin_login_flow(config)
    except Exception:
        return
    return None


def default_detect_completion(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> bool:
    """기본 사람-완료 감지(read-only).

    로그인 완료 여부만 다시 probe 한다. 인증번호를 읽거나 입력·제출하지 않는다.
    """

    platform = str((job.payload or {}).get("platform") or "baemin").strip().casefold()
    if platform == "coupang":
        return _detect_coupang_completion(job, secret_resolver=secret_resolver)
    if secret_resolver is None:
        return default_login_probe(job) == AUTH_STATE_ACTIVE
    return default_login_probe(job, secret_resolver=secret_resolver) == AUTH_STATE_ACTIVE


def _detect_coupang_completion(
    job: ClaimedJob,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> bool:
    from rider_crawl.platforms.coupang import crawler as coupang_crawler

    try:
        config = _config_from_auth_job(job, secret_resolver=secret_resolver)
        target_url = str(getattr(config, "coupang_eats_url", "") or "").strip()
        with _sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(config.cdp_url)
            timeout_errors = _playwright_timeout_errors()
            pages = coupang_crawler._browser_pages(browser)
            if coupang_crawler._login_required_page(pages) is not None:
                return False
            if target_url and not _coupang_target_url_has_readiness_signal(target_url):
                return False

            page = coupang_crawler._select_page_by_url(pages, target_url) if target_url else None
            if page is not None and target_url:
                try:
                    coupang_crawler._wait_for_target_page_ready(
                        page,
                        config,
                        target_url=target_url,
                        timeout_errors=timeout_errors,
                    )
                    return True
                except BrowserActionRequiredError:
                    return False
                except Exception:
                    return False

            return _has_coupang_partner_session(pages)
    except Exception:
        return False


def _has_coupang_partner_session(pages: list[Any]) -> bool:
    from urllib.parse import urlsplit

    for page in pages:
        host = (urlsplit(str(getattr(page, "url", ""))).hostname or "").casefold()
        if host == "partner.coupangeats.com":
            return True
    return False


def _coupang_target_url_has_readiness_signal(target_url: str) -> bool:
    from urllib.parse import urlsplit

    path = (urlsplit(str(target_url or "")).path or "").rstrip("/") or "/"
    return path.casefold() in {"/page/peak-dashboard", "/page/rider-performance"}


def _drive_baemin_login_flow(config: Any) -> None:
    with _sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(config.cdp_url)
        context = browser.contexts[0] if getattr(browser, "contexts", None) else browser.new_context()
        page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
        _navigate_to_auth_target(page, config)
        _fill_first_input(
            page,
            _USERNAME_INPUT_SELECTORS,
            getattr(config, "baemin_login_id", ""),
            config,
        )
        _fill_first_input(
            page,
            _PASSWORD_INPUT_SELECTORS,
            getattr(config, "baemin_login_password", ""),
            config,
        )
        _click_first_by_text(page, _LOGIN_BUTTON_TEXTS, config, roles=("button",))
        _wait_after_action(page, config)
        _wait_for_baemin_phone_code_request(page, config)
        _click_first_by_text(
            page, _PHONE_CODE_REQUEST_TEXTS, config, roles=("button", "link")
        )


def _wait_for_baemin_phone_code_request(page: Any, config: Any) -> None:
    timeout = _interaction_timeout(config)
    wait_for_selector = getattr(page, "wait_for_selector", None)
    if not callable(wait_for_selector):
        return
    for selector in _BAEMIN_PHONE_CODE_READY_SELECTORS:
        try:
            wait_for_selector(selector, timeout=timeout)
            return
        except Exception:
            continue


def _drive_coupang_email_2fa_flow(config: Any) -> bool:
    from rider_agent import reuse
    from rider_crawl.platforms.coupang import crawler as coupang_crawler

    with _sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(config.cdp_url)
        target_url = str(getattr(config, "coupang_eats_url", "") or "").strip()
        timeout_errors = _playwright_timeout_errors()
        pages = coupang_crawler._browser_pages(browser)
        page = coupang_crawler._login_required_page(pages)
        if page is None and target_url:
            page = coupang_crawler._select_page_by_url(pages, target_url)
        if page is None:
            page = _first_browser_page(browser)
        if page is None:
            return False

        # 인증 시작 시점엔 보통 로그인 화면이다. 이때 대상 페이지로 goto 하거나 대시보드
        # 텍스트를 기다리면(아래 _wait_for_target_page_ready) 로그인 전이라 절대 나타나지
        # 않을 텍스트를 page_timeout_seconds(기본 60초)만큼, peak-dashboard 면 reload 후
        # 한 번 더 기다린다(최대 ~130초). 그동안 사람은 아이디/비밀번호도 입력하지 못한다.
        # → 로그인 화면이면 이 대기를 통째로 건너뛰고 바로 2FA/로그인 복구로 진입한다.
        is_login_screen = coupang_crawler._page_looks_like_coupang_login_required(page)

        if target_url and not is_login_screen:
            try:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=getattr(config, "page_timeout_seconds", _INTERACTION_TIMEOUT_MS),
                )
            except timeout_errors:
                pass
            except Exception:
                return False
            try:
                page.wait_for_load_state("networkidle", timeout=_AUTH_NETWORKIDLE_TIMEOUT_MS)
            except timeout_errors:
                pass

        # 이미 로그인돼 대시보드가 떠 있는 경우에만 "이미 완료" 빠른 경로를 탄다. 로그인
        # 화면이면 여기서 대시보드 텍스트를 기다리지 않는다(절대 안 뜸 → 60초 헛대기 방지).
        try:
            if (
                target_url
                and not is_login_screen
                and _coupang_target_url_has_readiness_signal(target_url)
            ):
                coupang_crawler._wait_for_target_page_ready(
                    page,
                    config,
                    target_url=target_url,
                    timeout_errors=timeout_errors,
                )
                return True
        except BrowserActionRequiredError:
            pass
        except Exception:
            pass

        try:
            recovered = bool(reuse.recover_coupang_session_with_email_2fa(page, config))
        except Exception:
            recovered = False
        if recovered and target_url:
            if not _coupang_target_url_has_readiness_signal(target_url):
                return False
            coupang_crawler._reload_target_page(
                page,
                config,
                target_url=target_url,
                load_timeout_errors=timeout_errors,
            )
            try:
                coupang_crawler._wait_for_target_page_ready(
                    page,
                    config,
                    target_url=target_url,
                    timeout_errors=timeout_errors,
                )
                return True
            except BrowserActionRequiredError:
                return False
            except Exception:
                return False
        return False


def _first_browser_page(browser: Any) -> Any | None:
    contexts = list(getattr(browser, "contexts", []) or [])
    context = contexts[0] if contexts else None
    if context is None:
        new_context = getattr(browser, "new_context", None)
        if not callable(new_context):
            return None
        context = new_context()
    pages = list(getattr(context, "pages", []) or [])
    if pages:
        return pages[0]
    new_page = getattr(context, "new_page", None)
    if not callable(new_page):
        return None
    return new_page()


def _playwright_timeout_errors() -> tuple[type[BaseException], ...]:
    try:
        timeout_error = importlib.import_module("playwright.sync_api").TimeoutError
    except Exception:
        return ()
    return (timeout_error,)


def _navigate_to_auth_target(page: Any, config: Any) -> None:
    url = str(getattr(config, "coupang_eats_url", "") or "").strip()
    if not url:
        return
    goto = getattr(page, "goto", None)
    if goto is None:
        return
    goto(url, wait_until="domcontentloaded", timeout=_interaction_timeout(config))
    _wait_after_action(page, config)


def _sync_playwright() -> Any:
    return importlib.import_module("playwright.sync_api").sync_playwright()


def _interaction_timeout(config: Any) -> int:
    return min(
        int(getattr(config, "page_timeout_seconds", _INTERACTION_TIMEOUT_MS)),
        _INTERACTION_TIMEOUT_MS,
    )


def _fill_first_input(
    page: Any,
    selectors: tuple[str, ...],
    value: str,
    config: Any,
) -> bool:
    text = str(value or "")
    if not text:
        return False
    timeout = _interaction_timeout(config)
    for selector in selectors:
        try:
            _enter_text(page.locator(selector).first, text, timeout)
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


def _click_first_by_text(
    page: Any,
    texts: tuple[str, ...],
    config: Any,
    *,
    roles: tuple[str, ...] = ("button", "link"),
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


def _wait_after_action(page: Any, config: Any) -> None:
    try:
        page.wait_for_load_state(
            "networkidle", timeout=min(_interaction_timeout(config), 3_000)
        )
    except Exception:
        pass
    try:
        page.wait_for_timeout(300)
    except Exception:
        pass


# ── AUTH_CHECK 실행자(로그인 상태만 점검·보고 — 수집/전송 0) ────────────────────


def execute_auth_check_job(
    job: ClaimedJob,
    *,
    login_probe: Callable[[ClaimedJob], str] = default_login_probe,
    now: Callable[[], float] = time.time,
    log: Callable[[str], None] | None = None,
) -> JobResult:
    """``AUTH_CHECK`` job — 로그인 상태만 점검해 ``ACTIVE``/``AUTH_REQUIRED``/``UNKNOWN`` 를 보고한다.

    주입 ``login_probe(job) -> str``(auth_state 평문 상수)로 상태를 얻어:

    * ``ACTIVE`` → :func:`make_success_result` ``result_json={target_id, auth_state: ACTIVE}``.
    * ``AUTH_REQUIRED``/``UNKNOWN``/… → **메시지 생성 없이** 상태를 표면화한다
      — :func:`make_success_result` ``result_json={target_id, auth_state}`` (상태 점검은
      "실패"가 아니라 "현재 상태 확인"이라 success 결과로 일관, AUTH_CHECK 표면 정본).

    **수집/렌더/전송을 호출하지 않는다**(``crawl_snapshot``/``render_*``/``send_*`` 미호출) — 이
    실행자는 ``login_probe`` 만 부른다(fail-closed = 인증으로 막힌 대상에 잘못된 메시지 0,
    NFR-2). 보고 표면에는 ``target_id`` + 평문 auth_state 만 — raw 프로필 경로/secret/OTP/휴대폰
    0(NFR-5/8). 자유 텍스트 로그는 고정 메시지 + :func:`redact` 통과.
    [Source: src/rider_agent/job_loop.py(175-207), architecture-contract.md(126)]
    """

    state = login_probe(job)
    target_id = job.target_id
    if state == AUTH_STATE_ACTIVE:
        if log is not None:
            log(redact(f"auth check: ACTIVE (target {target_id})"))
        return make_success_result(
            result_json={"target_id": target_id, "auth_state": AUTH_STATE_ACTIVE}
        )

    # UNKNOWN 은 인증 필요가 아니라 판정 불가다. 그대로 보고해야 서버가 stale
    # AUTH_REQUIRED 를 UNKNOWN 으로 낮추고 최신 profile 오류를 계속 보여줄 수 있다.
    if state not in {
        AUTH_STATE_UNKNOWN,
        AUTH_STATE_AUTH_REQUIRED,
        AUTH_STATE_AUTH_VERIFIED,
    }:
        state = AUTH_STATE_AUTH_REQUIRED
    if log is not None:
        log(redact(f"auth check: {state} (target {target_id})"))
    return make_success_result(
        result_json={"target_id": target_id, "auth_state": state}
    )


# ── OPEN_AUTH_BROWSER 실행자(열기 + 사람-완료 감지 + bounded timeout) ───────────


def execute_open_auth_browser_job(
    job: ClaimedJob,
    *,
    open_auth_browser: Callable[[ClaimedJob], Any] = default_open_auth_browser,
    detect_completion: Callable[[ClaimedJob], bool] = default_detect_completion,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    log: Callable[[str], None] | None = None,
    wall_now: Callable[[], datetime] | None = None,
) -> JobResult:
    """``OPEN_AUTH_BROWSER`` job — 프로필 열기 + 인증 전용 조치 + bounded 재인증 대기.

    흐름: (a) ``open_auth_browser(job)`` 로 프로필 브라우저를 **연다**(정확히 1회), (b)
    인증이 즉시 완료되면 ``AUTH_VERIFIED`` 를 반환한다(Coupang 이메일 2FA 등), 아니면
    ``detect_completion(job) -> bool`` 를 **bounded** polling(주입 ``now``/``sleep``, 상한
    ``max_attempts`` 와 ``max_wait_seconds``)으로 호출, (c) 인증 완료 감지 →
    :func:`make_success_result` ``result_json={target_id, auth_state: AUTH_VERIFIED}`` (작업
    재개 신호), 상한 소진 → ``AUTH_REQUIRED`` + 사유 ``auth_timeout`` 실패 결과로 **멈춘다**
    (전송/메시지 생성 0, 무한 재시도·무한 polling 0 — 4.5 ``recover_profile`` bounded·NFR-4 동형).

    Baemin 경로는 **휴대폰 인증 코드(OTP)를 읽거나 입력·제출하지 않는다(ADD-15).** Coupang
    경로는 기존 이메일 2FA 복구 seam 만 사용한다. 사유는 평문 상수, 로그는 고정 메시지 +
    :func:`redact` 통과(raw 경로/OTP/휴대폰
    비노출). [Source: src/rider_agent/browser_profile.py(337-392), architecture-contract.md(118·127)]
    """

    target_id = job.target_id

    # payload TTL 이 지났으면 브라우저를 열기 전에 fail-fast(server preflight 우회돼도 오래된
    # 인증 브라우저를 열지 않게 — Task 5 defense-in-depth).
    wall_clock = wall_now or (lambda: datetime.now(timezone.utc))
    if _payload_expired(job, now=wall_clock()):
        if log is not None:
            log(redact(f"auth payload expired before browser open (target {target_id})"))
        return make_failure_result(
            ERROR_PAYLOAD_EXPIRED,
            "auth payload expired before browser open",
            result_json={
                "target_id": target_id,
                "auth_state": AUTH_STATE_AUTH_REQUIRED,
                "reason": REASON_PAYLOAD_EXPIRED,
            },
        )

    # (a) 프로필 브라우저를 인증 전용으로 연다. 정확히 1회.
    opened = open_auth_browser(job)
    if opened is True:
        if log is not None:
            log(redact(f"auth opened and verified (target {target_id})"))
        return make_success_result(
            result_json={
                "target_id": target_id,
                "auth_state": AUTH_STATE_AUTH_VERIFIED,
            }
        )
    first_incomplete_stage = "open_auth_browser"
    last_detect_state = "not_started"

    # (b) bounded polling — 주입 now/sleep + 상한(attempts·wall-clock). 무한 대기 0.
    start = now()
    attempts = 0
    while True:
        attempts += 1
        if detect_completion(job):
            last_detect_state = "completed"
            # (c) 사람-완료 감지 → AUTH_VERIFIED 로 작업 재개 신호를 표면화한다.
            if log is not None:
                log(redact(f"auth verified by human (target {target_id})"))
            return make_success_result(
                result_json={
                    "target_id": target_id,
                    "auth_state": AUTH_STATE_AUTH_VERIFIED,
                }
            )
        last_detect_state = "not_completed"
        # 상한 점검(다음 polling 전) — attempts 우선(cheap), 그다음 wall-clock.
        if attempts >= max_attempts:
            break
        if (now() - start) >= max_wait_seconds:
            break
        sleep(poll_interval_seconds)

    # 상한 소진: AUTH_VERIFIED 로 가지 않고 AUTH_REQUIRED(auth_timeout)로 멈춘다(전송 0).
    if log is not None:
        log(redact(f"auth re-verification timed out (target {target_id})"))
    return make_failure_result(
        ERROR_AUTH_REQUIRED,
        "auth re-verification not completed within bounded wait",
        result_json={
            "target_id": target_id,
            "auth_state": AUTH_STATE_AUTH_REQUIRED,
            "reason": REASON_AUTH_TIMEOUT,
            "first_incomplete_stage": first_incomplete_stage,
            "last_detect_state": last_detect_state,
            "detect_attempts": attempts,
        },
        metrics={"auth_reason": REASON_AUTH_TIMEOUT},
    )


# ── execute_job 라우터(AUTH_CHECK/OPEN_AUTH_BROWSER → 실행자, 그 외 → fallback) ──


def build_auth_execute_job(
    *,
    login_probe: Callable[[ClaimedJob], str] = default_login_probe,
    open_auth_browser: Callable[[ClaimedJob], Any] = default_open_auth_browser,
    detect_completion: Callable[[ClaimedJob], bool] = default_detect_completion,
    secret_resolver: Callable[[str], str | None] | None = None,
    fallback: Callable[[ClaimedJob], JobResult] = default_execute_job,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    log: Callable[[str], None] | None = None,
) -> Callable[[ClaimedJob], JobResult]:
    """``AUTH_CHECK``/``OPEN_AUTH_BROWSER`` 를 각 실행자로, 그 외 type 은 ``fallback`` 으로 보낸다.

    4.6 :func:`~rider_agent.workers.kakao_sender.build_execute_job` 패턴 동형(다른 type 용 빈
    stub 0). ``fallback`` 은 기존 ``execute_job``(기본 :func:`~rider_agent.job_loop.
    default_execute_job` = ``UNSUPPORTED_JOB_TYPE``, 또는 주입 워커)이라 auth 두 type 만
    가로챈다. job-type 매칭은 ``heartbeat.CAPABILITY_AUTH_CHECK``/``CAPABILITY_OPEN_AUTH_BROWSER``
    평문 상수를 재사용한다.

    이 산출물은 기존 ``execute_job`` 주입점 위에 합성된다 —
    ``run_agent(execute_job=build_auth_execute_job(...))`` 또는 ``JobRunner(execute_job=...)`` 로
    실 claim→execute→complete 루프가 auth job 을 라우팅한다. ``run_agent``/``job_loop.py``/
    ``__main__.py`` 는 0줄 변경(auth 실행자는 stateless 라 thread 수명 배선이 불필요 — 4.6 kakao
    와 대조). 미합성이면 기존 ``default_execute_job`` 그대로(4.7 동작 보존, 무회귀).
    [Source: src/rider_agent/workers/kakao_sender.py(393-411), src/rider_agent/job_loop.py(763·855-857)]
    """

    if secret_resolver is not None and login_probe is default_login_probe:
        login_probe = lambda job: default_login_probe(job, secret_resolver=secret_resolver)
    if secret_resolver is not None and open_auth_browser is default_open_auth_browser:
        open_auth_browser = lambda job: default_open_auth_browser(
            job, secret_resolver=secret_resolver
        )
    if secret_resolver is not None and detect_completion is default_detect_completion:
        detect_completion = lambda job: default_detect_completion(
            job, secret_resolver=secret_resolver
        )

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type == CAPABILITY_AUTH_CHECK:
            return execute_auth_check_job(job, login_probe=login_probe, now=now, log=log)
        if job.type == CAPABILITY_OPEN_AUTH_BROWSER:
            return execute_open_auth_browser_job(
                job,
                open_auth_browser=open_auth_browser,
                detect_completion=detect_completion,
                now=now,
                sleep=sleep,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_attempts=max_attempts,
                log=log,
            )
        return fallback(job)

    return _execute
