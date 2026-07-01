"""Tests for the transport-neutral rider lookup command core.

These lock the shared command contract from
``docs/superpowers/specs/2026-07-01-kakao-inbound-rider-lookup-design.md`` so it
can be reused by Kakao and Telegram. They must not depend on any transport.
"""

import unicodedata

import pytest

from rider_crawl.parser import parse_baemin_delivery_history_html
from rider_crawl.rider_lookup import (
    COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
    RiderCancelMatch,
    RiderCancelStats,
    RiderLookupCommandService,
    calculate_cancel_rate,
    find_rider_cancel_matches,
    find_rider_cancel_stats,
    parse_rider_lookup_command,
    render_lookup_reply,
    render_rider_cancel_reply,
    render_unsupported_platform_reply,
)


# --- Command parser -------------------------------------------------------

def test_parses_valid_command():
    command = parse_rider_lookup_command("!!강민기1234")

    assert command is not None
    assert command.type == COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP
    assert command.name == "강민기"
    assert command.phone_last4 == "1234"


def test_parses_command_preceded_by_whitespace():
    command = parse_rider_lookup_command("확인 !!강민기1234")

    assert command is not None
    assert command.name == "강민기"
    assert command.phone_last4 == "1234"


@pytest.mark.parametrize("text", ["!!강민기1234.", "!!김1234 확인", "!!강민기1234)"])
def test_parses_command_with_trailing_boundary(text):
    command = parse_rider_lookup_command(text)

    assert command is not None
    assert command.phone_last4 == "1234"


@pytest.mark.parametrize(
    "text",
    [
        "!!",
        "!!1234",
        "!!강민기12",
        "!!강민기12345",
        "!!hong1234",
        "메모!!강민기1234",
        "",
    ],
)
def test_rejects_invalid_command(text):
    assert parse_rider_lookup_command(text) is None


def test_processes_only_first_valid_token():
    command = parse_rider_lookup_command("!!강민기1234 !!이순신5678")

    assert command is not None
    assert command.name == "강민기"
    assert command.phone_last4 == "1234"


# --- Rider matching -------------------------------------------------------

def _row(name: str, phone: str, *, completed="100", rejected="5", dispatch="3", rider_fault="2"):
    return {
        "이름": name,
        "휴대폰번호": phone,
        "완료": completed,
        "거절": rejected,
        "배차취소": dispatch,
        "배달취소(라이더귀책)": rider_fault,
    }


def test_find_matches_exact_name_and_phone_suffix():
    rows = [
        _row("강민기", "010-1111-1234"),
        _row("강민기", "010-1111-9999"),
        _row("다른사람", "010-1111-1234"),
    ]

    matches = find_rider_cancel_stats(rows, name="강민기", phone_last4="1234")

    assert len(matches) == 1
    stats = matches[0]
    assert stats.name == "강민기"
    assert stats.phone_last4 == "1234"
    assert stats.completed_count == 100
    assert stats.rejected_count == 5
    assert stats.dispatch_cancel_count == 3
    assert stats.rider_fault_cancel_count == 2
    assert stats.total_cancel_count == 5


def test_find_matches_normalizes_row_name_to_nfc():
    decomposed = unicodedata.normalize("NFD", "강민기")
    assert decomposed != "강민기"  # sanity: the row name is not already NFC

    matches = find_rider_cancel_stats([_row(decomposed, "010-1111-1234")], name="강민기", phone_last4="1234")

    assert len(matches) == 1
    assert matches[0].name == "강민기"


def test_find_matches_does_not_fuzzy_match_phone_suffix():
    matches = find_rider_cancel_stats([_row("강민기", "010-1111-9234")], name="강민기", phone_last4="1234")

    assert matches == []


