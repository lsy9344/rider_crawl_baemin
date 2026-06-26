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


class _FocusFakeWindow:
    def __init__(self, handle: int = 4242) -> None:
        self.handle = handle
        self.restored = False
        self.focused = False

    def restore(self) -> None:
        self.restored = True

    def set_focus(self) -> None:
        self.focused = True


def test_bring_window_to_front_uses_win32_force_before_title_bar_click(monkeypatch):
    # set_focus()가 전면 전환에 실패해도, 화면 좌표 클릭에 의존하지 않는 Win32 강제
    # 전면 전환이 성공하면 타이틀바 클릭으로 떨어지지 않는다(여러 창이 떠 있을 때
    # 클릭 지점이 가려져 실패하던 문제 방지).
    window = _FocusFakeWindow()
    state = {"foreground": False}
    monkeypatch.setattr(sender_module, "_is_kakaotalk_foreground", lambda _w: state["foreground"])
    forced: list[int] = []

    def fake_force(handle):
        forced.append(handle)
        state["foreground"] = True
        return True

    monkeypatch.setattr(sender_module, "_force_foreground_win32", fake_force)
    clicked: list[object] = []
    monkeypatch.setattr(sender_module, "_click_window_title_bar", lambda w: clicked.append(w))
    monkeypatch.setattr(sender_module.time, "sleep", lambda _s: None)

    sender_module._bring_window_to_front(window)

    assert forced == [4242]
    assert clicked == []


def test_bring_window_to_front_falls_back_to_click_when_win32_force_does_not_take(monkeypatch):
    window = _FocusFakeWindow()
    fg_results = iter([False, False, True])  # check1, check2(after win32), check3(after click)
    monkeypatch.setattr(sender_module, "_is_kakaotalk_foreground", lambda _w: next(fg_results))
    monkeypatch.setattr(sender_module, "_force_foreground_win32", lambda _h: True)
    clicked: list[object] = []
    monkeypatch.setattr(sender_module, "_click_window_title_bar", lambda w: clicked.append(w))
    monkeypatch.setattr(sender_module.time, "sleep", lambda _s: None)

    sender_module._bring_window_to_front(window)

    assert clicked == [window]


def test_bring_window_to_front_raises_when_all_focus_methods_fail(monkeypatch):
    window = _FocusFakeWindow()
    monkeypatch.setattr(sender_module, "_is_kakaotalk_foreground", lambda _w: False)
    monkeypatch.setattr(sender_module, "_force_foreground_win32", lambda _h: False)
    monkeypatch.setattr(sender_module, "_click_window_title_bar", lambda _w: None)
    monkeypatch.setattr(sender_module.time, "sleep", lambda _s: None)

    with pytest.raises(KakaoSendError, match="전면으로 가져오지"):
        sender_module._bring_window_to_front(window)


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


def test_strict_selection_raises_unsafe_when_exact_window_focus_fails(monkeypatch):
    # 정확한 창은 찾았지만 포커스를 안전하게 가져오지 못한 경우, 메인창 검색
    # fallback으로 넘어가지 않고 KakaoUnsafeSelectionError로 즉시 실패해야 한다.
    windows = [_FakeKakaoWindow("실적봇_A", handle=11, with_input=True)]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))

    def fail_focus(_window):
        raise KakaoSendError("카카오톡 창을 전면으로 가져오지 못했습니다.")

    monkeypatch.setattr(sender_module, "_bring_window_to_front", fail_focus)

    with pytest.raises(KakaoUnsafeSelectionError):
        sender_module._select_kakao_chat_window("실적봇_A")


