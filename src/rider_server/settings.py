"""rider_server 설정 — Story 5.1.

stdlib ``os.environ`` 기반의 **최소 typed settings**. ``pydantic-settings`` 같은 외부
패키지는 5.1 범위에서 도입하지 않는다(7-dep lock 보호 / 필요해지면 5.2+에서 결정).
기존 ``AppConfig.from_env`` 패턴을 계승해 frozen dataclass + ``from_env`` 분류자를 쓴다.

향후 Secrets Manager ref(``*_ref``) 로딩은 이 모듈의 책임으로 두되, 5.1에는 와이어링할
secret 이 없어 자리만 남긴다(평문 secret 을 설정 객체에 절대 싣지 않는다).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# 합리적 기본값 — env 미설정 시 사용. 운영에서는 Docker/배포 env 로 주입한다.
_DEFAULT_APP_ENV = "development"
_DEFAULT_APP_VERSION = "0.1.0"
_DEFAULT_DB_POOL_SIZE = 5
_DEFAULT_DB_MAX_OVERFLOW = 10
_DEFAULT_UVICORN_WORKERS = 1
_DEFAULT_SCHEDULER_DUE_BATCH_SIZE = 100
_DEFAULT_JOB_LEASE_SECONDS = 120
_DEFAULT_JOB_RECOVERY_BATCH_SIZE = 100
_DEFAULT_DISPATCH_BATCH_SIZE = 50
_DEFAULT_DISPATCH_MAX_ATTEMPTS = 3
_DEFAULT_DISPATCH_LOCK_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Settings:
    """런타임 설정 스냅샷(불변).

    ``app_env``/``app_version`` 은 항상 값이 있고, ``build_sha``/``build_time`` 은
    빌드 파이프라인이 주입할 때만 존재한다(미설정 시 ``None``).
    """

    app_env: str
    app_version: str
    build_sha: str | None
    build_time: str | None
    # Story 5.2: DB 연결 문자열(예 ``postgresql+asyncpg://…``). env 에서만 읽고
    # 평문 비밀을 설정 객체에 싣지 않는다(미설정 시 None). 기존 4-필드 positional
    # 생성과 호환되도록 default 를 가진 마지막 필드로 둔다.
    database_url: str | None = None
    # Story 5.5: Telegram webhook secret / bot token 의 **참조 핸들**(``*_ref``)만 싣는다.
    # 평문 secret 은 절대 설정 객체에 두지 않는다(NFR-5·8 / ``database_url`` 추가 패턴 계승).
    # 실제 평문 해석은 ``create_app`` 의 ``resolve_telegram_secret``/``resolve_telegram_token``
    # 주입 seam 책임이고, 기존 positional 생성 호환을 위해 default 를 가진 마지막 필드로 둔다.
    telegram_webhook_secret_ref: str | None = None
    telegram_bot_token_ref: str | None = None
    # Story 5.8: Admin 접근 보안·복구 non-sending 설정(신규 third-party deps 0 — stdlib 파싱).
    #   * sending_enabled: 복구/신규 환경 실전송 게이트(기본 OFF — fail-closed, NFR-9·25).
    #   * admin_ip_allowlist: Admin 접근 허용 source IP/CIDR(빈 tuple = 추가 제한 없음, opt-in).
    #   * admin_mfa_required: privileged 액션의 MFA 강제 토글(기본 True — 게이트레일 #4).
    #   * admin_allowed_origins: Admin POST Origin/Referer same-origin 보강용 추가 허용 Origin.
    #   * admin_public_access: 임시 공개 운영 모드(기본 OFF). 켜면 외부 principal 없이 Admin 통과.
    # 기존 positional 생성 호환을 위해 default 를 가진 마지막 필드들로 둔다.
    sending_enabled: bool = False
    admin_ip_allowlist: tuple[str, ...] = ()
    admin_mfa_required: bool = True
    admin_allowed_origins: tuple[str, ...] = ()
    admin_public_access: bool = False
    admin_trusted_proxy_cidrs: tuple[str, ...] = ()
    # Crawl scale controls. 각 long-running process(API worker, scheduler, recovery)가
    # 자기 엔진을 만들기 때문에 pool 기준도 settings/env 에서 같은 방식으로 읽는다.
    db_pool_size: int = _DEFAULT_DB_POOL_SIZE
    db_max_overflow: int = _DEFAULT_DB_MAX_OVERFLOW
    uvicorn_workers: int = _DEFAULT_UVICORN_WORKERS
    scheduler_due_batch_size: int = _DEFAULT_SCHEDULER_DUE_BATCH_SIZE
    job_lease_seconds: int = _DEFAULT_JOB_LEASE_SECONDS
    job_recovery_batch_size: int = _DEFAULT_JOB_RECOVERY_BATCH_SIZE
    dispatch_batch_size: int = _DEFAULT_DISPATCH_BATCH_SIZE
    dispatch_max_attempts: int = _DEFAULT_DISPATCH_MAX_ATTEMPTS
    dispatch_lock_timeout_seconds: int = _DEFAULT_DISPATCH_LOCK_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """환경 변수에서 설정을 읽는다(테스트는 fake mapping 주입 가능)."""
        env = os.environ if environ is None else environ
        return cls(
            app_env=env.get("APP_ENV", _DEFAULT_APP_ENV),
            app_version=env.get("APP_VERSION", _DEFAULT_APP_VERSION),
            # 빈 문자열도 "미설정"으로 취급해 None 으로 정규화한다.
            build_sha=env.get("BUILD_SHA") or None,
            build_time=env.get("BUILD_TIME") or None,
            database_url=env.get("DATABASE_URL") or None,
            # ``*_ref`` 핸들(평문 secret 아님) — 미설정/빈 문자열은 None 으로 정규화한다.
            telegram_webhook_secret_ref=env.get("TELEGRAM_WEBHOOK_SECRET_REF") or None,
            telegram_bot_token_ref=env.get("TELEGRAM_BOT_TOKEN_REF") or None,
            # 복구 non-sending: 기본 OFF(미설정 시 차단). truthy("1"/"true"/"yes"/"on")만 활성화.
            sending_enabled=_env_bool(
                env.get("RIDER_SENDING_ENABLED"),
                default=False,
                name="RIDER_SENDING_ENABLED",
            ),
            admin_ip_allowlist=_env_tuple(env.get("RIDER_ADMIN_IP_ALLOWLIST")),
            admin_mfa_required=_env_bool(
                env.get("RIDER_ADMIN_MFA_REQUIRED"),
                default=True,
                name="RIDER_ADMIN_MFA_REQUIRED",
            ),
            admin_allowed_origins=_env_tuple(env.get("RIDER_ADMIN_ALLOWED_ORIGINS")),
            admin_public_access=_env_bool(
                env.get("RIDER_ADMIN_PUBLIC_ACCESS"),
                default=False,
                name="RIDER_ADMIN_PUBLIC_ACCESS",
            ),
            admin_trusted_proxy_cidrs=_env_tuple(env.get("RIDER_ADMIN_TRUSTED_PROXY_CIDRS")),
            db_pool_size=_env_int(
                env.get("RIDER_DB_POOL_SIZE"),
                default=_DEFAULT_DB_POOL_SIZE,
                minimum=1,
            ),
            db_max_overflow=_env_int(
                env.get("RIDER_DB_MAX_OVERFLOW"),
                default=_DEFAULT_DB_MAX_OVERFLOW,
                minimum=0,
            ),
            uvicorn_workers=_env_int(
                env.get("RIDER_UVICORN_WORKERS"),
                default=_DEFAULT_UVICORN_WORKERS,
                minimum=1,
            ),
            scheduler_due_batch_size=_env_int(
                env.get("SCHEDULER_DUE_BATCH_SIZE"),
                default=_DEFAULT_SCHEDULER_DUE_BATCH_SIZE,
                minimum=1,
            ),
            job_lease_seconds=_env_int(
                env.get("RIDER_JOB_LEASE_SECONDS"),
                default=_DEFAULT_JOB_LEASE_SECONDS,
                minimum=1,
            ),
            job_recovery_batch_size=_env_int(
                env.get("RIDER_JOB_RECOVERY_BATCH_SIZE"),
                default=_DEFAULT_JOB_RECOVERY_BATCH_SIZE,
                minimum=1,
            ),
            dispatch_batch_size=_env_int(
                env.get("TELEGRAM_DISPATCH_BATCH_SIZE"),
                default=_DEFAULT_DISPATCH_BATCH_SIZE,
                minimum=1,
            ),
            dispatch_max_attempts=_env_int(
                env.get("TELEGRAM_DISPATCH_MAX_ATTEMPTS"),
                default=_DEFAULT_DISPATCH_MAX_ATTEMPTS,
                minimum=1,
            ),
            dispatch_lock_timeout_seconds=_env_int(
                env.get("TELEGRAM_DISPATCH_LOCK_TIMEOUT_SECONDS"),
                default=_DEFAULT_DISPATCH_LOCK_TIMEOUT_SECONDS,
                minimum=1,
            ),
        )


def _env_bool(value: str | None, *, default: bool, name: str) -> bool:
    """env 문자열을 bool 로 — ``1``/``true``/``yes``/``on`` → True, ``0``/``false``/… → False.

    미설정(None)/빈 문자열은 ``default``. fail-closed 정책은 호출부의 default 선택으로 표현한다
    (sending_enabled default False = 복구 차단, mfa_required default True = MFA 강제).
    """
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_tuple(value: str | None) -> tuple[str, ...]:
    """콤마 구분 env 문자열을 정규화된 tuple 로(빈 항목 제거). 미설정/빈 문자열은 빈 tuple."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _env_int(value: str | None, *, default: int, minimum: int) -> int:
    """env 문자열을 int 로 읽고, 비어 있거나 범위를 벗어나면 default 를 쓴다."""
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    if parsed < minimum:
        return default
    return parsed
