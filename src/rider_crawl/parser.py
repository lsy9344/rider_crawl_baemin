from __future__ import annotations

import re
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import date, datetime

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


@dataclass(frozen=True)
class _AchievementPeriod:
    done: int
    goal: int
    rate: int


@dataclass(frozen=True)
class _AchievementRow:
    center_label: str
    date_label: str
    day_label: str
    lunch_peak: _AchievementPeriod
    afternoon_non_peak: _AchievementPeriod
    dinner_peak: _AchievementPeriod
    dinner_non_peak: _AchievementPeriod
    acceptance_rate: float

    @property
    def row_date(self) -> date:
        year, month, day = (int(part) for part in self.date_label.split("-"))
        return date(2000 + year, month, day)

    @property
    def has_delivery_count(self) -> bool:
        return any(
            period.done > 0
            for period in (
                self.lunch_peak,
                self.afternoon_non_peak,
                self.dinner_peak,
                self.dinner_non_peak,
            )
        )


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


def parse_achievement_report_text(
    text: str,
    *,
    center_id: str,
    center_name: str,
    now: datetime | None = None,
) -> CurrentScreenSnapshot:
    rows = _parse_achievement_rows(text, center_id=center_id)
    if not rows:
        raise MissingPerformanceDataError("배민 달성현황에서 설정 센터 행을 찾지 못했습니다")

    current_time = now or datetime.now()
    row = _select_achievement_row(rows, today=current_time.date())
    reject_rate = max(0, min(100, round(100 - row.acceptance_rate, 2)))

    return CurrentScreenSnapshot(
        center_name=center_name.strip() or row.center_label,
        date_label=row.date_label,
        shift_label="주간 배달 현황",
        shift_time_range="",
        shift_status="",
        updated_at=current_time.strftime("%H:%M"),
        available_current=0,
        available_total=0,
        waiting_count=0,
        online_riders=0,
        rejected_ignored_count=0,
        cancelled_count=0,
        completed_count=0,
        sequence_violation_count=0,
        lunch_peak_count=row.lunch_peak.done,
        lunch_peak_goal=row.lunch_peak.goal,
        lunch_peak_rate=row.lunch_peak.rate,
        afternoon_non_peak_count=row.afternoon_non_peak.done,
        afternoon_non_peak_goal=row.afternoon_non_peak.goal,
        afternoon_non_peak_rate=row.afternoon_non_peak.rate,
        dinner_peak_count=row.dinner_peak.done,
        dinner_peak_goal=row.dinner_peak.goal,
        dinner_peak_rate=row.dinner_peak.rate,
        dinner_non_peak_count=row.dinner_non_peak.done,
        dinner_non_peak_goal=row.dinner_non_peak.goal,
        dinner_non_peak_rate=row.dinner_non_peak.rate,
        non_peak_count=row.afternoon_non_peak.done + row.dinner_non_peak.done,
        active_riders=0,
        reject_rate=reject_rate,
    )


def parse_baemin_delivery_history_html(html: str) -> BaeminDeliveryHistoryTable:
    parser = _HtmlTableParser()
    parser.feed(html)

    parsed_tables: list[BaeminDeliveryHistoryTable] = []
    for table in parser.tables:
        parsed = _parse_baemin_table(table)
        if parsed is not None:
            parsed_tables.append(parsed)

    if parsed_tables:
        return _merge_baemin_tables(parsed_tables)

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
    afternoon_non_peak_count = _required_number(totals, "오후논피크")
    dinner_non_peak_count = _required_number(totals, "심야논피크")
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
        non_peak_count=afternoon_non_peak_count + dinner_non_peak_count,
        active_riders=online_riders,
        reject_rate=_rate(rejected_count + cancelled_count, delivery_event_count),
        cancel_rate=_rate(cancelled_count, delivery_event_count),
        afternoon_non_peak_count=afternoon_non_peak_count,
        dinner_non_peak_count=dinner_non_peak_count,
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

    non_peak_count = parse_count(_required_number_after("논피크", normalized))

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
        non_peak_count=non_peak_count,
        active_riders=len(re.findall(r"\b배달중\b", normalized)),
        afternoon_non_peak_count=non_peak_count,
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


