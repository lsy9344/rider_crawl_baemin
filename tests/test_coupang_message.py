from datetime import datetime

from rider_crawl.message import render_current_screen_message
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)

# 2026-06-08은 월요일(주중), 2026-06-13은 토요일(주말)
WEEKDAY = datetime(2026, 6, 8, 20, 38)
WEEKEND = datetime(2026, 6, 13, 20, 38)


def test_render_coupang_performance_message_matches_original_format():
    snapshot = PerformanceSnapshot(
        current_screen=_current_screen(active_riders=3),
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

    assert render_current_screen_message(snapshot, now=WEEKDAY) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰ 20:38 기준",
            "",
            "아침 : 완료 (06:00~10:54)",
            "점심 피크 : 완료 (10:54~12:59)",
            "점심 논피크 : 10건/19건 (13:00~16:54)",
            "저녁 피크 : 17건/39건 (16:55~19:59)",
            "저녁 논피크 : 2건/27건 (20:00~03:59)",
            "",
            "배정 103건 / 처리 67건",
            "🚨거절률: 6.5%🚨",
            "🌇수행중인인원 : 3명",
        ]
    )


def test_render_coupang_performance_message_omits_active_riders_when_current_screen_missing():
    # 쿠팡 탭은 peak-dashboard 한 페이지만 크롤링하므로 current_screen이 없다(None).
    # 이때 '수행중인인원' 줄은 생략하고, 나머지 peak 지표는 그대로 보낸다.
    snapshot = PerformanceSnapshot(
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

    assert render_current_screen_message(snapshot, now=WEEKDAY) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰ 20:38 기준",
            "",
            "아침 : 완료 (06:00~10:54)",
            "점심 피크 : 완료 (10:54~12:59)",
            "점심 논피크 : 10건/19건 (13:00~16:54)",
            "저녁 피크 : 17건/39건 (16:55~19:59)",
            "저녁 논피크 : 2건/27건 (20:00~03:59)",
            "",
            "배정 103건 / 처리 67건",
            "🚨거절률: 6.5%🚨",
        ]
    )
    assert "수행중인인원" not in render_current_screen_message(snapshot, now=WEEKDAY)


def test_render_coupang_performance_message_uses_weekend_times_on_weekend():
    snapshot = PerformanceSnapshot(
        current_screen=_current_screen(active_riders=3),
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

    message = render_current_screen_message(snapshot, now=WEEKEND)

    assert "아침 : 완료 (06:00~10:54)" in message
    assert "점심 피크 : 완료 (10:54~01:59)" in message
    assert "점심 논피크 : 10건/19건 (02:00~04:54)" in message
    assert "저녁 피크 : 17건/39건 (04:55~07:59)" in message
    assert "저녁 논피크 : 2건/27건 (20:00~03:59)" in message


def test_render_coupang_performance_message_keeps_current_tab_label_when_present():
    snapshot = PerformanceSnapshot(
        current_screen=_current_screen(active_riders=4),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:54",
            assigned_count=103,
            processed_count=68,
            reject_rate=6.2,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=19, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=3, total=27),
        ),
    )

    message = render_current_screen_message(snapshot, source_label="크롤링2", now=WEEKDAY)

    assert message.splitlines()[0:2] == ["[실시간 실적봇]", "[크롤링2]"]
    assert "점심 논피크 : 완료 (13:00~16:54)" in message


def _current_screen(*, active_riders: int) -> CurrentScreenSnapshot:
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
        active_riders=active_riders,
    )
