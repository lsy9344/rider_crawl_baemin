from __future__ import annotations

import argparse
import platform
import queue
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, messagebox
from tkinter import ttk
from typing import Any

from .app import RunResult, run_once
from .browser_launcher import BrowserLaunchError, prepare_chrome
from .scheduler import BotScheduler
from .telegram_commands import TelegramCommandProcessor, TelegramUpdatePoller
from .ui_settings import UiSettings, UiSettingsStore


DEFAULT_WINDOW_GEOMETRY = "900x900"
MIN_WINDOW_HEIGHT = 780
PREVIEW_TEXT_HEIGHT = 24


def default_settings_path() -> Path:
    return Path("runtime/state/ui_settings.json")


def active_crawling_settings(settings_list: list[UiSettings]) -> list[tuple[int, UiSettings]]:
    return [(index, settings) for index, settings in enumerate(settings_list) if settings.performance_url.strip()]


def app_configs_from_settings(indexed_settings: list[tuple[int, UiSettings]]):
    return [
        settings.to_app_config(crawl_name=f"크롤링{index + 1}", state_subdir=f"crawling{index + 1}")
        for index, settings in indexed_settings
    ]


def coerce_settings(values: dict[str, Any]) -> UiSettings:
    interval_minutes = _positive_int(
        values.get("interval_minutes", UiSettings.defaults().interval_minutes),
        "메세지 전송 간격",
    )
    page_timeout_seconds = _positive_int(values["page_timeout_seconds"], "페이지 타임아웃")
    run_lock_timeout_seconds = _positive_int(values["run_lock_timeout_seconds"], "중복 실행 락 타임아웃")

    return UiSettings(
        performance_url=str(values["performance_url"]).strip(),
        peak_dashboard_url=str(values["peak_dashboard_url"]).strip(),
        baemin_center_name=UiSettings.defaults().baemin_center_name,
        baemin_center_id=UiSettings.defaults().baemin_center_id,
        browser_mode=str(values["browser_mode"]).strip(),
        cdp_url=str(values["cdp_url"]).strip(),
        browser_user_data_dir=Path(str(values["browser_user_data_dir"]).strip()),
        headless=bool(values["headless"]),
        kakao_chat_name=str(values["kakao_chat_name"]).strip(),
        telegram_bot_token=str(values.get("telegram_bot_token", "")).strip(),
        telegram_chat_id=str(values.get("telegram_chat_id", "")).strip(),
        messenger_name=str(values.get("messenger_name", "telegram")).strip() or "telegram",
        log_dir=Path(str(values["log_dir"]).strip()),
        send_enabled=bool(values["send_enabled"]),
        send_only_on_change=bool(values["send_only_on_change"]),
        interval_minutes=interval_minutes,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=run_lock_timeout_seconds,
        page_timeout_seconds=page_timeout_seconds,
    )


def disable_unsupported_send(settings: UiSettings, *, platform_name: str | None = None) -> bool:
    if settings.messenger_name != "kakao" or (platform_name or platform.system()) == "Windows" or not settings.send_enabled:
        return False
    settings.send_enabled = False
    return True


def launch_ui(settings_path: Path | None = None) -> None:
    root = Tk()
    RiderBotUi(root, UiSettingsStore(settings_path or default_settings_path()))
    root.mainloop()


