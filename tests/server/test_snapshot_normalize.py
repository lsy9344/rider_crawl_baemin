"""Story 3.2 / AC1~AC7 (P2-02, FR-7, NFR-2) — Snapshot 정규화 + fail-closed 잠금.

(1) parser 출력을 정규화 ``Snapshot`` 으로 wrapping(필드·추적성·``normalized_json``
동등성), (2) 필수데이터 누락 시 0/기본값 없이 ``MissingSnapshotDataError`` raise +
Message 미진입(FR-7), (3) 배민/쿠팡 fixture 보존(``normalized_json == asdict(raw)``).

외부 호출 없음 — fake/in-memory·가짜 값만. 평면 ``tests/server/`` 컨벤션(conftest
공유 없이 자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime

import pytest

from rider_crawl.config import AppConfig
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)
from rider_crawl.parser import MissingPerformanceDataError
from rider_server.domain import Platform, Snapshot, SnapshotQualityState
from rider_server.services import (
    CrawlService,
    MessageRenderService,
    MissingSnapshotDataError,
    SnapshotNormalizer,
)
from rider_server.services.snapshot_normalizer import (
    _BAEMIN_PARSER_VERSION,
    _COUPANG_PARSER_VERSION,
)

# 고정 주입값(결정성) — 서비스 내부에서 now()/uuid4() 를 호출하지 않음을 잠근다.
_COLLECTED_AT = datetime(2026, 1, 1, 12, 0, 0)


# ── fixture: test_app.py 의 _snapshot/_performance_snapshot 동등 헬퍼(가짜 값만) ──


def _baemin_snapshot() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        afternoon_non_peak_count=41.8,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=41.8,
        active_riders=5,
    )


def _coupang_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_baemin_snapshot(),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def _config(tmp_path, *, platform_name: str = "baemin") -> AppConfig:
    # CrawlService.crawl 의 config 인자용(fake crawler 는 사용하지 않지만 시그니처 충실).
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        platform_name=platform_name,
        baemin_center_name="",
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_message_thread_id="",
        crawl_name="",
    )


# ── AC1·AC3 — happy path: 정규화 필드 + parser 출력 전량 보존 ────────────────────


def test_normalize_baemin_happy_path():
    raw = _baemin_snapshot()

    snap = SnapshotNormalizer.normalize(
        raw,
        snapshot_id="snap-1",
        target_id="mt-1",
        collected_at=_COLLECTED_AT,
        tenant_id="tnt-1",
        platform_account_id="pa-1",
        agent_id="ag-1",
    )

    assert isinstance(snap, Snapshot)
    assert snap.id == "snap-1"
    assert snap.target_id == "mt-1"
    assert snap.platform == Platform.BAEMIN
    assert snap.collected_at == _COLLECTED_AT
    assert snap.parser_version == _BAEMIN_PARSER_VERSION
    assert snap.quality_state == SnapshotQualityState.OK
    # 추적 필드 주입값 보존(AC2).
    assert snap.tenant_id == "tnt-1"
    assert snap.platform_account_id == "pa-1"
    assert snap.agent_id == "ag-1"
    # parser 출력 전량 보존: 필드 추가·삭제·기본값 주입 0(AC3 동등성).
    assert snap.normalized_json == dataclasses.asdict(raw)


def test_normalize_coupang_happy_path_recursive_asdict():
    raw = _coupang_snapshot()

    snap = SnapshotNormalizer.normalize(
        raw, snapshot_id="snap-2", target_id="mt-2", collected_at=_COLLECTED_AT
    )

    assert snap.platform == Platform.COUPANG
    assert snap.parser_version == _COUPANG_PARSER_VERSION
    assert snap.quality_state == SnapshotQualityState.OK
    assert snap.normalized_json == dataclasses.asdict(raw)
    # 중첩 dataclass(현재화면·피크대시보드·피크구간)가 재귀로 dict 변환됐는지 확인.
    assert snap.normalized_json["peak_dashboard"]["lunch_peak"] == {"done": 45, "total": 45}
    assert snap.normalized_json["current_screen"]["center_name"] == "제이앤에이치플러스 의정부남부"


def test_normalize_default_tracking_fields_blank():
    snap = SnapshotNormalizer.normalize(
        _baemin_snapshot(), snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    # 미주입 시 추적 필드는 ""(런타임 미배선 — Epic 5 wiring 전).
    assert snap.tenant_id == ""
    assert snap.platform_account_id == ""
    assert snap.agent_id == ""


def test_coupang_current_screen_none_is_ok():
    # 쿠팡은 current_screen=None 이 정상(models.py 59-66) — 누락으로 오판하지 않는다.
    raw = dataclasses.replace(_coupang_snapshot(), current_screen=None)

    snap = SnapshotNormalizer.normalize(
        raw, snapshot_id="snap-3", target_id="mt-3", collected_at=_COLLECTED_AT
    )

    assert snap.quality_state == SnapshotQualityState.OK
    assert snap.normalized_json["current_screen"] is None


def test_normalized_json_is_json_serializable():
    snap = SnapshotNormalizer.normalize(
        _coupang_snapshot(), snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    dumped = json.dumps(snap.normalized_json, ensure_ascii=False)
    assert json.loads(dumped) == snap.normalized_json


# ── AC2 — fail-closed: 예외·기본값 금지 ────────────────────────────────────────


def test_normalize_none_raises_and_inherits_base_exceptions():
    kwargs = dict(snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT)

    with pytest.raises(MissingSnapshotDataError):
        SnapshotNormalizer.normalize(None, **kwargs)
    # 계승 확인: 기존 except 가 그대로 잡는다(AC2).
    with pytest.raises(MissingPerformanceDataError):
        SnapshotNormalizer.normalize(None, **kwargs)
    with pytest.raises(ValueError):
        SnapshotNormalizer.normalize(None, **kwargs)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_normalize_empty_center_name_raises(blank):
    # 구조적으로 present 여도 center_name 이 비면 fail-closed(오발송 방지, §88).
    bad = dataclasses.replace(_baemin_snapshot(), center_name=blank)

    with pytest.raises(MissingSnapshotDataError):
        SnapshotNormalizer.normalize(
            bad, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
        )


def test_normalize_unexpected_type_raises():
    # CurrentScreenSnapshot/PerformanceSnapshot 가 아닌 입력 → 정규화 거부.
    with pytest.raises(MissingSnapshotDataError):
        SnapshotNormalizer.normalize(
            "not-a-snapshot",  # type: ignore[arg-type]
            snapshot_id="snap-1",
            target_id="mt-1",
            collected_at=_COLLECTED_AT,
        )


def test_failure_returns_no_snapshot():
    # 예외가 났을 때 부분/기본 Snapshot 이 만들어지지 않는다(0/빈 값 주입 없음).
    result = None
    with pytest.raises(MissingSnapshotDataError):
        result = SnapshotNormalizer.normalize(
            None, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
        )
    assert result is None


# ── AC2 (FR-7) — fail-closed 가 Message 생성으로 이어지지 않는다 ─────────────────


def test_normalize_failure_does_not_reach_render():
    # (a) normalize 가 raise 하면 그 뒤 render 가 호출되지 않는다(render 카운터 0).
    render_calls: list[Snapshot] = []

    def fake_render(snapshot: Snapshot) -> str:
        render_calls.append(snapshot)
        return "메시지"

    with pytest.raises(MissingSnapshotDataError):
        snap = SnapshotNormalizer.normalize(
            None, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
        )
        fake_render(snap)

    assert render_calls == []


def test_crawl_missing_data_does_not_reach_normalize_or_render(tmp_path):
    # (b) CrawlService.crawl 이 parser 의 MissingPerformanceDataError 를 던지면
    #     normalize/render 에 도달하지 않는다(3.1 AC3 계승).
    config = _config(tmp_path)
    normalize_calls: list[object] = []
    render_calls: list[str] = []

    def boom(_c: AppConfig):
        raise MissingPerformanceDataError("parser missing required field")

    with pytest.raises(MissingPerformanceDataError):
        raw = CrawlService.crawl(config, crawl_snapshot=boom)
        normalize_calls.append(raw)
        SnapshotNormalizer.normalize(
            raw, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
        )
        # render 는 3.1대로 raw(CrawlSnapshotResult)를 받는다(Snapshot 아님) — 무변경.
        render_calls.append(MessageRenderService.render(raw))

    assert normalize_calls == []
    assert render_calls == []


# ── AC1 — 결정성·frozen ────────────────────────────────────────────────────────


def test_snapshot_is_frozen():
    snap = SnapshotNormalizer.normalize(
        _baemin_snapshot(), snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.id = "other"  # type: ignore[misc]


def test_normalize_is_deterministic():
    raw = _baemin_snapshot()

    a = SnapshotNormalizer.normalize(
        raw, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )
    b = SnapshotNormalizer.normalize(
        raw, snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    # 내부 now()/uuid4() 미호출 → 같은 입력은 같은 출력.
    assert a == b
    assert a.normalized_json == b.normalized_json


# ── 회귀·누출 ──────────────────────────────────────────────────────────────────


# telegram bot token 형태(숫자:영숫자) 등 평문 secret 패턴.
_SECRET_PATTERNS = [
    re.compile(r"[0-9]{6,}:[A-Za-z0-9_-]{30,}"),
]


def test_no_plaintext_secret_in_normalized_json():
    snap = SnapshotNormalizer.normalize(
        _coupang_snapshot(), snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    blob = json.dumps(snap.normalized_json, ensure_ascii=False)
    for pat in _SECRET_PATTERNS:
        assert pat.search(blob) is None


def test_snapshot_reexports_are_additive():
    import rider_server.domain as domain
    import rider_server.services as services

    for name in ("Snapshot", "SnapshotQualityState"):
        assert hasattr(domain, name)
        assert name in domain.__all__
    for name in ("SnapshotNormalizer", "MissingSnapshotDataError"):
        assert hasattr(services, name)
        assert name in services.__all__


# ── QA 보강(gap fill) — 구현 분기·계약 커버리지 구멍 메우기 ──────────────────────


# 모든 fail-closed 입력. 정규화가 거부해야 하는 5가지 — raw 없음/배민 center_name
# 빈값·None/쿠팡 peak_dashboard None/예상 외 타입(_require_present 양쪽 분기 포함).
_FAIL_CLOSED_CASES = [
    pytest.param(None, id="raw_none"),
    pytest.param(dataclasses.replace(_baemin_snapshot(), center_name=""), id="baemin_empty_center"),
    pytest.param(dataclasses.replace(_baemin_snapshot(), center_name=None), id="baemin_none_center"),
    pytest.param(
        dataclasses.replace(_coupang_snapshot(), peak_dashboard=None), id="coupang_missing_peak"
    ),
    pytest.param("not-a-snapshot", id="unexpected_type"),
]


@pytest.mark.parametrize("bad", _FAIL_CLOSED_CASES)
def test_fail_closed_cases_raise_and_inherit_base_exceptions(bad):
    # AC2: 모든 fail-closed 케이스가 MissingSnapshotDataError 로 raise 되고, 동시에
    # base(MissingPerformanceDataError)·ValueError 로도 잡힌다(계승 확인 — 기존
    # except 코드가 그대로 처리). 쿠팡 peak_dashboard None·배민 center_name None 분기는
    # 기존 테스트에 없던 구멍이라 여기서 함께 잠근다.
    kwargs = dict(snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT)
    for exc_type in (MissingSnapshotDataError, MissingPerformanceDataError, ValueError):
        with pytest.raises(exc_type):
            SnapshotNormalizer.normalize(bad, **kwargs)  # type: ignore[arg-type]


def test_normalized_json_excludes_quality_meta():
    # AC1/AC3: quality_state·parser_version 은 normalized_json '밖'의 Snapshot 컬럼이다
    # (데이터 vs 품질 메타 분리 — data-api-contract separate columns, Dev Notes 130).
    # normalized_json 은 parser 출력만 순수 보존하고 품질 메타를 새로 주입하지 않는다.
    snap = SnapshotNormalizer.normalize(
        _baemin_snapshot(), snapshot_id="snap-1", target_id="mt-1", collected_at=_COLLECTED_AT
    )

    assert "quality_state" not in snap.normalized_json
    assert "parser_version" not in snap.normalized_json
    # 키 집합이 parser 출력 필드와 정확히 일치 — 추가/삭제 키 0(AC3 동등성).
    assert set(snap.normalized_json) == {f.name for f in dataclasses.fields(CurrentScreenSnapshot)}


def test_snapshot_quality_state_str_enum_contract():
    # AC1/Task1: (str, Enum) + 이름==값(대문자) 규약(2.5 정본). 정규화 성공은 OK,
    # MISSING_REQUIRED 는 Epic 5 persist 어휘로 값만 미리 둔다(정규화 성공 경로 미사용).
    assert SnapshotQualityState.OK == "OK"
    assert SnapshotQualityState.MISSING_REQUIRED == "MISSING_REQUIRED"
    assert {m.name for m in SnapshotQualityState} == {"OK", "MISSING_REQUIRED"}
    assert json.dumps(SnapshotQualityState.OK) == '"OK"'  # str 직렬화 = .value
    for member in SnapshotQualityState:
        assert isinstance(member, str)
        assert member.name == member.value


def test_normalize_coupang_preserves_tracking_and_is_deterministic():
    # AC1/AC2: 추적 필드·target_id·collected_at 보존과 결정성을 쿠팡에서도 잠근다
    # (기존엔 배민에만 있던 대칭 갭).
    raw = _coupang_snapshot()
    inject = dict(
        snapshot_id="snap-9",
        target_id="mt-9",
        collected_at=_COLLECTED_AT,
        tenant_id="tnt-9",
        platform_account_id="pa-9",
        agent_id="ag-9",
    )

    a = SnapshotNormalizer.normalize(raw, **inject)
    b = SnapshotNormalizer.normalize(raw, **inject)

    assert a.target_id == "mt-9"
    assert a.collected_at == _COLLECTED_AT
    assert (a.tenant_id, a.platform_account_id, a.agent_id) == ("tnt-9", "pa-9", "ag-9")
    # 내부 now()/uuid4() 미호출 → 같은 입력은 같은 출력(결정적).
    assert a == b
