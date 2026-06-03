from rider_crawl.message import render_current_screen_message
from rider_crawl.models import CurrentScreenSnapshot


def test_render_current_screen_message_matches_spec_order():
    snapshot = CurrentScreenSnapshot(
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

    assert render_current_screen_message(snapshot) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰ 14:02 기준",
            "",
            "오전오후피크 : 60.6건",
            "오후논피크 : 41.8건",
            "저녁피크 : 0건",
            "저녁논피크 : 3건",
            "",
            "거절율 : 2.3%",
        ]
    )


def test_render_current_screen_message_omits_reject_rate_when_unavailable():
    snapshot = CurrentScreenSnapshot(
        center_name="배민 배달현황",
        date_label="",
        shift_label="배달현황",
        shift_time_range="",
        shift_status="",
        updated_at="14:02",
        available_current=1,
        available_total=20,
        waiting_count=19,
        online_riders=1,
        rejected_ignored_count=2,
        cancelled_count=1,
        completed_count=9,
        sequence_violation_count=0,
        lunch_peak_count=7,
        afternoon_non_peak_count=2,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=2,
        active_riders=1,
    )

    message = render_current_screen_message(snapshot)

    assert "거절율" not in message
