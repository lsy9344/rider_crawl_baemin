from __future__ import annotations

from types import SimpleNamespace

import rider_crawl.sender as sender_mod
import rider_crawl.ui as ui_mod
from rider_crawl import log_rotation
from rider_crawl.log_rotation import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    rotate_if_needed,
)
from rider_crawl.sender import _write_kakao_diagnostics
from rider_crawl.ui import RiderBotUi


def test_module_defaults_are_size_based():
    assert DEFAULT_MAX_BYTES == 1_000_000  # 1MB
    assert DEFAULT_BACKUP_COUNT == 5


def test_rotate_skips_when_below_threshold(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("small", encoding="utf-8")

    rotate_if_needed(path, max_bytes=1000, backup_count=3)

    assert path.read_text(encoding="utf-8") == "small"  # 임계 미만 → 회전 없음
    assert not (tmp_path / "app.log.1").exists()


def test_rotate_skips_when_file_missing(tmp_path):
    path = tmp_path / "missing.log"

    rotate_if_needed(path, max_bytes=1, backup_count=3)  # 없으면 best-effort skip

    assert not path.exists()
    assert not (tmp_path / "missing.log.1").exists()


def test_rotate_moves_base_to_dot_one_and_empties_base(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("0123456789", encoding="utf-8")  # 10B >= 임계 5B

    rotate_if_needed(path, max_bytes=5, backup_count=3)

    assert (tmp_path / "app.log.1").read_text(encoding="utf-8") == "0123456789"
    assert not path.exists()  # 회전 후 base는 사라져 다음 append가 새 빈 파일을 만든다


def test_rotate_shifts_and_enforces_backup_count(tmp_path):
    path = tmp_path / "app.log"
    (tmp_path / "app.log.1").write_text("R1", encoding="utf-8")
    (tmp_path / "app.log.2").write_text("R2-oldest", encoding="utf-8")  # 보존 초과로 밀려 삭제
    path.write_text("CURRENT-1234567890", encoding="utf-8")  # 임계 초과

    rotate_if_needed(path, max_bytes=5, backup_count=2)

    # base→.1, 기존 .1→.2, 기존 .2(가장 오래됨)는 보존 개수 초과로 삭제된다.
    assert (tmp_path / "app.log.1").read_text(encoding="utf-8") == "CURRENT-1234567890"
    assert (tmp_path / "app.log.2").read_text(encoding="utf-8") == "R1"
    assert not (tmp_path / "app.log.3").exists()
    remaining = {p.read_text(encoding="utf-8") for p in tmp_path.glob("app.log*")}
    assert "R2-oldest" not in remaining  # 가장 오래된 회전본은 보존 기준에 따라 사라진다


def test_rotate_disabled_when_params_non_positive(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("0123456789", encoding="utf-8")

    rotate_if_needed(path, max_bytes=0, backup_count=5)
    rotate_if_needed(path, max_bytes=5, backup_count=0)

    assert not (tmp_path / "app.log.1").exists()  # 회전 비활성(파라미터 1 미만)


def test_write_run_error_log_rotates_and_keeps_return_contract(tmp_path, monkeypatch):
    # AC3 #7·#8: append 직전 rotation이 실제로 일어나고, Path 반환 계약과 best-effort가 유지된다.
    monkeypatch.setattr(
        ui_mod,
        "rotate_if_needed",
        lambda p: log_rotation.rotate_if_needed(p, max_bytes=50, backup_count=2),
    )
    app = RiderBotUi.__new__(RiderBotUi)  # __init__(tkinter) 우회
    detail = "x" * 200  # 임계(50B) 초과 보장 — 더미 텍스트(실제 토큰 형태 아님)

    first = app._write_run_error_log("중지", detail, log_dir=tmp_path)
    second = app._write_run_error_log("중지", detail, log_dir=tmp_path)

    base = tmp_path / "run_errors.log"
    assert first == base and second == base  # 기록 경로(Path) 반환 계약 유지
    assert (tmp_path / "run_errors.log.1").exists()  # 두 번째 호출 직전 실제 회전 발생
    assert base.exists()  # 회전 후 새 base에 두 번째 기록이 남는다


def test_write_kakao_diagnostics_rotates_and_stays_best_effort(tmp_path, monkeypatch):
    # AC3 #8: kakao 진단도 append 직전 rotation, 경로 반환·best-effort 유지.
    monkeypatch.setattr(
        sender_mod,
        "rotate_if_needed",
        lambda p: log_rotation.rotate_if_needed(p, max_bytes=50, backup_count=2),
    )
    monkeypatch.setattr(sender_mod, "_KAKAO_DIAGNOSTICS", ["진단 더미 한 줄" * 20])
    config = SimpleNamespace(log_dir=tmp_path)

    first = _write_kakao_diagnostics(config)
    second = _write_kakao_diagnostics(config)

    base = tmp_path / "kakao_diagnostics.log"
    assert first == base and second == base  # 경로 반환 계약 유지
    assert (tmp_path / "kakao_diagnostics.log.1").exists()  # 실제 회전 발생


def test_write_run_error_log_stays_best_effort_when_rotation_raises(tmp_path, monkeypatch):
    # AC3 #8: rotation 자체가 예외를 던져도 에러 로깅 경로가 폭주하거나 예외로 터지지 않는다.
    # 이미 감싼 try/except가 rotation 실패를 흡수한다(best-effort: None 또는 기록 경로).
    def boom(*_args, **_kwargs):
        raise RuntimeError("rotation blew up")

    monkeypatch.setattr(ui_mod, "rotate_if_needed", boom)
    app = RiderBotUi.__new__(RiderBotUi)  # __init__(tkinter) 우회

    result = app._write_run_error_log("중지", "더미 상세", log_dir=tmp_path)

    # 예외가 전파되지 않고 best-effort 값(None 또는 기록 경로)을 반환한다.
    assert result is None or result == tmp_path / "run_errors.log"


def test_write_kakao_diagnostics_stays_best_effort_when_rotation_raises(tmp_path, monkeypatch):
    # AC3 #8: kakao 진단 writer도 rotation 예외를 흡수하고 전송/진단 경로를 깨지 않는다.
    def boom(*_args, **_kwargs):
        raise RuntimeError("rotation blew up")

    monkeypatch.setattr(sender_mod, "rotate_if_needed", boom)
    monkeypatch.setattr(sender_mod, "_KAKAO_DIAGNOSTICS", ["진단 더미 한 줄"])
    config = SimpleNamespace(log_dir=tmp_path)

    result = _write_kakao_diagnostics(config)

    assert result is None or result == tmp_path / "kakao_diagnostics.log"
