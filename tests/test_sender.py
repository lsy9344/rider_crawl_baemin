import sys

import pytest

from rider_crawl import sender as sender_module
from rider_crawl.config import AppConfig
from rider_crawl.sender import KakaoSendError, KakaoUnsafeSelectionError, send_kakao_text


def test_send_kakao_text_requires_chat_name(tmp_path):
    config = _config(tmp_path, chat_name="")

    with pytest.raises(KakaoSendError, match="KAKAO_CHAT_NAME"):
        send_kakao_text(config, "hello")


def test_send_kakao_text_refuses_non_windows_runtime(tmp_path):
    config = _config(tmp_path, chat_name="실적봇_의정부남부")

    with pytest.raises(KakaoSendError, match="Windows"):
        send_kakao_text(config, "hello", platform_name="Darwin")


def test_focus_last_kakao_chat_window_rejects_different_chat_name(monkeypatch):
    sender_module._LAST_KAKAO_CHAT_HANDLE_BY_CHAT = {"실적봇_B": 123}
    monkeypatch.setattr(sender_module, "_window_from_handle", lambda _handle, _backend: _FakeWindow("실적봇_A"))
    monkeypatch.setattr(sender_module, "_is_kakao_window", lambda _window: True)
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    try:
        with pytest.raises(KakaoSendError, match="remembered KakaoTalk chat window"):
            sender_module._focus_last_kakao_chat_window("실적봇_B")
    finally:
        sender_module._LAST_KAKAO_CHAT_HANDLE_BY_CHAT = {}


def test_strict_selection_picks_exact_title_among_open_rooms(monkeypatch):
    windows = [
        _FakeKakaoWindow("실적봇_A", handle=11, with_input=True),
        _FakeKakaoWindow("실적봇_B", handle=22, with_input=True),
        _FakeKakaoWindow("실적봇_C", handle=33, with_input=True),
    ]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    selected = sender_module._select_kakao_chat_window("실적봇_B")

    assert sender_module._window_handle(selected) == 22


def test_strict_selection_does_not_collide_on_similar_titles(monkeypatch):
    windows = [
        _FakeKakaoWindow("실적봇_A", handle=11, with_input=True),
        _FakeKakaoWindow("실적봇_A_테스트", handle=22, with_input=True),
    ]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    # Exact-title match must pick only the exact room, never the longer one.
    assert sender_module._window_handle(sender_module._select_kakao_chat_window("실적봇_A")) == 11
    assert sender_module._window_handle(sender_module._select_kakao_chat_window("실적봇_A_테스트")) == 22
    # A bare partial-only request matches no exact window and must not silently
    # pick a longer room.
    with pytest.raises(KakaoSendError):
        sender_module._select_kakao_chat_window("실적봇")


def test_strict_selection_no_match_does_not_fall_back_to_message_window(monkeypatch):
    windows = [
        _FakeKakaoWindow("실적봇_A", handle=11, with_input=True),
        _FakeKakaoWindow("실적봇_B", handle=22, with_input=True),
    ]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    fallback_called = []
    monkeypatch.setattr(
        sender_module,
        "_focus_kakao_message_window",
        lambda: fallback_called.append(True),
    )

    with pytest.raises(KakaoSendError):
        sender_module._select_kakao_chat_window("실적봇_없음")
    assert fallback_called == []


def test_send_kakao_text_does_not_fall_back_when_selected_window_lacks_input(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    selected = _FakeKakaoWindow("실적봇_A", handle=11, with_input=False)
    monkeypatch.setattr(sender_module, "_select_kakao_chat_window", lambda _chat: selected)

    fallback_called = []
    monkeypatch.setattr(
        sender_module,
        "_focus_kakao_message_window",
        lambda: fallback_called.append(True),
    )
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))
    monkeypatch.setitem(sys.modules, "pyautogui", _FakePyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError):
        send_kakao_text(config, "hello")
    assert fallback_called == []


def test_list_kakao_windows_deduplicates_same_handle_across_backends(monkeypatch):
    uia_window = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)
    win32_window = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)

    def fake_desktop_windows(backend):
        return [uia_window] if backend == "uia" else [win32_window]

    monkeypatch.setattr(sender_module, "_desktop_windows", fake_desktop_windows)

    windows = sender_module._list_kakao_windows()

    assert len(windows) == 1
    assert sender_module._window_handle(windows[0]) == 11


def test_strict_selection_rejects_two_windows_with_same_exact_title(monkeypatch):
    windows = [
        _FakeKakaoWindow("실적봇_A", handle=11, with_input=True),
        _FakeKakaoWindow("실적봇_A", handle=22, with_input=True),
    ]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    with pytest.raises(KakaoUnsafeSelectionError):
        sender_module._select_kakao_chat_window("실적봇_A")


