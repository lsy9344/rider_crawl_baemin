"""rider_server 설정 — Story 5.1.

stdlib ``os.environ`` 기반의 **최소 typed settings**. ``pydantic-settings`` 같은 외부
패키지는 5.1 범위에서 도입하지 않는다(9-dep lock 보호 / 필요해지면 5.2+에서 결정).
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
        )