def _parse_achievement_rows(text: str, *, center_id: str) -> list[_AchievementRow]:
    expected_id = center_id.strip().upper()
    if not expected_id:
        raise MissingPerformanceDataError("배민 달성현황을 읽으려면 센터 ID가 필요합니다")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[_AchievementRow] = []
    for index, line in enumerate(lines):
        if expected_id not in line.upper():
            continue
        candidate = lines[index : index + 8]
        if len(candidate) < 8 or not _looks_like_achievement_date(candidate[1]):
            continue
        try:
            rows.append(
                _AchievementRow(
                    center_label=candidate[0],
                    date_label=candidate[1],
                    day_label=candidate[2],
                    lunch_peak=_parse_achievement_period(candidate[3]),
                    afternoon_non_peak=_parse_achievement_period(candidate[4]),
                    dinner_peak=_parse_achievement_period(candidate[5]),
                    dinner_non_peak=_parse_achievement_period(candidate[6]),
                    acceptance_rate=float(parse_count(candidate[7])),
                )
            )
        except (MissingPerformanceDataError, ValueError):
            continue
    return rows


def _select_achievement_row(rows: list[_AchievementRow], *, today: date) -> _AchievementRow:
    # 오늘 행이 표에 있으면 **무조건 오늘 행**을 쓴다. 오늘 배달건수가 아직 0이어도
    # (예: 이른 새벽, 운행 전) 어제 완료 행으로 내려가지 않는다 — 메시지에 항상 당일
    # 날짜가 찍혀야 하기 때문이다. 오늘 행이 여러 개면 건수 있는 마지막 행을, 없으면
    # 마지막 오늘 행을 쓴다.
    today_rows = [row for row in rows if row.row_date == today]
    if today_rows:
        completed_today = [row for row in today_rows if row.has_delivery_count]
        if completed_today:
            return completed_today[-1]
        return today_rows[-1]

    # 오늘 행이 아예 없을 때만 과거로 폴백한다(완료된 가장 최근 날 → 그것도 없으면
    # 가장 최근 과거 날 → 최후엔 표의 최신 행).
    completed_rows = [row for row in rows if row.row_date <= today and row.has_delivery_count]
    if completed_rows:
        return max(completed_rows, key=lambda row: row.row_date)

    past_rows = [row for row in rows if row.row_date <= today]
    if past_rows:
        return max(past_rows, key=lambda row: row.row_date)

    return max(rows, key=lambda row: row.row_date)


def _parse_achievement_period(raw: str) -> _AchievementPeriod:
    match = re.search(
        r"(?P<done>\d+(?:,\d{3})*)\s*/\s*(?P<goal>\d+(?:,\d{3})*)\s*"
        r"\(\s*(?P<rate>\d+(?:\.\d+)?)\s*%\s*\)",
        raw,
    )
    if not match:
        raise MissingPerformanceDataError(f"배민 달성현황 구간 값을 읽지 못했습니다: {raw!r}")
    return _AchievementPeriod(
        done=int(match.group("done").replace(",", "")),
        goal=int(match.group("goal").replace(",", "")),
        rate=round(float(match.group("rate"))),
    )


def _looks_like_achievement_date(raw: str) -> bool:
    return bool(re.fullmatch(r"\d{2}-\d{2}-\d{2}", raw.strip()))


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


def _merge_baemin_tables(tables: list[BaeminDeliveryHistoryTable]) -> BaeminDeliveryHistoryTable:
    headers = tables[0].headers
    summary = _sum_baemin_summaries(headers, [table.summary for table in tables if table.summary])
    riders = [rider for table in tables for rider in table.riders]
    return BaeminDeliveryHistoryTable(headers=headers, summary=summary, riders=riders)


def _sum_baemin_summaries(headers: list[str], summaries: list[dict[str, str] | None]) -> dict[str, str] | None:
    if not summaries:
        return None

    totals: dict[str, str] = {}
    for header in headers:
        values = [summary.get(header, "") for summary in summaries if summary is not None]
        numeric_values = []
        for value in values:
            try:
                numeric_values.append(parse_count(value))
            except ValueError:
                numeric_values = []
                break
        if numeric_values:
            total = sum(numeric_values)
            totals[header] = str(int(total) if isinstance(total, float) and total.is_integer() else total)
        else:
            totals[header] = values[0] if values else ""
    return totals


def _baemin_header_map(row: list[str]) -> dict[str, int]:
    header_map = {header: index for index, header in enumerate(row)}
    has_status = "운행상태" in header_map or "수행상태" in header_map
    if {"이름", "완료"}.issubset(header_map) and has_status:
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
