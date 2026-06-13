from __future__ import annotations

import json
import os
import tempfile
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
    coupang_center_name_risk,
)
from .secret_store import LocalFileSecretStore, SecretStore

# 직렬화에서 제외(비영속)하고 로컬 store로 분리하는 평문 secret 필드. 각 필드는 대응
# ``<field>_ref``를 갖는다(아래 dataclass). OTP/2FA는 비저장 분류라 여기 없다.
_SECRET_FIELDS: tuple[str, ...] = (
    "telegram_bot_token",
    "coupang_login_password",
    "coupang_login_id",
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
    # secret 분리(P1-06): 설정 JSON에는 평문 token/password 대신 ``*_ref``(로컬 store 핸들)만
    # 남긴다. 평문 ``telegram_bot_token``/``coupang_login_*`` 필드는 dataclass에 유지하되
    # 직렬화에서 제외(_to_jsonable)해 "비영속(transient)"으로 강등한다 — 로드 시 store에서
    # resolve해 in-memory 평문을 채우므로 to_app_config·UI StringVar·소비자는 무변경(무회귀).
    telegram_bot_token_ref: str = ""
    coupang_login_password_ref: str = ""
    coupang_login_id_ref: str = ""

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

    # 플랫폼 중립 Target 필드(P1-05): 배민·쿠팡이 같은 중립 이름으로 대상을 읽도록, 기존
    # legacy 필드 위에 얹는 read-only 별칭이다. 저장 정본은 legacy 필드(``performance_url``
    # 등)이고 이름을 rename하지 않으므로 30+ 호출부가 안 깨진다(ADD-8). ``@property``는
    # dataclass 필드가 아니라 ``asdict``/저장 JSON에 새 키를 만들지 않는다(직렬화 불변).
    # 순수 읽기라 strip/가공하지 않는다(소비자가 기존처럼 .strip()을 부른다).
    @property
    def primary_url(self) -> str:
        return self.performance_url

    @property
    def center_name(self) -> str:
        return self.baemin_center_name

    @property
    def target_external_id(self) -> str:
        return self.baemin_center_id

    @property
    def display_name(self) -> str:
        return self.legacy_alias

    def coupang_center_name_risk(self) -> tuple[bool, str]:
        # 편의 접근자: 중립 platform_name/center_name으로 비차단 위험 분류기를 호출한다.
        # bare 이름은 모듈 전역(config에서 import한 함수)으로 해석된다(이 메서드가 아님).
        # 분류만 하고 예외/저장/상태 전이는 하지 않는다(실제 차단은 Epic 4 소유).
        return coupang_center_name_risk(self.platform_name, self.center_name)

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
    def __init__(self, path: Path, secret_store: SecretStore | None = None) -> None:
        self.path = path
        # 기본 store는 설정 파일 옆의 **별도** 파일(secrets.local.json). ui.py 진입점은 기본
        # store를 쓰므로 시그니처 호환(기본 인자)으로 두어 호출부 무변경을 유지하고, 테스트는
        # tmp_path store를 주입해 실 파일을 만지지 않는다(AC6/7).
        self.secret_store: SecretStore = secret_store or LocalFileSecretStore(
            path.parent / "secrets.local.json"
        )

    def load(self) -> UiSettings:
        if not self.path.exists():
            return UiSettings.defaults()

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("crawlings"), list) and raw["crawlings"]:
            # 다중 탭 파일을 단일 load()로 읽으면 tab 0만 반환한다. 여기서 평면 save()로
            # 영속화하면 나머지 탭이 유실되므로(원본 보존, NFR-18) ID 발급/영속화는
            # load_all에 위임하고 기존 동작(tab 0 반환, write 없음)을 그대로 둔다. secret은
            # 표시용으로 in-memory resolve만 하고(평문 잔존 이관은 load_all 소유), 쓰지 않는다.
            settings = _settings_from_mapping(raw["crawlings"][0], UiSettings.defaults())
            self._resolve_secrets(settings)
            return settings

        settings = _settings_from_mapping(raw, UiSettings.defaults())
        # 단일 객체 파일이 활성 탭이면 안정 ID를 발급하고, legacy 평문 secret이 있으면 store로
        # 이관해야 하므로, 둘 중 하나라도 발생하면 한 번 영속화한다(persist-on-first-issue). 그
        # 결과 재로드 시 동일 ID가 유지되고 신규 파일엔 ref만 남는다. 파일은 위에서 존재 확인됨.
        needs_persist = False
        if settings.performance_url.strip() and _issue_missing_ids(settings, legacy_alias="크롤링1"):
            needs_persist = True
        if self._resolve_secrets(settings):
            needs_persist = True
        if needs_persist:
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
        needs_persist = False
        for index, item in enumerate(settings, start=1):
            if not item.performance_url.strip():
                continue
            if _issue_missing_ids(item, legacy_alias=f"크롤링{index}"):
                needs_persist = True
        # ID 발급 이후에 secret을 처리한다: legacy 평문은 발급된 target_id 기반 결정적 ref로
        # 이관되고(아래 save_all → _absorb_secrets), ref만 있는 신규 파일은 in-memory로 resolve
        # 된다. legacy 평문 이관이 한 건이라도 있으면 한 번 영속화해 신규 파일엔 ref만 남긴다.
        for item in settings:
            if self._resolve_secrets(item):
                needs_persist = True
        if needs_persist:
            self.save_all(settings)
        return settings

    def save(self, settings: UiSettings) -> None:
        # 직렬화 직전 평문 secret을 store로 빼고(ref 확정) 직렬화에서는 ref만 남긴다. 형식은
        # 그대로 두고(완성된 문자열만 넘긴다) write 방식만 atomic화한다.
        self._absorb_secrets(settings)
        _atomic_write_text(
            self.path,
            json.dumps(_to_jsonable(settings), ensure_ascii=False, indent=2),
        )

    def save_all(self, settings: list[UiSettings]) -> None:
        for item in settings:
            self._absorb_secrets(item)
        payload = {"crawlings": [_to_jsonable(item) for item in settings]}
        _atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _absorb_secrets(self, settings: UiSettings) -> None:
        # 직렬화 직전: 비어있지 않은 평문 secret을 store로 빼고 대응 ``*_ref``를 채운다. 평문
        # 자체는 _to_jsonable에서 제외된다. store를 먼저 영속화(ref 확정)한 뒤 호출부가 설정
        # JSON을 atomic write하므로, 크래시 시에도 "설정엔 ref, 값은 store"가 깨지지 않는다.
        # 평문과 ``*_ref``가 둘 다 있는(반쪽 마이그레이션) 경우 평문을 정본으로 재이관해
        # ref를 덮어쓴다 — 신규 파일에 평문 잔존 0을 보장한다(ADD-15).
        for field in _SECRET_FIELDS:
            plaintext = getattr(settings, field)
            if plaintext:
                ref = self.secret_store.put(plaintext, ref=_secret_ref(settings, field))
                setattr(settings, f"{field}_ref", ref)

    def _resolve_secrets(self, settings: UiSettings) -> bool:
        # 로드 직후: ``*_ref``만 있는 신규 파일은 store.resolve로 in-memory 평문을 복원한다
        # (store에 값이 없으면 빈 평문 — fail-closed). legacy 평문이 그대로 있으면(평문 우선)
        # 손대지 않고 ``True``를 반환해 호출부가 한 번 영속화(save)하게 한다 — save가 평문을
        # store로 흡수하고 신규 파일엔 ref만 남긴다(2.1 persist-on-first-issue와 동일 정신).
        legacy_plaintext = False
        for field in _SECRET_FIELDS:
            plaintext = getattr(settings, field)
            ref = getattr(settings, f"{field}_ref")
            if plaintext:
                legacy_plaintext = True
            elif ref:
                setattr(settings, field, self.secret_store.resolve(ref) or "")
        return legacy_plaintext