def test_send_does_not_run_main_search_when_exact_window_focus_fails(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    windows = [_FakeKakaoWindow("실적봇_A", handle=11, with_input=True)]
    monkeypatch.setattr(sender_module, "_list_kakao_windows", lambda: list(windows))
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))
    monkeypatch.setitem(sys.modules, "pyautogui", _FakePyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    def fail_focus(_window):
        raise KakaoSendError("카카오톡 창을 전면으로 가져오지 못했습니다.")

    monkeypatch.setattr(sender_module, "_bring_window_to_front", fail_focus)

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


def test_open_from_main_selects_existing_search_text_before_pasting(monkeypatch):
    # 검색창에 이전 실행 키워드가 남아 있으면 paste 가 덧붙어 잘못된 채팅방을 찾는다.
    # ctrl+f 직후 기존 텍스트를 선택해 paste 가 덮어쓰게 한다. 카카오톡 검색창에서는
    # ctrl+a 가 '친구추가'로 가로채이므로 전체 선택은 ctrl+shift+a 를 쓴다.
    main_window = _FakeKakaoWindow("카카오톡", handle=1, with_input=False)
    _patch_desktop(monkeypatch, lambda backend: [main_window])

    pyautogui = _RecordingPyAutoGui()
    monkeypatch.setattr(sender_module, "_bring_window_to_front", lambda _window: None)
    monkeypatch.setattr(sender_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    sender_module._open_kakao_chat_window_from_main("실적봇_A")

    assert pyautogui.actions == [
        ("hotkey", ("ctrl", "f")),
        ("hotkey", ("ctrl", "shift", "a")),
        ("hotkey", ("ctrl", "v")),
        ("press", ("enter",)),
    ]


def _patch_kakao_send_window(monkeypatch, message_input):
    selected = _FakeKakaoWindow("실적봇_A", handle=11, with_input=True)
    monkeypatch.setattr(sender_module, "_find_or_open_kakao_chat_window", lambda _chat: selected)
    monkeypatch.setattr(sender_module, "_focus_chat_message_input", lambda _window: message_input)
    monkeypatch.setattr(sender_module, "_remember_kakao_chat_window", lambda _chat, _window: None)
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))
    monkeypatch.setattr(sender_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sender_module, "KAKAO_SEND_VERIFY_TIMEOUT_SECONDS", 0.0)


def test_send_kakao_text_clears_existing_draft_before_pasting(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Reads happen once per phase: cleared(empty) -> pasted(message) -> sent(empty).
    message_input = _ScriptedMessageInput(["", "hello", ""])
    pyautogui = _RecordingPyAutoGui()
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, "hello")

    assert ("hotkey", ("ctrl", "a")) in pyautogui.actions
    assert ("press", ("delete",)) in pyautogui.actions
    # Clear happens before the paste.
    clear_index = pyautogui.actions.index(("press", ("delete",)))
    paste_index = pyautogui.actions.index(("hotkey", ("ctrl", "v")))
    assert clear_index < paste_index


def test_send_kakao_text_treats_empty_input_placeholder_as_empty(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # KakaoTalk's RichEdit can expose the empty input placeholder/control name as
    # the value. That is not a draft and should not block sending.
    message_input = _ScriptedMessageInput(
        ["메시지 입력 RichEdit Control", "hello", "메시지 입력 RichEdit Control"]
    )
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, "hello")


def test_send_kakao_text_rejects_when_draft_not_cleared(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Clear-check reads a non-empty draft: the input was not cleared.
    message_input = _ScriptedMessageInput([], default="기존 초안")
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="입력창을 비우지 못했습니다"):
        send_kakao_text(config, "hello")


def test_send_kakao_text_rejects_when_pasted_value_not_exactly_equal(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Clears fine, but paste leaves "draft + message" so it is not exactly equal.
    message_input = _ScriptedMessageInput([""], default="기존 초안hello")
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="정확히 일치하지 않습니다"):
        send_kakao_text(config, "hello")


def test_send_kakao_text_accepts_long_pasted_value_prefix(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    message = "\n".join(
        [
            "[실시간 실적봇]",
            "[크롤링2]",
            "⏰ 19:20 기준",
            "",
            "아침 : 1건/9건",
            "점심 피크 : 12.6건/45건",
            "점심 논피크 : 13.4건/57건",
            "저녁 피크 : 0건/120건",
            "저녁 논피크 : 0건/78건",
        ]
    )
    # Some Kakao RichEdit reads expose only the front part of a long multi-line
    # draft. If that front part is a clear prefix of the message, paste succeeded.
    message_input = _ScriptedMessageInput(["", message[:90], "메시지 입력"])
    _patch_kakao_send_window(monkeypatch, message_input)
    pyautogui = _RecordingPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, message)

    assert ("press", ("enter",)) in pyautogui.actions


