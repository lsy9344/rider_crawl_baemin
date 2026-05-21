from __future__ import annotations

import re
from html.parser import HTMLParser

from .models import CurrentScreenSnapshot


class MissingPerformanceDataError(ValueError):
    """Raised when the performance page text does not contain required fields."""


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


def html_to_text(html: str) -> str:
    scrapling_text = _scrapling_text(html)
    if scrapling_text:
        return _append_input_values(scrapling_text, html)

    parser = _VisibleTextParser()
    parser.feed(html)
    return _append_input_values(parser.text(), html)


def parse_current_screen_html(html: str) -> CurrentScreenSnapshot:
    return parse_current_screen_text(html_to_text(html))


def parse_current_screen_text(text: str) -> CurrentScreenSnapshot:
    normalized = _normalize_text(text)

    center_match = re.search(r"(?P<center>.+?)\s+의정부남부", normalized)
    heading_match = re.search(
        r"(?P<center>.+?)\s+(?P<shift>[가-힣]+)\((?P<range>\d{2}:\d{2}~\d{2}:\d{2})\)\s+"
        r"(?P<status>.+?)\s+라이더 현황",
        normalized,
    )
    update_match = re.search(r"(?P<time>\d{1,2}:\d{2})\s*업데이트", normalized)
    available_match = re.search(r"(?P<current>\d+)\s*/\s*(?P<total>\d+)\s*명", normalized)

    if not heading_match or not update_match or not available_match:
        raise MissingPerformanceDataError("required performance summary text was not found")

    return CurrentScreenSnapshot(
        center_name=(heading_match.group("center") if heading_match else center_match.group("center")).strip(),
        date_label=_extract_date_label(normalized),
        shift_label=heading_match.group("shift"),
        shift_time_range=heading_match.group("range"),
        shift_status=heading_match.group("status").strip(),
        updated_at=update_match.group("time"),
        available_current=int(available_match.group("current")),
        available_total=int(available_match.group("total")),
        waiting_count=int(parse_count(_required_number_after("대기", normalized))),
        online_riders=int(parse_count(_required_number_after("온라인", normalized))),
        rejected_ignored_count=parse_count(_required_number_after("거절/무시", normalized)),
        cancelled_count=parse_count(_required_number_after("취소", normalized)),
        completed_count=parse_count(_required_number_after("완료", normalized)),
        sequence_violation_count=parse_count(_required_number_after("순서 미준수", normalized)),
        lunch_peak_count=parse_count(_required_number_after("점심피크", normalized)),
        dinner_peak_count=parse_count(_required_number_after("저녁피크", normalized)),
        non_peak_count=parse_count(_required_number_after("논피크", normalized)),
        active_riders=len(re.findall(r"\b배달중\b", normalized)),
    )


def parse_count(raw: str) -> float | int:
    if raw.strip() == "-":
        return 0

    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", raw)
    if not match:
        raise ValueError(f"count text has no number: {raw!r}")

    value = float(match.group(0).replace(",", ""))
    return int(value) if value.is_integer() else value


def parse_pair(raw: str) -> tuple[int, int]:
    numbers = re.findall(r"\d+", raw.replace(",", ""))
    if len(numbers) < 2:
        raise ValueError(f"pair text needs two numbers: {raw!r}")
    return int(numbers[0]), int(numbers[1])


def _scrapling_text(html: str) -> str:
    try:
        from scrapling.parser import Selector
    except ImportError:
        return ""

    page = Selector(html)
    chunks = page.css("body *::text").getall()
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _append_input_values(text: str, html: str) -> str:
    values = re.findall(r"<input\b[^>]*\bvalue=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)
    if not values:
        return text
    return "\n".join([text, *values])


def _extract_date_label(text: str) -> str:
    match = re.search(r"\d+월\s+\d+일\(오늘\)", text)
    return match.group(0) if match else ""


def _required_number_after(label: str, text: str) -> str:
    compact = text.replace("\n", " ")
    match = re.search(rf"{re.escape(label)}\s*[: ]\s*(?P<value>-|\d+(?:,\d{{3}})*(?:\.\d+)?\s*(?:건|명|%)?)", compact)
    if not match:
        raise MissingPerformanceDataError(f"{label} value was not found")
    return match.group("value")
