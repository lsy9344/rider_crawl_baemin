from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_BAEMIN_DELIVERY_HISTORY_URL = (
    "https://deliverycenter.baemin.com/delivery/history?"
    "page=0&size=20&orderName=name&orderBy=asc&name=&userId=&phoneNumber=&riderStatus="
)
DEFAULT_COUPANG_RIDER_PERFORMANCE_URL = "https://partner.coupangeats.com/page/rider-performance"
DEFAULT_COUPANG_PEAK_DASHBOARD_URL = "https://partner.coupangeats.com/page/peak-dashboard"
DEFAULT_PLATFORM_NAME = "baemin"


@dataclass(frozen=True)
class AppConfig:
    coupang_eats_url: str
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
    timezone: str
    run_lock_timeout_seconds: int
    page_timeout_seconds: int
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_message_thread_id: str = ""
    messenger_name: str = "telegram"
    crawl_name: str = ""
    state_subdir: str = ""
    # ``peak_dashboard_url`` is the Coupang peak-dashboard page; ``coupang_eats_url``
    # is the generic primary performance URL (Baemin delivery-history or Coupang
    # rider-performance depending on ``platform_name``).
    peak_dashboard_url: str = ""
    platform_name: str = DEFAULT_PLATFORM_NAME

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        platform_name = _platform_name(os.getenv("PERFORMANCE_PLATFORM", DEFAULT_PLATFORM_NAME))
        return cls(
            coupang_eats_url=_primary_url_from_env(platform_name),
            peak_dashboard_url=_peak_dashboard_url_from_env(platform_name),
            platform_name=platform_name,
            baemin_center_name=_center_name_from_env(platform_name),
            baemin_center_id=_center_id_from_env(platform_name),
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
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            telegram_message_thread_id=os.getenv("TELEGRAM_MESSAGE_THREAD_ID", ""),
            messenger_name=os.getenv("MESSENGER_NAME", "telegram"),
            crawl_name=os.getenv("CRAWL_NAME", ""),
            state_subdir=os.getenv("STATE_SUBDIR", ""),
        )

    @property
    def runtime_dir(self) -> Path:
        # 상태 루트 정책(의도적 분리):
        # - run lock / last message hash는 ``runtime_dir``(= log_dir 기준)에 둔다.
        #   이들은 스코프/탭별(``state_subdir``)로 나뉘고, UI가 log_dir 위치를
        #   바꿀 수 있으며, 테스트는 tmp_path로 격리해야 한다.
        # - 반면 텔레그램 offset/lock은 "토큰별 단일·탭 독립"이라 log_dir과 무관해야
        #   하므로 ``app_state_root()``(고정 루트)에 둔다. 두 상태군의 요구가 달라
        #   루트가 갈라져 있으며, 이는 버그가 아니라 의도된 설계다.
        #   (telegram_commands._default_offset_store_path 참고)
        #
        # runtime은 항상 log_dir의 형제(``log_dir.parent / "runtime"``)에 둔다.
        # 이전에는 ``log_dir.name == "logs"``일 때만 그렇게 하고 그 외에는 cwd 기준
        # ``runtime``으로 떨어졌다. 그러면 LOG_DIR=C:/acct1/custom-log,
        # LOG_DIR=C:/acct2/custom-log처럼 커스텀 로그 경로로 계정을 나눠도 둘 다 cwd의
        # ``runtime``을 공유해 lock/last-hash가 섞였다. 디렉터리 이름과 무관하게 항상
        # log_dir 옆에 두어 계정/스코프별로 격리한다. 기본값(LOG_DIR=logs)에서는
        # log_dir.parent가 cwd라 결과가 ``runtime``으로 동일하게 유지된다.
        return self.log_dir.parent / "runtime"

    @property
    def state_dir(self) -> Path:
        # ``state_subdir``는 탭/스코프별로 last message hash를 분리한다. 이 분리가
        # 필요하기 때문에 last hash는 고정 ``app_state_root()``이 아니라
        # ``runtime_dir`` 아래에 둔다(위 runtime_dir 주석의 정책 참고).
        base = self.runtime_dir / "state"
        return base / self.state_subdir if self.state_subdir else base


def app_state_root() -> Path:
    """Return a fixed app state root that does not depend on the current cwd.

    텔레그램 offset/lock처럼 "토큰별 단일" 상태 파일은 실행 작업 디렉터리(cwd)에
    묶이면 안 된다. 다른 디렉터리에서 실행하면 같은 봇 토큰도 다른 파일을 써서 같은
    업데이트를 다시 처리할 수 있기 때문이다. 그래서 cwd가 아니라 고정된 루트를 쓴다.

    상태 루트 정책: 이 고정 루트는 "토큰별 단일·탭 독립" 상태(텔레그램 offset)
    전용이다. run lock / last message hash는 스코프/탭별 분리가 필요해 일부러
    ``AppConfig.runtime_dir``(log_dir 기준)에 둔다. 두 상태군의 요구가 달라 루트가
    갈라진 것은 의도된 설계다(``AppConfig.runtime_dir`` 주석 참고).

    우선순위: ``RIDER_CRAWL_STATE_ROOT`` 환경변수 > 패키지 설치 위치 기준 프로젝트
    루트(개발용 ``src`` 레이아웃) > 그래도 못 찾으면 사용자 홈 아래 고정 경로.
    """

    override = os.getenv("RIDER_CRAWL_STATE_ROOT")
    if override and override.strip():
        return Path(override).expanduser().resolve()

    # src 레이아웃: .../<project_root>/src/rider_crawl/config.py → parents[2]가 루트.
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "src").is_dir() or (package_root / "pyproject.toml").is_file():
        return package_root

    return (Path.home() / ".rider_crawl").resolve()


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _platform_name(raw: str) -> str:
    value = str(raw or "").strip().casefold() or DEFAULT_PLATFORM_NAME
    if value not in {"baemin", "coupang"}:
        raise ValueError("PERFORMANCE_PLATFORM은 baemin 또는 coupang이어야 합니다")
    return value