def _atomic_write_text(path: Path, text: str) -> None:
    """``text``를 ``path``에 원자적으로 쓴다: 같은 디렉터리 임시 파일 → fsync → os.replace.

    저장 도중 강제 종료(=replace 직전 중단)에도 기존 ``path``는 이전 유효 상태가 그대로
    보존되고 반쪽짜리로 손상되지 않는다. ``os.replace``는 같은 볼륨에서만 원자적이라 temp는
    반드시 ``path.parent``에 만든다(다른 디렉터리면 cross-device로 비원자적이 될 수 있다).
    디렉터리 fsync는 Windows 미지원이라 생략한다(크로스플랫폼 우선). 실패 시 temp를 정리한
    뒤 예외를 재발생해 호출자(save_settings)가 기존 messagebox/상태 흐름으로 처리하게 둔다.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        with tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


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


def _secret_ref(settings: UiSettings, field: str) -> str:
    # 결정적 핸들: target_id가 있으면 재로드/재정렬에도 안정적인 per-field ref를 만든다
    # (dedup/diff 안정 + 테스트 결정적). 없으면 빈 문자열을 넘겨 store가 내용 기반 ref를
    # 발급하게 한다(inactive 탭 등 ID 미발급 케이스의 fail-safe). 필드 구분자는 ``:``가 아니라
    # ``/``를 쓴다 — ``<digits>:<word>``는 redaction의 Telegram 토큰 형태 정규식에 걸려 ref가
    # 로그에서 마스킹되는데(추적성 저하), ``/``는 ``vault://…`` 선례처럼 redaction이 보존한다.
    target = settings.monitoring_target_id
    if not target:
        return ""
    return f"local:{target}/{field}"


def _to_jsonable(settings: UiSettings) -> dict[str, Any]:
    data = asdict(settings)
    data["browser_user_data_dir"] = str(settings.browser_user_data_dir)
    data["log_dir"] = str(settings.log_dir)
    # 평문 secret은 설정 JSON에 절대 쓰지 않는다(P1-06/ADD-15) — ``*_ref``만 남긴다. 키 자체는
    # 빈 문자열로 유지해 기존 평면 구조/하위호환을 깨지 않는다(값만 비운다).
    for field in _SECRET_FIELDS:
        data[field] = ""
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
