from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL,
    DEFAULT_BAEMIN_CENTER_ID,
    DEFAULT_BAEMIN_CENTER_NAME,
    AppConfig,
)


@dataclass
class UiSettings:
    performance_url: str
    peak_dashboard_url: str
    platform_name: str
    baemin_center_name: str
    baemin_center_id: str
    browser_mode: str
    cdp_url: str
    browser_user_data_dir: Path
    headless: bool
    kakao_chat_name: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_message_thread_id: str
    messenger_name: str
    log_dir: Path
    send_enabled: bool
    send_only_on_change: bool
    interval_minutes: int
    timezone: str
    run_lock_timeout_seconds: int
    page_timeout_seconds: int

    @classmethod
    def defaults(cls) -> "UiSettings":
        return cls(
            performance_url=DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL,
            peak_dashboard_url="",
            platform_name="baemin",
            baemin_center_name=DEFAULT_BAEMIN_CENTER_NAME,
            baemin_center_id=DEFAULT_BAEMIN_CENTER_ID,
            browser_mode="cdp",
            cdp_url="http://127.0.0.1:9222",
            browser_user_data_dir=Path("runtime/browser-profile"),
            headless=False,
            kakao_chat_name="",
            telegram_bot_token="",
            telegram_chat_id="",
            telegram_message_thread_id="",
            messenger_name="telegram",
            log_dir=Path("logs"),
            send_enabled=False,
            send_only_on_change=False,
            interval_minutes=35,
            timezone="Asia/Seoul",
            run_lock_timeout_seconds=900,
            page_timeout_seconds=60000,
        )

    @classmethod
    def default_for_tab(cls, tab_index: int) -> "UiSettings":
        settings = cls.defaults()
        if tab_index <= 1:
            return settings

        settings.performance_url = ""
        settings.baemin_center_name = ""
        settings.baemin_center_id = ""
        settings.cdp_url = f"http://127.0.0.1:{9221 + tab_index}"
        settings.browser_user_data_dir = Path(f"runtime/browser-profile-{tab_index}")
        return settings

    def to_app_config(self, *, crawl_name: str = "", state_subdir: str = "") -> AppConfig:
        return AppConfig(
            coupang_eats_url=self.performance_url,
            peak_dashboard_url=self.peak_dashboard_url,
            platform_name=self.platform_name,
            baemin_center_name=self.baemin_center_name,
            baemin_center_id=self.baemin_center_id,
            browser_mode=self.browser_mode,
            cdp_url=self.cdp_url,
            browser_user_data_dir=self.browser_user_data_dir,
            headless=self.headless,
            kakao_chat_name=self.kakao_chat_name,
            telegram_bot_token=self.telegram_bot_token,
            telegram_chat_id=self.telegram_chat_id,
            telegram_message_thread_id=self.telegram_message_thread_id,
            messenger_name=self.messenger_name,
            log_dir=self.log_dir,
            send_enabled=self.send_enabled,
            send_only_on_change=self.send_only_on_change,
            timezone=self.timezone,
            run_lock_timeout_seconds=self.run_lock_timeout_seconds,
            page_timeout_seconds=self.page_timeout_seconds,
            crawl_name=crawl_name,
            state_subdir=state_subdir,
        )


class UiSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> UiSettings:
        if not self.path.exists():
            return UiSettings.defaults()

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("crawlings"), list) and raw["crawlings"]:
            raw = raw["crawlings"][0]
        return _settings_from_mapping(raw, UiSettings.defaults())

    def load_all(self, *, max_tabs: int = 9) -> list[UiSettings]:
        if not self.path.exists():
            return [UiSettings.default_for_tab(index) for index in range(1, max_tabs + 1)]

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("crawlings"), list):
            items = [item for item in raw["crawlings"] if isinstance(item, dict)]
        elif isinstance(raw, dict):
            items = [raw]
        else:
            items = []

        settings: list[UiSettings] = []
        for index in range(1, max_tabs + 1):
            source = items[index - 1] if index - 1 < len(items) else {}
            settings.append(_settings_from_mapping(source, UiSettings.default_for_tab(index)))
        return settings

    def save(self, settings: UiSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(_to_jsonable(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_all(self, settings: list[UiSettings]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"crawlings": [_to_jsonable(item) for item in settings]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_jsonable(settings: UiSettings) -> dict[str, Any]:
    data = asdict(settings)
    data["browser_user_data_dir"] = str(settings.browser_user_data_dir)
    data["log_dir"] = str(settings.log_dir)
    return data


def _settings_from_mapping(raw: dict[str, Any], defaults: UiSettings) -> UiSettings:
    data = asdict(defaults)
    if "interval_minutes" not in raw and "refresh_interval_seconds" in raw:
        data["interval_minutes"] = _seconds_to_minutes(int(raw["refresh_interval_seconds"]))
    for key, value in raw.items():
        if key in data:
            data[key] = value
    if _is_legacy_kakao_mapping(raw):
        data["messenger_name"] = "kakao"
    data["platform_name"] = _infer_platform_name(raw, defaults.platform_name)
    if data["platform_name"] not in {"baemin", "coupang"}:
        data["platform_name"] = defaults.platform_name
    data["browser_user_data_dir"] = Path(data["browser_user_data_dir"])
    data["log_dir"] = Path(data["log_dir"])
    return UiSettings(**data)


def _infer_platform_name(raw: dict[str, Any], default: str) -> str:
    explicit = str(raw.get("platform_name", "")).strip().casefold()
    if explicit:
        return explicit
    url = str(raw.get("performance_url", "")).casefold()
    peak_url = str(raw.get("peak_dashboard_url", "")).casefold()
    if "partner.coupangeats.com" in url or "partner.coupangeats.com" in peak_url:
        return "coupang"
    return default


def _is_legacy_kakao_mapping(raw: dict[str, Any]) -> bool:
    """Old Kakao setups predate ``messenger_name`` and have no Telegram fields.

    Load them as ``kakao`` (regardless of ``send_enabled``) so an existing Kakao
    configuration is not silently treated as Telegram. New tabs still default to
    Telegram because they are created from :meth:`UiSettings.defaults`, not loaded
    from a legacy mapping.
    """

    return (
        bool(str(raw.get("kakao_chat_name", "")).strip())
        and not str(raw.get("messenger_name", "")).strip()
        and not str(raw.get("telegram_bot_token", "")).strip()
        and not str(raw.get("telegram_chat_id", "")).strip()
    )


def _seconds_to_minutes(seconds: int) -> int:
    return max(1, (seconds + 59) // 60)
