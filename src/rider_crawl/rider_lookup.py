"""Transport-neutral rider lookup command core.

This module owns the rider lookup *business contract* so KakaoTalk, Telegram, and
any future transport share one implementation of:

- the command token parser (``!!강민기1234``);
- the parsed command / matched-stats DTOs;
- rider row matching (exact NFC name + exact phone last-4);
- the cancel-rate calculation; and
- the final reply rendering (match / no-match / ambiguous / unsupported).

It intentionally has **no** KakaoTalk, Telegram, browser, Agent, FastAPI,
SQLAlchemy, or queue dependency. Transports provide only adapters (fetch rows,
send text) around this core. See
``docs/superpowers/specs/2026-07-01-kakao-inbound-rider-lookup-design.md``.

Both ``rider_agent`` (Kakao inbound) and ``rider_crawl.telegram_commands``
(Telegram) parse, match, and render through this core, so the ``!!`` + Hangul
command contract is shared across every transport. Telegram was converged onto
this contract in phase 6, dropping its legacy single-``!`` grammar.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from .parser import parse_count

# A reject/cancel rate at or above this percentage is reported as risky.
RISK_CANCEL_RATE_PERCENT = 9.0

# The only command type implemented in phase 1.
COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP = "RIDER_CANCEL_RATE_LOOKUP"

# A valid command is exactly ``!!`` + 1-20 Hangul syllables + 4 ASCII digits,
# starting at message start or after whitespace and ending at message end,
# whitespace, or one of the listed punctuation marks. Only the first valid token
# in a message is used (``re.search`` returns the leftmost match).
COMMAND_TOKEN_RE = re.compile(
    r"(?<!\S)!!(?P<name>[가-힣]{1,20})(?P<phone_last4>[0-9]{4})(?=$|\s|[.,!?;:)\]\}])"
)

# Fixed, redaction-safe reply strings. These never contain raw message text.
NO_MATCH_REPLY = "해당 라이더를 찾지 못했습니다."
AMBIGUOUS_REPLY_PREFIX = "동명이인 또는 중복 후보가 있어 조회할 수 없습니다: "
UNSUPPORTED_PLATFORM_REPLY = "라이더 조회 명령은 배민/쿠팡 탭에서만 지원합니다."

# Phone columns that may carry the rider's number, in preference order. Column
# matching also falls back to a whitespace-insensitive compare (see ``_cell``).
_PHONE_COLUMNS = ("휴대폰번호", "휴대폰 번호", "전화번호")


@dataclass(frozen=True)
class RiderLookupCommand:
    """A parsed, validated lookup command. ``name`` is NFC-normalized."""

    type: str
    name: str
    phone_last4: str


@dataclass(frozen=True)
class RiderCancelStats:
    name: str
    phone_last4: str
    completed_count: float | int
    rejected_count: float | int
    dispatch_cancel_count: float | int
    rider_fault_cancel_count: float | int
    total_cancel_count: float | int
    cancel_rate: float


@dataclass(frozen=True)
class RiderCancelMatch:
    """A matched rider's stats tagged with a redacted source label."""

    source_label: str
    stats: RiderCancelStats


def parse_rider_lookup_command(text: str) -> RiderLookupCommand | None:
    """Return the first valid command in ``text``, or ``None``.

    The cheap ``!!`` prefilter used by the DB reader is not enough; a message is
    only actionable when this parser finds a valid token.
    """

    if not text:
        return None
    match = COMMAND_TOKEN_RE.search(text)
    if not match:
        return None
    name = _normalize_name(match.group("name"))
    if not name:
        return None
    return RiderLookupCommand(
        type=COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
        name=name,
        phone_last4=match.group("phone_last4"),
    )


