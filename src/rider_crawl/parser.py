from __future__ import annotations

import re
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import datetime

from .models import CurrentScreenSnapshot


class MissingPerformanceDataError(ValueError):
    """Raised when the performance page text does not contain required fields."""


BAEMIN_DELIVERY_COLUMNS = [
    "이름",
    "운행상태",
    "휴대폰번호",
    "완료",
    "거절",
    "배차취소",
    "배달취소(라이더귀책)",
    "아침점심피크",
    "오후논피크",
    "저녁피크",
    "심야논피크",
    "6시",
    "7시",
    "8시",
    "9시",
    "10시",
    "11시",
    "12시",
    "13시",
    "14시",
    "15시",
    "16시",
    "17시",
    "18시",
    "19시",
    "20시",
    "21시",
    "22시",
    "23시",
    "0시",
    "1시",
    "2시",
    "3시",
    "4시",
    "5시",
    "아이디",
]

BAEMIN_REQUIRED_METRIC_COLUMNS = [
    "완료",
    "거절",
    "배차취소",
    "배달취소(라이더귀책)",
    "아침점심피크",
    "오후논피크",
    "저녁피크",
    "심야논피크",
]


@dataclass(frozen=True)
class BaeminDeliveryHistoryTable:
    headers: list[str]
    summary: dict[str, str] | None
    riders: list[dict[str, str]]


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


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
        elif self._table_depth and tag == "tr":
            self._current_row = []
        elif self._table_depth and tag in {"th", "td"}:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append(_normalize_cell_text("".join(self._current_cell)))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_table is not None and any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1 and self._current_table is not None:
                self.tables.append(self._current_table)
                self._current_table = None
            self._table_depth -= 1


def html_to_text(html: str) -> str:
    scrapling_text = _scrapling_text(html)
    if scrapling_text:
        return _append_input_values(scrapling_text, html)

    parser = _VisibleTextParser()
    parser.feed(html)
    return _append_input_values(parser.text(), html)


def parse_current_screen_html(html: str) -> CurrentScreenSnapshot:
    try:
        table = parse_baemin_delivery_history_html(html)
    except MissingPerformanceDataError:
        return parse_current_screen_text(html_to_text(html))

    return baemin_delivery_history_to_snapshot(table)


def parse_baemin_delivery_history_html(html: str) -> BaeminDeliveryHistoryTable:
    parser = _HtmlTableParser()
    parser.feed(html)

    for table in parser.tables:
        parsed = _parse_baemin_table(table)
        if parsed is not None:
            return parsed

    raise MissingPerformanceDataError("배민 배달현황 테이블을 찾지 못했습니다")


def baemin_delivery_history_to_snapshot(table: BaeminDeliveryHistoryTable) -> CurrentScreenSnapshot:
    _require_headers(table.headers, BAEMIN_REQUIRED_METRIC_COLUMNS)
    if table.summary is None and not table.riders:
        raise MissingPerformanceDataError("배민 배달현황 데이터 행을 찾지 못했습니다")

    totals = table.summary or _sum_rider_rows(table.riders)
    online_riders = sum(1 for row in table.riders if _is_active_status(row.get("운행상태", "")))
    waiting_count = sum(1 for row in table.riders if _is_waiting_status(row.get("운행상태", "")))
    rider_count = len(table.riders)
    completed_count = _required_number(totals, "완료")
    rejected_count = _required_number(totals, "거절")
    cancelled_count = _required_number(totals, "배차취소") + _required_number(totals, "배달취소(라이더귀책)")
    delivery_event_count = completed_count + rejected_count + cancelled_count

    return CurrentScreenSnapshot(
        center_name="배민 배달현황",
        date_label="",
        shift_label="배달현황",
        shift_time_range="",
        shift_status="",
        updated_at=datetime.now().strftime("%H:%M"),
        available_current=online_riders,
        available_total=rider_count,
        waiting_count=waiting_count,
        online_riders=online_riders,
        rejected_ignored_count=rejected_count,
        cancelled_count=cancelled_count,
        completed_count=completed_count,
        sequence_violation_count=0,
        lunch_peak_count=_required_number(totals, "아침점심피크"),
        dinner_peak_count=_required_number(totals, "저녁피크"),
        non_peak_count=_required_number(totals, "오후논피크") + _required_number(totals, "심야논피크"),
        active_riders=online_riders,
        reject_rate=_rate(rejected_count, delivery_event_count),
        cancel_rate=_rate(cancelled_count, delivery_event_count),
    )


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


def _parse_baemin_table(table: list[list[str]]) -> BaeminDeliveryHistoryTable | None:
    for index, row in enumerate(table):
        header_map = _baemin_header_map(row)
        if not header_map:
            continue

        headers = row
        summary: dict[str, str] | None = None
        riders: list[dict[str, str]] = []
        for data_row in table[index + 1 :]:
            mapped = _map_row_by_headers(headers, data_row)
            name = mapped.get("이름", "").strip()
            if not name:
                continue
            if name == "합계":
                summary = mapped
            else:
                riders.append(mapped)
        return BaeminDeliveryHistoryTable(headers=headers, summary=summary, riders=riders)
    return None


def _baemin_header_map(row: list[str]) -> dict[str, int]:
    header_map = {header: index for index, header in enumerate(row)}
    required = {"이름", "운행상태", "완료"}
    if required.issubset(header_map):
        return header_map
    return {}


def _map_row_by_headers(headers: list[str], row: list[str]) -> dict[str, str]:
    return {
        header: row[index] if index < len(row) else ""
        for index, header in enumerate(headers)
    }


def _normalize_cell_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def _is_active_status(status: str) -> bool:
    compact = status.replace(" ", "")
    return any(label in compact for label in ("운행중", "배달중", "온라인"))


def _is_waiting_status(status: str) -> bool:
    return "대기" in status.replace(" ", "")


def _sum_rider_rows(rows: list[dict[str, str]]) -> dict[str, str]:
    totals: dict[str, str] = {}
    for column in BAEMIN_REQUIRED_METRIC_COLUMNS:
        totals[column] = str(sum(_required_number(row, column) for row in rows))
    return totals


def _require_headers(headers: list[str], required_columns: list[str]) -> None:
    missing = [column for column in required_columns if column not in headers]
    if missing:
        raise MissingPerformanceDataError(f"배민 배달현황 필수 열 누락: {', '.join(missing)}")


def _required_number(row: dict[str, str], column: str) -> float | int:
    raw = row.get(column, "")
    if not raw.strip():
        raise MissingPerformanceDataError(f"{column} 값이 비어 있습니다")
    try:
        return parse_count(raw)
    except ValueError as exc:
        raise MissingPerformanceDataError(f"{column} 값을 숫자로 읽지 못했습니다: {raw!r}") from exc


def _rate(numerator: float | int, denominator: float | int) -> float:
    if denominator == 0:
        return 0
    return round((numerator / denominator) * 100, 1)


def _required_number_after(label: str, text: str) -> str:
    compact = text.replace("\n", " ")
    match = re.search(rf"{re.escape(label)}\s*[: ]\s*(?P<value>-|\d+(?:,\d{{3}})*(?:\.\d+)?\s*(?:건|명|%)?)", compact)
    if not match:
        raise MissingPerformanceDataError(f"{label} value was not found")
    return match.group("value")
