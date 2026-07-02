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
            "[택트런 실적봇]",
            "⏰{5월21일} 14:02 기준",
            "",
            "오전오후피크 : 60.6건",
            "오후논피크 : 41.8건",
            "저녁피크 : 0건",
            "저녁논피크 : 3건",
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
    assert "취소율" not in message


def test_render_current_screen_message_shows_plain_cancel_rate_when_available():
    snapshot = CurrentScreenSnapshot(
        center_name="배민 배달현황",
        date_label="5월 21일(오늘)",
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at="14:02",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=3,
        completed_count=57,
        sequence_violation_count=0,
        lunch_peak_count=0,
        afternoon_non_peak_count=0,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=0,
        active_riders=0,
        reject_rate=2.3,
        cancel_rate=5.0,
    )

    message = render_current_screen_message(snapshot)

    assert "취소율 : 5%" in message
    assert "거절율" not in message
    assert "위험" not in message
    assert "취소 3건" not in message


def test_render_current_screen_message_keeps_cancel_rate_decimal_without_adjustment():
    snapshot = CurrentScreenSnapshot(
        center_name="배민 배달현황",
        date_label="",
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at="14:02",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=8,
        completed_count=55,
        sequence_violation_count=0,
        lunch_peak_count=0,
        afternoon_non_peak_count=0,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=0,
        active_riders=0,
        cancel_rate=12.5,
    )

    message = render_current_screen_message(snapshot)

    assert "취소율 : 12.5%" in message
    assert "13.5%" not in message


def test_render_current_screen_message_includes_achievement_goals_when_available():
    snapshot = CurrentScreenSnapshot(
        center_name="표준서울마포B이츠앤홀딩스3",
        date_label="26-06-10",
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at="00:37",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=0,
        completed_count=0,
        sequence_violation_count=0,
        lunch_peak_count=323,
        lunch_peak_goal=231,
        lunch_peak_rate=100,
        afternoon_non_peak_count=296,
        afternoon_non_peak_goal=220,
        afternoon_non_peak_rate=100,
        dinner_peak_count=433,
        dinner_peak_goal=330,
        dinner_peak_rate=100,
        dinner_non_peak_count=374,
        dinner_non_peak_goal=319,
        dinner_non_peak_rate=100,
        non_peak_count=670,
        active_riders=0,
        reject_rate=11.82,
    )

    assert render_current_screen_message(snapshot, source_label="표준서울마포B이츠앤홀딩스3") == "\n".join(
        [
            "[택트런 실적봇]",
            "[표준서울마포B이츠앤홀딩스3]",
            "⏰{6월10일} 00:37 기준",
            "",
            "오전오후피크 : 323건/231건[100%]",
            "██████████",
            "오후논피크 : 296건/220건[100%]",
            "██████████",
            "저녁피크 : 433건/330건[100%]",
            "██████████",
            "저녁논피크 : 374건/319건[100%]",
            "██████████",
        ]
    )


def test_render_current_screen_message_drops_reject_rate_line():
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
        reject_rate=99.4,
        cancel_rate=8.3,
    )

    message = render_current_screen_message(snapshot)

    assert "거절율" not in message
    assert "취소율 : 8.3%" in message


def test_render_current_screen_message_shows_active_riders_when_history_merged():
    # 배달현황을 함께 읽으면(취소율이 채워지면) '수행중인원'(운행상태가 '운행중'인 라이더
    # 수)을 쿠팡 메시지처럼 붙인다. 줄 순서는 취소율 다음(맨 끝)이다.
    snapshot = CurrentScreenSnapshot(
        center_name="표준서울마포B이츠앤홀딩스3",
        date_label="26-06-17",
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at="20:30",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=23,
        rejected_ignored_count=0,
        cancelled_count=0,
        completed_count=0,
        sequence_violation_count=0,
        lunch_peak_count=0,
        afternoon_non_peak_count=0,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=0,
        active_riders=23,
        reject_rate=11.82,
        cancel_rate=4.7,
    )

    message = render_current_screen_message(snapshot)

    assert "수행중인원 : 23명" in message
    lines = message.splitlines()
    assert lines.index("취소율 : 4.7%") < lines.index("수행중인원 : 23명")


def test_render_current_screen_message_omits_active_riders_without_history():
    # 달성현황만 읽어 취소율이 없으면(=배달현황 미수집) 수행중인원도 생략한다.
    snapshot = CurrentScreenSnapshot(
        center_name="표준서울마포B이츠앤홀딩스3",
        date_label="26-06-17",
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at="20:30",
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=0,
        completed_count=0,
        sequence_violation_count=0,
        lunch_peak_count=323,
        afternoon_non_peak_count=296,
        dinner_peak_count=433,
        dinner_non_peak_count=374,
        non_peak_count=670,
        active_riders=0,
        reject_rate=11.82,
    )

    message = render_current_screen_message(snapshot)

    assert "수행중인원" not in message
