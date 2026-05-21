from __future__ import annotations

import platform
import time

from .config import AppConfig


class KakaoSendError(RuntimeError):
    """Raised when KakaoTalk text delivery cannot be attempted safely."""


def send_kakao_text(
    config: AppConfig,
    message: str,
    *,
    platform_name: str | None = None,
) -> None:
    if not config.kakao_chat_name.strip():
        raise KakaoSendError("KAKAO_CHAT_NAME is required before sending")

    current_platform = platform_name or platform.system()
    if current_platform != "Windows":
        raise KakaoSendError("KakaoTalk UI sending is only supported on Windows")

    try:
        import pyautogui
        import pyperclip
    except ImportError as exc:
        raise KakaoSendError("pyautogui and pyperclip are required for KakaoTalk sending") from exc

    _focus_kakaotalk_window()

    pyperclip.copy(config.kakao_chat_name)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(0.5)

    pyperclip.copy(message)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    pyautogui.press("enter")


def _focus_kakaotalk_window() -> None:
    try:
        from pywinauto import Desktop
    except ImportError:
        return

    windows = Desktop(backend="uia").windows()
    for window in windows:
        title = window.window_text()
        if "카카오톡" in title or "KakaoTalk" in title:
            window.set_focus()
            return

    raise KakaoSendError("KakaoTalk window was not found")
