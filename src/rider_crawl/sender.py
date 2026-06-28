from __future__ import annotations

import hashlib
import json
import platform
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen as default_urlopen

from .config import AppConfig, app_state_root
from .log_rotation import rotate_if_needed
from .lock import RunLock


class TelegramSendConfigLike(Protocol):
    """``send_telegram_text`` 가 실제로 읽는 Telegram 전송 설정 표면(구조적 타입).

    ``AppConfig`` 는 이 셋(+다수의 무관 필드)을 가지므로 그대로 만족한다. 서버 중앙 전송 경로
    (``rider_server.services.telegram_central_dispatch``)는 ``AppConfig`` 의 15+ placeholder
    필드를 채우는 carrier 대신 :class:`TelegramSendConfig` 만 만들어 넘긴다(maintenance Task 8-A).
    """

    @property
    def telegram_bot_token(self) -> str: ...

    @property
    def telegram_chat_id(self) -> str: ...

    @property
    def telegram_message_thread_id(self) -> str: ...


@dataclass(frozen=True)
class TelegramSendConfig:
    """``send_telegram_text`` 전용 최소 설정 DTO(bot token·chat_id·thread_id 만).

    placeholder 로 가득 찬 ``AppConfig`` carrier 를 대체한다 — 전송에 필요한 3개 값만 들고,
    token 은 ``repr`` 에 남기지 않는다(secret 비노출). thread_id 는 ``send_telegram_text``
    인자로도 넘길 수 있어 빈 문자열 기본값을 둔다.
    """

    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    telegram_message_thread_id: str = ""

KAKAO_SEND_VERIFY_TIMEOUT_SECONDS = 2.0
KAKAO_SEND_VERIFY_INTERVAL_SECONDS = 0.1
KAKAO_TRUNCATED_PREFIX_MIN_CHARS = 40
KAKAO_ORDERED_MATCH_MIN_LINES = 5
TELEGRAM_LOCAL_MIN_SEND_INTERVAL_SECONDS = 1.0
_LAST_KAKAO_CHAT_HANDLE_BY_CHAT: dict[str, int] = {}
_KAKAO_DIAGNOSTICS: list[str] = []
_LAST_TELEGRAM_SEND_AT_BY_ROUTE: dict[tuple[str, str, str], float] = {}