class RiderBotUi:
    def __init__(self, root: Tk, store: UiSettingsStore) -> None:
        self.root = root
        self.store = store
        self.settings_tabs = store.load_all()
        self.settings = self.settings_tabs[0]
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.workers: list[threading.Thread] = []
        self.telegram_worker: threading.Thread | None = None
        self.settings_notebook: ttk.Notebook | None = None
        self.crawl_lock = threading.Lock()

        self.root.title("배민 배달현황 실적봇")
        self.root.geometry(DEFAULT_WINDOW_GEOMETRY)
        self.root.minsize(780, MIN_WINDOW_HEIGHT)

        self.vars_by_tab = [self._build_vars(settings) for settings in self.settings_tabs]
        self.vars = self.vars_by_tab[0]
        self.status_var = StringVar(value="대기 중")
        self.next_run_var = StringVar(value="-")

        self._build()
        self._poll_messages()

    def _build_vars(self, settings: UiSettings) -> dict[str, StringVar | BooleanVar]:
        return {
            "performance_url": StringVar(value=settings.performance_url),
            "peak_dashboard_url": StringVar(value=settings.peak_dashboard_url),
            "browser_mode": StringVar(value=settings.browser_mode),
            "cdp_url": StringVar(value=settings.cdp_url),
            "browser_user_data_dir": StringVar(value=str(settings.browser_user_data_dir)),
            "log_dir": StringVar(value=str(settings.log_dir)),
            "kakao_chat_name": StringVar(value=settings.kakao_chat_name),
            "telegram_bot_token": StringVar(value=settings.telegram_bot_token),
            "telegram_chat_id": StringVar(value=settings.telegram_chat_id),
            "messenger_name": StringVar(value=settings.messenger_name),
            "interval_minutes": StringVar(value=str(settings.interval_minutes)),
            "page_timeout_seconds": StringVar(value=str(settings.page_timeout_seconds)),
            "run_lock_timeout_seconds": StringVar(value=str(settings.run_lock_timeout_seconds)),
            "headless": BooleanVar(value=settings.headless),
            "send_enabled": BooleanVar(value=settings.send_enabled),
            "send_only_on_change": BooleanVar(value=settings.send_only_on_change),
        }

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        title = ttk.Label(outer, text="배민 배달현황 실적봇", font=("", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            outer,
            text="로그인된 배민 배달현황 페이지를 읽고 텔레그램 그룹방에 텍스트 실적을 보냅니다.",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 14))

        self._build_settings(outer).grid(row=2, column=0, sticky="ew")
        self._build_runtime(outer).grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        self._build_buttons(outer).grid(row=4, column=0, sticky="ew", pady=(14, 0))

    def _build_settings(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="설정", padding=14)
        frame.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(frame)
        notebook.grid(row=0, column=0, sticky="ew")
        self.settings_notebook = notebook

        for index, tab_vars in enumerate(self.vars_by_tab):
            tab = ttk.Frame(notebook, padding=10)
            tab.columnconfigure(1, weight=1)
            self._build_settings_fields(tab, tab_vars)
            notebook.add(tab, text=f"크롤링{index + 1}")

        notebook.bind("<<NotebookSelected>>", lambda _event: self._sync_selected_vars())
        return frame

    def _build_settings_fields(self, frame: ttk.Frame, tab_vars: dict[str, StringVar | BooleanVar]) -> None:
        rows = [
            ("배달현황 URL", "performance_url"),
            ("보조 URL", "peak_dashboard_url"),
            ("CDP 주소", "cdp_url"),
            ("앱 전용 브라우저 프로필 경로", "browser_user_data_dir"),
            ("텔레그램 봇 토큰", "telegram_bot_token"),
            ("텔레그램 채팅방 ID", "telegram_chat_id"),
            ("로그 경로", "log_dir"),
            ("카카오톡 채팅방명(기존)", "kakao_chat_name"),
            ("메세지 전송 간격(분)", "interval_minutes"),
            ("페이지 타임아웃(ms)", "page_timeout_seconds"),
            ("락 타임아웃(초)", "run_lock_timeout_seconds"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            ttk.Entry(frame, textvariable=tab_vars[key]).grid(row=row, column=1, sticky="ew", pady=4)

        checks = ttk.Frame(frame)
        checks.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(checks, text="브라우저 연결").grid(row=0, column=0, padx=(0, 8))
        ttk.Combobox(
            checks,
            textvariable=tab_vars["browser_mode"],
            values=("cdp", "persistent"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, padx=(0, 18))
        ttk.Checkbutton(checks, text="Headless", variable=tab_vars["headless"]).grid(row=0, column=2, padx=(0, 18))
        ttk.Checkbutton(checks, text="텔레그램 전송", variable=tab_vars["send_enabled"]).grid(row=0, column=3, padx=(0, 18))
        ttk.Checkbutton(checks, text="변경 시에만 전송", variable=tab_vars["send_only_on_change"]).grid(row=0, column=4)

    def _build_runtime(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        checklist = ttk.LabelFrame(frame, text="시작 전 확인", padding=12)
        checklist.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        checklist.columnconfigure(0, weight=1)
        ttk.Label(
            checklist,
            text=(
                "1. 배민 배달현황 링크는 로그인된 상태로 열려 있어야 합니다.\n"
                "2. 기본값은 원격 디버깅 포트로 실행한 Chrome에 연결합니다.\n"
                "3. 2차 인증은 앱이 처리하지 않습니다. 로그인 만료 시 직접 다시 로그인하세요.\n"
                "4. 텔레그램 봇 토큰과 그룹방 chat_id를 입력하세요.\n"
                "5. 그룹방에서 !이름1234 명령을 받으려면 BotFather privacy mode를 끄거나 봇을 관리자로 넣으세요.\n"
                "6. 여러 계정은 탭마다 다른 CDP 포트와 브라우저 프로필 경로를 사용하세요.\n"
                "7. 처음에는 텔레그램 전송을 끄고 1회 실행으로 메시지를 확인하세요.\n"
                "8. 시작 버튼을 누르면 즉시 1회 실행 후 설정한 메세지 전송 간격으로 반복됩니다."
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        status = ttk.LabelFrame(frame, text="상태", padding=12)
        status.grid(row=0, column=1, sticky="nsew")
        status.columnconfigure(1, weight=1)
        ttk.Label(status, text="현재 상태").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(status, text="다음 실행").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Label(status, textvariable=self.next_run_var).grid(row=1, column=1, sticky="w", pady=2)

        preview_box = ttk.LabelFrame(frame, text="메시지 미리보기와 로그", padding=8)
        preview_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)
        self.preview = _make_text(preview_box)
        self.preview.grid(row=0, column=0, sticky="nsew")
        return frame

    def _build_buttons(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)

        ttk.Button(frame, text="Chrome 준비하기", command=self.prepare_app_clicked).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(frame, text="설정 저장", command=self.save_settings).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(frame, text="1회 실행", command=self.run_once_clicked).grid(row=0, column=3, padx=(8, 0))
        self.start_button = ttk.Button(frame, text="시작", command=self.start)
        self.start_button.grid(row=0, column=4, padx=(8, 0))
        self.stop_button = ttk.Button(frame, text="중지", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=5, padx=(8, 0))
        return frame

    def save_settings(self) -> UiSettings | None:
        try:
            settings_tabs = self._read_all_settings()
        except ValueError as exc:
            messagebox.showerror("설정 오류", str(exc))
            return None

        for index, settings in enumerate(settings_tabs):
            if disable_unsupported_send(settings):
                self.vars_by_tab[index]["send_enabled"].set(False)
                self._append_preview("[안내]\n카카오톡 전송은 Windows에서만 지원되어 미리보기로 실행합니다.\n")

        self.store.save_all(settings_tabs)
        self.settings_tabs = settings_tabs
        selected_index = self._selected_tab_index()
        self.settings = settings_tabs[selected_index]
        self.status_var.set("설정 저장됨")
        return self.settings

    def prepare_app_clicked(self) -> None:
        selected_index = self._selected_tab_index()
        self.vars_by_tab[selected_index]["browser_mode"].set("cdp")
        settings = self.save_settings()
        if settings is None:
            return

        try:
            message = prepare_chrome(settings.to_app_config(crawl_name=f"크롤링{selected_index + 1}", state_subdir=f"crawling{selected_index + 1}"))
        except BrowserLaunchError as exc:
            self.status_var.set("준비 오류")
            self._append_preview(f"[준비 오류]\n{exc}\n")
            return

        self.status_var.set("Chrome 준비됨")
        self._append_preview(f"[준비]\n{message}\n")

    def run_once_clicked(self) -> None:
        settings = self.save_settings()
        if settings is None:
            return

        threading.Thread(target=self._run_once_background, args=(self._selected_tab_index(), settings), daemon=True).start()

    def start(self) -> None:
        if self.save_settings() is None:
            return

        active_settings = active_crawling_settings(self.settings_tabs)
        if not active_settings:
            self.status_var.set("활성 탭 없음")
            self._append_preview("[안내]\n배달현황 URL이 입력된 탭이 없습니다.\n")
            return

        self.stop_event = threading.Event()
        self.workers = []
        for index, settings in active_settings:
            scheduler = BotScheduler(
                interval_minutes=settings.interval_minutes,
                run_job=lambda tab_index=index, tab_settings=settings: self._run_once_background(tab_index, tab_settings),
            )
            worker = threading.Thread(
                target=scheduler.run_loop,
                kwargs={"stop_event": self.stop_event},
                daemon=True,
            )
            self.workers.append(worker)

        self._start_telegram_listener(active_settings)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("실행 중")
        for worker in self.workers:
            worker.start()

    def stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self.status_var.set("중지 요청됨")
        self.next_run_var.set("-")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _run_once_background(self, tab_index: int, settings: UiSettings) -> None:
        self.messages.put(("status", f"크롤링{tab_index + 1} 실행 중"))
        try:
            with self.crawl_lock:
                result = run_once(settings.to_app_config(crawl_name=f"크롤링{tab_index + 1}", state_subdir=f"crawling{tab_index + 1}"))
        except Exception as exc:  # UI boundary: surface errors to the operator.
            self.messages.put(("error", str(exc)))
            return
        self.messages.put(("result", (tab_index, result, settings.interval_minutes)))

    def _poll_messages(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.status_var.set(str(payload))
            elif kind == "error":
                self.status_var.set("오류")
                self._append_preview(f"[오류]\n{payload}\n")
            elif kind == "result":
                tab_index, result, interval_minutes = payload
                self._show_result(tab_index, result, interval_minutes)

        self.root.after(200, self._poll_messages)

    def _show_result(self, tab_index: int, result: RunResult, interval_minutes: int) -> None:
        if result.skipped:
            status = "중복 메시지 건너뜀"
        elif result.sent:
            status = "전송 완료"
        else:
            status = "메시지 생성 완료"

        self.status_var.set(status)
        self.next_run_var.set((datetime.now() + timedelta(minutes=interval_minutes)).strftime("%H:%M:%S"))
        self._append_preview(f"[{datetime.now().strftime('%H:%M:%S')}] 크롤링{tab_index + 1} {status}\n{result.message}\n\n")

    def _read_settings(self) -> UiSettings:
        return self._read_all_settings()[self._selected_tab_index()]

    def _read_all_settings(self) -> list[UiSettings]:
        return [self._read_settings_from_vars(tab_vars) for tab_vars in self.vars_by_tab]

    def _read_settings_from_vars(self, tab_vars: dict[str, StringVar | BooleanVar]) -> UiSettings:
        values = {
            key: variable.get()
            for key, variable in tab_vars.items()
        }
        return coerce_settings(values)

    def _selected_tab_index(self) -> int:
        if self.settings_notebook is None:
            return 0
        try:
            return int(self.settings_notebook.index("current"))
        except Exception:
            return 0

    def _sync_selected_vars(self) -> None:
        self.vars = self.vars_by_tab[self._selected_tab_index()]

    def _start_telegram_listener(self, active_settings: list[tuple[int, UiSettings]]) -> None:
        configs = app_configs_from_settings(active_settings)
        bot_config = next(
            (config for config in configs if config.telegram_bot_token.strip() and config.telegram_chat_id.strip()),
            None,
        )
        if bot_config is None or self.stop_event is None:
            self._append_preview("[안내]\n텔레그램 봇 토큰과 채팅방 ID가 없어 명령 감지를 시작하지 않습니다.\n")
            return

        processor = TelegramCommandProcessor(configs, bot_config=bot_config, lock=self.crawl_lock)
        poller = TelegramUpdatePoller(bot_config, handle_text=processor.handle_text)
        self.telegram_worker = threading.Thread(
            target=self._telegram_poll_loop,
            args=(poller, self.stop_event),
            daemon=True,
        )
        self.telegram_worker.start()

    def _telegram_poll_loop(self, poller: TelegramUpdatePoller, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                poller.poll_once()
            except Exception as exc:
                self.messages.put(("error", f"텔레그램 명령 감지 오류: {exc}"))
                stop_event.wait(5)

    def _append_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.insert("end", text)
        self.preview.see("end")
        self.preview.configure(state="disabled")


def run_cli_once() -> None:
    settings = UiSettingsStore(default_settings_path()).load()
    result = run_once(settings.to_app_config())
    if not result.sent:
        print(result.message)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once without opening the UI")
    args = parser.parse_args(argv)
    if args.once:
        run_cli_once()
    else:
        launch_ui()


def _positive_int(raw: Any, label: str) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{label}은 숫자로 입력하세요") from exc
    if value <= 0:
        raise ValueError(f"{label}은 1 이상이어야 합니다")
    return value


def _make_text(parent: ttk.Frame):
    from tkinter import Text

    widget = Text(parent, height=PREVIEW_TEXT_HEIGHT, wrap="word", state="disabled")
    return widget
