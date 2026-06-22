from __future__ import annotations

from pathlib import Path

# 크기 기준 rotation 기본값. run_errors.log·kakao_diagnostics.log 두 writer가 무한히
# 커지지 않게 append 직전에 검사한다. 추후 조정 가능하도록 모듈 상수로 노출한다.
# logging.handlers.RotatingFileHandler를 쓰지 않는 이유: 두 writer는 logging 모듈 기반이
# 아니라 커스텀 타임스탬프 포맷 + 경로 반환 계약을 가진 수동 append라, 핸들러로 바꾸면
# 포맷·반환 계약·기존 테스트가 깨진다. append 직전 크기 검사만 더하는 최소 변경이 안전.
DEFAULT_MAX_BYTES = 1_000_000  # 1MB
DEFAULT_BACKUP_COUNT = 5


def rotate_if_needed(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """``path``가 임계치 이상이면 RotatingFileHandler식으로 회전한다(크기 기준).

    ``path.{backup_count}``(가장 오래된 회전본)를 지워 보존 기준을 적용한 뒤
    ``path.{k}``→``path.{k+1}``로 한 칸씩 밀고 마지막에 ``path``→``path.1``로 옮긴다. 회전 후
    ``path``는 사라지므로 호출자의 ``open("a")``가 새 빈 파일을 만든다. 모든 파일 연산은
    best-effort라 회전본이 없거나 부분 실패해도 조용히 넘어가고, 호출자의 append 흐름
    (에러 로깅/카카오 진단)을 예외로 깨지 않는다. ``max_bytes``/``backup_count``가 1 미만
    (=회전 비활성)이면 아무것도 하지 않는다.
    """

    if max_bytes <= 0 or backup_count <= 0:
        return
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    _silent_unlink(_rotated(path, backup_count))
    for index in range(backup_count - 1, 0, -1):
        _silent_replace(_rotated(path, index), _rotated(path, index + 1))
    _silent_replace(path, _rotated(path, 1))


def _rotated(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _silent_replace(src: Path, dst: Path) -> None:
    try:
        if src.exists():
            src.replace(dst)
    except OSError:
        pass
