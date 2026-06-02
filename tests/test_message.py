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
        dinner_peak_count=0,
        non_peak_count=41.8,
        active_riders=5,
    )

    assert render_current_screen_message(snapshot) == "\n".join(
        [
            "[실시간 실적봇]",
            "⏰ 14:02 기준",
            "",
            "오후논피크 : 7명/25명",
            "대기 : 0명",
            "",
            "완료 : 102.4건",
            "거절/무시 : 2.4건",
            "취소 : 0건",
            "점심피크 : 60.6건",
            "저녁피크 : 0건",
            "논피크 : 41.8건",
            "수행중인인원 : 5명",
        ]
    )


def test_render_current_screen_message_includes_calculated_rates_when_available():
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
        dinner_peak_count=0,
        non_peak_count=2,
        active_riders=1,
        reject_rate=16.7,
        cancel_rate=8.3,
    )

    message = render_current_screen_message(snapshot)

    assert "거절율 : 16.7%" in message
    assert "취소율 : 8.3%" in message