def test_find_matches_uses_rows_from_html_parser():
    html = """
    <table>
      <thead><tr>
        <th>이름</th><th>수행상태</th><th>휴대폰번호</th><th>완료</th><th>거절</th>
        <th>배차취소</th><th>배달취소(라이더귀책)</th>
      </tr></thead>
      <tbody>
        <tr><td>강민기</td><td>수행중</td><td>010-1111-1234</td><td>100</td><td>5</td><td>3</td><td>2</td></tr>
      </tbody>
    </table>
    """
    table = parse_baemin_delivery_history_html(html)
    command = parse_rider_lookup_command("!!강민기1234")

    matches = find_rider_cancel_matches(table.riders, command=command, source_label="남구센터")

    assert len(matches) == 1
    assert matches[0].source_label == "남구센터"
    assert matches[0].stats.total_cancel_count == 5


# --- Cancel-rate calculation ----------------------------------------------

def test_calculate_cancel_rate_threshold_is_four_percent():
    # 5 / (100 + 20 + 5) = 4.0% -> exactly at the risky threshold.
    assert calculate_cancel_rate(completed=100, rejected=20, total_cancelled=5) == 4.0


def test_calculate_cancel_rate_zero_denominator_is_zero():
    assert calculate_cancel_rate(completed=0, rejected=0, total_cancelled=0) == 0


def test_calculate_cancel_rate_rounds_to_one_decimal():
    # 2 / (50 + 0 + 2) = 3.846... -> 3.8
    assert calculate_cancel_rate(completed=50, rejected=0, total_cancelled=2) == 3.8


# --- Reply rendering ------------------------------------------------------

def _stats(cancel_rate: float, total_cancel: int) -> RiderCancelStats:
    return RiderCancelStats(
        name="강민기",
        phone_last4="1234",
        completed_count=0,
        rejected_count=0,
        dispatch_cancel_count=0,
        rider_fault_cancel_count=0,
        total_cancel_count=total_cancel,
        cancel_rate=cancel_rate,
    )


def test_render_single_match_normal_range():
    reply = render_rider_cancel_reply(_stats(3.8, 2))

    assert reply == "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다."


def test_render_single_match_risky_at_threshold():
    reply = render_rider_cancel_reply(_stats(4.0, 5))

    assert reply == "강민기1234\n취소율 4%, 취소 5개\n위험합니다."


def test_render_no_match_reply():
    command = parse_rider_lookup_command("!!강민기1234")

    reply = render_lookup_reply(command, [])

    assert reply == "강민기1234\n해당 라이더를 찾지 못했습니다."


def test_render_ambiguous_reply_uses_source_labels_without_full_phones():
    command = parse_rider_lookup_command("!!강민기1234")
    matches = [
        RiderCancelMatch(source_label="크롤링1", stats=_stats(3.8, 2)),
        RiderCancelMatch(source_label="크롤링2", stats=_stats(1.0, 1)),
    ]

    reply = render_lookup_reply(command, matches)

    assert reply == "강민기1234\n동명이인 또는 중복 후보가 있어 조회할 수 없습니다: 크롤링1, 크롤링2"
    assert "010" not in reply  # no full phone numbers leak


def test_render_single_match_via_lookup_reply():
    command = parse_rider_lookup_command("!!강민기1234")
    matches = [RiderCancelMatch(source_label="남구센터", stats=_stats(3.8, 2))]

    reply = render_lookup_reply(command, matches)

    assert reply == "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다."


def test_render_unsupported_platform_reply():
    assert render_unsupported_platform_reply() == "라이더 조회 명령은 배민/쿠팡 탭에서만 지원합니다."


# --- Service facade -------------------------------------------------------

def test_service_end_to_end_baemin_rows():
    service = RiderLookupCommandService()
    command = service.parse("운영방 !!강민기1234")
    assert command is not None

    rows = [_row("강민기", "010-1111-1234", completed="50", rejected="0", dispatch="1", rider_fault="1")]
    matches = service.find_matches(rows, command=command, source_label="남구센터")

    assert service.render_reply(command, matches) == "강민기1234\n취소율 3.8%, 취소 2개\n정상 범위입니다."


def test_service_render_reply_no_match():
    service = RiderLookupCommandService()
    command = service.parse("!!강민기1234")

    assert service.render_reply(command, []) == "강민기1234\n해당 라이더를 찾지 못했습니다."
