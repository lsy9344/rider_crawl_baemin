import json
from pathlib import Path

import pytest

from rider_crawl.keyword_responder import (
    DEFAULT_AUTO_MESSAGE,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_KEYWORDS,
    KeywordResponder,
    load_keyword_config,
    match_keyword,
)


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# load_keyword_config
# --------------------------------------------------------------------------- #
def test_load_keyword_config_reads_file(tmp_path):
    path = _write_config(
        tmp_path,
        {"keywords": ["a", "b"], "auto_message": "hi", "cooldown_seconds": 5},
    )
    config = load_keyword_config(path)
    assert config.keywords == ["a", "b"]
    assert config.auto_message == "hi"
    assert config.cooldown_seconds == 5


def test_load_keyword_config_missing_file_uses_defaults(tmp_path):
    config = load_keyword_config(tmp_path / "nope.json")
    assert config.keywords == DEFAULT_KEYWORDS
    assert config.auto_message == DEFAULT_AUTO_MESSAGE
    assert config.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS


def test_load_keyword_config_invalid_json_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not valid json", encoding="utf-8")
    config = load_keyword_config(path)
    assert config.keywords == DEFAULT_KEYWORDS
    assert config.auto_message == DEFAULT_AUTO_MESSAGE


def test_load_keyword_config_fills_missing_keys(tmp_path):
    path = _write_config(tmp_path, {"keywords": ["x"]})
    config = load_keyword_config(path)
    assert config.keywords == ["x"]
    assert config.auto_message == DEFAULT_AUTO_MESSAGE
    assert config.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS


def test_load_keyword_config_bad_types_fall_back(tmp_path):
    path = _write_config(
        tmp_path,
        {"keywords": "사고", "auto_message": 123, "cooldown_seconds": "x"},
    )
    config = load_keyword_config(path)
    assert config.keywords == DEFAULT_KEYWORDS
    assert config.auto_message == DEFAULT_AUTO_MESSAGE
    assert config.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS


def test_load_keyword_config_negative_cooldown_falls_back(tmp_path):
    # 운영자가 직접 수정하는 파일이라 음수가 들어올 수 있다. 음수는 기본값으로 되돌린다.
    path = _write_config(
        tmp_path,
        {"keywords": ["사고"], "auto_message": "A", "cooldown_seconds": -5},
    )
    config = load_keyword_config(path)
    assert config.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS


def test_load_keyword_config_zero_cooldown_is_allowed(tmp_path):
    path = _write_config(
        tmp_path,
        {"keywords": ["사고"], "auto_message": "A", "cooldown_seconds": 0},
    )
    config = load_keyword_config(path)
    assert config.cooldown_seconds == 0


# --------------------------------------------------------------------------- #
# match_keyword (포함 검색)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("사고가 났습니다", True),
        ("병원 어디로 가야 하나요", True),
        ("교통사고 접수 문의", True),  # 포함 검색
        ("가까운 병원 알려주세요", True),
        ("안녕하세요", False),
        ("", False),
    ],
)
def test_match_keyword(text, expected):
    assert match_keyword(text, ["사고", "병원"]) is expected


# --------------------------------------------------------------------------- #
# KeywordResponder (대상별 쿨다운)
# --------------------------------------------------------------------------- #
def test_responder_replies_on_keyword(tmp_path):
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "AUTO", "cooldown_seconds": 30}
    )
    responder = KeywordResponder(config_path=path)
    assert responder.reply_for(("-100", ""), "사고 났어요", now=100.0) == "AUTO"


def test_responder_ignores_non_keyword(tmp_path):
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "AUTO", "cooldown_seconds": 30}
    )
    responder = KeywordResponder(config_path=path)
    assert responder.reply_for(("-100", ""), "안녕하세요", now=100.0) is None


def test_responder_applies_cooldown_after_mark_sent(tmp_path):
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "AUTO", "cooldown_seconds": 30}
    )
    responder = KeywordResponder(config_path=path)
    target = ("-100", "")
    # 첫 번째 → 응답, 전송 성공 후 쿨다운 기록
    assert responder.reply_for(target, "사고", now=100.0) == "AUTO"
    responder.mark_sent(target, now=100.0)
    # 30초 이내 → 응답 안 함
    assert responder.reply_for(target, "사고", now=120.0) is None
    # 30초 이후 → 다시 응답
    assert responder.reply_for(target, "사고", now=131.0) == "AUTO"


def test_responder_does_not_apply_cooldown_without_mark_sent(tmp_path):
    # P1: reply_for만으로는 쿨다운이 걸리지 않는다(전송 실패 시 다음 메시지 응답 가능).
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "AUTO", "cooldown_seconds": 30}
    )
    responder = KeywordResponder(config_path=path)
    target = ("-100", "")
    assert responder.reply_for(target, "사고", now=100.0) == "AUTO"
    # mark_sent를 부르지 않았으므로(전송 실패 가정) 곧바로 다시 응답해야 한다.
    assert responder.reply_for(target, "사고", now=101.0) == "AUTO"


def test_responder_cooldown_is_independent_per_target(tmp_path):
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "AUTO", "cooldown_seconds": 30}
    )
    responder = KeywordResponder(config_path=path)
    # 한 대상에 쿨다운을 기록해도 다른 대상(채팅방/토픽)은 영향을 받지 않는다.
    assert responder.reply_for(("-100", ""), "사고", now=100.0) == "AUTO"
    responder.mark_sent(("-100", ""), now=100.0)
    assert responder.reply_for(("-200", ""), "사고", now=100.0) == "AUTO"
    assert responder.reply_for(("-100", "5"), "사고", now=100.0) == "AUTO"


def test_responder_reflects_config_edits_without_restart(tmp_path):
    path = _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "OLD", "cooldown_seconds": 0}
    )
    responder = KeywordResponder(config_path=path)
    assert responder.reply_for(("-100", ""), "사고", now=1.0) == "OLD"
    # config.json을 수정하면 다음 메시지에 바로 반영된다(재시작 불필요).
    _write_config(
        tmp_path, {"keywords": ["사고"], "auto_message": "NEW", "cooldown_seconds": 0}
    )
    assert responder.reply_for(("-100", ""), "사고", now=2.0) == "NEW"