def test_send_kakao_text_accepts_long_pasted_value_with_ui_read_variation(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    message = "\n".join(
        [
            "[실시간 실적봇]",
            "[크롤링2]",
            "⏰ 19:24 기준",
            "",
            "아침 : 1건/9건",
            "점심 피크 : 12.6건/45건",
            "점심 논피크 : 13.4건/57건",
            "저녁 피크 : 0건/120건",
            "저녁 논피크 : 0건/78건",
        ]
    )
    # UIA can read a long RichEdit value with whitespace/control differences, so
    # the value is neither exact nor a clean prefix even though the message is in
    # KakaoTalk. It still has enough ordered content to prove paste landed there.
    ui_value = (
        "[실시간 실적봇]\r\n"
        "[크롤링2]\r\n"
        "⏰ 19:24 기준\r\n"
        "아침 : 1건/9건\r\n"
        "점심 피크 : 12.6건/45건\r\n"
        "점심 논피크 : 13.4건/57건\r\n"
        "..."
    )
    message_input = _ScriptedMessageInput(["", ui_value, "메시지 입력"])
    _patch_kakao_send_window(monkeypatch, message_input)
    pyautogui = _RecordingPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, message)

    assert ("press", ("enter",)) in pyautogui.actions


def test_send_kakao_text_accepts_pasted_bot_header_when_rich_edit_read_is_partial(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    message = "\n".join(
        [
            "[실시간 실적봇]",
            "[크롤링2]",
            "⏰ 19:27 기준",
            "",
            "아침 : 1건/9건",
            "점심 피크 : 12.6건/45건",
            "점심 논피크 : 13.4건/57건",
            "저녁 피크 : 17.4건/120건",
            "저녁 논피크 : 0건/78건",
            "",
            "배정 309건 / 처리 44.4건",
            "🚨거절률: 11.3%🚨",
            "🌇수행중인인원 : 3명",
        ]
    )
    # On the real Kakao RichEdit control, UIA may repeatedly expose only a partial
    # preview. Once clear/focus already succeeded, seeing our message headers in
    # the target input is enough to press Enter.
    ui_value = "[실시간 실적봇] [크롤링2] ⏰ 19:27 기준 아침 : 1건/9건 점심 피크 :"
    message_input = _ScriptedMessageInput(["", ui_value, "메시지 입력"])
    _patch_kakao_send_window(monkeypatch, message_input)
    pyautogui = _RecordingPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, message)

    assert ("press", ("enter",)) in pyautogui.actions


def test_send_kakao_text_sends_when_richedit_name_echoes_pasted_body(monkeypatch, tmp_path):
    # Regression: a real RICHEDIT50W reports its live content through
    # window_text()/element_info.name. The old placeholder check added that value
    # to the placeholder set, so the pasted body equalled "its own name" and was
    # blanked to "" — making every match check fail with "정확히 일치하지 않습니다".
    config = _config(tmp_path, chat_name="실적봇_A")
    message = "\n".join(
        [
            "[실시간 실적봇]",
            "[크롤링2]",
            "⏰ 19:46 기준",
            "",
            "아침 : 1건/9건",
            "점심 피크 : 12.6건/45건",
            "점심 논피크 : 13.4건/57건",
            "저녁 피크 : 17.4건/120건",
            "저녁 논피크 : 0건/78건",
            "",
            "배정 309건 / 처리 44.4건",
            "🚨거절률: 11.3%🚨",
            "🌇수행중인인원 : 3명",
        ]
    )
    # cleared(empty) -> pasted(full body) -> sent(empty), with name/window_text echoing each.
    message_input = _RichEditMessageInput(["", message, ""])
    _patch_kakao_send_window(monkeypatch, message_input)
    pyautogui = _RecordingPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, message)

    assert ("press", ("enter",)) in pyautogui.actions


def test_send_kakao_text_fails_when_pasted_value_unreadable(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Clears fine (empty), but value becomes unreadable after paste.
    message_input = _ScriptedMessageInput([""], default=None)
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="읽을 수 없어 붙여넣기 결과"):
        send_kakao_text(config, "hello")


