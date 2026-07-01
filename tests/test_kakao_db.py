"""Tests for the KakaoTalk local DB reader (latest-one fallback).

These drive a plain in-memory SQLite database through the injectable connection
seam, so they exercise the real query + row parsing without SQLCipher and
without any secrets.
"""

import sqlite3

import pytest

from rider_crawl.kakao_db import (
    DEFAULT_ACCEPTED_CHAT_TYPES,
    LATEST_ONE_WINDOW_SIZE,
    LATEST_TWENTY_WINDOW_SIZE,
    ChatLogsReader,
    ChatRoomListReader,
    KakaoDbDependencyMissing,
    KakaoRoomRef,
    chat_type_accepted,
    sqlcipher_available,
)


def _seeded_connect(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE chatRoomList ("
        "chatId INTEGER, chatRoomTitle TEXT, lastChatMessage TEXT, "
        "lastLogId INTEGER, lastUpdatedAt INTEGER, type TEXT, directChatMemberId INTEGER)"
    )
    conn.executemany(
        "INSERT INTO chatRoomList "
        "(chatId, chatRoomTitle, lastChatMessage, lastLogId, lastUpdatedAt, type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return lambda: conn


def _seeded_chatlogs_connect(rows):
    def connect(_room):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE chatLogs ("
            "logId INTEGER PRIMARY KEY, authorId INTEGER, type INTEGER, "
            "clientMsgId INTEGER, sendAt INTEGER, message TEXT, deleted INTEGER)"
        )
        conn.executemany(
            "INSERT INTO chatLogs "
            "(logId, authorId, type, clientMsgId, sendAt, message, deleted) "
            "VALUES (?, 1, ?, 1, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return conn

    return connect


def test_list_rooms_returns_room_refs():
    rows = [
        (111, "운영방", "!!강민기1234", 1001, 50, "MultiChat"),
        (222, "  ", "안녕하세요", 1002, 60, "DirectChat"),
    ]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))

    rooms = reader.list_rooms()

    assert len(rooms) == 2
    assert rooms[0] == KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")
    # whitespace-only title becomes empty room_name
    assert rooms[1].room_name == ""


def test_latest_messages_returns_candidate_message_for_room():
    rows = [(111, "운영방", "확인 !!강민기1234", 1001, 50, "MultiChat")]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=20)

    assert len(messages) == 1
    msg = messages[0]
    assert msg.chat_id == "111"
    assert msg.room_name == "운영방"
    assert msg.log_id == "1001"
    assert msg.timestamp == 50
    assert msg.text == "확인 !!강민기1234"


def test_latest_messages_filters_out_non_candidate_rows():
    rows = [(111, "운영방", "그냥 메시지", 1001, 50, "MultiChat")]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    assert reader.latest_messages(room, limit=20) == []


def test_latest_messages_caps_to_window_size_one():
    # Even with a high limit, the fallback exposes only one latest message.
    rows = [(111, "운영방", "!!강민기1234", 1001, 50, "MultiChat")]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    assert reader.latest_window_size == LATEST_ONE_WINDOW_SIZE
    assert len(reader.latest_messages(room, limit=20)) == 1
    assert reader.latest_messages(room, limit=0) == []


