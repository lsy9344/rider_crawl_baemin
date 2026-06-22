"""Story 3.3 / AC1~AC7 (P2-03, FR-8, FR-2·FR-3 토대) — Message 정의 + 안정적 렌더링 분리.

(1) 유효 Snapshot → 정규화 ``Message`` 레코드(필드·``template_version``·안정적
``text_hash=sha256(text)``), (2) 재수집 없이 재렌더링 재현(같은 입력 → 같은 Message) +
``render`` 무관 순수 함수, (3) 기존 ``render_current_screen_message`` 출력과 바이트 동등
(재구현 0) — 의도치 않은 렌더링 변경을 골든으로 실패 식별.

외부 호출 없음 — fake/in-memory·가짜 값만. 평면 ``tests/server/`` 컨벤션(conftest
공유 없이 자급자족, ``__init__.py`` 미추가). 평문 secret/식별자 금지.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from rider_crawl.config import AppConfig
from rider_crawl.message import render_current_screen_message
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)
from rider_crawl.redaction import redact
from rider_server.domain import Message
from rider_server.services import DispatchService, MessageRenderService
from rider_server.services.message_render_service import (
    _BAEMIN_TEMPLATE_VERSION,
    _COUPANG_TEMPLATE_VERSION,
    _PREVIEW_MAX_CHARS,
)

# 고정 주입 시각(결정성) — render_message 내부에서 now()/uuid4() 를 호출하지 않음을 잠근다.
# 2026-01-05 = 월요일(주중), 2026-01-03 = 토요일(주말) — 쿠팡 피크 시간표 분기 검증용.
_WEEKDAY = datetime(2026, 1, 5, 14, 2)
_WEEKEND = datetime(2026, 1, 3, 14, 2)


# ── fixture: test_message.py / test_coupang_message.py 의 골든 fixture(가짜 값만) ──


def _baemin_snapshot() -> CurrentScreenSnapshot:
    # test_message.py::test_render_current_screen_message_matches_spec_order 와 동일 값
    # (골든 텍스트 동등 단언을 위해 일치시킴).
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
        dinner_non_peak_count=3,
        non_peak_count=44.8,
        active_riders=5,
        reject_rate=2.3,
    )


def _coupang_current_screen() -> CurrentScreenSnapshot:
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
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=3,
    )


def _coupang_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_coupang_current_screen(),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=18, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def _config(tmp_path) -> AppConfig:
    # test_run_once_split.py 의 _config 동등(가짜 값만) — DispatchService hash 정합 단언용.
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        platform_name="baemin",
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


# ── AC1·AC3 — 배민 happy path: 필드·골든 동등(재구현 0) ────────────────────────


def test_render_message_baemin_fields_and_golden_text():
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="msg-1", snapshot_id="snap-1", source_label="센터", now=_WEEKDAY
    )

    assert isinstance(msg, Message)
    assert msg.id == "msg-1"
    assert msg.snapshot_id == "snap-1"
    assert msg.template_version == _BAEMIN_TEMPLATE_VERSION
    # 기존 renderer 출력과 바이트 동등(재구현 0 — AC3).
    assert msg.text == render_current_screen_message(raw, source_label="센터", now=_WEEKDAY)
    assert msg.text_hash == hashlib.sha256(msg.text.encode("utf-8")).hexdigest()
    # 배민 골든(now 무관): test_message.py 와 동일 텍스트(source_label 없는 형태).
    assert render_current_screen_message(raw) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰{5월21일} 14:02 기준",
            "",
            "오전오후피크 : 60.6건",
            "오후논피크 : 41.8건",
            "저녁피크 : 0건",
            "저녁논피크 : 3건",
        ]
    )


# ── AC1·AC3 — 쿠팡 happy path: 필드·동등 ───────────────────────────────────────


def test_render_message_coupang_fields_and_equivalence():
    raw = _coupang_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="msg-2", snapshot_id="snap-2", source_label="크롤링2", now=_WEEKDAY
    )

    assert msg.id == "msg-2"
    assert msg.snapshot_id == "snap-2"
    assert msg.template_version == _COUPANG_TEMPLATE_VERSION
    assert msg.text == render_current_screen_message(raw, source_label="크롤링2", now=_WEEKDAY)
    assert msg.text_hash == hashlib.sha256(msg.text.encode("utf-8")).hexdigest()
    # 쿠팡 골든(고정 now=주중): render_message 출력을 리터럴로 직접 잠가 self-reference 가
    # 아닌 실제 포맷 변경을 식별한다(배민 inline 골든과 동형 — _WEEKDAY 라 주중 시간표).
    assert msg.text == "\n".join(
        [
            "[실시간 실적봇]",
            "[크롤링2]",
            "⏰ 20:38 기준",
            "",
            "아침 : 완료 (06:00~10:54)",
            "██████████",
            "점심 피크 : 완료 (10:55~12:59)",
            "██████████",
            "점심 논피크 : 10건/19건 (13:00~16:54)",
            "█████░░░░░",
            "저녁 피크 : 17건/39건 (16:55~19:59)",
            "████░░░░░░",
            "저녁 논피크 : 2건/27건 (20:00~03:59)",
            "█░░░░░░░░░",
            "",
            "배정 103건 / 처리 67건",
            "거절률: 7.5%",
            "수행중인원: 3명",
        ]
    )


# ── AC1.2 — text_hash 가 3.1 DispatchService.message_hash 와 동일(3.5 dedup 정합) ──


def test_text_hash_matches_dispatch_message_hash(tmp_path):
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="msg-1", snapshot_id="snap-1", source_label="센터", now=_WEEKDAY
    )

    # text_hash 정의식(AC1.2).
    assert msg.text_hash == hashlib.sha256(msg.text.encode("utf-8")).hexdigest()
    # 같은 텍스트 → DispatchResult.message_hash == Message.text_hash (3.5 dedup 토대).
    result = DispatchService.dispatch(_config(tmp_path), msg.text, send_message=lambda _c, _m: None)
    assert result.message_hash == msg.text_hash


# ── AC1.3·AC2 — 결정성·재현성(같은 입력 → 같은 Message) ────────────────────────


def test_render_message_is_deterministic_and_reproducible():
    raw = _coupang_snapshot()
    kwargs = dict(message_id="msg-1", snapshot_id="snap-1", source_label="센터", now=_WEEKDAY)

    # 재수집 없이 같은 인자로 두 번 호출 → text/text_hash/template_version 동일(재현성).
    first = MessageRenderService.render_message(raw, **kwargs)
    second = MessageRenderService.render_message(raw, **kwargs)

    assert first.text == second.text
    assert first.text_hash == second.text_hash
    assert first.template_version == second.template_version


def test_baemin_render_is_now_independent():
    # 배민 경로는 피크 시간표를 안 써 now 무관 — 항상 결정적.
    raw = _baemin_snapshot()
    weekday = MessageRenderService.render_message(raw, message_id="m", snapshot_id="s", now=_WEEKDAY)
    weekend = MessageRenderService.render_message(raw, message_id="m", snapshot_id="s", now=_WEEKEND)

    assert weekday.text == weekend.text
    assert weekday.text_hash == weekend.text_hash


def test_coupang_render_now_determines_hash():
    # 쿠팡은 now(주중/주말)가 피크 시간표를 바꿔 text/text_hash 가 달라짐 → now 결정성 명시.
    raw = _coupang_snapshot()
    weekday = MessageRenderService.render_message(raw, message_id="m", snapshot_id="s", now=_WEEKDAY)
    weekend = MessageRenderService.render_message(raw, message_id="m", snapshot_id="s", now=_WEEKEND)

    assert weekday.text != weekend.text
    assert weekday.text_hash != weekend.text_hash
    assert weekday.template_version == weekend.template_version == _COUPANG_TEMPLATE_VERSION


# ── AC1 — frozen 불변 ──────────────────────────────────────────────────────────


def test_message_is_frozen():
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(raw, message_id="m", snapshot_id="s", now=_WEEKDAY)

    with pytest.raises(FrozenInstanceError):
        msg.text = "변경불가"  # type: ignore[misc]


# ── AC3 — 예상 외 타입 방어(정규화 후라 정상 미발생) ──────────────────────────


def test_render_message_rejects_unexpected_type():
    with pytest.raises(TypeError):
        MessageRenderService.render_message(object(), message_id="m", snapshot_id="s")  # type: ignore[arg-type]


# ── 누출·redaction — text_redacted_preview 는 redact(text)[:500] ───────────────


def test_text_redacted_preview_is_redacted_and_capped():
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="m", snapshot_id="s", source_label="센터", now=_WEEKDAY
    )

    assert msg.text_redacted_preview == redact(msg.text)[:_PREVIEW_MAX_CHARS]
    assert len(msg.text_redacted_preview) <= _PREVIEW_MAX_CHARS


# ── QA 갭 A — 쿠팡 일반 케이스(current_screen=None, peak-dashboard만) ───────────


def _coupang_snapshot_dashboard_only() -> PerformanceSnapshot:
    # 쿠팡 탭의 일반 케이스: peak-dashboard 한 페이지만 크롤링해 current_screen 이 없다
    # (models.py 61-64). 렌더러는 이때 '수행중인인원' 줄을 생략한다(message.py 96-97).
    return PerformanceSnapshot(
        current_screen=None,
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=18, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def test_render_message_coupang_without_current_screen():
    # current_screen=None 분기를 render_message 가 그대로 통과시켜 '수행중인인원' 줄이 없고,
    # template_version=쿠팡·hash 정합·골든 동등(재구현 0)이 유지된다.
    raw = _coupang_snapshot_dashboard_only()
    msg = MessageRenderService.render_message(
        raw, message_id="msg-3", snapshot_id="snap-3", source_label="크롤링3", now=_WEEKDAY
    )

    assert msg.template_version == _COUPANG_TEMPLATE_VERSION
    assert "수행중인인원" not in msg.text
    assert msg.text == render_current_screen_message(raw, source_label="크롤링3", now=_WEEKDAY)
    assert msg.text_hash == hashlib.sha256(msg.text.encode("utf-8")).hexdigest()


# ── QA 갭 B — render(3.1) ↔ render_message(3.3) 텍스트 정본 동등 ────────────────


def test_render_message_text_equals_render_str():
    # 텍스트 정본은 하나(모듈 docstring·Dev Notes (d) 불변식 — render(3.1) 본문 무변경
    # 회귀 그물). 두 메서드가 같은 render_current_screen_message 를 호출하므로
    # render_message(...).text == render(snapshot, source_label=...) 다. 배민은 now 무관이라
    # render(now 미주입)와 render_message(now 주입)가 같은 텍스트를 낸다.
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="m", snapshot_id="s", source_label="센터", now=_WEEKDAY
    )

    assert msg.text == MessageRenderService.render(raw, source_label="센터")


# ── QA 갭 C — 미리보기 길이 cap(_PREVIEW_MAX_CHARS) 경계: 500자 초과 시 잘림 ────


def test_text_redacted_preview_caps_text_over_limit():
    # source_label 은 렌더 텍스트에 [라벨] 줄로 포함되므로(message.py 51-52) 긴 비밀 아닌
    # 라벨로 텍스트를 500자 초과로 만들어 cap 을 실제로 유발한다. "구역"*300 = 600자
    # (숫자/콜론/@ 없음 → redact 무변경)이라 잘림이 결정적으로 일어난다.
    long_label = "구역" * 300
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="m", snapshot_id="s", source_label=long_label, now=_WEEKDAY
    )

    # cap 이 실제로 잘랐음을 증명: 원본 redact 결과는 500자보다 길고, 미리보기는 정확히 500자.
    assert len(redact(msg.text)) > _PREVIEW_MAX_CHARS
    assert len(msg.text_redacted_preview) == _PREVIEW_MAX_CHARS
    assert msg.text_redacted_preview == redact(msg.text)[:_PREVIEW_MAX_CHARS]


# ── QA 갭 D — 방어적 심층(NFR-5): 비밀 형태 문자열이 미리보기에서 마스킹됨 ──────


def test_text_redacted_preview_masks_secret_shaped_text():
    # 만약 비밀처럼 보이는 문자열이 렌더 텍스트에 섞여도 text_redacted_preview 는 redact 를
    # 통과해 그 본문이 남지 않는다(영속·표시용 무누출 — 기존 동어반복 단언을 보완). 명백한
    # 가짜 토큰 형태 — 진짜 봇 토큰 정규식 [0-9]{6,}:[A-Za-z0-9_-]{30,} 에 해당하지 않는다.
    fake_token_body = "AAE-fake-token-xyz"
    raw = _baemin_snapshot()
    msg = MessageRenderService.render_message(
        raw, message_id="m", snapshot_id="s", source_label=f"8:{fake_token_body}", now=_WEEKDAY
    )

    assert fake_token_body not in msg.text_redacted_preview
    assert "***REDACTED***" in msg.text_redacted_preview