def test_send_kakao_text_rejects_residual_text_after_send(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Clears (empty), pastes exact message, but after Enter a residual "hell" remains.
    # "전체 메시지 미포함"만 보면 통과하지만, 정확히 빈 문자열이 아니므로 막아야 한다.
    message_input = _ScriptedMessageInput(["", "hello", "hell"], default="hell")
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="비워지지 않아") as exc_info:
        send_kakao_text(config, "hello")
    # 잔여 텍스트가 보이면 메시지가 전송되지 않은 것이 확실하므로 ambiguous가 아니다.
    # 빠른 재시도로 안전하게 다시 보낼 수 있다.
    assert exc_info.value.ambiguous is False


def test_send_kakao_text_fails_when_post_send_value_unreadable(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Clears (empty), pastes exact message, but value becomes unreadable after Enter.
    message_input = _ScriptedMessageInput(["", "hello"], default=None)
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="읽을 수 없어 전송 결과") as exc_info:
        send_kakao_text(config, "hello")
    # Enter는 눌렀지만 전송 결과를 확인할 수 없으므로 실제로 전송됐을 수 있다(ambiguous).
    # 빠른 재시도로 같은 메시지를 또 보내지 않도록 ambiguous로 표시한다.
    assert exc_info.value.ambiguous is True


def test_send_kakao_text_aborts_when_input_loses_focus_before_clear(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Focus is stolen: the destructive ctrl+a/delete must not run on another app.
    message_input = _ScriptedMessageInput(["", "hello", ""], focus=False)
    pyautogui = _RecordingPyAutoGui()
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="포커스"):
        send_kakao_text(config, "hello")
    # No clear/paste/enter was attempted once focus could not be confirmed.
    assert ("press", ("delete",)) not in pyautogui.actions
    assert ("hotkey", ("ctrl", "v")) not in pyautogui.actions


def test_send_kakao_text_reasserts_focus_before_each_destructive_step(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    message_input = _ScriptedMessageInput(["", "hello", ""], focus=True)
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    send_kakao_text(config, "hello")

    # Focus is re-clicked before clear, before paste, and before enter.
    assert message_input.click_count == 3


def test_send_kakao_text_uses_global_lock_for_clipboard_and_hotkeys(monkeypatch, tmp_path):
    events: list[str] = []
    config = _config(tmp_path, chat_name="실적봇_A")
    message_input = _ScriptedMessageInput(["", "hello", ""])
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setattr(sender_module, "platform", _FakePlatform("Windows"))

    class FakeLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, *_args):
            events.append("lock-exit")
            return False

    pyautogui = _RecordingPyAutoGui()
    original_hotkey = pyautogui.hotkey
    original_press = pyautogui.press

    def hotkey(*args):
        events.append(f"hotkey:{'+'.join(args)}")
        original_hotkey(*args)

    def press(*args):
        events.append(f"press:{'+'.join(args)}")
        original_press(*args)

    pyautogui.hotkey = hotkey
    pyautogui.press = press

    class RecordingPyperclip:
        def copy(self, value) -> None:
            events.append(f"copy:{value}")

    monkeypatch.setattr(sender_module, "RunLock", FakeLock)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", RecordingPyperclip())

    send_kakao_text(config, "hello")

    assert events[0] == "lock-enter"
    assert events[-1] == "lock-exit"
    assert "copy:hello" in events[1:-1]
    assert "hotkey:ctrl+v" in events[1:-1]
    assert "press:enter" in events[1:-1]


def test_send_kakao_text_aborts_when_focus_cannot_be_determined(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Focus state is unknown (no has_keyboard_focus, no resolvable handle): a safety
    # check must block rather than fire destructive keys on an unverified control.
    message_input = _UnknownFocusMessageInput()
    pyautogui = _RecordingPyAutoGui()
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setattr(sender_module, "_foreground_window_handle", lambda: None)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="확인하지 못했습니다"):
        send_kakao_text(config, "hello")
    assert ("press", ("delete",)) not in pyautogui.actions
    assert ("hotkey", ("ctrl", "v")) not in pyautogui.actions


