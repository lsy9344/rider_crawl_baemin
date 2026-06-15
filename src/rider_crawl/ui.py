from __future__ import annotations

import argparse
import platform
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import BooleanVar, Canvas, StringVar, Tk, messagebox
from tkinter import ttk
from typing import Any
from urllib.parse import urlsplit

from .app import RunResult, run_once
from .auth.imap_2fa import IMAP_HOST_BY_DOMAIN
from .browser_launcher import BrowserActionRequiredError, BrowserLaunchError, CdpUnavailableError, prepare_chrome
from .config import DEFAULT_BAEMIN_CENTER_NAME, DEFAULT_COUPANG_PEAK_DASHBOARD_URL, AppConfig
from .keyword_responder import KeywordResponder
from .log_rotation import rotate_if_needed
from .messengers import dispatch_text_message
from .scheduler import BotScheduler
from .sender import KakaoSendError, TelegramSendError
from .telegram_commands import TelegramCommandProcessor, TelegramUpdatePoller
from .ui_settings import UiSettings, UiSettingsStore


DEFAULT_WINDOW_GEOMETRY = "900x900"
MIN_WINDOW_HEIGHT = 780
PREVIEW_TEXT_HEIGHT = 24
MESSENGER_OPTIONS = (("telegram", "텔레그램"), ("kakao", "카카오톡"))
PLATFORM_OPTIONS = (("baemin", "배민"), ("coupang", "쿠팡이츠"))
TELEGRAM_SEND_MIN_INTERVAL_SECONDS = 1.0
TELEGRAM_FIELD_KEYS = ("telegram_bot_token", "telegram_chat_id", "telegram_message_thread_id")
KAKAO_FIELD_KEYS = ("kakao_chat_name",)
MESSENGER_FIELD_KEYS = TELEGRAM_FIELD_KEYS + KAKAO_FIELD_KEYS
# 플랫폼 선택에 따라 잠그거나 자동 채우는 URL 입력칸. 쿠팡이츠를 고르면 '실적/달성현황
# URL'에 peak-dashboard가 고정 입력되고, 더 이상 쓰지 않는 '보조 URL'은 비활성화된다.
PLATFORM_URL_FIELD_KEYS = ("performance_url", "peak_dashboard_url")


@dataclass
class _TelegramPollerHandle:
    """봇 토큰 하나에 대한 단일 폴러와 그 폴러가 담당하는 활성 탭 집합.

    여러 탭이 같은 토큰을 공유할 때 폴러는 하나만 돌고, ``tab_indexes``에 든 탭들이
    모두 중지되면(빈 집합) 폴러도 멈춘다. ``processor``는 같은 토큰의 활성 탭 전부를
    라우팅 대상으로 알고 있어 '!조회' 명령이 올바른 탭으로 전달된다.
    """

    stop_event: threading.Event
    worker: threading.Thread
    processor: TelegramCommandProcessor
    tab_indexes: set[int] = field(default_factory=set)


def default_settings_path() -> Path:
    return Path("runtime/state/ui_settings.json")


def active_crawling_settings(settings_list: list[UiSettings]) -> list[tuple[int, UiSettings]]:
    return [(index, settings) for index, settings in enumerate(settings_list) if settings.performance_url.strip()]


def validate_active_tab_isolation(settings_list: list[UiSettings]) -> None:
    active_settings = active_crawling_settings(settings_list)
    _validate_active_cdp_local(active_settings)
    _validate_active_baemin_center_identity(active_settings)
    _validate_active_coupang_urls(active_settings)
    _validate_active_telegram_required(active_settings)
    _validate_active_kakao_required(active_settings)
    _validate_unique_active_value(
        active_settings,
        label="CDP 주소",
        key=_cdp_port_key,
    )
    _validate_unique_active_value(
        active_settings,
        label="브라우저 프로필 경로",
        key=lambda settings: _profile_path_key(settings.browser_user_data_dir),
    )
    _validate_unique_active_value(
        active_settings,
        label="텔레그램 채팅방 ID",
        key=_telegram_target_key,
    )
    _validate_unique_active_value(
        active_settings,
        label="카카오톡 채팅방명",
        key=_kakao_chat_name_key,
    )


def _state_subdir_for(settings: UiSettings, index: int) -> str:
    # 상태 경로(last_message dedup 등)의 주 식별자는 안정 ID(monitoring_target_id)다. 탭을
    # 재정렬해도 ID가 따라가므로 다른 대상의 상태와 섞이지 않는다. crawlingN 순번은 2.1
    # 마이그레이션 전/미저장(=ID 빈값)인 탭에 한해서만 충돌 없는 legacy 폴백으로 쓰고,
    # 다음 load_all이 안정 ID를 발급·영속화하면 자동 치유된다(주 식별 스킴 아님).
    # 빈 id를 그대로 쓰면 모든 탭이 ``targets/``(슬래시만)로 충돌하므로 strip 검사 필수.
    target_id = settings.monitoring_target_id.strip()
    if target_id:
        return f"targets/{target_id}"
    return f"crawling{index + 1}"


def app_configs_from_settings(indexed_settings: list[tuple[int, UiSettings]]):
    return [
        settings.to_app_config(crawl_name=f"크롤링{index + 1}", state_subdir=_state_subdir_for(settings, index))
        for index, settings in indexed_settings
    ]


def telegram_configs_by_token(configs: list[AppConfig]) -> dict[str, list[AppConfig]]:
    grouped: dict[str, list[AppConfig]] = {}
    for config in configs:
        token = config.telegram_bot_token.strip()
        if config.messenger_name != "telegram" or not config.send_enabled:
            continue
        if not token or not config.telegram_chat_id.strip():
            continue
        grouped.setdefault(token, []).append(config)
    return grouped