def test_latest_messages_matches_integer_chat_id_against_text_param():
    # chatId is stored as an integer; the reader compares it as text.
    rows = [(35189107907951, "운영방", "!!강민기1234", 1001, 50, "MultiChat")]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))
    room = KakaoRoomRef(chat_id="35189107907951", room_name="운영방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=1)

    assert len(messages) == 1
    assert messages[0].chat_id == "35189107907951"


def test_latest_messages_only_returns_requested_room():
    rows = [
        (111, "운영방", "!!강민기1234", 1001, 50, "MultiChat"),
        (222, "다른방", "!!이순신5678", 1002, 60, "MultiChat"),
    ]
    reader = ChatRoomListReader(connect=_seeded_connect(rows))
    room = KakaoRoomRef(chat_id="222", room_name="다른방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=1)

    assert [m.chat_id for m in messages] == ["222"]


def test_chat_logs_reader_returns_latest_candidates_from_room_log_oldest_first():
    room_list = ChatRoomListReader(
        connect=_seeded_connect(
            [(111, "운영방", "latest", 1009, 90, "MultiChat")]
        )
    )
    rows = [
        (1001, 1, 10, "!!강민기1234", 0),
        (1002, 1, 20, "일반 메시지", 0),
        (1003, 1, 30, "확인 !!이순신5678", 0),
        (1004, 1, 40, "!!삭제됨1234", 1),
    ]
    reader = ChatLogsReader(
        rooms_reader=room_list,
        chat_logs_connect=_seeded_chatlogs_connect(rows),
    )
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=20)

    assert reader.latest_window_size == LATEST_TWENTY_WINDOW_SIZE
    assert [m.log_id for m in messages] == ["1001", "1003"]
    assert [m.chat_id for m in messages] == ["111", "111"]
    assert [m.room_name for m in messages] == ["운영방", "운영방"]
    assert messages[0].timestamp == 10
    assert messages[1].text == "확인 !!이순신5678"


def test_chat_logs_reader_caps_to_latest_twenty_candidates():
    room_list = ChatRoomListReader(
        connect=_seeded_connect(
            [(111, "운영방", "latest", 2000, 90, "MultiChat")]
        )
    )
    rows = [(log_id, 1, log_id, f"!!강민기{log_id % 10000:04d}", 0) for log_id in range(1, 26)]
    reader = ChatLogsReader(
        rooms_reader=room_list,
        chat_logs_connect=_seeded_chatlogs_connect(rows),
    )
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=25)

    assert len(messages) == 20
    assert [m.log_id for m in messages[:2]] == ["6", "7"]
    assert messages[-1].log_id == "25"


def test_chat_logs_reader_falls_back_to_chat_room_list_and_degrades():
    room_list = ChatRoomListReader(
        connect=_seeded_connect(
            [(111, "운영방", "!!강민기1234", 1001, 50, "MultiChat")]
        )
    )

    def broken_connect(_room):
        raise RuntimeError("schema mismatch")

    reader = ChatLogsReader(
        rooms_reader=room_list,
        chat_logs_connect=broken_connect,
    )
    room = KakaoRoomRef(chat_id="111", room_name="운영방", chat_type="MultiChat")

    messages = reader.latest_messages(room, limit=20)

    assert reader.latest_window_size == LATEST_ONE_WINDOW_SIZE
    assert len(messages) == 1
    assert messages[0].log_id == "1001"


def test_chat_type_accepted_uses_substring_match():
    assert chat_type_accepted("MultiChat", DEFAULT_ACCEPTED_CHAT_TYPES) is True
    assert chat_type_accepted("DirectChat", DEFAULT_ACCEPTED_CHAT_TYPES) is True
    assert chat_type_accepted("ChatType.MultiChat", DEFAULT_ACCEPTED_CHAT_TYPES) is True
    assert chat_type_accepted("PlusChat", DEFAULT_ACCEPTED_CHAT_TYPES) is False
    assert chat_type_accepted("OM", DEFAULT_ACCEPTED_CHAT_TYPES) is False


def test_close_is_idempotent():
    reader = ChatRoomListReader(connect=_seeded_connect([]))
    reader.list_rooms()
    reader.close()
    reader.close()  # no error


def test_default_open_requires_dependency_when_sqlcipher_missing():
    if sqlcipher_available():
        pytest.skip("sqlcipher3 is installed; dependency-missing path not exercised")

    reader = ChatRoomListReader(db_path="C:/does/not/matter.edb", db_key="deadbeef")

    with pytest.raises(KakaoDbDependencyMissing):
        reader.list_rooms()


def test_sqlcipher_available_returns_bool():
    assert isinstance(sqlcipher_available(), bool)
