from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig


@dataclass
class UiSettings:
    performance_url: str
    peak_dashboard_url: str
    baemin_center_name: str
    baemin_center_id: str
    browser_mode: str
    cdp_url: str
    browser_user_data_dir: Path
    headless: bool
    kakao_chat_name: str
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
            performance_url=(
                "https://deliverycenter.baemin.com/delivery/history?"
                "page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
            ),
            peak_dashboard_url="",
            baemin_center_name="표준서울마포B이츠앤홀딩스3",
            baemin_center_id="DP2605181318",
            browser_mode="cdp",
            cdp_url="http://127.0.0.1:9222",
            browser_user_data_dir=Path("runtime/browser-profile"),
            headless=False,
            kakao_chat_name="",
            log_dir=Path("logs"),
            send_enabled=False,
            send_only_on_change=False,
            interval_minutes=35,
            timezone="Asia/Seoul",
            run_lock_timeout_seconds=900,
            page_timeout_seconds=60000,
        )

    def to_app_config(self) -> AppConfig:
        return AppConfig(
            coupang_eats_url=self.performance_url,
            baemin_center_name=self.baemin_center_name,
            baemin_center_id=self.baemin_center_id,
            browser_mode=self.browser_mode,
            cdp_url=self.cdp_url,
            browser_user_data_dir=self.browser_user_data_dir,
            headless=self.headless,
            kakao_chat_name=self.kakao_chat_name,
            log_dir=self.log_dir,
            send_enabled=self.send_enabled,
            send_only_on_change=self.send_only_on_change,
            timezone=self.timezone,
            run_lock_timeout_seconds=self.run_lock_timeout_seconds,
            page_timeout_seconds=self.page_timeout_seconds,
        )


class UiSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> UiSettings:
        if not self.path.exists():
            return UiSettings.defaults()

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        defaults = UiSettings.defaults()
        data = asdict(defaults)
        if "interval_minutes" not in raw and "refresh_interval_seconds" in raw:
            data["interval_minutes"] = _seconds_to_minutes(int(raw["refresh_interval_seconds"]))
        data.update(raw)
        data.pop("refresh_interval_seconds", None)
        data["browser_user_data_dir"] = Path(data["browser_user_data_dir"])
        data["log_dir"] = Path(data["log_dir"])
        return UiSettings(**data)

    def save(self, settings: UiSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(_to_jsonable(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _to_jsonable(settings: UiSettings) -> dict[str, Any]:
    data = asdict(settings)
    data["browser_user_data_dir"] = str(settings.browser_user_data_dir)
    data["log_dir"] = str(settings.log_dir)
    return data


def _seconds_to_minutes(seconds: int) -> int:
    return max(1, (seconds + 59) // 60)
