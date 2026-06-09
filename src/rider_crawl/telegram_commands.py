from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config import AppConfig
from .lock import RunLock
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


@dataclass(frozen=True)
class _UpdateHandlingResult:
    update_id: int | None
    error: Exception | None = None


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
        self.config_by_target = _config_by_unique_target(configs)
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
            try:
                self.send_text(config, "조회 중 오류가 발생했습니다.", message_thread_id=message_thread_id)
            except Exception as send_exc:
                self.log_event(f"{source} 오류 답장 전송 오류: {send_exc}")
                raise
            return True

        try:
            self.send_text(config, _render_lookup_reply(command, matches), message_thread_id=message_thread_id)
        except Exception as exc:
            self.log_event(f"{source} 최종 답장 전송 오류: {exc}")
            raise
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
        offset_store_path: Path | None = None,
    ) -> None:
        self.config = config
        self.handle_text = handle_text
        self.get_updates = get_updates or _get_telegram_updates
        self.timeout_seconds = timeout_seconds
        self.offset_store_path = offset_store_path or _default_offset_store_path(config)
        self.next_update_id: int | None = _read_update_offset(self.offset_store_path)
        self.completed_store_path = _completed_updates_store_path(self.offset_store_path)
        self._completed_update_ids: set[int] = _read_completed_update_ids(self.completed_store_path)

    def poll_once(self) -> None:
        with RunLock(
            _offset_lock_store_path(self.offset_store_path),
            stale_timeout_seconds=max(60, self.timeout_seconds + 10),
        ):
            self._reload_offset_state()
            self._poll_once_unlocked()

    def _poll_once_unlocked(self) -> None:
        updates = self.get_updates(
            self.config,
            offset=self.next_update_id,
            timeout_seconds=self.timeout_seconds,
        )
        if not updates:
            return

        updates_to_handle = [
            update for update in updates if self._update_id(update) not in self._completed_update_ids
        ]
        if not updates_to_handle:
            self._advance_offset_after_completed_updates()
            return

        max_workers = min(len(updates_to_handle), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._handle_update, update) for update in updates_to_handle]
            results = [future.result() for future in futures]

        errors: list[_UpdateHandlingResult] = []
        completed_changed = False
        for result in results:
            if result.error is not None:
                errors.append(result)
            elif result.update_id is not None:
                self._completed_update_ids.add(result.update_id)
                completed_changed = True

        if completed_changed:
            _write_completed_update_ids(self.completed_store_path, self._completed_update_ids)

        if errors:
            failed_update_ids = [result.update_id for result in errors if result.update_id is not None]
            if failed_update_ids:
                self._advance_offset_before(min(failed_update_ids))
            raise errors[0].error

        self._advance_offset_after_completed_updates()

    def run_loop(self, *, stop_event) -> None:
        while not stop_event.is_set():
            self.poll_once()

    def _handle_update(self, update: dict) -> _UpdateHandlingResult:
        normalized_update_id = self._update_id(update)
        try:
            message = update.get("message")
            if isinstance(message, dict):
                text = message.get("text")
                chat_id = _message_chat_id(message)
                message_thread_id = _message_thread_id(message)
                if isinstance(text, str) and chat_id:
                    self.handle_text(chat_id, text, message_thread_id)
        except Exception as exc:
            return _UpdateHandlingResult(normalized_update_id, exc)
        return _UpdateHandlingResult(normalized_update_id)

    def _reload_offset_state(self) -> None:
        self.next_update_id = _read_update_offset(self.offset_store_path)
        self._completed_update_ids = _read_completed_update_ids(self.completed_store_path)

    def _advance_offset_before(self, blocked_update_id: int) -> None:
        completed_before_blocker = [
            update_id for update_id in self._completed_update_ids if update_id < blocked_update_id
        ]
        if not completed_before_blocker:
            return
        self._set_next_update_id(max(completed_before_blocker) + 1)

    def _advance_offset_after_completed_updates(self) -> None:
        if not self._completed_update_ids:
            return
        self._set_next_update_id(max(self._completed_update_ids) + 1)

    def _set_next_update_id(self, next_update_id: int) -> None:
        if self.next_update_id is not None and next_update_id <= self.next_update_id:
            return
        self.next_update_id = next_update_id
        _write_update_offset(self.offset_store_path, self.next_update_id)
        self._completed_update_ids = {
            update_id for update_id in self._completed_update_ids if update_id >= self.next_update_id
        }
        _write_completed_update_ids(self.completed_store_path, self._completed_update_ids)

    @staticmethod
    def _update_id(update: dict) -> int | None:
        update_id = update.get("update_id")
        return update_id if isinstance(update_id, int) else None


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
    value = str(message_thread_id or "").strip()
    if not value:
        return ""
    try:
        return str(int(value))
    except ValueError:
        return value


def _config_target(config: AppConfig) -> TelegramTarget:
    return (_normalize_chat_id(config.telegram_chat_id), _normalize_thread_id(config.telegram_message_thread_id))


def _config_by_unique_target(configs: list[AppConfig]) -> dict[TelegramTarget, AppConfig]:
    config_by_target: dict[TelegramTarget, AppConfig] = {}
    for config in configs:
        target = _config_target(config)
        if not target[0]:
            continue
        if target in config_by_target:
            raise ValueError(f"텔레그램 대상이 중복되었습니다: {_target_label(target)}")
        config_by_target[target] = config
    return config_by_target


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


def _default_offset_store_path(config: AppConfig) -> Path:
    token_hash = hashlib.sha256(config.telegram_bot_token.strip().encode("utf-8")).hexdigest()[:16]
    return Path("runtime") / "state" / "telegram_offsets" / f"{token_hash}.txt"


def _completed_updates_store_path(offset_store_path: Path) -> Path:
    return Path(f"{offset_store_path}.completed.json")


def _offset_lock_store_path(offset_store_path: Path) -> Path:
    return Path(f"{offset_store_path}.lock")


def _read_update_offset(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _write_update_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding="utf-8")


def _read_completed_update_ids(path: Path) -> set[int]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, list):
        return set()

    completed: set[int] = set()
    for value in raw:
        try:
            update_id = int(value)
        except (TypeError, ValueError):
            continue
        if update_id > 0:
            completed.add(update_id)
    return completed


def _write_completed_update_ids(path: Path, update_ids: set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(update_ids)), encoding="utf-8")


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