def _peak_dashboard_url_from_env(platform_name: str) -> str:
    # 피크 대시보드는 쿠팡 전용 보조 URL이다. 배민이면 PEAK_DASHBOARD_URL env가
    # 있어도 무조건 빈 값으로 둔다. 이 값은 메시지 scope hash에 들어가므로, 배민에서
    # 값을 채우면 UI 배민 설정(빈 값)과 CLI 배민 설정의 중복 감지 파일이 갈라진다.
    # 문서/UI 동작과 맞춰 배민은 항상 빈 값으로 통일한다.
    if platform_name != "coupang":
        return ""
    return os.getenv("PEAK_DASHBOARD_URL", DEFAULT_COUPANG_PEAK_DASHBOARD_URL)


def _primary_url_from_env(platform_name: str) -> str:
    performance_url = os.getenv("PERFORMANCE_URL")
    if performance_url:
        return performance_url

    if platform_name == "coupang":
        return os.getenv("COUPANG_EATS_URL", DEFAULT_COUPANG_RIDER_PERFORMANCE_URL)

    baemin_url = os.getenv("BAEMIN_DELIVERY_HISTORY_URL")
    if baemin_url:
        return baemin_url
    # ``COUPANG_EATS_URL`` is kept as a legacy fallback only when no Baemin URL is
    # set, so old ``.env`` files keep working without overriding an explicit Baemin URL.
    return os.getenv("COUPANG_EATS_URL", DEFAULT_BAEMIN_DELIVERY_HISTORY_URL)


# 배민 기본 센터명/ID. 배민 플랫폼에서만 기본값으로 쓰고, 쿠팡 플랫폼에서는 이
# 값을 절대 기본값으로 넣지 않는다(아래 ``_center_name_from_env`` 참고).
DEFAULT_BAEMIN_CENTER_NAME = "표준서울마포B이츠앤홀딩스3"
DEFAULT_BAEMIN_CENTER_ID = "DP2605181318"


def _center_name_from_env(platform_name: str) -> str:
    # 쿠팡 탭은 ``BAEMIN_CENTER_NAME``을 "기대 센터/상점명"으로 재사용한다
    # (crawler._validate_coupang_center). 배민 기본 센터명을 쿠팡 기본값으로 넣으면
    # 화면 센터명과 절대 일치하지 않아 크롤링이 항상 실패한다. 그래서 쿠팡에서는
    # 배민 기본값을 넣지 않고 env 값만 쓰며, 미설정이면 빈 값으로 둔 뒤
    # ``_require_coupang_center``에서 명확한 설정 오류를 낸다.
    if platform_name == "coupang":
        center_name = os.getenv("BAEMIN_CENTER_NAME", "").strip()
        _require_coupang_center(center_name)
        return center_name
    return os.getenv("BAEMIN_CENTER_NAME", DEFAULT_BAEMIN_CENTER_NAME)


def _center_id_from_env(platform_name: str) -> str:
    # 배민 센터 ID는 쿠팡 탭에서 쓰지 않으므로 쿠팡에서는 배민 기본값을 넣지 않는다.
    if platform_name == "coupang":
        return os.getenv("BAEMIN_CENTER_ID", "")
    return os.getenv("BAEMIN_CENTER_ID", DEFAULT_BAEMIN_CENTER_ID)


def _require_coupang_center(center_name: str) -> None:
    # 쿠팡 계정/센터/상점은 CDP 포트와 Chrome 프로필 로그인으로만 결정되므로, 포트나
    # 프로필이 꼬이면 다른 쿠팡 계정 실적을 정상처럼 전송할 수 있다. 기대 센터명이
    # 없으면 크롤러가 센터 검증을 건너뛰므로, CLI(--once)도 UI 저장 검증과 동일하게
    # 명시적으로 기대 센터명을 요구한다.
    if not center_name:
        raise ValueError(
            "PERFORMANCE_PLATFORM=coupang에서는 BAEMIN_CENTER_NAME에 "
            "실제 쿠팡 센터/상점명을 입력하세요. 이 값은 화면에서 확인된 센터와 대조해 "
            "다른 쿠팡 계정 실적 전송을 막는 데 쓰입니다."
        )
    if center_name == DEFAULT_BAEMIN_CENTER_NAME:
        raise ValueError(
            "PERFORMANCE_PLATFORM=coupang인데 BAEMIN_CENTER_NAME이 배민 기본값입니다. "
            "실제 쿠팡 센터/상점명으로 바꿔 입력하세요."
        )
