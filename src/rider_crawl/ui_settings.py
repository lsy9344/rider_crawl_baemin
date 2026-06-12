from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_BAEMIN_ACHIEVEMENT_REPORT_URL,
    DEFAULT_BAEMIN_CENTER_ID,
    DEFAULT_BAEMIN_CENTER_NAME,
    DEFAULT_GMAIL_2FA_QUERY,
    DEFAULT_GMAIL_CREDENTIALS_PATH,
    DEFAULT_GMAIL_TOKEN_PATH,
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
    # 쿠팡이츠 로그인 만료 시 자동복구(이메일 2FA) 설정. .env가 아니라 UI에서 입력받아
    # 탭별로 저장한다. 기본은 비활성이며, 켜기 전까지는 기존처럼 로그인 만료 시 탭이 멈춘다.
    coupang_auto_email_2fa_enabled: bool = False
    coupang_login_id: str = ""
    coupang_login_password: str = ""
    gmail_2fa_query: str = DEFAULT_GMAIL_2FA_QUERY
    gmail_credentials_path: str = DEFAULT_GMAIL_CREDENTIALS_PATH
    gmail_token_path: str = DEFAULT_GMAIL_TOKEN_PATH
    # 운영 추적용 안정 식별자(P1-01). 탭 번호가 아니라 ID로 고객/대상을 추적한다. 로드
    # 마이그레이션이 활성 탭에만 불투명 ID(uuid4)를 발급·영속화하고(load_all), 기존 탭명은
    # legacy_alias로 표시/보조 식별만 한다(주 식별자는 monitoring_target_id). state_subdir
    # 연결·플랫폼 중립 필드·secret 분리는 본 스토리 범위 밖(Story 2.2~2.4).
    customer_id: str = ""
    customer_name: str = ""
    platform_account_id: str = ""
    monitoring_target_id: str = ""
    legacy_alias: str = ""

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
            # 쿠팡 자동 이메일 2FA 복구 설정은 UI에서 입력받아 탭별로 저장한 값을 쓴다
            # (.env 사용 안 함). poll/코드 자릿수 등 자주 안 바뀌는 값은 AppConfig 기본값을
            # 그대로 둔다. 빈 경로/검색식은 기본값으로 보정해 잘못된 빈 값으로 덮이지 않게 한다.
            coupang_auto_email_2fa_enabled=self.coupang_auto_email_2fa_enabled,
            coupang_login_id=self.coupang_login_id,
            coupang_login_password=self.coupang_login_password,
            gmail_2fa_query=self.gmail_2fa_query or DEFAULT_GMAIL_2FA_QUERY,
            gmail_credentials_path=Path(self.gmail_credentials_path or DEFAULT_GMAIL_CREDENTIALS_PATH),
            gmail_token_path=Path(self.gmail_token_path or DEFAULT_GMAIL_TOKEN_PATH),
        )


class UiSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> UiSettings:
        if not self.path.exists():
            return UiSettings.defaults()

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("crawlings"), list) and raw["crawlings"]:
            # 다중 탭 파일을 단일 load()로 읽으면 tab 0만 반환한다. 여기서 평면 save()로
            # 영속화하면 나머지 탭이 유실되므로(원본 보존, NFR-18) ID 발급/영속화는
            # load_all에 위임하고 기존 동작(tab 0 반환, write 없음)을 그대로 둔다.
            return _settings_from_mapping(raw["crawlings"][0], UiSettings.defaults())

        settings = _settings_from_mapping(raw, UiSettings.defaults())
        # 단일 객체 파일이 활성 탭이면 안정 ID를 발급하고, 새로 발급됐으면 한 번 영속화해
        # 재로드 시 동일 ID가 유지되게 한다(persist-on-first-issue). 파일은 위에서 존재 확인됨.
        if settings.performance_url.strip() and _issue_missing_ids(settings, legacy_alias="크롤링1"):
            self.save(settings)
        return settings

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

        # 활성(=performance_url 있는) 탭에만 안정 ID를 발급한다. "활성" 판정은
        # ui.active_crawling_settings와 동일 의미(performance_url.strip())를 인라인 복제한다
        # (ui→ui_settings 의존 방향이라 ui import 시 순환 import 발생 — import 금지).
        # 새로 발급된 ID가 있으면 한 번 영속화해 재로드 시 동일 ID가 유지되게 한다
        # (persist-on-first-issue). 파일이 없을 때는 위에서 이미 반환했으므로 여기서 write는
        # 항상 기존 원본 파일에만 일어난다(새 파일 생성 없음). atomic write는 Story 2.2 소유.
        issued = False
        for index, item in enumerate(settings, start=1):
            if not item.performance_url.strip():
                continue
            if _issue_missing_ids(item, legacy_alias=f"크롤링{index}"):
                issued = True
        if issued:
            self.save_all(settings)
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


def _issue_missing_ids(settings: UiSettings, *, legacy_alias: str) -> bool:
    """비어 있는 식별자에만 불투명 ID(uuid4)를 발급하고 legacy_alias를 seed한다.

    이미 값이 있는 필드는 보존한다(idempotent — 재로드/재정렬에도 동일 ID 유지). 각 ID는
    독립적으로 발급해 같은 값을 재사용하지 않는다. ``customer_name``은 사람 표시명이라 자동
    발급 대상이 아니므로 비워 둔다(운영자가 이후 채움). 하나라도 새로 채워지면 영속화가
    필요하다는 뜻으로 ``True``를 반환한다.
    """

    changed = False
    if not settings.monitoring_target_id:
        settings.monitoring_target_id = uuid.uuid4().hex
        changed = True
    if not settings.customer_id:
        settings.customer_id = uuid.uuid4().hex
        changed = True
    if not settings.platform_account_id:
        settings.platform_account_id = uuid.uuid4().hex
        changed = True
    if not settings.legacy_alias and legacy_alias:
        settings.legacy_alias = legacy_alias
        changed = True
    return changed


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