def test_ensure_message_input_focus_blocks_on_unknown_focus(monkeypatch):
    control = _UnknownFocusMessageInput()
    monkeypatch.setattr(sender_module, "_foreground_window_handle", lambda: None)

    assert sender_module._control_has_keyboard_focus(control) is None
    with pytest.raises(KakaoSendError, match="확인하지 못했습니다"):
        sender_module._ensure_message_input_focus(control)


def test_send_kakao_text_fails_when_clear_value_unreadable(monkeypatch, tmp_path):
    config = _config(tmp_path, chat_name="실적봇_A")
    # Value unreadable from the start: cannot confirm the input was cleared.
    message_input = _ScriptedMessageInput([], default=None)
    _patch_kakao_send_window(monkeypatch, message_input)
    monkeypatch.setitem(sys.modules, "pyautogui", _RecordingPyAutoGui())
    monkeypatch.setitem(sys.modules, "pyperclip", _FakePyperclip())

    with pytest.raises(KakaoSendError, match="읽을 수 없어 비우기 결과"):
        send_kakao_text(config, "hello")


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


class _RecordingPyAutoGui:
    def __init__(self) -> None:
        self.actions: list[tuple[str, tuple]] = []

    def hotkey(self, *args) -> None:
        self.actions.append(("hotkey", args))

    def press(self, *args) -> None:
        self.actions.append(("press", args))


class _ScriptedMessageInput:
    """A fake message-input control that yields scripted values on each read.

    Each call to ``get_value`` returns the next scripted value; once the script is
    exhausted it returns ``default``. A scripted ``None`` simulates an unreadable
    input control. ``focus`` controls what ``has_keyboard_focus`` reports: ``True``
    (default) keeps focus checks passing, ``False`` simulates focus being stolen.
    """

    def __init__(
        self,
        values: list[str | None],
        *,
        default: str | None = "",
        focus: bool = True,
    ) -> None:
        self._values = list(values)
        self._default = default
        self._focus = focus
        self.click_count = 0
        self.element_info = _FakeElementInfo(control_type="Document", class_name="RICHEDIT50W", name="메시지 입력")

    def click_input(self) -> None:
        self.click_count += 1

    def has_keyboard_focus(self) -> bool:
        return self._focus

    def get_value(self):
        if self._values:
            value = self._values.pop(0)
        else:
            value = self._default
        if value is None:
            raise RuntimeError("input value unreadable")
        return value


class _RichEditMessageInput:
    """A fake control that mirrors a real KakaoTalk RICHEDIT50W input.

    A real RichEdit control exposes its *current* content through both
    ``window_text()`` and ``element_info.name`` (not a static placeholder). When
    empty it instead reports the "메시지 입력" placeholder string. This is what
    poisoned the old placeholder detection: the live body equalled the control's
    own name/window_text, so a non-empty input was wrongly treated as empty.
    """

    _PLACEHOLDER = "메시지 입력"

    def __init__(self, values: list[str], *, default: str = "", focus: bool = True) -> None:
        self._values = list(values)
        self._default = default
        self._focus = focus
        self.click_count = 0
        self.element_info = _FakeElementInfo(control_type="Document", class_name="RICHEDIT50W")
        self._current = ""
        self._refresh_name()

    def _refresh_name(self) -> None:
        # Empty input shows the placeholder; otherwise the live body, like the real control.
        self.element_info.name = self._PLACEHOLDER if self._current == "" else self._current

    def click_input(self) -> None:
        self.click_count += 1

    def has_keyboard_focus(self) -> bool:
        return self._focus

    def window_text(self) -> str:
        return self._PLACEHOLDER if self._current == "" else self._current

    def get_value(self):
        self._current = self._values.pop(0) if self._values else self._default
        self._refresh_name()
        return self._current


class _UnknownFocusMessageInput:
    """A control whose focus state cannot be determined.

    No ``has_keyboard_focus`` and no resolvable top-level handle, so
    ``_control_has_keyboard_focus`` returns None (unknown).
    """

    def __init__(self) -> None:
        self.click_count = 0
        self.element_info = _FakeElementInfo(control_type="Document", class_name="RICHEDIT50W", name="메시지 입력")

    def click_input(self) -> None:
        self.click_count += 1

    def get_value(self):
        return ""


class _FakePyperclip:
    def copy(self, _value) -> None:
        pass