def test_send_does_not_run_main_search_when_exact_title_is_ambiguous(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    windows = [
        _FakeKakaoWindow("실적봇_A", handle=11, with_input=True),
        _FakeKakaoWindow("실적봇_A", handle=22, with_input=True),
    ]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))
    monkeypatch.setitem(sys.modules, "pyautogui", _FakePyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    main_search_called = []
    monkeypatch.setattr(
        sender_module,
        "_open_kakao_chat_window_from_main",
        lambda _chat: main_search_called.append(True),
    )

    with pytest.raises(KakaoSendError):
        send_kakao_text(config, "hello")
    assert main_search_called == []


def test_send_fails_when_no_backend_can_be_scanned(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")

    def fail_scan(_backend):
        raise ImportError("pywinauto missing")

    monkeypatch.setattr(sender_module, "_desktop_windows", fail_scan)
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))
    monkeypatch.setitem(sys.modules, "pyautogui", _FakePyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    main_search_called = []
    monkeypatch.setattr(
        sender_module,
        "_open_kakao_chat_window_from_main",
        lambda _chat: main_search_called.append(True),
    )

    with pytest.raises(KakaoSendError):
        send_kakao_text(config, "hello")
    assert main_search_called == []


def test_remembered_handle_is_not_reused_across_different_chat_names(monkeypatch):
    sender_module._LAST_KAKAO_CHAT_HANDLE_BY_CHAT = {"실적봇_A": 11}
    monkeypatch.setattr(sender_module, "_window_from_handle", lambda _handle, _backend: _FakeKakaoWindow("실적봇_A", handle=11, with_input=True))
    monkeypatch.setattr(sender_module, "_is_kakao_window", lambda _window: True)
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    try:
        with pytest.raises(KakaoSendError):
            sender_module._focus_last_kakao_chat_window("실적봇_B")
    finally:
        sender_module._LAST_KAKAO_CHAT_HANDLE_BY_CHAT = {}


def test_focus_kakao_main_window_picks_exact_main_title(monkeypatch):
    main_window = _FakeKakaoWindow("카카오톡", handle=1, with_input=False)
    chat_window = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)

    def fake_windows(backend):
        return [chat_window, main_window]

    _patch_desktop(monkeypatch, fake_windows)
    brought = []
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda window: brought.append(window))

    selected = sender_module._focus_kakao_main_window()

    assert sender_module._window_handle(selected) == 1
    assert brought == [main_window]


def test_focus_kakao_main_window_fails_when_only_chat_rooms_open(monkeypatch):
    chat_window = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)
    _patch_desktop(monkeypatch, lambda backend: [chat_window])
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)

    with pytest.raises(KakaoSendError, match="메인 창"):
        sender_module._focus_kakao_main_window()


def test_focus_kakao_main_window_unsafe_when_no_backend_scannable(monkeypatch):
    def fail_windows(backend):
        raise RuntimeError("scan failed")

    _patch_desktop(monkeypatch, fail_windows)

    with pytest.raises(KakaoUnsafeSelectionError):
        sender_module._focus_kakao_main_window()


def test_open_from_main_focuses_main_window_not_arbitrary_kakao(monkeypatch):
    chat_window = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)
    main_window = _FakeKakaoWindow("카카오톡", handle=1, with_input=False)
    _patch_desktop(monkeypatch, lambda backend: [chat_window, main_window])

    focused = []
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda window: focused.append(window))
    monkeypatch.setattr(sender_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(sys.modules, "pyautogui", _FakePyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    sender_module._open_kakao_chat_window_from_main("실적봇_A")

    assert focused == [main_window]


def _patch_desktop(monkeypatch, windows_for_backend):
    class _FakeDesktop:
        def __init__(self, backend):
            self._backend = backend

        def windows(self):
            return windows_for_backend(self._backend)

    fake_pywinauto = type(sys)("pywinauto")
    fake_pywinauto.Desktop = _FakeDesktop
    monkeypatch.setitem(sys.modules, "pywinauto", fake_pywinauto)


def _config(tmp_path, *, chat_name: str) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser",
        headless=False,
        kakao_chat_name=chat_name,
        log_dir=tmp_path / "logs",
        send_enabled=True,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
    )


class _FakeWindow:
    def __init__(self, title: str) -> None:
        self.title = title

    def window_text(self) -> str:
        return self.title


class _FakeElementInfo:
    def __init__(self, control_type: str = "", class_name: str = "", name: str = "") -> None:
        self.control_type = control_type
        self.class_name = class_name
        self.name = name


class _FakeControl:
    def __init__(self, control_type: str, class_name: str, name: str) -> None:
        self.element_info = _FakeElementInfo(control_type=control_type, class_name=class_name, name=name)
        self.click_count = 0

    def window_text(self) -> str:
        return ""

    def click_input(self) -> None:
        self.click_count += 1


class _FakeKakaoWindow:
    def __init__(self, title: str, *, handle: int, with_input: bool) -> None:
        self.title = title
        self.handle = handle
        self.element_info = _FakeElementInfo(class_name="EVA_Window_Dblclk")
        self._inputs = (
            [_FakeControl("Edit", "RICHEDIT50W", "메시지 입력")] if with_input else []
        )

    def window_text(self) -> str:
        return self.title

    def descendants(self, control_type: str | None = None):
        if control_type in (None, "Document", "Edit"):
            return list(self._inputs)
        return []

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def restore(self) -> None:
        pass

    def set_focus(self) -> None:
        pass

    def rectangle(self):
        return _FakeRect()


class _FakeRect:
    left = 0
    top = 0
    right = 100
    bottom = 100


class _FakePlatform:
    def __init__(self, system_name: str) -> None:
        self._system_name = system_name

    def system(self) -> str:
        return self._system_name


class _FakePyAutoGui:
    def hotkey(self, *args) -> None:
        pass

    def press(self, *args) -> None:
        pass


class _FakePyperclip:
    def copy(self, _value) -> None:
        pass