class KakaoSendError(RuntimeError):
    """Raised when KakaoTalk text delivery cannot be attempted safely.

    ``ambiguous`` marks failures where the send may already have reached the
    chat: Enter was pressed but the result could not be confirmed (input value
    unreadable). Such failures must NOT be retried automatically on the fast
    5-second path, or the same message can be sent twice. Pre-send failures
    (window/focus/clear/paste verification) leave ``ambiguous`` False because
    the message was visibly never delivered, so a fast retry is safe.
    """

    def __init__(self, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous


class KakaoUnsafeSelectionError(KakaoSendError):
    """Raised when the target chat window is ambiguous or cannot be scanned.

    These conditions must fail immediately. Falling back to the KakaoTalk
    main-window search would automate the UI (ctrl+f / paste / Enter) while the
    real target is already known to be unsafe, risking a send to the wrong room
    or even the wrong application.
    """


class TelegramSendError(RuntimeError):
    """Raised when Telegram Bot API delivery cannot be attempted safely.

    ``retryable`` marks "definitely not delivered" failures (e.g. rate limit)
    where re-sending soon is safe. ``ambiguous`` marks failures where the request
    may have reached Telegram but the outcome could not be confirmed (the POST
    raised after sending, or the response could not be read). An ambiguous send
    must NOT be retried automatically on the fast path, or the same message can be
    delivered twice, because run_once only records the last hash after a clean
    success. The two flags are independent: a rate limit is retryable and not
    ambiguous; a lost response is ambiguous and not retryable.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.ambiguous = ambiguous


UrlOpen = Callable[..., Any]


def send_telegram_text(
    config: TelegramSendConfigLike,
    message: str,
    *,
    message_thread_id: int | None = None,
    urlopen: UrlOpen = default_urlopen,
    timeout_seconds: int = 10,
    retry_attempts: int = 3,
    sleep: Callable[[float], object] = time.sleep,
    local_rate_limit_seconds: float | None = None,
) -> None:
    token = _required_telegram_bot_token(config)
    chat_id = _required_telegram_chat_id(config)
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    target_thread_id = message_thread_id
    if target_thread_id is None:
        target_thread_id = _optional_telegram_message_thread_id(config)
    if target_thread_id is not None:
        payload["message_thread_id"] = target_thread_id
    rate_limit_seconds = _telegram_local_rate_limit_seconds(urlopen, local_rate_limit_seconds)

    attempts = max(1, retry_attempts)
    for attempt in range(attempts):
        try:
            _wait_for_telegram_local_rate_limit(
                token,
                chat_id,
                target_thread_id,
                rate_limit_seconds=rate_limit_seconds,
                sleep=sleep,
            )
            _telegram_api_request(
                config,
                "sendMessage",
                payload,
                urlopen=urlopen,
                timeout_seconds=timeout_seconds,
            )
            _record_telegram_local_send(token, chat_id, target_thread_id, rate_limit_seconds=rate_limit_seconds)
            return
        except TelegramSendError as exc:
            if attempt >= attempts - 1 or not _should_retry_telegram_send(exc):
                raise
            sleep(_telegram_retry_delay(exc, attempt))


def get_telegram_updates(
    config: AppConfig,
    *,
    offset: int | None = None,
    timeout_seconds: int = 30,
    urlopen: UrlOpen = default_urlopen,
) -> list[dict[str, Any]]:
    payload: dict[str, str | int] = {
        "timeout": timeout_seconds,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset is not None:
        payload["offset"] = offset

    result = _telegram_api_request(
        config,
        "getUpdates",
        payload,
        urlopen=urlopen,
        timeout_seconds=timeout_seconds + 5,
    )
    if not isinstance(result, list):
        raise TelegramSendError("Telegram getUpdates response result must be a list")
    return [update for update in result if isinstance(update, dict)]


def _telegram_api_request(
    config: TelegramSendConfigLike,
    method: str,
    payload: dict[str, object],
    *,
    urlopen: UrlOpen,
    timeout_seconds: int,
) -> object:
    token = _required_telegram_bot_token(config)
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        response = urlopen(request, timeout=timeout_seconds)
    except HTTPError as exc:
        body = _read_response_body(exc)
        raise _telegram_error_from_response(method, body, status_code=exc.code) from exc
    except Exception as exc:
        # The POST raised without a usable HTTP response. The request bytes may
        # still have reached Telegram (e.g. the connection dropped while reading
        # the reply), so for sendMessage the delivery is ambiguous. Mark it so the
        # caller does not fast-retry and double-send the same message.
        raise TelegramSendError(
            f"Telegram Bot API request failed: {method}",
            ambiguous=method == "sendMessage",
        ) from exc

    try:
        with response:
            body = response.read().decode("utf-8")
    except Exception as exc:
        # Telegram accepted and processed the request, but the response body could
        # not be read. For sendMessage the message was almost certainly delivered,
        # so this is ambiguous and must not be fast-retried.
        raise TelegramSendError(
            f"Telegram Bot API response could not be read: {method}",
            ambiguous=method == "sendMessage",
        ) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TelegramSendError("Telegram Bot API response was not valid JSON") from exc

    if not isinstance(data, dict) or data.get("ok") is not True:
        raise _telegram_error_from_response(method, body)
    return data.get("result")


def _read_response_body(response: object) -> str:
    try:
        return response.read().decode("utf-8")
    except Exception:
        return ""


def _telegram_error_from_response(method: str, body: str, *, status_code: int | None = None) -> TelegramSendError:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return TelegramSendError(
            f"Telegram Bot API request failed: {method}",
            retryable=status_code == 429,
            retry_after_seconds=None,
        )

    # 텔레그램이 dict가 아닌 JSON(예: ``[]``)을 주면 ``data.get(...)``이 AttributeError를
    # 낸다. dict가 아닐 때는 본문 전체를 설명으로 쓰고 error_code 검사는 건너뛴다.
    if not isinstance(data, dict):
        return TelegramSendError(
            f"Telegram Bot API error: {body}",
            retryable=status_code == 429,
            retry_after_seconds=None,
        )

    description = data.get("description")
    retry_after = _telegram_retry_after_seconds(data)
    retryable = status_code == 429 or retry_after is not None or data.get("error_code") == 429

    # 일반 그룹이 슈퍼그룹으로 전환되면 chat_id가 바뀐다(보통 -100… 형태). 텔레그램은
    # 에러 응답의 parameters.migrate_to_chat_id에 새 chat_id를 담아준다. 그 값을 그대로
    # 안내해 사용자가 설정의 채팅방 ID를 새 값으로 바꾸도록 한다. 자동 재시도해도 옛
    # ID로는 절대 성공하지 못하므로 retryable로 두지 않는다.
    migrate_to = _telegram_migrate_to_chat_id(data)
    if migrate_to is not None:
        return TelegramSendError(
            "Telegram Bot API error: 그룹이 슈퍼그룹으로 전환되어 채팅방 ID가 바뀌었습니다. "
            f"설정의 '텔레그램 채팅방 ID'를 {migrate_to} 로 바꿔 저장하세요. "
            f"(원문: {description})",
            retryable=False,
        )

    return TelegramSendError(
        f"Telegram Bot API error: {description}",
        retryable=retryable,
        retry_after_seconds=retry_after,
    )


def _telegram_migrate_to_chat_id(data: object) -> int | None:
    if not isinstance(data, dict):
        return None
    parameters = data.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("migrate_to_chat_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _telegram_retry_after_seconds(data: object) -> int | None:
    if not isinstance(data, dict):
        return None
    parameters = data.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after = parameters.get("retry_after")
    if isinstance(retry_after, int) and retry_after >= 0:
        return retry_after
    return None


def _should_retry_telegram_send(exc: TelegramSendError) -> bool:
    return exc.retryable or exc.retry_after_seconds is not None


def _telegram_retry_delay(exc: TelegramSendError, attempt: int) -> float:
    if exc.retry_after_seconds is not None:
        return exc.retry_after_seconds
    return float(attempt + 1)


def _telegram_local_rate_limit_seconds(urlopen: UrlOpen, requested: float | None) -> float:
    if requested is not None:
        return max(0.0, requested)
    if urlopen is default_urlopen:
        return TELEGRAM_LOCAL_MIN_SEND_INTERVAL_SECONDS
    return 0.0


def _wait_for_telegram_local_rate_limit(
    token: str,
    chat_id: str,
    thread_id: int | None,
    *,
    rate_limit_seconds: float,
    sleep: Callable[[float], object],
) -> None:
    if rate_limit_seconds <= 0:
        return
    previous = _LAST_TELEGRAM_SEND_AT_BY_ROUTE.get(_telegram_local_rate_limit_key(token, chat_id, thread_id))
    if previous is None:
        return
    remaining = rate_limit_seconds - (time.monotonic() - previous)
    if remaining > 0:
        sleep(remaining)


def _record_telegram_local_send(
    token: str,
    chat_id: str,
    thread_id: int | None,
    *,
    rate_limit_seconds: float,
) -> None:
    if rate_limit_seconds <= 0:
        return
    _LAST_TELEGRAM_SEND_AT_BY_ROUTE[_telegram_local_rate_limit_key(token, chat_id, thread_id)] = time.monotonic()


def _telegram_local_rate_limit_key(token: str, chat_id: str, thread_id: int | None) -> tuple[str, str, str]:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return (token_hash, chat_id, "" if thread_id is None else str(thread_id))


def _required_telegram_bot_token(config: TelegramSendConfigLike) -> str:
    token = config.telegram_bot_token.strip()
    if not token:
        raise TelegramSendError("TELEGRAM_BOT_TOKEN is required before sending")
    return token


def _required_telegram_chat_id(config: TelegramSendConfigLike) -> str:
    chat_id = config.telegram_chat_id.strip()
    if not chat_id:
        raise TelegramSendError("TELEGRAM_CHAT_ID is required before sending")
    return chat_id


def _optional_telegram_message_thread_id(config: TelegramSendConfigLike) -> int | None:
    raw = config.telegram_message_thread_id.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise TelegramSendError("TELEGRAM_MESSAGE_THREAD_ID must be a number") from exc


def send_kakao_text(
    config: AppConfig,
    message: str,
    *,
    platform_name: str | None = None,
) -> None:
    with RunLock(_kakao_os_automation_lock_path(), stale_timeout_seconds=config.run_lock_timeout_seconds):
        _send_kakao_text_locked(config, message, platform_name=platform_name)


def _send_kakao_text_locked(
    config: AppConfig,
    message: str,
    *,
    platform_name: str | None = None,
) -> None:
    _reset_kakao_diagnostics()
    _record_kakao_diagnostic(f"chat_name={config.kakao_chat_name.strip() or '<empty>'}")
    try:
        if not config.kakao_chat_name.strip():
            raise KakaoSendError("KAKAO_CHAT_NAME is required before sending")

        current_platform = platform_name or platform.system()
        _record_kakao_diagnostic(f"platform={current_platform}")
        if current_platform != "Windows":
            raise KakaoSendError("KakaoTalk UI sending is only supported on Windows")

        try:
            import pyautogui
            import pyperclip
        except ImportError as exc:
            raise KakaoSendError("pyautogui and pyperclip are required for KakaoTalk sending") from exc

        chat_name = config.kakao_chat_name.strip()
        chat_window = _find_or_open_kakao_chat_window(chat_name)
        _record_kakao_diagnostic(f"selected_window={_window_debug_summary(chat_window)}")
        # Strict mode: after a chat window is selected by exact title, never
        # recover from a missing input control by switching to another KakaoTalk
        # window. Sending to an arbitrary room is worse than failing.
        message_input = _focus_chat_message_input(chat_window)
        _record_kakao_diagnostic(f"message_input={_control_debug_summary(message_input)}")
        _remember_kakao_chat_window(chat_name, chat_window)

        # 기존 초안이 입력창에 남아 있으면 "기존문구 + 실적 메시지"가 함께 전송될 수
        # 있으므로, 붙여넣기 전에 입력창을 비우고 비워졌는지 확인한다. 전역 ctrl+a/
        # delete/ctrl+v/enter는 포커스가 빗나가면 다른 앱 입력을 지우거나 거기에
        # 붙여넣을 수 있으므로, 파괴적 키 입력 직전마다 대상 컨트롤 포커스를 재확인한다.
        _ensure_message_input_focus(message_input)
        _clear_message_input(message_input, pyautogui)

        _ensure_message_input_focus(message_input)
        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
        # 붙여넣은 값이 메시지와 "완전 동일"한지 검증한다. "포함" 검증은 기존 초안이
        # 남아 있어도 통과하므로, 잘못된 합쳐진 메시지 전송을 막지 못한다.
        _wait_for_message_input_equals(message_input, message)
        _ensure_message_input_focus(message_input)
        pyautogui.press("enter")
        _wait_for_message_input_to_clear(message_input)
    except KakaoSendError as exc:
        # Preserve the ``ambiguous`` flag through diagnostic re-wrapping so the UI
        # still knows an Enter-pressed-but-unconfirmed send must not be fast-retried.
        raise KakaoSendError(
            _error_with_kakao_diagnostics(exc, config),
            ambiguous=getattr(exc, "ambiguous", False),
        ) from exc


def _kakao_os_automation_lock_path() -> Path:
    return app_state_root() / "runtime" / "state" / "kakao_locks" / "kakao.os_automation.lock"


def _clear_message_input(control: object, pyautogui: object) -> None:
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    # 입력창이 빈 문자열과 "완전 동일"해질 때까지 기다린다(value == "").
    result = _wait_for_input_condition(control, "", should_contain=True, match_exact=True)
    if result is True:
        return
    if result is None:
        # 입력값을 읽을 수 없으면 비워졌는지 확인할 수 없다. 기존 초안이 남은 채로
        # 실적 메시지가 합쳐져 전송되는 것을 막기 위해 성공으로 처리하지 않는다.
        raise KakaoSendError(
            "카카오톡 입력창 내용을 읽을 수 없어 비우기 결과를 확인하지 못했습니다. "
            "기존 초안과 실적 메시지가 합쳐져 전송될 수 있어 중단합니다."
        )

    raise KakaoSendError(
        "카카오톡 입력창을 비우지 못했습니다. "
        "기존 초안이 남아 있어 실적 메시지와 함께 전송되는 것을 막기 위해 중단합니다."
    )


def _reset_kakao_diagnostics() -> None:
    _KAKAO_DIAGNOSTICS.clear()


def _record_kakao_diagnostic(message: str) -> None:
    _KAKAO_DIAGNOSTICS.append(message)
    if len(_KAKAO_DIAGNOSTICS) > 80:
        del _KAKAO_DIAGNOSTICS[0]


def _error_with_kakao_diagnostics(exc: KakaoSendError, config: AppConfig) -> str:
    log_path = _write_kakao_diagnostics(config)
    lines = "\n".join(f"- {line}" for line in _KAKAO_DIAGNOSTICS[-20:])
    message = f"{exc}\n\n[카카오톡 진단]\n{lines}"
    if log_path is not None:
        message = f"{message}\n진단 로그 파일: {log_path}"
    return message


def _write_kakao_diagnostics(config: AppConfig) -> object | None:
    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        path = config.log_dir / "kakao_diagnostics.log"
        # append 직전 크기 기준 rotation(무한 증가 방지, NFR-10). 이미 감싼 try/except로
        # best-effort 유지 — rotation 실패가 진단/전송 경로를 깨지 않는다.
        rotate_if_needed(path)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = "\n".join(f"- {line}" for line in _KAKAO_DIAGNOSTICS)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}]\n{body}\n\n")
        return path
    except Exception:
        return None


def _window_debug_summary(window: object) -> str:
    try:
        rect = str(window.rectangle())
    except Exception:
        rect = ""
    return (
        f"title={_window_title(window)!r}, "
        f"class={_window_class_name(window)!r}, "
        f"handle={_window_handle(window)}, "
        f"rect={rect!r}"
    )


def _control_debug_summary(control: object) -> str:
    info = getattr(control, "element_info", None)
    return (
        f"name={_control_text(control)!r}, "
        f"type={getattr(info, 'control_type', '')!r}, "
        f"class={_control_class_name(control)!r}"
    )


def _find_or_open_kakao_chat_window(chat_name: str) -> object:
    # Primary path: strict selection among already-open KakaoTalk chat windows.
    _record_kakao_diagnostic("lookup=strict_open_window")
    try:
        return _select_kakao_chat_window(chat_name)
    except KakaoUnsafeSelectionError:
        # Ambiguous target or unscannable desktop: do not run main-window search.
        # Automating the UI here could send to the wrong room or wrong app.
        raise
    except KakaoSendError as exc:
        _record_kakao_diagnostic(f"lookup_strict_error={exc}")

    # Compatibility fallback: only reached when no matching window was open yet.
    # Search from the KakaoTalk main window to open the room, then re-run strict
    # selection. The opened window's title is rechecked by
    # _select_kakao_chat_window, so this cannot select an unrelated room.
    _record_kakao_diagnostic("lookup=main_search_then_strict")
    _open_kakao_chat_window_from_main(chat_name)
    try:
        return _select_kakao_chat_window(chat_name)
    except KakaoSendError as exc:
        raise KakaoSendError(
            f"카카오톡 채팅방 '{chat_name}' 창을 안전하게 선택하지 못했습니다. "
            "해당 채팅방을 별도 창으로 열어둔 뒤 다시 실행하세요."
        ) from exc


def _select_kakao_chat_window(chat_name: str) -> object:
    """Return the single open KakaoTalk window whose title exactly matches.

    Scans open windows across both pywinauto backends, deduplicates by handle,
    and requires exactly one normalized-title match. Zero or multiple matches
    raise ``KakaoSendError`` with candidate titles in diagnostics. This path
    never falls back to an arbitrary message-input window.
    """

    target = _normalize_kakao_title(chat_name)
    if not target:
        raise KakaoSendError("KAKAO_CHAT_NAME is required before sending")

    windows = _list_kakao_windows()
    candidates: list[tuple[str, object]] = []
    rejected: list[str] = []
    for window in windows:
        title = _window_title(window)
        if _normalize_kakao_title(title) == target:
            candidates.append((title, window))
        else:
            rejected.append(title)

    _record_kakao_diagnostic(
        f"strict_candidates={[title for title, _ in candidates]}, rejected={rejected}"
    )

    if not candidates:
        raise KakaoSendError(
            f"카카오톡 채팅방 '{chat_name}' 창을 찾지 못했습니다. "
            "카카오톡에서 해당 채팅방을 더블클릭해 별도 창으로 열어둔 뒤 다시 실행하세요."
        )
    if len(candidates) > 1:
        raise KakaoUnsafeSelectionError(
            f"카카오톡 채팅방 '{chat_name}' 와 같은 이름의 창이 여러 개 열려 있습니다. "
            "잘못된 채팅방으로 전송하지 않도록 중복 창을 닫고 다시 실행하세요."
        )

    window = candidates[0][1]
    _record_kakao_diagnostic(f"strict_selected={_window_debug_summary(window)}")
    try:
        _bring_window_to_front(window)
    except KakaoSendError as exc:
        # The exact target window was found but could not be safely focused. Do
        # NOT let this fall through to the main-window search fallback: that path
        # automates ctrl+f / paste / Enter against whatever app is in foreground,
        # which could send to the wrong room or wrong app. Fail immediately as an
        # unsafe selection so the caller surfaces the error instead of searching.
        _record_kakao_diagnostic(f"strict_focus_failed={exc}")
        raise KakaoUnsafeSelectionError(
            f"카카오톡 채팅방 '{chat_name}' 창을 찾았지만 전면으로 가져오지 못했습니다. "
            "해당 채팅방 창을 직접 클릭해 활성화한 뒤 다시 실행하세요."
        ) from exc
    return window


def _list_kakao_windows() -> list[object]:
    windows: list[object] = []
    seen_handles: set[int] = set()
    scanned_any_backend = False
    for backend in ("uia", "win32"):
        try:
            backend_windows = _desktop_windows(backend)
        except Exception:
            _record_kakao_diagnostic(f"scan_backend={backend}, error=window_list_failed")
            continue
        scanned_any_backend = True
        _record_kakao_diagnostic(f"scan_backend={backend}, windows={len(backend_windows)}")
        for window in backend_windows:
            if not _is_kakao_chat_window(window):
                continue
            handle = _window_handle(window)
            if isinstance(handle, int):
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
            windows.append(window)

    if not scanned_any_backend:
        # Neither backend could be scanned (e.g. pywinauto missing). Returning an
        # empty list would let the caller fall through to main-window search,
        # which can paste/Enter into whatever app is currently in foreground.
        raise KakaoUnsafeSelectionError(
            "카카오톡 창 목록을 조회하지 못했습니다. "
            "pywinauto 설치와 카카오톡 PC 앱 실행 상태를 확인하세요."
        )
    return windows


def _desktop_windows(backend: str) -> list[object]:
    from pywinauto import Desktop

    return list(Desktop(backend=backend).windows())


def _is_kakao_chat_window(window: object) -> bool:
    """A KakaoTalk window that can be a chat room, not the main contact window."""

    if not _is_kakao_window(window):
        return False
    title = _window_title(window)
    return bool(title) and title not in {"카카오톡", "KakaoTalk"}


def _normalize_kakao_title(title: str) -> str:
    return (title or "").strip()


def _remember_kakao_chat_window(chat_name: str, window: object) -> None:
    handle = _window_handle(window)
    key = _normalize_kakao_title(chat_name)
    if key and isinstance(handle, int) and handle > 0:
        _LAST_KAKAO_CHAT_HANDLE_BY_CHAT[key] = handle
        _record_kakao_diagnostic(f"remembered_handle chat={key!r} handle={handle}")


def _focus_last_kakao_chat_window(chat_name: str) -> object:
    key = _normalize_kakao_title(chat_name)
    handle = _LAST_KAKAO_CHAT_HANDLE_BY_CHAT.get(key)
    if handle is None:
        raise KakaoSendError("remembered KakaoTalk chat window was not found")

    for backend in ("uia", "win32"):
        window = _window_from_handle(handle, backend)
        if window is None:
            continue
        try:
            if not _is_kakao_window(window):
                continue
            if _normalize_kakao_title(_window_title(window)) != key:
                continue
            _bring_window_to_front(window)
        except Exception:
            continue
        return window

    raise KakaoSendError("remembered KakaoTalk chat window was not found")


def _window_from_handle(handle: int, backend: str) -> object | None:
    try:
        from pywinauto import Desktop
    except ImportError:
        return None

    try:
        return Desktop(backend=backend).window(handle=handle)
    except Exception:
        return None


def _focus_kakao_message_window() -> object:
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise KakaoSendError("pywinauto is required for KakaoTalk sending") from exc

    fallback = None
    foreground_handle = _foreground_window_handle()
    for backend in ("uia", "win32"):
        try:
            windows = Desktop(backend=backend).windows()
        except Exception:
            _record_kakao_diagnostic(f"scan_backend={backend}, error=window_list_failed")
            continue
        _record_kakao_diagnostic(f"scan_backend={backend}, windows={len(windows)}")
        for window in windows:
            if not _is_kakao_window(window):
                continue
            candidates = _message_input_candidates(window)
            _record_kakao_diagnostic(
                f"candidate_window backend={backend}, {_window_debug_summary(window)}, input_count={len(candidates)}"
            )
            if not candidates:
                continue
            if _window_handle(window) == foreground_handle:
                _bring_window_to_front(window)
                return window
            fallback = fallback or window

    if fallback is not None:
        _bring_window_to_front(fallback)
        return fallback

    raise KakaoSendError(
        "카카오톡 메시지 입력창이 있는 채팅방 창을 찾지 못했습니다. "
        "대상 채팅방을 한 번 열어둔 뒤 다시 실행하세요."
    )


def _is_kakao_window(window: object) -> bool:
    title = _window_title(window)
    class_name = _window_class_name(window)
    return (
        "카카오톡" in title
        or "KakaoTalk" in title
        or class_name.startswith("EVA_Window")
    )


def _window_title(window: object) -> str:
    try:
        window_text = getattr(window, "window_text", None)
    except Exception:
        return ""
    if not callable(window_text):
        return ""
    try:
        return str(window_text()).strip()
    except Exception:
        return ""


def _window_class_name(window: object) -> str:
    try:
        element_info = getattr(window, "element_info", None)
        class_name = getattr(element_info, "class_name", "")
        return str(class_name or "").strip()
    except Exception:
        return ""


def _window_handle(window: object) -> int | None:
    try:
        handle = getattr(window, "handle", None)
    except Exception:
        return None
    return handle if isinstance(handle, int) else None


def _focus_kakao_window_candidates(windows: list[object]) -> object:
    candidates = _ordered_kakao_window_candidates(windows)
    if not candidates:
        raise KakaoSendError(
            "카카오톡 창을 찾지 못했습니다. 카카오톡 PC 앱을 실행하고 로그인한 뒤 다시 시도하세요."
        )

    last_error: Exception | None = None
    for window in candidates:
        try:
            _bring_window_to_front(window)
            return window
        except Exception as exc:
            last_error = exc

    raise KakaoSendError("카카오톡 창을 전면으로 가져오지 못했습니다. 카카오톡 창을 열어둔 뒤 다시 실행하세요.") from last_error


def _ordered_kakao_window_candidates(windows: list[object]) -> list[object]:
    predicates = [
        lambda window: _is_enabled_visible(window) and _has_kakao_title(window),
        lambda window: _is_enabled_visible(window) and _has_kakao_class(window),
        lambda window: _is_visible(window) and _has_kakao_title(window),
        lambda window: _is_visible(window) and _has_kakao_class(window),
        _has_kakao_title,
        _has_kakao_class,
    ]
    ordered = []
    seen_handles = set()
    for predicate in predicates:
        for window in windows:
            if not predicate(window):
                continue
            handle = _window_handle(window) or id(window)
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            ordered.append(window)
    return ordered


def _has_kakao_title(window: object) -> bool:
    title = _window_title(window)
    return "카카오톡" in title or "KakaoTalk" in title


def _has_kakao_class(window: object) -> bool:
    return _window_class_name(window).startswith("EVA_Window")


# ── KakaoTalk 로그인/세션 probe (heartbeat interactive_session_available 소스) ─────────
# 로그인되면 메인 연락처 창(title 정확히 ``카카오톡``/``KakaoTalk`` + class ``EVA_Window*``)이
# 보인다. 로그아웃이면 로그인 창만 떠 메인 연락처 창이 없다. 라이브 검증(pywinauto uia/win32)
# 결과 메인 창은 ``EVA_Window_Dblclk`` title ``카카오톡`` 로 관측됨.
#: 메인 연락처 창의 정확한 제목 — 채팅방/로그인 창과 구분하는 anchor.
_KAKAO_MAIN_WINDOW_TITLES = ("카카오톡", "KakaoTalk")
_KAKAO_LOGIN_WINDOW_TITLE_PARTS = ("로그인", "Login")


def _is_kakao_main_contact_window(window: object) -> bool:
    """로그인 시에만 존재하는 메인 연락처 창인가(가시 + EVA_Window* + 정확한 메인 제목)."""

    if not _is_visible(window):
        return False
    if not _window_class_name(window).startswith("EVA_Window"):
        return False
    return _window_title(window) in _KAKAO_MAIN_WINDOW_TITLES


def _is_kakao_login_window(window: object) -> bool:
    title = _window_title(window)
    return bool(title) and any(part in title for part in _KAKAO_LOGIN_WINDOW_TITLE_PARTS)


def _all_kakao_windows() -> list[object]:
    """양 backend(uia/win32)에서 KakaoTalk 로 보이는 모든 top-level 창(진단/probe 용).

    pywinauto 미설치나 양 backend 모두 조회 실패면 ``None`` 을 돌려준다 — 호출자가 "미상"
    으로 처리해 로그인 여부를 거짓으로 단정하지 않게 한다(false alarm 방지).
    """

    windows: list[object] = []
    scanned_any = False
    seen: set[int] = set()
    for backend in ("uia", "win32"):
        try:
            backend_windows = _desktop_windows(backend)
        except Exception:
            continue
        scanned_any = True
        for window in backend_windows:
            if not _is_kakao_window(window):
                continue
            handle = _window_handle(window)
            if isinstance(handle, int):
                if handle in seen:
                    continue
                seen.add(handle)
            windows.append(window)
    if not scanned_any:
        raise KakaoUnsafeSelectionError("카카오톡 창 목록을 조회하지 못했습니다.")
    return windows


def kakao_login_available(
    *, list_windows: Callable[[], list[object]] | None = None
) -> bool | None:
    """KakaoTalk PC 앱이 로그인되어 전송 가능한 상태인지 best-effort 로 판정한다.

    반환:
    * ``True``  — 로그인됨(메인 연락처 창이 보인다).
    * ``False`` — KakaoTalk 창이 없거나, 로그인 창만 보여 전송 준비가 안 됨.
    * ``None``  — 미상(pywinauto 미설치/조회 실패). 로그인 여부를 단정하지 않는다 —
      호출자는 이 신호를 생략한다(거짓 경보 금지).

    OS/창 식별자 raw 값은 반환하지 않는다(분류 결과 bool 만). 비-Windows 에서는 ``None``.
    """

    lister = list_windows if list_windows is not None else _all_kakao_windows
    try:
        windows = lister()
    except Exception:
        return None
    if windows is None:
        return None
    if not windows:
        # KakaoTalk 창이 하나도 안 보이면 앱 미실행/전송 불가 상태다.
        return False
    if any(_is_kakao_main_contact_window(window) for window in windows):
        return True
    if any(_window_title(window) in _KAKAO_MAIN_WINDOW_TITLES for window in windows):
        return False
    if any(_is_kakao_login_window(window) for window in windows):
        # KakaoTalk 로그인 창이 보이면 메인 연락처 창을 통한 전송 준비가 안 된 상태다.
        return False
    # 열린 채팅방만 보이는 경우는 로그인/전송 가능 여부를 단정하지 않는다.
    return None


def _is_visible(window: object) -> bool:
    try:
        is_visible = getattr(window, "is_visible", None)
    except Exception:
        return False
    if not callable(is_visible):
        return True
    try:
        return bool(is_visible())
    except Exception:
        return True


def _is_enabled_visible(window: object) -> bool:
    try:
        is_enabled = getattr(window, "is_enabled", None)
    except Exception:
        return False
    if not callable(is_enabled):
        return _is_visible(window)
    try:
        return bool(is_enabled()) and _is_visible(window)
    except Exception:
        return _is_visible(window)


def _open_kakao_chat_window_from_main(chat_name: str) -> None:
    try:
        import pyautogui
        import pyperclip
    except ImportError as exc:
        raise KakaoSendError("pyautogui and pyperclip are required for KakaoTalk sending") from exc

    # Ctrl+F search-then-Enter only opens a room when it runs against the
    # KakaoTalk main contact-list window. Focusing an arbitrary Kakao window
    # (e.g. an already-open chat room) would run an in-room search instead and
    # could leave the wrong window in front, so require the real main window.
    _focus_kakao_main_window()
    pyperclip.copy(chat_name)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.2)
    # 검색창에 이전 실행의 키워드가 남아 있으면 paste 가 그 뒤에 덧붙어 잘못된
    # 채팅방을 찾는다(예: "이수열" 뒤에 신규 키워드). paste 전에 기존 텍스트를
    # 선택해 덮어쓰게 한다. 카카오톡 검색창에서는 ctrl+a 가 '친구추가'로 가로채이므로
    # 전체 선택 단축키는 ctrl+shift+a 를 쓴다.
    pyautogui.hotkey("ctrl", "shift", "a")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(1.0)


def _focus_chat_message_input(window: object) -> object:
    for control in _message_input_candidates(window):
        control.click_input()
        time.sleep(0.1)
        return control

    raise KakaoSendError(
        "카카오톡 메시지 입력창을 찾지 못했습니다. "
        "대상 채팅방 창을 열고 입력창이 보이는 상태에서 다시 실행하세요."
    )


def _ensure_message_input_focus(control: object) -> None:
    """Re-assert and verify that ``control`` holds keyboard focus.

    전역 ctrl+a/delete/ctrl+v/enter 직전에 호출한다. 클릭으로 포커스를 다시 준 뒤
    실제로 대상 입력창이 포커스를 가졌는지 확인한다. 확인하지 못하면(포커스가 다른
    창/앱으로 빗나갔을 수 있으므로) 성공으로 진행하지 않고 실패 처리한다.
    """

    click_input = getattr(control, "click_input", None)
    if callable(click_input):
        try:
            click_input()
            time.sleep(0.1)
        except Exception as exc:
            raise KakaoSendError(
                "카카오톡 입력창에 포커스를 주지 못했습니다. "
                "대상 채팅방 창이 가려졌거나 닫혔을 수 있습니다."
            ) from exc

    focus_state = _control_has_keyboard_focus(control)
    _record_kakao_diagnostic(f"input_focus_check={focus_state}")
    # 안전 목적의 검사이므로 "포커스 있음(True)"이 확인될 때만 진행한다. False(빗나감)
    # 뿐 아니라 None(확인 불가)도 막는다. 포커스를 확인할 수 없는 컨트롤에서 ctrl+a/
    # delete/ctrl+v/enter를 그대로 보내면 다른 앱 입력을 지우거나 거기에 붙여넣을 수
    # 있기 때문이다.
    if focus_state is not True:
        raise KakaoSendError(
            "카카오톡 입력창이 포커스를 가졌는지 확인하지 못했습니다. "
            "포커스가 다른 창으로 빗나가면 다른 앱 입력을 지우거나 거기에 붙여넣을 수 "
            "있어 중단합니다."
        )


def _control_has_keyboard_focus(control: object) -> bool | None:
    """Return True/False if focus can be determined, else None (unknown).

    pywinauto UIA 컨트롤은 ``has_keyboard_focus()``를 제공한다. 없으면 컨트롤이 속한
    최상위 창 핸들이 현재 포그라운드 창과 같은지로 대체 확인한다. 둘 다 불가하면
    알 수 없음(None)을 반환한다. 호출부는 None을 안전을 위해 실패로 처리한다.
    """

    has_focus = getattr(control, "has_keyboard_focus", None)
    if callable(has_focus):
        try:
            return bool(has_focus())
        except Exception:
            pass

    top_handle = _control_top_level_handle(control)
    foreground_handle = _foreground_window_handle()
    if top_handle is not None and foreground_handle is not None:
        return top_handle == foreground_handle

    return None


def _control_top_level_handle(control: object) -> int | None:
    top_level = getattr(control, "top_level_parent", None)
    if callable(top_level):
        try:
            return _window_handle(top_level())
        except Exception:
            return None
    return None


def _message_input_candidates(window: object) -> list[object]:
    descendants = _message_input_descendants(window)
    candidates = []
    for control in descendants:
        control_type = getattr(getattr(control, "element_info", None), "control_type", "")
        name = _control_text(control)
        class_name = _control_class_name(control)
        if (
            control_type == "Document"
            and ("메시지 입력" in name or "message" in name.lower())
        ) or class_name in {"RICHEDIT50W", "Edit"}:
            candidates.append(control)

    if candidates:
        return candidates

    documents = [
        control
        for control in descendants
        if getattr(getattr(control, "element_info", None), "control_type", "") == "Document"
    ]
    return documents if len(documents) == 1 else []


def _message_input_descendants(window: object) -> list[object]:
    try:
        descendants = window.descendants(control_type="Document")
    except TypeError:
        return window.descendants()
    except Exception:
        descendants = []

    if descendants:
        _record_kakao_diagnostic(f"descendants=document,count={len(descendants)}")
        return descendants

    try:
        descendants = window.descendants()
        _record_kakao_diagnostic(f"descendants=all,count={len(descendants)}")
        return descendants
    except Exception:
        _record_kakao_diagnostic("descendants=all,error=failed")
        return []


def _control_text(control: object) -> str:
    text = ""
    window_text = getattr(control, "window_text", None)
    if callable(window_text):
        text = window_text() or ""

    element_name = getattr(getattr(control, "element_info", None), "name", "")
    return f"{text} {element_name}".strip()


def _control_class_name(control: object) -> str:
    return str(getattr(getattr(control, "element_info", None), "class_name", "") or "").strip()


def _wait_for_message_input_equals(control: object, message: str) -> None:
    # "포함"이 아니라 "완전 동일"을 요구한다. 기존 초안이 남아 있으면 합쳐진 값이
    # 메시지를 포함하더라도 완전 동일하지 않아 실패한다.
    result = _wait_for_input_condition(control, message, should_contain=True, match_exact=True)
    if result is True:
        return
    if result is None:
        # 붙여넣기 후 입력값을 읽을 수 없으면 성공처럼 진행하지 않는다. 읽기 실패를
        # 성공으로 처리하면 app.py가 마지막 해시를 기록해 재전송 기회를 잃는다.
        raise KakaoSendError(
            "카카오톡 입력창 내용을 읽을 수 없어 붙여넣기 결과를 확인하지 못했습니다. "
            "붙여넣기 실패나 포커스 이탈을 성공으로 처리하지 않습니다."
        )

    raise KakaoSendError(
        "카카오톡 입력창 내용이 보낼 메시지와 정확히 일치하지 않습니다. "
        "기존 초안이 남아 있거나 다른 창이 입력을 가로챘을 수 있습니다."
    )


def _wait_for_message_input_to_clear(control: object) -> None:
    # 전송 후 입력창이 "정확히 빈 문자열"인지 확인한다. "전체 메시지를 포함하지
    # 않는지"만 보면 'hello' 전송 후 'hell' 같은 잔여 텍스트가 남아도 통과해, 실제로는
    # 전송이 안 됐는데 성공으로 처리될 수 있다. 그러면 마지막 해시가 기록되어 재전송
    # 기회를 잃는다.
    result = _wait_for_input_condition(control, "", should_contain=True, match_exact=True)
    if result is True:
        return
    if result is None:
        # 전송 후 입력값을 읽을 수 없으면 전송 성공 여부를 확인할 수 없다. 읽기 실패를
        # 성공으로 처리하면 마지막 해시가 기록되어 재전송 기회가 사라지므로 실패 처리한다.
        # 단, Enter는 이미 눌렀으므로 실제로 전송됐을 수 있다(ambiguous). 5초 후 빠른
        # 재시도로 같은 메시지를 또 보내지 않도록 ambiguous로 표시한다.
        raise KakaoSendError(
            "카카오톡 입력창 내용을 읽을 수 없어 전송 결과를 확인하지 못했습니다. "
            "전송 성공으로 처리하지 않습니다.",
            ambiguous=True,
        )

    raise KakaoSendError(
        "카카오톡 메시지가 전송되지 않았습니다. "
        "입력창이 비워지지 않아(잔여 텍스트) 성공으로 처리하지 않았습니다."
    )


def _wait_for_input_condition(
    control: object,
    message: str,
    *,
    should_contain: bool,
    match_exact: bool = False,
) -> bool | None:
    deadline = time.monotonic() + KAKAO_SEND_VERIFY_TIMEOUT_SECONDS
    unreadable = False
    while True:
        matched = _message_input_matches(control, message, match_exact=match_exact)
        if matched is None:
            unreadable = True
        elif matched == should_contain:
            return True
        if time.monotonic() >= deadline:
            return None if unreadable else False
        time.sleep(KAKAO_SEND_VERIFY_INTERVAL_SECONDS)


def _message_input_matches(control: object, message: str, *, match_exact: bool) -> bool | None:
    value = _message_input_value(control)
    if value is None:
        return None

    normalized_value = _normalize_message_input_value(control, value)
    normalized_message = _normalize_input_text(message)
    if match_exact:
        exact = normalized_value == normalized_message
        prefix = _is_long_message_prefix(normalized_value, normalized_message)
        ordered = _has_ordered_message_lines(normalized_value, normalized_message)
        markers = _has_bot_message_markers(normalized_value, normalized_message)
        if not (exact or prefix or ordered or markers):
            # 어떤 매처도 통과하지 못한 첫 사례를 진단에 남긴다. 값/메시지 길이와 각
            # 매처 결과가 있으면 80자 절단 추측 없이 실패 원인을 확정할 수 있다.
            _record_kakao_diagnostic(
                f"match_exact_failed exact={exact} prefix={prefix} "
                f"ordered={ordered} markers={markers} "
                f"value_len={len(normalized_value)} message_len={len(normalized_message)}"
            )
            _record_kakao_diagnostic(f"match_target_message={_diagnostic_text_sample(normalized_message)}")
        return exact or prefix or ordered or markers
    if not normalized_value:
        return False
    return normalized_message in normalized_value


def _message_input_value(control: object) -> str | None:
    legacy_properties = getattr(control, "legacy_properties", None)
    if callable(legacy_properties):
        try:
            properties = legacy_properties()
            if "Value" in properties:
                value = str(properties.get("Value", ""))
                _record_kakao_diagnostic(f"input_value={_diagnostic_text_sample(value)}")
                return value
        except Exception:
            pass

    get_value = getattr(control, "get_value", None)
    if callable(get_value):
        try:
            value = str(get_value())
            _record_kakao_diagnostic(f"input_value={_diagnostic_text_sample(value)}")
            return value
        except Exception:
            pass

    return None


def _normalize_message_input_value(control: object, value: str) -> str:
    normalized_value = _normalize_input_text(value)
    if _is_empty_message_input_placeholder(control, normalized_value):
        return ""
    return normalized_value


def _is_long_message_prefix(normalized_value: str, normalized_message: str) -> bool:
    if len(normalized_value) < KAKAO_TRUNCATED_PREFIX_MIN_CHARS:
        return False
    if len(normalized_value) >= len(normalized_message):
        return False
    matched = normalized_message.startswith(normalized_value)
    if matched:
        _record_kakao_diagnostic("input_value_matches_message_prefix=True")
    return matched


def _has_ordered_message_lines(normalized_value: str, normalized_message: str) -> bool:
    if len(normalized_value) < KAKAO_TRUNCATED_PREFIX_MIN_CHARS:
        return False

    value_lines = _meaningful_message_lines(normalized_value)
    message_lines = _meaningful_message_lines(normalized_message)
    if len(value_lines) < KAKAO_ORDERED_MATCH_MIN_LINES:
        return False

    message_index = 0
    matched = 0
    for value_line in value_lines:
        while message_index < len(message_lines) and message_lines[message_index] != value_line:
            message_index += 1
        if message_index >= len(message_lines):
            return False
        matched += 1
        message_index += 1

    result = matched >= KAKAO_ORDERED_MATCH_MIN_LINES
    if result:
        _record_kakao_diagnostic(f"input_value_ordered_line_matches={matched}")
    return result


def _has_bot_message_markers(normalized_value: str, normalized_message: str) -> bool:
    compact_value = _compact_for_marker_match(normalized_value)
    compact_message = _compact_for_marker_match(normalized_message)
    required_markers = [
        "[실시간실적봇]",
        "[크롤링",
        "기준",
    ]
    if not all(marker in compact_message and marker in compact_value for marker in required_markers):
        return False

    if "아침:" not in compact_value and "배정" not in compact_value:
        return False

    _record_kakao_diagnostic("input_value_bot_markers=True")
    return True


def _compact_for_marker_match(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _meaningful_message_lines(value: str) -> list[str]:
    ignored = {"", "..."}
    return [line.strip() for line in value.split("\n") if line.strip() not in ignored]


_KAKAO_INPUT_PLACEHOLDERS = {
    "메시지 입력",
    "메시지 입력 RichEdit Control",
    "메시지를 입력하세요",
    "RichEdit Control",
}


def _is_empty_message_input_placeholder(control: object, normalized_value: str) -> bool:
    if not normalized_value:
        return False

    # 빈 RichEdit 입력창은 안내 문구("메시지 입력" 등)를 노출한다. 입력값이 그 고정
    # 안내 문구와 같을 때만 비어 있는 것으로 본다.
    #
    # 중요: RICHEDIT50W는 비어 있지 않을 때 window_text()/element_info.name이 "현재 입력된
    # 본문"을 그대로 돌려준다. 예전 코드는 그 값을 placeholder 집합에 넣어 비교했기 때문에,
    # 실제 본문이 입력된 경우에도 "값 == 자기 자신"이 성립해 빈 placeholder로 잘못 판정했다.
    # 그러면 실적 메시지가 통째로 빈 문자열로 치환되어 모든 일치 검사가 실패했다(전송 차단).
    # 그래서 컨트롤이 동적으로 돌려주는 name/window_text는 더 이상 placeholder로 쓰지 않고,
    # 아래 고정 안내 문구 집합만 사용한다.
    normalized_placeholders = {
        _normalize_input_text(value) for value in _KAKAO_INPUT_PLACEHOLDERS
    }
    return normalized_value in normalized_placeholders


def _diagnostic_text_sample(value: str) -> str:
    normalized = _normalize_input_text(value)
    if not normalized:
        return "''"
    # 매처가 실패하는 원인을 진단하려면 80자 절단본이 아니라 값 전체가 필요하다.
    # 입력창 한 건은 길어야 수백 자라 로그가 과도하게 커지지 않는다.
    if len(normalized) > 400:
        normalized = f"{normalized[:397]}..."
    return repr(normalized)


def _normalize_input_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _focus_kakaotalk_window() -> object:
    try:
        from pywinauto import Desktop
    except ImportError:
        return

    windows = []
    for backend in ("uia", "win32"):
        try:
            windows.extend(Desktop(backend=backend).windows())
        except Exception:
            pass
    return _focus_kakao_window_candidates(windows)


def _focus_kakao_main_window() -> object:
    """Focus the KakaoTalk main contact-list window, or fail safely.

    The main-window search fallback drives Ctrl+F over whatever window is in
    front, which only opens a room from the main contact list. Selecting an
    arbitrary Kakao window (a chat room, a settings dialog) could trigger an
    in-room search or focus an unrelated window. So scan for windows whose
    normalized title is exactly the KakaoTalk main title and require one.
    """

    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise KakaoSendError("pywinauto is required for KakaoTalk sending") from exc

    candidates: list[object] = []
    seen_handles: set[int] = set()
    scanned_any_backend = False
    for backend in ("uia", "win32"):
        try:
            windows = Desktop(backend=backend).windows()
        except Exception:
            _record_kakao_diagnostic(f"main_scan_backend={backend}, error=window_list_failed")
            continue
        scanned_any_backend = True
        for window in windows:
            if not _is_kakao_main_window(window):
                continue
            handle = _window_handle(window)
            if isinstance(handle, int):
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
            candidates.append(window)

    if not scanned_any_backend:
        raise KakaoUnsafeSelectionError(
            "카카오톡 창 목록을 조회하지 못했습니다. "
            "pywinauto 설치와 카카오톡 PC 앱 실행 상태를 확인하세요."
        )

    _record_kakao_diagnostic(
        f"main_candidates={[_window_title(window) for window in candidates]}"
    )
    if not candidates:
        raise KakaoSendError(
            "카카오톡 메인 창을 찾지 못했습니다. "
            "대상 채팅방을 별도 창으로 열어두거나 카카오톡 메인 창을 띄운 뒤 다시 실행하세요."
        )

    window = candidates[0]
    _bring_window_to_front(window)
    return window


def _is_kakao_main_window(window: object) -> bool:
    """The KakaoTalk main contact-list window, not a chat room or dialog."""

    if not _is_kakao_window(window):
        return False
    return _normalize_kakao_title(_window_title(window)) in {"카카오톡", "KakaoTalk"}


def _bring_window_to_front(window: object) -> None:
    restore = getattr(window, "restore", None)
    if callable(restore):
        try:
            restore()
        except Exception:
            pass

    set_focus = getattr(window, "set_focus", None)
    if callable(set_focus):
        try:
            set_focus()
        except Exception:
            pass
    time.sleep(0.2)
    if _is_kakaotalk_foreground(window):
        return

    # ``set_focus()``는 백그라운드 스레드에서 호출되거나(스케줄러 스레드) UIA 백엔드로
    # 잡힌 창이면 OS 포그라운드를 바꾸지 못하고 조용히 무시될 수 있다(UIA의 SetFocus는
    # 요소 키보드 포커스만 바꾸고, 백그라운드 프로세스의 SetForegroundWindow는 Windows
    # 포그라운드 잠금에 막힌다). 다른 창이 위를 덮고 있어도 동작하는, 화면 좌표에
    # 의존하지 않는 Win32 강제 전면 전환을 먼저 시도한다(여러 탭/창이 떠 있을 때 특정
    # 채팅방만 계속 전면 전환에 실패하던 원인).
    handle = _window_handle(window)
    if handle is not None and _force_foreground_win32(handle):
        time.sleep(0.2)
        if _is_kakaotalk_foreground(window):
            return

    # 마지막 수단: 타이틀바 클릭. 위 두 방법이 막혔을 때만 쓰며, 클릭 지점이 다른 창에
    # 가려져 있으면 실패할 수 있어 더 이상 1차 수단으로 의존하지 않는다.
    _click_window_title_bar(window)
    time.sleep(0.2)
    if _is_kakaotalk_foreground(window):
        return

    raise KakaoSendError("카카오톡 창을 전면으로 가져오지 못했습니다. 카카오톡 창을 열어둔 뒤 다시 실행하세요.")


def _force_foreground_win32(handle: int) -> bool:
    """Force ``handle`` to the OS foreground without depending on screen pixels.

    백그라운드 스레드에서 ``SetForegroundWindow``는 Windows 포그라운드 잠금에 막혀
    무시되기 쉽다. 현재 포그라운드 창의 입력 스레드와 대상 창의 입력 스레드에
    ``AttachThreadInput``으로 붙으면 그 잠금을 우회할 수 있다. 타이틀바 클릭과 달리
    화면 좌표에 의존하지 않으므로, 다른 창이 위를 덮고 있어도 전면 전환이 된다.
    실패하거나 Win32 API를 쓸 수 없으면 ``False``를 돌려 호출부가 다음 수단으로 넘어간다.
    """

    try:
        import ctypes
    except Exception:
        return False

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return False

    SW_RESTORE = 9
    SW_SHOW = 5
    try:
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, SW_RESTORE)

        current_thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        target_thread = user32.GetWindowThreadProcessId(handle, None)

        attached_foreground = bool(
            foreground_thread
            and foreground_thread != current_thread
            and user32.AttachThreadInput(current_thread, foreground_thread, True)
        )
        attached_target = bool(
            target_thread
            and target_thread != current_thread
            and user32.AttachThreadInput(current_thread, target_thread, True)
        )
        try:
            user32.BringWindowToTop(handle)
            user32.ShowWindow(handle, SW_SHOW)
            user32.SetForegroundWindow(handle)
        finally:
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
        return True
    except Exception:
        return False


def _is_kakaotalk_foreground(window: object) -> bool:
    window_handle = _window_handle(window)
    foreground_handle = _foreground_window_handle()
    if window_handle is not None and foreground_handle is not None:
        return window_handle == foreground_handle

    title = _foreground_window_title()
    expected_title = ""
    window_text = getattr(window, "window_text", None)
    if callable(window_text):
        expected_title = window_text().strip()

    if expected_title and (title == expected_title or expected_title in title):
        return True

    return False


def _foreground_window_handle() -> int | None:
    try:
        import win32gui
    except ImportError:
        return None

    handle = win32gui.GetForegroundWindow()
    return handle or None


def _foreground_window_title() -> str:
    try:
        import win32gui
    except ImportError:
        return ""
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def _click_window_title_bar(window: object) -> None:
    from pywinauto import mouse

    rect = window.rectangle()
    mouse.click(button="left", coords=((rect.left + rect.right) // 2, rect.top + 15))
