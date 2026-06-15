from __future__ import annotations

import re
from html.parser import HTMLParser

from rider_crawl.models import CurrentScreenSnapshot, PeakDashboardSnapshot, PeakPeriodSnapshot


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


def parse_peak_dashboard_html(html: str) -> PeakDashboardSnapshot:
    return parse_peak_dashboard_text(html_to_text(html))


def parse_peak_dashboard_text(text: str) -> PeakDashboardSnapshot:
    normalized = _normalize_text(text)
    update_match = re.search(r"(?P<time>\d{1,2}:\d{2})\s*업데이트", normalized)
    if not update_match:
        raise MissingPerformanceDataError("peak dashboard update time was not found")

    return PeakDashboardSnapshot(
        updated_at=update_match.group("time"),
        assigned_count=parse_count(_required_number_after("배정 물량", normalized)),
        processed_count=parse_count(_required_number_after("처리 물량", normalized)),
        reject_rate=parse_count(_required_number_after("거절률", normalized)),
        morning=_required_peak_period("아침", normalized),
        lunch_peak=_required_peak_period("점심 피크", normalized),
        lunch_non_peak=_required_peak_period("점심 논피크", normalized),
        dinner_peak=_required_peak_period("저녁 피크", normalized),
        dinner_non_peak=_required_peak_period("저녁 논피크", normalized),
    )


def parse_current_screen_text(text: str) -> CurrentScreenSnapshot:
    normalized = _normalize_text(text)

    # The page heading carries the center name, shift, time range and status, so
    # use it as the primary source instead of hardcoding any single region.
    heading_match = re.search(
        r"(?P<center>.+?)\s+(?P<shift>[가-힣]+)\((?P<range>\d{2}:\d{2}~\d{2}:\d{2})\)\s+"
        r"(?P<status>.+?)\s+라이더 현황",
        normalized,
    )
    update_match = re.search(r"(?P<time>\d{1,2}:\d{2})\s*업데이트", normalized)
    available_match = re.search(r"(?P<current>\d+)\s*/\s*(?P<total>\d+)(?:\s*명)?", normalized)

    if not heading_match or not update_match or not available_match:
        record_snapshot = _parse_record_table_current_screen_text(normalized)
        if record_snapshot is not None:
            return record_snapshot
        raise MissingPerformanceDataError("required performance summary text was not found")

    online_riders = int(parse_count(_required_number_after("온라인", normalized)))

    return CurrentScreenSnapshot(
        center_name=heading_match.group("center").strip(),
        date_label=_extract_date_label(normalized),
        shift_label=heading_match.group("shift"),
        shift_time_range=heading_match.group("range"),
        shift_status=heading_match.group("status").strip(),
        updated_at=update_match.group("time"),
        available_current=int(available_match.group("current")),
        available_total=int(available_match.group("total")),
        waiting_count=int(parse_count(_required_number_after("대기", normalized))),
        online_riders=online_riders,
        rejected_ignored_count=parse_count(_required_number_after("거절/무시", normalized)),
        cancelled_count=parse_count(_required_number_after("취소", normalized)),
        completed_count=parse_count(_required_number_after("완료", normalized)),
        sequence_violation_count=parse_count(_required_number_after("순서 미준수", normalized)),
        lunch_peak_count=parse_count(_required_number_after("점심피크", normalized)),
        dinner_peak_count=parse_count(_required_number_after("저녁피크", normalized)),
        non_peak_count=parse_count(_required_number_after("논피크", normalized)),
        active_riders=online_riders,
    )


def _parse_record_table_current_screen_text(text: str) -> CurrentScreenSnapshot | None:
    if "라이더 현황" not in text or "이름 / 연락처" not in text:
        return None

    update_match = re.search(r"(?P<time>\d{1,2}:\d{2})\s*업데이트", text)
    if not update_match:
        return None

    try:
        online_riders = int(parse_count(_required_number_after("온라인", text)))
        rejected_ignored_count = parse_count(_required_number_after("거절/무시", text))
        cancelled_count = parse_count(_required_number_after("취소", text))
        completed_count = parse_count(_required_number_after("완료", text))
        sequence_violation_count = parse_count(_required_number_after("순서 미준수", text))
        lunch_peak_count = parse_count(_required_number_after("점심피크", text))
        dinner_peak_count = parse_count(_required_number_after("저녁피크", text))
        non_peak_count = parse_count(_required_number_after("논피크", text))
    except (MissingPerformanceDataError, ValueError):
        return None

    return CurrentScreenSnapshot(
        center_name=_record_table_center_name(text),
        date_label=_extract_date_label(text),
        shift_label="",
        shift_time_range="",
        shift_status="",
        updated_at=update_match.group("time"),
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=online_riders,
        rejected_ignored_count=rejected_ignored_count,
        cancelled_count=cancelled_count,
        completed_count=completed_count,
        sequence_violation_count=sequence_violation_count,
        lunch_peak_count=lunch_peak_count,
        dinner_peak_count=dinner_peak_count,
        non_peak_count=non_peak_count,
        active_riders=online_riders,
    )


def _record_table_center_name(text: str) -> str:
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if value in {"라이더 기록 - vendor-portal", "Hi there! Please", "enable Javascript"}:
            continue
        if re.fullmatch(r"\d{1,2}\.\d{1,2}", value) or re.fullmatch(r"\d{1,2}:\d{2}", value):
            continue
        if value in {"~", "(오늘)"}:
            continue
        return value
    return ""


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


def parse_quantity_pair(raw: str) -> tuple[float | int, float | int]:
    parts = [part.strip() for part in raw.split("/")]
    if len(parts) != 2:
        raise ValueError(f"pair text needs two values: {raw!r}")
    return parse_count(parts[0]), parse_count(parts[1])


def _scrapling_text(html: str) -> str:
    try:
        from scrapling.parser import Selector
    except ImportError:
        try:
            from scrapling.parser import Adaptor
        except ImportError:
            return ""

        page = Adaptor(html)
        text = page.get_all_text()
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

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


def _required_peak_period(label: str, text: str) -> PeakPeriodSnapshot:
    peak_text = _peak_time_section(text)
    match = re.search(
        rf"{re.escape(label)}\n.*?목표/완료\n(?P<pair>-?\d+(?:\.\d+)?\s*/\s*-?\d+(?:\.\d+)?)",
        peak_text,
        flags=re.DOTALL,
    )
    if not match:
        raise MissingPerformanceDataError(f"{label} target/completed pair was not found")

    total, done = parse_quantity_pair(match.group("pair"))
    return PeakPeriodSnapshot(done=done, total=total)


def _peak_time_section(text: str) -> str:
    start = text.find("피크타임별 현황")
    if start == -1:
        return text
    end = text.find("시간대별 기록", start)
    return text[start:] if end == -1 else text[start:end]
