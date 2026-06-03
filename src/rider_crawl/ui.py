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
from .browser_launcher import BrowserLaunchError, prepare_mac_chrome
from .scheduler import BotScheduler
from .ui_settings import UiSettings, UiSettingsStore


def default_settings_path() -> Path:
    return Path("runtime/state/ui_settings.json")


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
        log_dir=Path(str(values["log_dir"]).strip()),
        send_enabled=bool(values["send_enabled"]),
        send_only_on_change=bool(values["send_only_on_change"]),
        interval_minutes=interval_minutes,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=run_lock_timeout_seconds,
        page_timeout_seconds=page_timeout_seconds,
    )


def disable_unsupported_send(settings: UiSettings, *, platform_name: str | None = None) -> bool:
    if (platform_name or platform.system()) == "Windows" or not settings.send_enabled:
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
        self.settings = store.load()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.send_supported = platform.system() == "Windows"

        self.root.title("배민 배달현황 실적봇")
        self.root.geometry("900x760")
        self.root.minsize(780, 680)

        self.vars = self._build_vars(self.settings)
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
            "interval_minutes": StringVar(value=str(settings.interval_minutes)),
            "page_timeout_seconds": StringVar(value=str(settings.page_timeout_seconds)),
            "run_lock_timeout_seconds": StringVar(value=str(settings.run_lock_timeout_seconds)),
            "headless": BooleanVar(value=settings.headless),
            "send_enabled": BooleanVar(value=settings.send_enabled if self.send_supported else False),
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
            text="로그인된 배민 배달현황 페이지를 읽고 카카오톡 단체방에 텍스트 실적을 보냅니다.",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 14))

        self._build_settings(outer).grid(row=2, column=0, sticky="ew")
        self._build_runtime(outer).grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        self._build_buttons(outer).grid(row=4, column=0, sticky="ew", pady=(14, 0))

    def _build_settings(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="설정", padding=14)
        frame.columnconfigure(1, weight=1)

        rows = [
            ("배달현황 URL", "performance_url"),
            ("보조 URL", "peak_dashboard_url"),
            ("CDP 주소", "cdp_url"),
            ("앱 전용 브라우저 프로필 경로", "browser_user_data_dir"),
            ("로그 경로", "log_dir"),
            ("카카오톡 채팅방명", "kakao_chat_name"),
            ("메세지 전송 간격(분)", "interval_minutes"),
            ("페이지 타임아웃(ms)", "page_timeout_seconds"),
            ("락 타임아웃(초)", "run_lock_timeout_seconds"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            ttk.Entry(frame, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=4)

        checks = ttk.Frame(frame)
        checks.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(checks, text="브라우저 연결").grid(row=0, column=0, padx=(0, 8))
        ttk.Combobox(
            checks,
            textvariable=self.vars["browser_mode"],
            values=("cdp", "persistent"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, padx=(0, 18))
        ttk.Checkbutton(checks, text="Headless", variable=self.vars["headless"]).grid(row=0, column=2, padx=(0, 18))
        send_state = "normal" if self.send_supported else "disabled"
        ttk.Checkbutton(checks, text="카카오톡 전송", variable=self.vars["send_enabled"], state=send_state).grid(row=0, column=3, padx=(0, 18))
        ttk.Checkbutton(checks, text="변경 시에만 전송", variable=self.vars["send_only_on_change"]).grid(row=0, column=4)
        return frame

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
                "4. 보낼 카카오톡 단체방을 더블클릭해서 채팅방 창을 따로 띄우세요.\n"
                "5. 그 채팅방 입력칸을 한 번 클릭해 커서가 깜박이게 두고, 창을 최소화하지 마세요.\n"
                "6. 앱의 [카카오톡 채팅방명]에는 채팅방 창 제목을 똑같이 적습니다.\n"
                "7. 처음에는 카카오톡 전송을 끄고 1회 실행으로 메시지를 확인하세요.\n"
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

        ttk.Button(frame, text="앱 실행 준비하기(mac)", command=self.prepare_app_clicked).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(frame, text="설정 저장", command=self.save_settings).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(frame, text="1회 실행", command=self.run_once_clicked).grid(row=0, column=3, padx=(8, 0))
        self.start_button = ttk.Button(frame, text="시작", command=self.start)
        self.start_button.grid(row=0, column=4, padx=(8, 0))
        self.stop_button = ttk.Button(frame, text="중지", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=5, padx=(8, 0))
        return frame

    def save_settings(self) -> UiSettings | None:
        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("설정 오류", str(exc))
            return None

        if disable_unsupported_send(settings):
            self.vars["send_enabled"].set(False)
            self._append_preview("[안내]\n카카오톡 전송은 Windows에서만 지원되어 macOS에서는 미리보기로 실행합니다.\n")

        self.store.save(settings)
        self.settings = settings
        self.status_var.set("설정 저장됨")
        return settings

    def prepare_app_clicked(self) -> None:
        self.vars["browser_mode"].set("cdp")
        settings = self.save_settings()
        if settings is None:
            return

        try:
            message = prepare_mac_chrome(settings.to_app_config())
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

        threading.Thread(target=self._run_once_background, args=(settings,), daemon=True).start()

    def start(self) -> None:
        settings = self.save_settings()
        if settings is None:
            return

        self.stop_event = threading.Event()
        scheduler = BotScheduler(
            interval_minutes=settings.interval_minutes,
            run_job=lambda: self._run_once_background(settings),
        )
        self.worker = threading.Thread(
            target=scheduler.run_loop,
            kwargs={"stop_event": self.stop_event},
            daemon=True,
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("실행 중")
        self.worker.start()

    def stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self.status_var.set("중지 요청됨")
        self.next_run_var.set("-")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _run_once_background(self, settings: UiSettings) -> None:
        self.messages.put(("status", "실행 중"))
        try:
            result = run_once(settings.to_app_config())
        except Exception as exc:  # UI boundary: surface errors to the operator.
            self.messages.put(("error", str(exc)))
            return
        self.messages.put(("result", (result, settings.interval_minutes)))

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
                result, interval_minutes = payload
                self._show_result(result, interval_minutes)

        self.root.after(200, self._poll_messages)

    def _show_result(self, result: RunResult, interval_minutes: int) -> None:
        if result.skipped:
            status = "중복 메시지 건너뜀"
        elif result.sent:
            status = "전송 완료"
        else:
            status = "메시지 생성 완료"

        self.status_var.set(status)
        self.next_run_var.set((datetime.now() + timedelta(minutes=interval_minutes)).strftime("%H:%M:%S"))
        self._append_preview(f"[{datetime.now().strftime('%H:%M:%S')}] {status}\n{result.message}\n\n")

    def _read_settings(self) -> UiSettings:
        values = {
            key: variable.get()
            for key, variable in self.vars.items()
        }
        return coerce_settings(values)

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

    widget = Text(parent, height=14, wrap="word", state="disabled")
    return widget