def coerce_settings(values: dict[str, Any]) -> UiSettings:
    defaults = UiSettings.defaults()
    interval_minutes = _positive_int(
        values.get("interval_minutes", defaults.interval_minutes),
        "메세지 전송 간격",
    )
    page_timeout_seconds = _positive_int(values["page_timeout_seconds"], "페이지 타임아웃")
    run_lock_timeout_seconds = _positive_int(values["run_lock_timeout_seconds"], "중복 실행 락 타임아웃")
    messenger_name = _messenger_name(values.get("messenger_name", "telegram"))
    platform_name = _platform_name(values.get("platform_name", "baemin"))

    return UiSettings(
        performance_url=str(values["performance_url"]).strip(),
        peak_dashboard_url=str(values["peak_dashboard_url"]).strip(),
        platform_name=platform_name,
        baemin_center_name=str(values.get("baemin_center_name", defaults.baemin_center_name)).strip(),
        baemin_center_id=str(values.get("baemin_center_id", defaults.baemin_center_id)).strip(),
        browser_mode=str(values["browser_mode"]).strip(),
        cdp_url=str(values["cdp_url"]).strip(),
        browser_user_data_dir=Path(str(values["browser_user_data_dir"]).strip()),
        headless=bool(values["headless"]),
        kakao_chat_name=str(values["kakao_chat_name"]).strip(),
        telegram_bot_token=str(values.get("telegram_bot_token", "")).strip(),
        telegram_chat_id=str(values.get("telegram_chat_id", "")).strip(),
        telegram_message_thread_id=_normalize_telegram_thread_id(values.get("telegram_message_thread_id", "")),
        messenger_name=messenger_name,
        log_dir=Path(str(values["log_dir"]).strip()),
        send_enabled=bool(values["send_enabled"]),
        send_only_on_change=bool(values["send_only_on_change"]),
        interval_minutes=interval_minutes,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=run_lock_timeout_seconds,
        page_timeout_seconds=page_timeout_seconds,
        coupang_auto_email_2fa_enabled=bool(
            values.get("coupang_auto_email_2fa_enabled", defaults.coupang_auto_email_2fa_enabled)
        ),
        coupang_login_id=str(values.get("coupang_login_id", "")).strip(),
        # 비밀번호는 앞뒤 공백도 의미가 있을 수 있어 strip하지 않는다.
        coupang_login_password=str(values.get("coupang_login_password", "")),
        verification_email_address=str(values.get("verification_email_address", "")).strip(),
        # 앱 비밀번호는 Gmail 공백 붙여넣기를 IMAP 계층에서 처리하므로 UI 수집 시 strip하지 않는다.
        verification_email_app_password=str(values.get("verification_email_app_password", "")),
        verification_email_subject_keyword=str(
            values.get("verification_email_subject_keyword", defaults.verification_email_subject_keyword)
        ).strip()
        or defaults.verification_email_subject_keyword,
        verification_email_sender_keyword=str(
            values.get("verification_email_sender_keyword", defaults.verification_email_sender_keyword)
        ).strip()
        or defaults.verification_email_sender_keyword,
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
        # 탭마다 독립적으로 시작/중지한다. 한 탭의 '시작'이 다른 탭을 함께 돌리지
        # 않도록 stop_event/워커를 탭 인덱스별로 따로 보관한다.
        self.stop_events_by_tab: dict[int, threading.Event] = {}
        self.workers_by_tab: dict[int, list[threading.Thread]] = {}
        # 텔레그램 명령 폴러는 봇 토큰 단위로 하나만 돈다. 여러 탭이 같은 토큰을
        # 공유하면 getUpdates를 두 폴러가 나눠 받아 '!조회' 명령이 다른 탭으로 가
        # 누락될 수 있으므로, 토큰별로 폴러 하나를 두고 그 토큰을 쓰는 활성 탭 전부를
        # 명령 라우팅 대상으로 등록한다.
        self.telegram_pollers_by_token: dict[str, _TelegramPollerHandle] = {}
        self.settings_notebook: ttk.Notebook | None = None
        self.crawl_locks_by_tab: dict[int, threading.Lock] = {
            index: threading.Lock() for index in range(len(self.settings_tabs))
        }
        self.kakao_send_lock = threading.Lock()
        self.telegram_send_locks: dict[str, threading.Lock] = {}
        self.telegram_last_send_monotonic: dict[str, float] = {}
        # 키워드 감지 자동응답기. 모든 토큰 폴러가 공유한다. 대상(채팅방/토픽)별
        # 쿨다운 상태를 한 곳에서 관리하고, 설정은 메시지마다 config.json에서 다시 읽는다.
        self.keyword_responder = KeywordResponder()

        self.root.title("배달 실적봇 (배민·쿠팡이츠)")
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
            "platform_name": StringVar(value=settings.platform_name),
            "baemin_center_name": StringVar(value=settings.baemin_center_name),
            "baemin_center_id": StringVar(value=settings.baemin_center_id),
            "browser_mode": StringVar(value=settings.browser_mode),
            "cdp_url": StringVar(value=settings.cdp_url),
            "browser_user_data_dir": StringVar(value=str(settings.browser_user_data_dir)),
            "log_dir": StringVar(value=str(settings.log_dir)),
            "kakao_chat_name": StringVar(value=settings.kakao_chat_name),
            "telegram_bot_token": StringVar(value=settings.telegram_bot_token),
            "telegram_chat_id": StringVar(value=settings.telegram_chat_id),
            "telegram_message_thread_id": StringVar(value=settings.telegram_message_thread_id),
            "messenger_name": StringVar(value=settings.messenger_name),
            "interval_minutes": StringVar(value=str(settings.interval_minutes)),
            "page_timeout_seconds": StringVar(value=str(settings.page_timeout_seconds)),
            "run_lock_timeout_seconds": StringVar(value=str(settings.run_lock_timeout_seconds)),
            "coupang_login_id": StringVar(value=settings.coupang_login_id),
            "coupang_login_password": StringVar(value=settings.coupang_login_password),
            "verification_email_address": StringVar(value=settings.verification_email_address),
            "verification_email_app_password": StringVar(value=settings.verification_email_app_password),
            "verification_email_subject_keyword": StringVar(value=settings.verification_email_subject_keyword),
            "verification_email_sender_keyword": StringVar(value=settings.verification_email_sender_keyword),
            "headless": BooleanVar(value=settings.headless),
            "send_enabled": BooleanVar(value=settings.send_enabled),
            "send_only_on_change": BooleanVar(value=settings.send_only_on_change),
            "coupang_auto_email_2fa_enabled": BooleanVar(value=settings.coupang_auto_email_2fa_enabled),
        }

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # 창 높이보다 내용(설정 9탭 + 미리보기·로그 + 버튼)이 길면 하단이 화면 밖으로
        # 잘린다. 전체 내용을 세로 스크롤 가능한 캔버스에 담아 작은 창에서도 메시지
        # 미리보기·로그와 버튼까지 모두 스크롤해서 볼 수 있게 한다.
        container = ttk.Frame(self.root)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        canvas = Canvas(container, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        vscroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vscroll.set)

        outer = ttk.Frame(canvas, padding=18)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        outer_window = canvas.create_window((0, 0), window=outer, anchor="nw")

        def _sync_scrollregion(_event: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _stretch_outer(event: Any) -> None:
            # 내용 프레임 너비를 캔버스 너비에 맞춰 가로 스크롤 없이 꽉 채운다.
            canvas.itemconfigure(outer_window, width=event.width)

        outer.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _stretch_outer)
        self._bind_mousewheel(canvas)

        title = ttk.Label(outer, text="배달 실적봇 (배민·쿠팡이츠)", font=("", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            outer,
            text="선택한 플랫폼(배민·쿠팡이츠)의 로그인된 실적 페이지를 읽고 선택한 채널로 텍스트 실적을 보냅니다.",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 14))

        self._build_settings(outer).grid(row=2, column=0, sticky="ew")
        self._build_runtime(outer).grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        self._build_buttons(outer).grid(row=4, column=0, sticky="ew", pady=(14, 0))

    def _bind_mousewheel(self, canvas: Canvas) -> None:
        # 마우스 휠로 전체 창을 스크롤한다. bind_all이라 어느 위젯 위에 있어도 동작하며,
        # 미리보기·로그 Text는 자체 휠 바인딩에서 "break"로 가로채 그 안만 스크롤한다.
        def _on_mousewheel(event: Any) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _on_preview_mousewheel(self, event: Any) -> str:
        self.preview.yview_scroll(int(-event.delta / 120), "units")
        return "break"

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

        notebook.bind("<<NotebookSelected>>", lambda _event: self._on_tab_changed())
        return frame

    def _build_settings_fields(self, frame: ttk.Frame, tab_vars: dict[str, StringVar | BooleanVar]) -> None:
        rows = [
            ("실적/달성현황 URL", "performance_url"),
            ("보조 URL(쿠팡 피크 대시보드)", "peak_dashboard_url"),
            ("센터명(배민 센터명 / 쿠팡 기대 센터·상점명)", "baemin_center_name"),
            ("배민 센터 ID(쿠팡 미사용)", "baemin_center_id"),
            ("CDP 주소", "cdp_url"),
            ("앱 전용 브라우저 프로필 경로", "browser_user_data_dir"),
            ("텔레그램 봇 토큰", "telegram_bot_token"),
            ("텔레그램 채팅방 ID", "telegram_chat_id"),
            ("텔레그램 토픽 ID(선택)", "telegram_message_thread_id"),
            ("로그 경로", "log_dir"),
            ("카카오톡 채팅방명", "kakao_chat_name"),
            ("메세지 전송 간격(분)", "interval_minutes"),
            ("페이지 타임아웃(ms)", "page_timeout_seconds"),
            ("락 타임아웃(초)", "run_lock_timeout_seconds"),
            ("쿠팡 로그인 아이디(자동복구)", "coupang_login_id"),
            ("쿠팡 로그인 비밀번호(자동복구)", "coupang_login_password"),
            ("인증 이메일 주소(naver/gmail)", "verification_email_address"),
            ("인증 이메일 비밀번호(앱 비밀번호)", "verification_email_app_password"),
            ("인증 메일 제목 키워드(기본 인증번호)", "verification_email_subject_keyword"),
            ("인증 메일 발신자 키워드(기본 coupang)", "verification_email_sender_keyword"),
        ]
        entry_widgets = {}
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            entry_kwargs: dict[str, Any] = {"textvariable": tab_vars[key]}
            if key in ("coupang_login_password", "verification_email_app_password"):
                # 비밀번호는 화면에 가린다. 저장 시 설정 JSON에는 ref만 남고 평문은 local store로 분리된다.
                entry_kwargs["show"] = "*"
            entry = ttk.Entry(frame, **entry_kwargs)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if key in MESSENGER_FIELD_KEYS or key in PLATFORM_URL_FIELD_KEYS:
                entry_widgets[key] = entry

        checks = ttk.Frame(frame)
        checks.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(checks, text="플랫폼").grid(row=0, column=0, padx=(0, 8))
        for offset, (value, label) in enumerate(PLATFORM_OPTIONS, start=1):
            ttk.Radiobutton(
                checks,
                text=label,
                value=value,
                variable=tab_vars["platform_name"],
            ).grid(row=0, column=offset, sticky="w", padx=(0, 18))
        ttk.Label(checks, text="브라우저 연결").grid(row=1, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Combobox(
            checks,
            textvariable=tab_vars["browser_mode"],
            values=("cdp", "persistent"),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="w", pady=(8, 0), padx=(0, 18))
        ttk.Checkbutton(checks, text="Headless", variable=tab_vars["headless"]).grid(
            row=1, column=2, sticky="w", pady=(8, 0), padx=(0, 18)
        )
        ttk.Label(checks, text="전송 방식").grid(row=2, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        for offset, (value, label) in enumerate(MESSENGER_OPTIONS, start=1):
            ttk.Radiobutton(
                checks,
                text=label,
                value=value,
                variable=tab_vars["messenger_name"],
            ).grid(row=2, column=offset, sticky="w", pady=(8, 0), padx=(0, 18))
        ttk.Checkbutton(checks, text="메시지 전송", variable=tab_vars["send_enabled"]).grid(
            row=2,
            column=3,
            sticky="w",
            pady=(8, 0),
            padx=(0, 18),
        )
        ttk.Checkbutton(checks, text="변경 시에만 전송", variable=tab_vars["send_only_on_change"]).grid(
            row=2,
            column=4,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Checkbutton(
            checks,
            text="쿠팡 로그인 만료 시 자동복구(이메일 2FA)",
            variable=tab_vars["coupang_auto_email_2fa_enabled"],
        ).grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self._bind_messenger_field_states(tab_vars, entry_widgets)
        self._bind_platform_field_states(tab_vars, entry_widgets)

    def _bind_platform_field_states(
        self,
        tab_vars: dict[str, StringVar | BooleanVar],
        entry_widgets: dict[str, object],
    ) -> None:
        platform_var = tab_vars["platform_name"]

        def update_states(*_args: object) -> None:
            _apply_platform_field_states(entry_widgets, str(platform_var.get()), tab_vars)

        platform_var.trace_add("write", update_states)
        update_states()

    def _bind_messenger_field_states(
        self,
        tab_vars: dict[str, StringVar | BooleanVar],
        entry_widgets: dict[str, object],
    ) -> None:
        messenger_var = tab_vars["messenger_name"]

        def update_states(*_args: object) -> None:
            _apply_messenger_field_states(entry_widgets, str(messenger_var.get()))

        messenger_var.trace_add("write", update_states)
        update_states()

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
                "1. 플랫폼을 배민 또는 쿠팡이츠로 선택하세요. 플랫폼별로 입력 항목이 다릅니다.\n"
                "2. 배민은 로그인된 달성현황(beta) 페이지를, 쿠팡이츠는 로그인 직후 열리는 peak-dashboard 페이지를 열어두세요.\n"
                "3. 쿠팡이츠는 '실적/달성현황 URL'에 peak-dashboard가 자동 입력·고정됩니다(보조 URL은 사용하지 않음). "
                "센터명 칸은 배민은 센터명, 쿠팡은 기대 센터/상점명으로 두 플랫폼 모두 필수입니다"
                "(쿠팡은 배민 기본값 그대로 두면 저장이 거부됩니다).\n"
                "4. 기본값은 원격 디버깅 포트로 실행한 Chrome에 연결합니다.\n"
                "5. 쿠팡이츠는 '쿠팡 로그인 만료 시 자동복구'를 켜면 입력한 아이디·비밀번호와 "
                "인증 이메일을 IMAP으로 읽어 자동 재로그인을 시도합니다. Gmail/Naver 공급자는 이메일 주소 도메인으로 "
                "자동 선택되고, Gmail 앱 비밀번호는 공백 포함 형태로 붙여넣어도 됩니다. "
                "자동복구는 탭별 설정이며 CLI --once는 이메일 자동복구를 지원하지 않습니다.\n"
                "6. 전송 방식(텔레그램/카카오톡)은 플랫폼 선택과 무관하게 따로 고르세요.\n"
                "7. 텔레그램은 봇 토큰과 그룹방 chat_id를 입력하고, 토픽 그룹이면 토픽 ID도 입력하세요.\n"
                "8. 카카오톡은 채팅방명을 입력하고 PC 앱 채팅방 창을 열어두세요.\n"
                "9. 여러 계정은 탭마다 다른 CDP 포트와 브라우저 프로필 경로를 사용하세요.\n"
                "10. 처음에는 메시지 전송을 끄고 1회 실행으로 메시지를 확인하세요.\n"
                "11. 시작/중지는 현재 보고 있는 탭에만 적용됩니다. 탭별로 따로 시작하고 각 탭의 메세지 전송 간격으로 반복됩니다."
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
        preview_scroll = ttk.Scrollbar(preview_box, orient="vertical", command=self.preview.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=preview_scroll.set)
        self.preview.bind("<MouseWheel>", self._on_preview_mousewheel)
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
            selected_index = self._selected_tab_index()
            validate_active_tab_isolation(settings_tabs)
            _validate_coupang_auto_2fa_credentials(selected_index, settings_tabs[selected_index])
        except ValueError as exc:
            messagebox.showerror("설정 오류", str(exc))
            return None

        for index, settings in enumerate(settings_tabs):
            if disable_unsupported_send(settings):
                self.vars_by_tab[index]["send_enabled"].set(False)
                self._append_preview("[안내]\n카카오톡 전송은 Windows에서만 지원되어 미리보기로 실행합니다.\n")

        self.store.save_all(settings_tabs)
        self.settings_tabs = settings_tabs
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
            message = prepare_chrome(settings.to_app_config(crawl_name=f"크롤링{selected_index + 1}", state_subdir=_state_subdir_for(settings, selected_index)))
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
        # '시작'은 현재 보고 있는 탭 하나만 실행한다. 다른 탭은 영향을 주지 않으므로
        # 탭마다 다른 메세지 전송 간격이 그대로 적용된다.
        tab_index = self._selected_tab_index()
        if self._has_live_workers(tab_index):
            self.status_var.set(f"크롤링{tab_index + 1} 중지 처리 중")
            return

        if self.save_settings() is None:
            return

        settings = self.settings_tabs[tab_index]
        if not settings.performance_url.strip():
            self.status_var.set("활성 탭 없음")
            self._append_preview(f"[안내]\n크롤링{tab_index + 1} 실적 URL이 입력되지 않았습니다.\n")
            return

        stop_event = threading.Event()
        self.stop_events_by_tab[tab_index] = stop_event
        scheduler = BotScheduler(
            interval_minutes=settings.interval_minutes,
            run_job=lambda event=stop_event: self._run_once_background(
                tab_index,
                settings,
                event,
            ),
        )
        worker = threading.Thread(
            target=scheduler.run_loop,
            kwargs={"stop_event": stop_event},
            daemon=True,
        )
        self.workers_by_tab[tab_index] = [worker]

        self._start_telegram_listener(tab_index, settings)
        worker.start()
        self._refresh_run_controls()
        self.status_var.set(f"크롤링{tab_index + 1} 실행 중")

    def stop(self) -> None:
        tab_index = self._selected_tab_index()
        stop_event = self.stop_events_by_tab.get(tab_index)
        if stop_event is not None:
            stop_event.set()
        # 이 탭의 텔레그램 명령 폴러도 함께 정리한다(같은 토큰의 다른 탭이 남아
        # 있으면 폴러는 유지하고 이 탭만 라우팅에서 제외).
        self._stop_telegram_listener(tab_index)
        self.status_var.set(f"크롤링{tab_index + 1} 중지 요청됨")
        self.next_run_var.set("-")
        self._refresh_run_controls()

    def _run_once_background(
        self,
        tab_index: int,
        settings: UiSettings,
        stop_event: threading.Event | None = None,
    ) -> bool:
        if _stop_requested(stop_event):
            self.messages.put(("status", f"크롤링{tab_index + 1} 중지됨"))
            return False
        label = f"크롤링{tab_index + 1}"
        tab_lock = self._crawl_lock_for_tab(tab_index)
        if not tab_lock.acquire(blocking=False):
            self.messages.put(("status", f"{label} 이미 실행 중, 건너뜀"))
            self.messages.put(("log", f"{label} 이미 실행 중, 건너뜀"))
            return True

        self.messages.put(("status", f"{label} 실행 중"))
        self.messages.put(("log", f"{label} 시작"))
        try:
            if _stop_requested(stop_event):
                self.messages.put(("status", f"{label} 중지됨"))
                return False
            result = run_once(
                settings.to_app_config(crawl_name=label, state_subdir=_state_subdir_for(settings, tab_index)),
                send_message=self._send_message_with_kakao_lock,
            )
        except CdpUnavailableError as exc:
            # Chrome이 CDP 포트에 안 떠 있는 환경 오류. 5초 재시도로는 사람이 Chrome을
            # 켜기 전에는 절대 복구되지 않으므로, 빠른 재시도(False)를 막고 정규 주기까지
            # 기다린다(True). 또 전체 스택트레이스를 매번 파일에 쌓지 않고 한 줄로만
            # 알려, run_errors.log와 연결 시도가 폭주하던 문제를 함께 없앤다.
            self.messages.put(("error", f"{label} {exc}"))
            return True
        except BrowserActionRequiredError as exc:
            # 로그인 만료나 중복/누락된 CDP 탭처럼 사용자가 Chrome에서 조치해야 하는
            # 상태다. 자동 복구되지 않으므로 이 탭의 반복 실행을 완전히 멈춘다.
            # 멈춘 원인을 나중에 확인할 수 있도록 run_errors.log에도 남긴다(과거에는
            # UI 메시지로만 떠서, 왜 특정 시각 이후 탭이 멈췄는지 로그로 확인할 수 없었다.
            # 쿠팡 자동 2FA 복구가 실패해 여기로 떨어진 경우도 이 줄로 흔적이 남는다).
            self._append_run_error(f"{label} 로그인 만료·조치 필요로 자동 중지", exc, log_dir=settings.log_dir)
            if stop_event is not None:
                stop_event.set()
            self.messages.put(("status", f"{label} 중지됨"))
            return False
        except TelegramSendError as exc:
            # Transient send failures (rate limit, brief network blip) retry soon
            # by returning False. But when the failure is ambiguous (the request
            # may have reached Telegram and the message could already be sent), a
            # fast retry would re-send every 5s, because run_once only records the
            # last hash after a clean success. Wait the full interval there so the
            # operator can intervene instead of double-sending.
            self.messages.put(("error", f"{label} 텔레그램 전송 오류: {exc}"))
            return _send_failure_requests_retry(exc)
        except KakaoSendError as exc:
            # Transient KakaoTalk failures (window briefly closed, focus stolen)
            # should retry soon like Telegram, not wait the full interval. The
            # kakao_send_lock is released by its `with` block on the exception,
            # so a fast retry here does not block other tabs' sends. But when the
            # failure is ambiguous (Enter pressed, result unconfirmed) the message
            # may already be delivered, so skip the fast retry to avoid double-send.
            self.messages.put(("error", f"{label} 카카오톡 전송 오류: {exc}"))
            return _send_failure_requests_retry(exc)
        except Exception as exc:  # UI boundary: surface errors to the operator.
            # 크롤링/파싱/플랫폼 오류(일시적 페이지 로딩 실패 등)도 텔레그램/카카오
            # 전송 오류처럼 빠른 재시도 경로(False 반환)를 타게 한다. True를 반환하면
            # 스케줄러가 다음 정규 주기까지 기다려 일시 장애 복구가 늦어진다.
            self._append_run_error(f"{label} 실행 중 예외", exc, log_dir=settings.log_dir)
            return False
        finally:
            tab_lock.release()
        self.messages.put(("log", f"{label} 완료"))
        self.messages.put(("result", (tab_index, result, settings.interval_minutes)))
        return True

    def _crawl_lock_for_tab(self, tab_index: int) -> threading.Lock:
        if not hasattr(self, "crawl_locks_by_tab"):
            self.crawl_locks_by_tab = {}
        return self.crawl_locks_by_tab.setdefault(tab_index, threading.Lock())

    def _send_message_with_kakao_lock(self, config: AppConfig, message: str) -> None:
        label = config.crawl_name.strip() or "크롤링"
        if config.messenger_name == "telegram":
            lock = self._telegram_send_lock_for(config)
            self.messages.put(("log", f"{label} 텔레그램 전송 대기"))
            with lock:
                self._wait_for_telegram_send_slot(config)
                try:
                    dispatch_text_message(config, message)
                finally:
                    self._remember_telegram_send_time(config)
            self.messages.put(("log", f"{label} 텔레그램 전송 완료"))
            return

        if config.messenger_name != "kakao":
            dispatch_text_message(config, message)
            return

        if not hasattr(self, "kakao_send_lock"):
            self.kakao_send_lock = threading.Lock()
        self.messages.put(("log", f"{label} 카카오톡 전송 대기"))
        with self.kakao_send_lock:
            dispatch_text_message(config, message)
        self.messages.put(("log", f"{label} 카카오톡 전송 완료"))

    def _telegram_send_lock_for(self, config: AppConfig) -> threading.Lock:
        if not hasattr(self, "telegram_send_locks"):
            self.telegram_send_locks = {}
        token = config.telegram_bot_token.strip()
        return self.telegram_send_locks.setdefault(token, threading.Lock())

    def _send_telegram_command_reply_with_lock(
        self,
        config: AppConfig,
        message: str,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        from .sender import send_telegram_text

        lock = self._telegram_send_lock_for(config)
        with lock:
            self._wait_for_telegram_send_slot(config)
            try:
                send_telegram_text(config, message, message_thread_id=message_thread_id)
            finally:
                self._remember_telegram_send_time(config)

    def _wait_for_telegram_send_slot(self, config: AppConfig) -> None:
        if not hasattr(self, "telegram_last_send_monotonic"):
            self.telegram_last_send_monotonic = {}
        token = config.telegram_bot_token.strip()
        if not token:
            return
        previous = self.telegram_last_send_monotonic.get(token)
        if previous is None:
            return
        wait_seconds = TELEGRAM_SEND_MIN_INTERVAL_SECONDS - (time.monotonic() - previous)
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _remember_telegram_send_time(self, config: AppConfig) -> None:
        if not hasattr(self, "telegram_last_send_monotonic"):
            self.telegram_last_send_monotonic = {}
        token = config.telegram_bot_token.strip()
        if token:
            self.telegram_last_send_monotonic[token] = time.monotonic()

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
                self._append_preview(f"[오류]\n{payload}\n", tag="error")
            elif kind == "log":
                self._append_preview(f"[로그]\n{payload}\n")
            elif kind == "result":
                tab_index, result, interval_minutes = payload
                self._show_result(tab_index, result, interval_minutes)

        # 시작/중지 버튼은 현재 보고 있는 탭의 실행 상태를 따라야 한다. 탭 전환
        # 이벤트(<<NotebookSelected>>)에만 의존하지 않고, 폴링마다 선택 탭 기준으로
        # 버튼 상태를 다시 맞춰 "다른 탭에서 시작했는데 이 탭 버튼이 잠겨 있는" 문제를
        # 막는다.
        self._refresh_run_controls()
        self.root.after(200, self._poll_messages)

    def _show_result(self, tab_index: int, result: RunResult, interval_minutes: int) -> None:
        if result.skipped:
            status = "중복 메시지 건너뜀"
        elif result.sent:
            status = "전송 완료"
        else:
            status = "메시지 생성 완료(전송 꺼짐)"

        self.status_var.set(status)
        next_run_time = (datetime.now() + timedelta(minutes=interval_minutes)).strftime("%H:%M:%S")
        self.next_run_var.set(f"크롤링{tab_index + 1} {next_run_time}")
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

    def _on_tab_changed(self) -> None:
        # 시작/중지 버튼은 현재 보고 있는 탭에 작용하므로, 탭을 바꾸면 그 탭의 실행
        # 상태에 맞게 버튼 활성화를 다시 맞춘다.
        self._sync_selected_vars()
        self._refresh_run_controls()

    def _start_telegram_listener(self, tab_index: int, settings: UiSettings) -> None:
        config = settings.to_app_config(
            crawl_name=f"크롤링{tab_index + 1}", state_subdir=_state_subdir_for(settings, tab_index)
        )
        if not telegram_configs_by_token([config]):
            self._append_preview(
                f"[안내]\n크롤링{tab_index + 1} 텔레그램 봇 토큰과 채팅방 ID가 없어 명령 감지를 시작하지 않습니다.\n"
            )
            return

        token = config.telegram_bot_token.strip()
        self._cleanup_stopped_telegram_poller(token)
        handle = self.telegram_pollers_by_token.get(token)
        if handle is not None and handle.worker.is_alive() and handle.stop_event.is_set():
            self.messages.put(("log", f"Telegram poller stopping for token; start skipped: {token[:8]}..."))
            return
        if handle is not None and handle.worker.is_alive():
            # 같은 토큰의 폴러가 이미 돌고 있다. 폴러를 새로 띄우지 않고 이 탭을
            # 라우팅 대상에 추가만 한다. 두 폴러가 getUpdates를 나눠 받아 명령이
            # 누락되는 것을 막는다.
            handle.tab_indexes.add(tab_index)
            self._rebuild_telegram_routing(token)
            self.messages.put(
                ("log", f"크롤링{tab_index + 1} 텔레그램 명령 라우팅 추가(토큰 공유): 채팅방 {len(handle.processor.configs)}개")
            )
            return

        configs, locks_by_target = self._telegram_routing_for_token(token, extra_tab=tab_index)
        processor = TelegramCommandProcessor(
            configs,
            locks_by_target=locks_by_target,
            send_text=self._send_telegram_command_reply_with_lock,
            log_event=lambda message: self.messages.put(("log", message)),
            keyword_responder=getattr(self, "keyword_responder", None),
        )
        poller = TelegramUpdatePoller(configs[0], handle_text=processor.handle_text)
        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._telegram_poll_loop,
            args=(poller, stop_event),
            daemon=True,
        )
        self.telegram_pollers_by_token[token] = _TelegramPollerHandle(
            stop_event=stop_event,
            worker=worker,
            processor=processor,
            tab_indexes={tab_index},
        )
        self.messages.put(("log", f"크롤링{tab_index + 1} 텔레그램 poller 시작: 채팅방 {len(configs)}개"))
        worker.start()

    def _stop_telegram_listener(self, tab_index: int) -> None:
        # 이 탭이 쓰던 토큰 폴러에서 탭을 뺀다. 같은 토큰의 다른 탭이 아직 돌고
        # 있으면 폴러는 유지하고 라우팅만 갱신한다. 마지막 탭이면 폴러를 멈춘다.
        for token, handle in list(self.telegram_pollers_by_token.items()):
            if tab_index not in handle.tab_indexes:
                continue
            handle.tab_indexes.discard(tab_index)
            if handle.tab_indexes:
                self._rebuild_telegram_routing(token)
            else:
                handle.stop_event.set()
                self._cleanup_stopped_telegram_poller(token)

    def _cleanup_stopped_telegram_poller(self, token: str) -> None:
        handle = self.telegram_pollers_by_token.get(token)
        if handle is not None and not handle.worker.is_alive():
            self.telegram_pollers_by_token.pop(token, None)

    def _rebuild_telegram_routing(self, token: str) -> None:
        handle = self.telegram_pollers_by_token.get(token)
        if handle is None:
            return
        configs, locks_by_target = self._telegram_routing_for_token(token)
        if not configs:
            return
        handle.processor.update_routing(configs, locks_by_target=locks_by_target)

    def _telegram_routing_for_token(
        self, token: str, *, extra_tab: int | None = None
    ) -> tuple[list[AppConfig], dict]:
        # 이 토큰을 공유하는 '실행 중'(또는 막 시작하는 extra_tab) 탭들의 config와
        # 대상별 락을 모은다. 라우팅은 활성 탭만 대상으로 해야 중지된 탭으로 명령이
        # 가지 않는다.
        handle = self.telegram_pollers_by_token.get(token)
        active_indexes = set(handle.tab_indexes) if handle is not None else set()
        if extra_tab is not None:
            active_indexes.add(extra_tab)

        indexed: list[tuple[int, UiSettings]] = []
        for index in sorted(active_indexes):
            tab_settings = self.settings_tabs[index]
            if tab_settings.telegram_bot_token.strip() != token:
                continue
            indexed.append((index, tab_settings))

        configs = app_configs_from_settings(indexed)
        locks_by_target = {
            _telegram_target_key(tab_settings): self._crawl_lock_for_tab(index)
            for index, tab_settings in indexed
            if _telegram_target_key(tab_settings) is not None
        }
        return configs, locks_by_target

    def _telegram_poll_loop(self, poller: TelegramUpdatePoller, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                poller.poll_once()
            except Exception as exc:
                self._append_run_error("텔레그램 명령 감지", exc, log_dir=self.settings.log_dir)
                stop_event.wait(5)

    def _has_live_workers(self, tab_index: int) -> bool:
        # 시작/중지 버튼은 이 탭의 크롤링 워커 상태만 따른다. 텔레그램 명령 폴러는
        # 토큰 단위로 별도 관리되며(같은 토큰의 다른 탭과 공유될 수 있어) 버튼 상태의
        # 기준이 아니다.
        workers = self.workers_by_tab.get(tab_index, [])
        return any(worker.is_alive() for worker in workers)

    def _refresh_run_controls(self) -> None:
        if not hasattr(self, "start_button") or not hasattr(self, "stop_button"):
            return

        tab_index = self._selected_tab_index()
        stop_event = self.stop_events_by_tab.get(tab_index)
        stopping = stop_event is not None and stop_event.is_set()

        # 중지 요청 후 워커가 모두 종료되면 이 탭의 실행 상태를 비운다.
        if stopping and not self._has_live_workers(tab_index):
            self.workers_by_tab.pop(tab_index, None)
            self.stop_events_by_tab.pop(tab_index, None)
            stopping = False

        if stopping:
            # 정지 처리 중: 두 버튼 모두 비활성화해 중복 조작을 막는다.
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            return

        running = self._has_live_workers(tab_index)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _append_preview(self, text: str, *, tag: str | None = None) -> None:
        self.preview.configure(state="normal")
        if tag is None:
            self.preview.insert("end", text)
        else:
            self.preview.insert("end", text, tag)
        self.preview.see("end")
        self.preview.configure(state="disabled")

    def _append_run_error(self, prefix: str, exc: Exception, *, log_dir: Path) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        log_path = self._write_run_error_log(prefix, detail, log_dir=log_dir)
        if log_path is None:
            self.messages.put(("error", f"{prefix}: {exc}\n{detail}"))
        else:
            self.messages.put(("error", f"{prefix}: {exc}\n상세 로그: {log_path}"))

    def _write_run_error_log(self, prefix: str, detail: str, *, log_dir: Path) -> Path | None:
        target_dir = log_dir if log_dir else Path("logs")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / "run_errors.log"
            # append 직전 크기 기준 rotation(무한 증가 방지, NFR-10). 이미 감싼 try/except
            # 안이라 rotation 실패도 None 반환으로 흡수되고, 헬퍼 자체도 best-effort다.
            rotate_if_needed(path)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = (
                f"[{ts}] {prefix}\n"
                f"{detail}\n"
                "----------------------------------------\n"
            )
            with path.open("a", encoding="utf-8") as file:
                file.write(message)
            return path
        except Exception:
            return None


def run_cli_once() -> None:
    result = run_once(AppConfig.from_env())
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


def _messenger_name(raw: Any) -> str:
    value = str(raw).strip() or "telegram"
    valid_names = {name for name, _label in MESSENGER_OPTIONS}
    if value not in valid_names:
        raise ValueError("전송 방식은 텔레그램 또는 카카오톡만 선택하세요")
    return value


def _platform_name(raw: Any) -> str:
    value = str(raw).strip().casefold() or "baemin"
    valid_names = {name for name, _label in PLATFORM_OPTIONS}
    if value not in valid_names:
        raise ValueError("플랫폼은 배민 또는 쿠팡이츠만 선택하세요")
    return value


def _messenger_field_states(messenger_name: str) -> dict[str, str]:
    value = messenger_name.strip() or "telegram"
    telegram_state = "normal" if value == "telegram" else "disabled"
    kakao_state = "normal" if value == "kakao" else "disabled"
    return {
        **{key: telegram_state for key in TELEGRAM_FIELD_KEYS},
        **{key: kakao_state for key in KAKAO_FIELD_KEYS},
    }


def _apply_messenger_field_states(entry_widgets: dict[str, object], messenger_name: str) -> None:
    for key, state in _messenger_field_states(messenger_name).items():
        widget = entry_widgets.get(key)
        if widget is not None:
            widget.configure(state=state)


def _apply_platform_field_states(
    entry_widgets: dict[str, object],
    platform_name: str,
    tab_vars: dict[str, StringVar | BooleanVar],
) -> None:
    is_coupang = platform_name.strip().casefold() == "coupang"
    perf_entry = entry_widgets.get("performance_url")
    peak_entry = entry_widgets.get("peak_dashboard_url")
    if is_coupang:
        # 쿠팡은 로그인 직후 열리는 peak-dashboard 한 페이지만 읽는다. '실적/달성현황
        # URL'에 그 값을 고정 입력하고 읽기 전용으로 잠가 다른 URL이 들어가지 않게 한다.
        # 보조 URL은 더 이상 쓰지 않으므로 비우고 비활성화한다.
        if tab_vars["performance_url"].get().strip() != DEFAULT_COUPANG_PEAK_DASHBOARD_URL:
            tab_vars["performance_url"].set(DEFAULT_COUPANG_PEAK_DASHBOARD_URL)
        if tab_vars["peak_dashboard_url"].get().strip():
            tab_vars["peak_dashboard_url"].set("")
        if perf_entry is not None:
            perf_entry.configure(state="readonly")
        if peak_entry is not None:
            peak_entry.configure(state="disabled")
    else:
        if perf_entry is not None:
            perf_entry.configure(state="normal")
        if peak_entry is not None:
            peak_entry.configure(state="normal")
        # 쿠팡 → 배민으로 바꾸면 잠겨 있던 peak-dashboard 값이 '실적/달성현황 URL'에 남는다.
        # 배민에는 맞지 않으므로, 쿠팡 URL이 남아 있으면 비워 사용자가 배민 URL을 입력하게 한다.
        if _looks_like_coupang_url(str(tab_vars["performance_url"].get())):
            tab_vars["performance_url"].set("")


def _looks_like_coupang_url(url: str) -> bool:
    return (urlsplit(url.strip()).hostname or "").casefold() == _COUPANG_HOST


def _validate_unique_active_value(
    indexed_settings: list[tuple[int, UiSettings]],
    *,
    label: str,
    key,
) -> None:
    seen: dict[object, int] = {}
    for index, settings in indexed_settings:
        value = key(settings)
        if value is None:
            continue
        if value in seen:
            first_index = seen[value]
            raise ValueError(
                f"{label}가 중복되었습니다: 크롤링{first_index + 1}, 크롤링{index + 1}. "
                "여러 배민 아이디는 탭마다 다른 CDP 포트와 다른 브라우저 프로필 경로를 사용하세요."
            )
        seen[value] = index


def _validate_active_cdp_local(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    for index, settings in indexed_settings:
        if settings.browser_mode != "cdp":
            continue
        host = (urlsplit(settings.cdp_url.strip()).hostname or "").casefold()
        if host in {"127.0.0.1", "localhost"}:
            continue
        raise ValueError(
            f"크롤링{index + 1} CDP 주소는 IPv4 로컬 주소만 허용합니다. 예: http://127.0.0.1:9222\n"
            "원격 CDP 주소는 다른 로그인 세션을 읽을 수 있어 차단합니다."
        )


def _validate_active_baemin_center_identity(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    seen_name_only: dict[str, int] = {}
    for index, settings in indexed_settings:
        if settings.platform_name != "baemin":
            continue
        center_name = settings.baemin_center_name.strip()
        center_id = settings.baemin_center_id.strip()
        if not center_name and not center_id:
            raise ValueError(
                f"크롤링{index + 1} 배민 탭은 배민 센터명 또는 배민 센터 ID를 입력하세요. "
                "여러 배민 아이디는 탭마다 센터 정보를 확인할 수 있어야 합니다."
            )
        # Same center name across tabs cannot tell two accounts apart, so each
        # such tab must carry a center ID to distinguish the underlying account.
        if center_name and not center_id:
            previous = seen_name_only.get(center_name.casefold())
            if previous is not None:
                raise ValueError(
                    f"배민 센터명이 중복되었습니다: 크롤링{previous + 1}, 크롤링{index + 1}. "
                    "같은 센터명을 쓰는 탭은 계정 구분을 위해 각각 배민 센터 ID를 입력하세요."
                )
            seen_name_only[center_name.casefold()] = index


_COUPANG_HOST = "partner.coupangeats.com"


def _validate_active_coupang_urls(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    for index, settings in indexed_settings:
        if settings.platform_name != "coupang":
            continue
        # 쿠팡 탭은 로그인 직후 열리는 peak-dashboard 한 페이지만 읽는다. '실적/달성현황
        # URL'(주 URL)이 peak-dashboard인지 확인해 잘못된 페이지 크롤링을 막는다. scheme
        # 까지 https로 강제한다. 크롤러의 탭 매칭은 scheme까지 비교하므로(crawler.
        # _url_matches), http로 저장하면 "저장은 됐는데 https 탭을 못 찾는" 상태가 된다.
        # 보조 URL(peak_dashboard_url)은 더 이상 쓰지 않으므로 검증하지 않는다.
        performance_url = settings.performance_url.strip()
        if not performance_url:
            raise ValueError(f"크롤링{index + 1} 쿠팡 실적/달성현황 URL을 입력하세요.")
        if not _is_coupang_path_url(performance_url, "/page/peak-dashboard"):
            raise ValueError(
                f"크롤링{index + 1} 쿠팡 실적/달성현황 URL은 "
                f"https://{_COUPANG_HOST}/page/peak-dashboard 형식이어야 합니다."
            )
        _validate_coupang_expected_center(index, settings)


def _validate_coupang_expected_center(index: int, settings: UiSettings) -> None:
    # 쿠팡 계정/센터/상점은 CDP 포트와 Chrome 프로필 로그인으로만 결정되므로, 포트나
    # 프로필이 꼬이면 다른 쿠팡 계정 실적을 정상처럼 전송할 수 있다. 크롤러는 기대
    # 센터명(``baemin_center_name``을 쿠팡 탭의 기대 센터/상점명으로 재사용)이 비어
    # 있으면 검증을 건너뛰므로, 다중 쿠팡 계정 운영에서 안전하게 막으려면 저장 단계에서
    # 기대 센터명을 필수로 받아 크롤러의 exact-match 검증이 항상 돌게 한다.
    center_name = settings.baemin_center_name.strip()
    if not center_name:
        raise ValueError(
            f"크롤링{index + 1} 쿠팡 탭은 기대 센터/상점명(배민 센터명 칸)을 입력하세요. "
            "포트/프로필이 잘못 연결되면 다른 쿠팡 계정 실적을 보낼 수 있어, "
            "화면에서 확인된 센터와 대조할 기대값이 필요합니다."
        )
    # 배민 기본 센터명이 그대로 남아 있으면 쿠팡 화면 센터명과 절대 일치하지 않아
    # 크롤링이 항상 실패한다. 저장 단계에서 실제 쿠팡 센터명으로 바꾸도록 막는다.
    if center_name == DEFAULT_BAEMIN_CENTER_NAME:
        raise ValueError(
            f"크롤링{index + 1} 쿠팡 탭의 기대 센터/상점명이 배민 기본값입니다. "
            "실제 쿠팡 센터/상점명으로 바꿔 입력하세요."
        )


def _validate_coupang_auto_2fa_credentials(index: int, settings: UiSettings) -> None:
    if settings.platform_name != "coupang" or not settings.coupang_auto_email_2fa_enabled:
        return
    if not settings.coupang_login_id.strip():
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 쿠팡 로그인 아이디를 입력하세요.")
    if not settings.coupang_login_password:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 쿠팡 로그인 비밀번호를 입력하세요.")
    address = settings.verification_email_address.strip()
    if not address:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 인증 이메일 주소를 입력하세요.")
    if not settings.verification_email_app_password:
        raise ValueError(f"크롤링{index + 1} 자동복구를 켜려면 인증 이메일 앱 비밀번호를 입력하세요.")
    domain = address.rsplit("@", 1)[-1].strip().casefold() if "@" in address else ""
    if domain not in IMAP_HOST_BY_DOMAIN:
        raise ValueError(f"크롤링{index + 1} 인증 이메일은 naver.com 또는 gmail.com 주소여야 합니다.")


def _is_coupang_path_url(url: str, path: str) -> bool:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").casefold()
    scheme = (parsed.scheme or "").casefold()
    return scheme == "https" and host == _COUPANG_HOST and parsed.path.rstrip("/").casefold() == path


def _validate_active_telegram_required(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    for index, settings in indexed_settings:
        if settings.messenger_name != "telegram" or not settings.send_enabled:
            continue
        if not settings.telegram_bot_token.strip():
            raise ValueError(f"크롤링{index + 1} 텔레그램 봇 토큰을 입력하세요.")
        if not settings.telegram_chat_id.strip():
            raise ValueError(f"크롤링{index + 1} 텔레그램 채팅방 ID를 입력하세요.")


def _validate_active_kakao_required(indexed_settings: list[tuple[int, UiSettings]]) -> None:
    for index, settings in indexed_settings:
        if settings.messenger_name != "kakao" or not settings.send_enabled:
            continue
        if not settings.kakao_chat_name.strip():
            raise ValueError(f"크롤링{index + 1} 카카오톡 채팅방명을 입력하세요.")


def _cdp_port_key(settings: UiSettings) -> object:
    if settings.browser_mode != "cdp":
        return None
    value = settings.cdp_url.strip()
    port = urlsplit(value).port
    return port if port is not None else value.casefold()


def _profile_path_key(path: Path) -> str:
    return str(path.expanduser().resolve()).casefold()


def _telegram_target_key(settings: UiSettings | AppConfig) -> tuple[str, str] | None:
    if settings.messenger_name != "telegram" or not settings.send_enabled:
        return None
    chat_id = settings.telegram_chat_id.strip()
    if not chat_id:
        return None
    return (chat_id, _normalize_telegram_thread_id(settings.telegram_message_thread_id))


def _kakao_chat_name_key(settings: UiSettings | AppConfig) -> str | None:
    if settings.messenger_name != "kakao" or not settings.send_enabled:
        return None
    name = settings.kakao_chat_name.strip()
    return name.casefold() if name else None


def _normalize_telegram_thread_id(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return str(int(value))
    except ValueError:
        return value


def _stop_requested(stop_event: threading.Event | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _send_failure_requests_retry(exc: Exception) -> bool:
    """Return what ``_run_once_background`` should return for a send failure.

    ``False`` asks the scheduler for a fast (5s) retry; ``True`` lets it wait the
    full interval. Non-ambiguous failures (message visibly not delivered) take the
    fast path. Ambiguous failures — the message may already have been sent and the
    last hash was not recorded — must skip the fast retry to avoid double-sending.
    """

    return bool(getattr(exc, "ambiguous", False))


def _make_text(parent: ttk.Frame):
    from tkinter import Text

    widget = Text(parent, height=PREVIEW_TEXT_HEIGHT, wrap="word", state="disabled")
    return widget
