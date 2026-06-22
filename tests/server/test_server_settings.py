"""Story 5.1 / Task 2 — rider_server.settings.Settings.from_env (QA gap-fill).

``settings.py`` 는 stdlib ``os.environ`` 기반 typed settings 다. 기존 스위트는
엔드포인트를 통해 간접적으로만 settings 를 건드리고 ``from_env`` 분류자 자체는
직접 검증하지 않는다. 여기서 기본값·env override·빈 문자열→None 정규화·frozen
불변성을 fake mapping 으로 잠근다(실제 env/외부 의존 미사용).
"""

from __future__ import annotations

import dataclasses

import pytest

from rider_server.settings import Settings


def test_from_env_uses_defaults_when_empty():
    s = Settings.from_env({})
    assert s.app_env == "development"
    assert s.app_version == "0.1.0"
    assert s.build_sha is None
    assert s.build_time is None
    assert s.admin_allowed_origins == ()


def test_from_env_reads_all_values():
    s = Settings.from_env(
        {
            "APP_ENV": "production",
            "APP_VERSION": "2.0.0",
            "BUILD_SHA": "deadbee",
            "BUILD_TIME": "2026-06-14T01:02:03Z",
        }
    )
    assert s.app_env == "production"
    assert s.app_version == "2.0.0"
    assert s.build_sha == "deadbee"
    assert s.build_time == "2026-06-14T01:02:03Z"


def test_empty_build_meta_normalized_to_none():
    # 빈 문자열은 "미설정"으로 취급해 None 으로 정규화한다(/version 키 누락 조건).
    s = Settings.from_env({"BUILD_SHA": "", "BUILD_TIME": ""})
    assert s.build_sha is None
    assert s.build_time is None


def test_partial_build_meta_preserved_independently():
    # build_sha 만 설정되고 build_time 은 빈 문자열 → 각각 독립 정규화.
    s = Settings.from_env({"BUILD_SHA": "onlysha", "BUILD_TIME": ""})
    assert s.build_sha == "onlysha"
    assert s.build_time is None


def test_admin_allowed_origins_parsed_from_env_tuple():
    s = Settings.from_env(
        {
            "RIDER_ADMIN_ALLOWED_ORIGINS": (
                " https://admin.example ,https://ops.example:8443, "
            )
        }
    )

    assert s.admin_allowed_origins == (
        "https://admin.example",
        "https://ops.example:8443",
    )


def test_settings_is_frozen_immutable():
    s = Settings.from_env({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.app_version = "9.9.9"  # type: ignore[misc]