def find_rider_cancel_stats(
    rows: Iterable[Mapping[str, str]],
    *,
    name: str,
    phone_last4: str,
) -> list[RiderCancelStats]:
    """Return cancel stats for every row whose name and phone last-4 match.

    Matching is exact: names are compared after Unicode NFC normalization and
    whitespace trim; phone fields are reduced to digits and compared on their
    last four. No fuzzy matching.
    """

    target_name = _normalize_name(name)
    matches: list[RiderCancelStats] = []
    for row in rows:
        row_name = _normalize_name(_cell(row, "이름"))
        phone = _cell(row, *_PHONE_COLUMNS)
        if row_name != target_name or _phone_last4(phone) != phone_last4:
            continue

        completed = _number(row, "완료")
        rejected = _number(row, "거절")
        dispatch_cancelled = _number(row, "배차취소")
        rider_fault_cancelled = _number(row, "배달취소(라이더귀책)")
        total_cancelled = dispatch_cancelled + rider_fault_cancelled
        matches.append(
            RiderCancelStats(
                name=row_name,
                phone_last4=phone_last4,
                completed_count=completed,
                rejected_count=rejected,
                dispatch_cancel_count=dispatch_cancelled,
                rider_fault_cancel_count=rider_fault_cancelled,
                total_cancel_count=total_cancelled,
                cancel_rate=calculate_cancel_rate(
                    completed=completed,
                    rejected=rejected,
                    total_cancelled=total_cancelled,
                ),
            )
        )
    return matches


def find_rider_cancel_matches(
    rows: Iterable[Mapping[str, str]],
    *,
    command: RiderLookupCommand,
    source_label: str,
) -> list[RiderCancelMatch]:
    """Find matches in one source's rows, tagged with ``source_label``.

    Callers aggregate matches across sources before rendering; the renderer
    decides single / no-match / ambiguous from the aggregated list.
    """

    return [
        RiderCancelMatch(source_label=source_label, stats=stats)
        for stats in find_rider_cancel_stats(
            rows, name=command.name, phone_last4=command.phone_last4
        )
    ]


def calculate_cancel_rate(
    *,
    completed: float | int,
    rejected: float | int,
    total_cancelled: float | int,
) -> float:
    denominator = completed + rejected + total_cancelled
    if denominator == 0:
        return 0
    return round(((rejected + total_cancelled) / denominator) * 100, 1)


def render_rider_cancel_reply(stats: RiderCancelStats) -> str:
    """Render the three-line reply for a single matched rider."""

    return (
        f"{stats.name}{stats.phone_last4}님\n"
        f"거절:{_format_number(stats.rejected_count)}개/취소:{_format_number(stats.total_cancel_count)}개\n"
        f"거절/취소율:{_format_number(stats.cancel_rate)}%"
    )


def render_lookup_reply(
    command: RiderLookupCommand,
    matches: list[RiderCancelMatch],
) -> str:
    """Render the final reply for a command given its aggregated matches."""

    header = f"{command.name}{command.phone_last4}"
    if not matches:
        return f"{header}\n{NO_MATCH_REPLY}"
    if len(matches) > 1:
        labels = ", ".join(match.source_label for match in matches)
        return f"{header}\n{AMBIGUOUS_REPLY_PREFIX}{labels}"
    return render_rider_cancel_reply(matches[0].stats)


def render_unsupported_platform_reply() -> str:
    """The fixed reply when a command maps to a non-Baemin target."""

    return UNSUPPORTED_PLATFORM_REPLY


class RiderLookupCommandService:
    """Thin facade over the transport-neutral command core.

    Transports (Kakao, Telegram) call ``parse`` on inbound text, ``find_matches``
    on fetched rider rows, and one of the ``render_*`` methods to build the reply.
    """

    risk_cancel_rate_percent: float = RISK_CANCEL_RATE_PERCENT

    def parse(self, text: str) -> RiderLookupCommand | None:
        return parse_rider_lookup_command(text)

    def find_matches(
        self,
        rows: Iterable[Mapping[str, str]],
        *,
        command: RiderLookupCommand,
        source_label: str,
    ) -> list[RiderCancelMatch]:
        return find_rider_cancel_matches(
            rows, command=command, source_label=source_label
        )

    def render_reply(
        self,
        command: RiderLookupCommand,
        matches: list[RiderCancelMatch],
    ) -> str:
        return render_lookup_reply(command, matches)

    def render_unsupported_platform_reply(self) -> str:
        return render_unsupported_platform_reply()


def _cell(row: Mapping[str, str], *names: str) -> str:
    for name in names:
        if name in row:
            return row[name]
    for name in names:
        compact_name = _compact(name)
        for key, value in row.items():
            if _compact(key) == compact_name:
                return value
    return ""


def _number(row: Mapping[str, str], *names: str) -> float | int:
    return parse_count(_cell(row, *names) or "0")


def _phone_last4(phone: str) -> str:
    digits = "".join(re.findall(r"\d", phone))
    return digits[-4:] if len(digits) >= 4 else ""


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFC", value or "").strip()


def _format_number(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
