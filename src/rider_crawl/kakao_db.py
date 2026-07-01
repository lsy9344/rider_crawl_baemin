"""KakaoTalk local DB reader (production interface).

This is a *tested production* reimplementation of the DB-reading idea proven by
the research package under ``docs/kakao_db`` — it does **not** import or run any
research script, and it hard-codes no DB key, user hash, room id, or message.

Phase 2 ships only the **latest-one fallback** (:class:`ChatRoomListReader`),
which reads ``chatRoomList`` from a copied ``chatListInfo.edb``. That source
exposes one latest visible message per room, so it can miss messages and must be
reported as *degraded* health (``latest_window_size == 1``). A latest-N reader
over ``chatLogs_<id>.edb`` is a later phase.

Security:

- Optional SQLCipher support is imported lazily inside methods, so importing this
  module (and the Agent reuse seam) never requires ``sqlcipher3``. A missing
  dependency raises :class:`KakaoDbDependencyMissing`, which the Agent watcher
  turns into a disabled health state instead of crashing.
- The DB key is held in memory only and is never logged or put in exceptions.
- The connection seam is injectable so tests drive a plain in-memory SQLite DB
  (no SQLCipher, no secrets) and still exercise the query + row parsing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

# Default KakaoTalk chat types the watcher accepts. PlusChat/OM/unknown are
# ignored unless a later design explicitly enables them per room.
CHAT_TYPE_DIRECT = "DirectChat"
CHAT_TYPE_MULTI = "MultiChat"
DEFAULT_ACCEPTED_CHAT_TYPES: tuple[str, ...] = (CHAT_TYPE_DIRECT, CHAT_TYPE_MULTI)

# Cheap candidate prefilter for the SQL scan. A row is only *actionable* if the
# shared command parser later finds a valid token; this just narrows the scan.
CANDIDATE_LIKE = "%!!%"

# The fallback reader sees only the single latest message per room.
LATEST_ONE_WINDOW_SIZE = 1
LATEST_TWENTY_WINDOW_SIZE = 20


class KakaoDbError(RuntimeError):
    """Kakao DB read failure. Never carries the DB key or message text."""


class KakaoDbDependencyMissing(KakaoDbError):
    """Optional SQLCipher support (``sqlcipher3``) is not installed."""


@dataclass(frozen=True)
class KakaoRoomRef:
    chat_id: str
    room_name: str
    chat_type: str


@dataclass(frozen=True)
class KakaoMessageRef:
    chat_id: str
    room_name: str
    log_id: str
    timestamp: int | None
    text: str


class KakaoDbReader(Protocol):
    """Production reader interface consumed by the Agent watcher."""

    latest_window_size: int

    def list_rooms(self) -> list[KakaoRoomRef]: ...

    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]: ...


# A connection factory returns an open DB-API connection. The default factory
# copies the locked DB and opens it with SQLCipher; tests inject a factory that
# returns a seeded in-memory SQLite connection.
ConnectFactory = Callable[[], Any]
CopyLockedDb = Callable[[Path], Path]
ChatLogsConnectFactory = Callable[[KakaoRoomRef], Any]
ChatLogsKeyResolver = Callable[[KakaoRoomRef], str | None]


def sqlcipher_available() -> bool:
    """Return whether the optional ``sqlcipher3`` dependency can be imported."""

    try:
        import sqlcipher3  # noqa: F401
    except Exception:
        return False
    return True


def _copy_locked_db(db_path: Path) -> Path:
    """Copy the (possibly locked) DB to a temp file, mirroring the research flow.

    KakaoTalk holds the live DB open, so we read a copy rather than the original.
    """

    db_path = Path(db_path)
    fd, tmp_name = tempfile.mkstemp(suffix=".edb", prefix="kakao_db_")
    os.close(fd)
    tmp_path = Path(tmp_name)
    shutil.copy2(db_path, tmp_path)
    return tmp_path


class ChatRoomListReader:
    """Latest-one fallback reader over ``chatRoomList`` (degraded coverage).

    Each ``chatRoomList`` row carries one room's latest message, so this reader
    cannot recover missed messages — callers must surface
    ``latest_window_size == 1`` as degraded health.
    """

    latest_window_size = LATEST_ONE_WINDOW_SIZE

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        db_key: str | None = None,
        connect: ConnectFactory | None = None,
        copy_locked_db: CopyLockedDb | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        # secret — held only in memory, never logged or put in exceptions.
        self._db_key = db_key
        self._connect_factory = connect
        self._copy_locked_db = copy_locked_db or _copy_locked_db
        self._conn: Any | None = None
        self._temp_copy: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def _connection(self) -> Any:
        if self._conn is None:
            self._conn = self._open()
        return self._conn

    def _open(self) -> Any:
        if self._connect_factory is not None:
            return self._connect_factory()
        if self._db_path is None or not self._db_key:
            raise KakaoDbError("kakao db path/key is not configured")
        try:
            import sqlcipher3
        except Exception as exc:  # noqa: BLE001 — optional dep → degraded, not crash
            raise KakaoDbDependencyMissing("sqlcipher3 is not installed") from exc

        copy_path = self._copy_locked_db(self._db_path)
        self._temp_copy = copy_path
        conn = sqlcipher3.connect(str(copy_path))
        # Validated order from the research: set cipher compatibility, then the
        # raw hex key. The key is embedded in the PRAGMA text and cannot be a
        # bound parameter, so this statement must NEVER be logged.
        conn.execute("PRAGMA cipher_compatibility = 4")
        conn.execute("PRAGMA key = \"x'" + self._db_key + "'\"")
        return conn

    def close(self) -> None:
        """Close the connection and remove any temp DB copy (best-effort)."""

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
            self._conn = None
        if self._temp_copy is not None:
            try:
                self._temp_copy.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            self._temp_copy = None

    def __enter__(self) -> "ChatRoomListReader":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- reads -------------------------------------------------------------

    def list_rooms(self) -> list[KakaoRoomRef]:
        cursor = self._connection().execute(
            "SELECT chatId, chatRoomTitle, type FROM chatRoomList"
        )
        return [_room_ref(row) for row in cursor.fetchall()]

    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]:
        if limit <= 0:
            return []
        cursor = self._connection().execute(
            "SELECT chatId, chatRoomTitle, lastChatMessage, lastLogId, lastUpdatedAt, type "
            "FROM chatRoomList "
            "WHERE CAST(chatId AS TEXT) = ? "
            "  AND lastChatMessage IS NOT NULL "
            "  AND lastChatMessage LIKE ? "
            "ORDER BY lastUpdatedAt DESC "
            "LIMIT 1",
            (str(room.chat_id), CANDIDATE_LIKE),
        )
        rows = cursor.fetchall()
        capped = min(limit, self.latest_window_size)
        return [_message_ref(row) for row in rows[:capped]]


class ChatLogsReader:
    """Latest-N reader over per-room ``chatLogs_<chat_id>.edb`` files.

    Room discovery remains delegated to ``chatListInfo.edb`` because each log DB
    is per-room and does not carry a room title. Message reads use the confirmed
    PC KakaoTalk schema:

    ``chatLogs(logId, sendAt, message, type, deleted, ...)``.

    If a room log DB is unavailable, has an unexpected schema, or cannot be
    opened with its configured key, this reader falls back to the room-list
    latest-one reader and reports ``latest_window_size == 1`` for degraded
    health. Keys are supplied by config/secure-store seams and are never logged.
    """

    def __init__(
        self,
        *,
        rooms_reader: KakaoDbReader | None = None,
        chat_list_db_path: Path | str | None = None,
        chat_list_db_key: str | None = None,
        chat_logs_dir: Path | str | None = None,
        chat_logs_db_key: str | None = None,
        chat_logs_db_keys_by_chat_id: Mapping[str, str] | None = None,
        chat_logs_key_resolver: ChatLogsKeyResolver | None = None,
        chat_logs_connect: ChatLogsConnectFactory | None = None,
        copy_locked_db: CopyLockedDb | None = None,
    ) -> None:
        if rooms_reader is None:
            rooms_reader = ChatRoomListReader(
                db_path=chat_list_db_path,
                db_key=chat_list_db_key,
                copy_locked_db=copy_locked_db,
            )
        self._rooms_reader = rooms_reader
        self._fallback_reader = rooms_reader
        self._chat_logs_dir = (
            Path(chat_logs_dir)
            if chat_logs_dir is not None
            else (
                Path(chat_list_db_path).parent
                if chat_list_db_path is not None
                else None
            )
        )
        self._chat_logs_db_key = chat_logs_db_key
        self._chat_logs_db_keys_by_chat_id = {
            str(key): value
            for key, value in (chat_logs_db_keys_by_chat_id or {}).items()
        }
        self._chat_logs_key_resolver = chat_logs_key_resolver
        self._chat_logs_connect = chat_logs_connect
        self._copy_locked_db = copy_locked_db or _copy_locked_db
        self._degraded = False
        self._temp_copies: list[Path] = []

    @property
    def latest_window_size(self) -> int:
        if self._degraded:
            return LATEST_ONE_WINDOW_SIZE
        return LATEST_TWENTY_WINDOW_SIZE

    def list_rooms(self) -> list[KakaoRoomRef]:
        return self._rooms_reader.list_rooms()

    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]:
        if limit <= 0:
            return []
        try:
            return self._latest_chatlog_messages(room, min(limit, LATEST_TWENTY_WINDOW_SIZE))
        except Exception:  # noqa: BLE001 - one room log failure degrades to fallback.
            self._degraded = True
            return self._fallback_reader.latest_messages(room, limit)

    def close(self) -> None:
        close = getattr(self._rooms_reader, "close", None)
        if callable(close):
            close()
        for path in self._temp_copies:
            try:
                path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self._temp_copies = []

    def __enter__(self) -> "ChatLogsReader":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _latest_chatlog_messages(
        self, room: KakaoRoomRef, limit: int
    ) -> list[KakaoMessageRef]:
        conn = self._open_chatlogs(room)
        try:
            cursor = conn.execute(
                "SELECT logId, sendAt, message, type "
                "FROM chatLogs "
                "WHERE message IS NOT NULL "
                "  AND message LIKE ? "
                "  AND COALESCE(deleted, 0) = 0 "
                "ORDER BY logId DESC "
                "LIMIT ?",
                (CANDIDATE_LIKE, int(limit)),
            )
            rows = list(reversed(cursor.fetchall()))
            return [_chatlog_message_ref(room, row) for row in rows]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _open_chatlogs(self, room: KakaoRoomRef) -> Any:
        if self._chat_logs_connect is not None:
            return self._chat_logs_connect(room)
        if self._chat_logs_dir is None:
            raise KakaoDbError("kakao chatlogs dir is not configured")
        if not room.chat_id:
            raise KakaoDbError("kakao chat_id is not configured")
        key = self._key_for_room(room)
        if not key:
            raise KakaoDbError("kakao chatlogs key is not configured")
        db_path = self._chat_logs_dir / f"chatLogs_{room.chat_id}.edb"
        if not db_path.exists():
            raise KakaoDbError("kakao chatlogs db is not available")
        try:
            import sqlcipher3
        except Exception as exc:  # noqa: BLE001
            raise KakaoDbDependencyMissing("sqlcipher3 is not installed") from exc

        copy_path = self._copy_locked_db(db_path)
        self._temp_copies.append(copy_path)
        conn = sqlcipher3.connect(str(copy_path))
        conn.execute("PRAGMA cipher_compatibility = 4")
        conn.execute("PRAGMA key = \"x'" + key + "'\"")
        return conn

    def _key_for_room(self, room: KakaoRoomRef) -> str | None:
        if self._chat_logs_key_resolver is not None:
            value = self._chat_logs_key_resolver(room)
            if value:
                return value
        if room.chat_id in self._chat_logs_db_keys_by_chat_id:
            return self._chat_logs_db_keys_by_chat_id[room.chat_id]
        return self._chat_logs_db_key


def _room_ref(row: Any) -> KakaoRoomRef:
    chat_id, title, chat_type = row[0], row[1], row[2]
    return KakaoRoomRef(
        chat_id=str(chat_id) if chat_id is not None else "",
        room_name=(title or "").strip(),
        chat_type=str(chat_type or ""),
    )


def _message_ref(row: Any) -> KakaoMessageRef:
    chat_id, title, message, log_id, updated_at, _chat_type = (
        row[0], row[1], row[2], row[3], row[4], row[5],
    )
    return KakaoMessageRef(
        chat_id=str(chat_id) if chat_id is not None else "",
        room_name=(title or "").strip(),
        log_id=str(log_id) if log_id is not None else "",
        timestamp=_as_int(updated_at),
        text=message or "",
    )


def _chatlog_message_ref(room: KakaoRoomRef, row: Any) -> KakaoMessageRef:
    log_id, send_at, message, _chat_type = row[0], row[1], row[2], row[3]
    return KakaoMessageRef(
        chat_id=room.chat_id,
        room_name=room.room_name,
        log_id=str(log_id) if log_id is not None else "",
        timestamp=_as_int(send_at),
        text=message or "",
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chat_type_accepted(chat_type: str, accepted: tuple[str, ...]) -> bool:
    """Whether ``chat_type`` matches an accepted type (substring, as in research).

    Kakao stores the type as a string that *contains* tokens like ``DirectChat``
    or ``MultiChat``; match by substring so wrapped values still classify.
    """

    value = str(chat_type or "")
    return any(token and token in value for token in accepted)
