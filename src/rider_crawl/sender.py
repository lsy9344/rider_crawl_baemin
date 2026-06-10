from __future__ import annotations

import json
import platform
import time
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen as default_urlopen

from .config import AppConfig

KAKAO_SEND_VERIFY_TIMEOUT_SECONDS = 2.0
KAKAO_SEND_VERIFY_INTERVAL_SECONDS = 0.1
_LAST_KAKAO_CHAT_HANDLE_BY_CHAT: dict[str, int] = {}
_KAKAO_DIAGNOSTICS: list[str] = []


class KakaoSendError(RuntimeError):
    """Raised when KakaoTalk text delivery cannot be attempted safely."""


class KakaoUnsafeSelectionError(KakaoSendError):
    """Raised when the target chat window is ambiguous or cannot be scanned.

    These conditions must fail immediately. Falling back to the KakaoTalk
    main-window search would automate the UI (ctrl+f / paste / Enter) while the
    real target is already known to be unsafe, risking a send to the wrong room
    or even the wrong application.
    """


class TelegramSendError(RuntimeError):
    """Raised when Telegram Bot API delivery cannot be attempted safely."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


UrlOpen = Callable[..., Any]


def send_telegram_text(
    config: AppConfig,
    message: str,
    *,
    message_thread_id: int | None = None,
    urlopen: UrlOpen = default_urlopen,
    timeout_seconds: int = 10,
    retry_attempts: int = 3,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    _required_telegram_bot_token(config)
    payload: dict[str, object] = {
        "chat_id": _required_telegram_chat_id(config),
        "text": message,
        "disable_web_page_preview": "true",
    }
    target_thread_id = message_thread_id
    if target_thread_id is None:
        target_thread_id = _optional_telegram_message_thread_id(config)
    if target_thread_id is not None:
        payload["message_thread_id"] = target_thread_id

    attempts = max(1, retry_attempts)
    for attempt in range(attempts):
        try:
            _telegram_api_request(
                config,
                "sendMessage",
                payload,
                urlopen=urlopen,
                timeout_seconds=timeout_seconds,
            )
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
    config: AppConfig,
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
        raise TelegramSendError(f"Telegram Bot API request failed: {method}") from exc

    try:
        with response:
            body = response.read().decode("utf-8")
    except Exception as exc:
        raise TelegramSendError(f"Telegram Bot API response could not be read: {method}") from exc

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

    description = data.get("description") if isinstance(data, dict) else body
    retry_after = _telegram_retry_after_seconds(data)
    retryable = status_code == 429 or retry_after is not None or data.get("error_code") == 429
    return TelegramSendError(
        f"Telegram Bot API error: {description}",
        retryable=retryable,
        retry_after_seconds=retry_after,
    )


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


def _required_telegram_bot_token(config: AppConfig) -> str:
    token = config.telegram_bot_token.strip()
    if not token:
        raise TelegramSendError("TELEGRAM_BOT_TOKEN is required before sending")
    return token


def _required_telegram_chat_id(config: AppConfig) -> str:
    chat_id = config.telegram_chat_id.strip()
    if not chat_id:
        raise TelegramSendError("TELEGRAM_CHAT_ID is required before sending")
    return chat_id


def _optional_telegram_message_thread_id(config: AppConfig) -> int | None:
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

        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
        _wait_for_message_input_contains(message_input, message)
        pyautogui.press("enter")
        _wait_for_message_input_to_clear(message_input, message)
    except KakaoSendError as exc:
        raise KakaoSendError(_error_with_kakao_diagnostics(exc, config)) from exc


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
    _bring_window_to_front(window)
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
    pyautogui.hotkey("ctrl", "a")
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


def _wait_for_message_input_contains(control: object, message: str) -> None:
    result = _wait_for_input_condition(control, message, should_contain=True)
    if result is True:
        return
    if result is None:
        _record_kakao_diagnostic("input_value_unreadable_after_paste=continue_to_enter")
        return

    raise KakaoSendError(
        "카카오톡 입력창에 메시지가 붙여넣어지지 않았습니다. "
        "다른 창이 입력을 가로챘거나 카카오톡 입력창 포커스가 풀렸습니다."
    )


def _wait_for_message_input_to_clear(control: object, message: str) -> None:
    result = _wait_for_input_condition(control, message, should_contain=False)
    if result is True:
        return
    if result is None:
        _record_kakao_diagnostic("input_value_unreadable_after_enter=skip_clear_check")
        return

    raise KakaoSendError(
        "카카오톡 메시지가 전송되지 않았습니다. "
        "입력창에 메시지가 남아 있어 성공으로 처리하지 않았습니다."
    )


def _wait_for_input_condition(control: object, message: str, *, should_contain: bool) -> bool | None:
    deadline = time.monotonic() + KAKAO_SEND_VERIFY_TIMEOUT_SECONDS
    unreadable = False
    while True:
        contains = _message_input_contains(control, message)
        if contains is None:
            unreadable = True
        elif contains == should_contain:
            return True
        if time.monotonic() >= deadline:
            return None if unreadable else False
        time.sleep(KAKAO_SEND_VERIFY_INTERVAL_SECONDS)


def _message_input_contains(control: object, message: str) -> bool | None:
    value = _message_input_value(control)
    if value is None:
        return None
    if not value:
        return False

    return _normalize_input_text(message) in _normalize_input_text(value)


def _message_input_value(control: object) -> str | None:
    legacy_properties = getattr(control, "legacy_properties", None)
    if callable(legacy_properties):
        try:
            properties = legacy_properties()
            if "Value" in properties:
                return str(properties.get("Value", ""))
        except Exception:
            pass

    get_value = getattr(control, "get_value", None)
    if callable(get_value):
        try:
            return str(get_value())
        except Exception:
            pass

    return None


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

    _click_window_title_bar(window)
    time.sleep(0.2)
    if _is_kakaotalk_foreground(window):
        return

    raise KakaoSendError("카카오톡 창을 전면으로 가져오지 못했습니다. 카카오톡 창을 열어둔 뒤 다시 실행하세요.")


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
