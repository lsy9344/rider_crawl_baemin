from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    coupang_eats_url: str
    browser_mode: str
    cdp_url: str
    browser_user_data_dir: Path
    headless: bool
    kakao_chat_name: str
    log_dir: Path
    send_enabled: bool
    send_only_on_change: bool
    timezone: str
    run_lock_timeout_seconds: int
    page_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            coupang_eats_url=os.getenv(
                "COUPANG_EATS_URL",
                "https://partner.coupangeats.com/page/rider-performance",
            ),
            browser_mode=os.getenv("BROWSER_MODE", "cdp"),
            cdp_url=os.getenv("CDP_URL", "http://127.0.0.1:9222"),
            browser_user_data_dir=Path(os.getenv("BROWSER_USER_DATA_DIR", "runtime/browser-profile")),
            headless=_env_bool("HEADLESS", default=False),
            kakao_chat_name=os.getenv("KAKAO_CHAT_NAME", ""),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            send_enabled=_env_bool("SEND_ENABLED", default=False),
            send_only_on_change=_env_bool("SEND_ONLY_ON_CHANGE", default=False),
            timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
            run_lock_timeout_seconds=int(os.getenv("RUN_LOCK_TIMEOUT_SECONDS", "900")),
            page_timeout_seconds=int(os.getenv("PAGE_TIMEOUT_SECONDS", "60000")),
        )

    @property
    def runtime_dir(self) -> Path:
        if self.log_dir.name == "logs":
            return self.log_dir.parent / "runtime"
        return Path("runtime")

    @property
    def state_dir(self) -> Path:
        return self.runtime_dir / "state"


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
