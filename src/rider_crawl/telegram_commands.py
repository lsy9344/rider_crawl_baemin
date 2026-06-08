from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Iterable

from .config import AppConfig
from .parser import parse_baemin_delivery_history_html, parse_count


RISK_CANCEL_RATE_PERCENT = 4.0


@dataclass(frozen=True)
class RiderLookupCommand:
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
    source_label: str
    stats: RiderCancelStats


FetchHtml = Callable[[AppConfig], str]
SendText = Callable[..., None]
HandleText = Callable[[str, str, int | None], object]
GetUpdates = Callable[..., list[dict]]
LogEvent = Callable[[str], None]
TelegramTarget = tuple[str, str]


def parse_rider_lookup_command(text: str) -> RiderLookupCommand | None:
    match = re.fullmatch(r"\s*!\s*(?P<name>.+?)(?P<phone_last4>\d{4})\s*", text)
    if not match:
        return None

    name = match.group("name").strip()
    phone_last4 = match.group("phone_last4")
    if not name:
        return None
    return RiderLookupCommand(name=name, phone_last4=phone_last4)


def find_rider_cancel_stats(
    rows: Iterable[dict[str, str]],
    *,
    name: str,
    phone_last4: str,
) -> list[RiderCancelStats]:
    matches = []
    for row in rows:
        row_name = _cell(row, "이름")
        phone = _cell(row, "휴대폰번호", "휴대폰 번호")
        if row_name.strip() != name or _phone_last4(phone) != phone_last4:
            continue

        completed = _number(row, "완료")
        rejected = _number(row, "거절")
        dispatch_cancelled = _number(row, "배차취소")
        rider_fault_cancelled = _number(row, "배달취소(라이더귀책)")
        total_cancelled = dispatch_cancelled + rider_fault_cancelled
        matches.append(
            RiderCancelStats(
                name=row_name.strip(),
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


def calculate_cancel_rate(
    *,
    completed: float | int,
    rejected: float | int,
    total_cancelled: float | int,
) -> float:
    denominator = completed + rejected + total_cancelled
    if denominator == 0:
        return 0
    return round((total_cancelled / denominator) * 100, 1)


def render_rider_cancel_reply(stats: RiderCancelStats) -> str:
    risk_text = "위험합니다." if stats.cancel_rate >= RISK_CANCEL_RATE_PERCENT else "정상 범위입니다."
    return (
        f"{stats.name}{stats.phone_last4}\n"
        f"취소율 {_format_number(stats.cancel_rate)}%, 취소 {_format_number(stats.total_cancel_count)}개\n"
        f"{risk_text}"
    )


class TelegramCommandProcessor:
    def __init__(
        self,
        configs: list[AppConfig],
        *,
        bot_config: AppConfig | None = None,
        fetch_html: FetchHtml | None = None,
        send_text: SendText | None = None,
        lock: threading.Lock | None = None,
        locks_by_chat_id: dict[str, threading.Lock] | None = None,
        locks_by_target: dict[TelegramTarget, threading.Lock] | None = None,
        log_event: LogEvent | None = None,
    ) -> None:
        self.configs = configs
        self.bot_config = bot_config
        self.fetch_html = fetch_html or _fetch_page_html
        self.send_text = send_text or _send_telegram_text
        self.log_event = log_event or (lambda _message: None)
        self.config_by_target = {
            _config_target(config): config
            for config in configs
            if _config_target(config)[0]
        }
        if locks_by_target is not None:
            self.locks_by_target = {
                (_normalize_chat_id(chat_id), _normalize_thread_id(thread_id)): lock
                for (chat_id, thread_id), lock in locks_by_target.items()
                if _normalize_chat_id(chat_id)
            }
        elif locks_by_chat_id is not None:
            self.locks_by_target = {
                (_normalize_chat_id(chat_id), ""): lock
                for chat_id, lock in locks_by_chat_id.items()
                if _normalize_chat_id(chat_id)
            }
        elif lock is not None:
            self.locks_by_target = {target: lock for target in self.config_by_target}
        else:
            self.locks_by_target = {target: threading.Lock() for target in self.config_by_target}
        for target in self.config_by_target:
            self.locks_by_target.setdefault(target, threading.Lock())

    def handle_text(self, chat_id: str, text: str, message_thread_id: int | None = None) -> bool:
        command = parse_rider_lookup_command(text)
        if command is None:
            return False

        normalized_chat_id = _normalize_chat_id(chat_id)
        target = (normalized_chat_id, _normalize_thread_id(message_thread_id))
        config = self.config_by_target.get(target)
        if config is None:
            self.log_event(f"텔레그램 대상 미매칭: {_target_label(target)}")
            return False

        source = _source_label(config, self.configs.index(config))
        self.log_event(f"{source} 텔레그램 명령 수신: !{command.name}{command.phone_last4}")
        self.send_text(config, "조회 중입니다.", message_thread_id=message_thread_id)
        try:
            with self.locks_by_target[target]:
                self.log_event(f"{source} 명령 조회 시작")
                matches = self._lookup(config, command)
                self.log_event(f"{source} 명령 조회 완료")
        except Exception as exc:
            self.log_event(f"{source} 명령 조회 오류: {exc}")
            self.send_text(config, "조회 중 오류가 발생했습니다.", message_thread_id=message_thread_id)
            return True

        self.send_text(config, _render_lookup_reply(command, matches), message_thread_id=message_thread_id)
        return True

    def _lookup(self, config: AppConfig, command: RiderLookupCommand) -> list[RiderCancelMatch]:
        matches: list[RiderCancelMatch] = []
        if not config.coupang_eats_url.strip():
            return matches
        html = self.fetch_html(config)
        table = parse_baemin_delivery_history_html(html)
        index = self.configs.index(config)
        for stats in find_rider_cancel_stats(
            table.riders,
            name=command.name,
            phone_last4=command.phone_last4,
        ):
            matches.append(RiderCancelMatch(source_label=_source_label(config, index), stats=stats))
        return matches


class TelegramUpdatePoller:
    def __init__(
        self,
        config: AppConfig,
        *,
        handle_text: HandleText,
        get_updates: GetUpdates | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.config = config
        self.handle_text = handle_text
        self.get_updates = get_updates or _get_telegram_updates
        self.timeout_seconds = timeout_seconds
        self.next_update_id: int | None = None

    def poll_once(self) -> None:
        updates = self.get_updates(
            self.config,
            offset=self.next_update_id,
            timeout_seconds=self.timeout_seconds,
        )
        for update in updates:
            update_id = update.get("update_id")
            message = update.get("message")
            if isinstance(message, dict):
                text = message.get("text")
                chat_id = _message_chat_id(message)
                message_thread_id = _message_thread_id(message)
                if isinstance(text, str) and chat_id:
                    self.handle_text(chat_id, text, message_thread_id)
            if isinstance(update_id, int):
                self.next_update_id = max(self.next_update_id or 0, update_id + 1)

    def run_loop(self, *, stop_event) -> None:
        while not stop_event.is_set():
            self.poll_once()


def _render_lookup_reply(command: RiderLookupCommand, matches: list[RiderCancelMatch]) -> str:
    if not matches:
        return f"{command.name}{command.phone_last4}\n해당 라이더를 찾지 못했습니다."

    reply = render_rider_cancel_reply(matches[0].stats)
    if len(matches) > 1:
        labels = ", ".join(match.source_label for match in matches)
        reply = f"{reply}\n중복 발견: {labels}"
    return reply


def _cell(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row:
            return row[name]
    for name in names:
        compact_name = _compact(name)
        for key, value in row.items():
            if _compact(key) == compact_name:
                return value
    return ""


def _number(row: dict[str, str], *names: str) -> float | int:
    return parse_count(_cell(row, *names) or "0")


def _phone_last4(phone: str) -> str:
    digits = "".join(re.findall(r"\d", phone))
    return digits[-4:] if len(digits) >= 4 else ""


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _format_number(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _source_label(config: AppConfig, index: int) -> str:
    return config.crawl_name.strip() or config.baemin_center_name.strip() or f"크롤링{index + 1}"


def _normalize_chat_id(chat_id: object) -> str:
    return str(chat_id or "").strip()


def _normalize_thread_id(message_thread_id: object) -> str:
    return str(message_thread_id or "").strip()


def _config_target(config: AppConfig) -> TelegramTarget:
    return (_normalize_chat_id(config.telegram_chat_id), _normalize_thread_id(config.telegram_message_thread_id))


def _target_label(target: TelegramTarget) -> str:
    chat_id, thread_id = target
    if not chat_id:
        return "<empty>"
    if thread_id:
        return f"{chat_id}/{thread_id}"
    return chat_id


def _fetch_page_html(config: AppConfig) -> str:
    from .crawler import fetch_page_html

    return fetch_page_html(config)


def _send_telegram_text(config: AppConfig, message: str, *, message_thread_id: int | None = None) -> None:
    from .sender import send_telegram_text

    send_telegram_text(config, message, message_thread_id=message_thread_id)


def _get_telegram_updates(config: AppConfig, *, offset: int | None, timeout_seconds: int) -> list[dict]:
    from .sender import get_telegram_updates

    return get_telegram_updates(config, offset=offset, timeout_seconds=timeout_seconds)


def _message_matches_chat(message: dict, chat_id: str) -> bool:
    return _message_chat_id(message) == chat_id.strip()


def _message_chat_id(message: dict) -> str:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return ""
    return str(chat.get("id", "")).strip()


def _message_thread_id(message: dict) -> int | None:
    value = message.get("message_thread_id")
    return value if isinstance(value, int) else None
