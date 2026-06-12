"""키워드 감지 자동응답 (텔레그램 그룹방).

그룹방 메시지에 설정된 키워드(예: 사고, 병원)가 포함되면 ``config.json``에 저장된
자동 안내 메시지를 같은 그룹방(필요하면 같은 토픽)으로 발송한다.

이 모듈은 텔레그램 수신 파이프라인(:class:`rider_crawl.telegram_commands.TelegramCommandProcessor`)에
끼워 쓰도록 만들어졌다. 그래서 UI/exe를 실행해 탭을 시작하면 별도 프로세스 없이
키워드 자동응답이 함께 동작한다.

설정은 코드에 하드코딩하지 않고 ``config.json``에서 읽으며, 메시지마다 다시 읽어
봇 재시작 없이 수정 사항이 반영된다. 파일이 없거나 형식이 잘못되어도 봇이 멈추지
않도록 기본값(DEFAULT_CONFIG)을 fallback 으로 쓴다.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def default_config_path() -> Path:
    """``config.json``을 찾을 위치를 반환한다.

    운영자가 직접 수정하는 파일이므로 "실행 파일/프로그램 바로 옆"에 둔다.
    - exe로 패키징(PyInstaller onefile)된 경우: exe가 있는 폴더(``sys.executable``).
      onefile에 번들하면 임시 폴더에 풀려 읽기 전용이 되므로 번들하지 않고 exe 옆을 읽는다.
    - 개발/CLI 실행: 현재 작업 디렉터리(프로젝트 루트)의 ``config.json``.

    이는 UI가 ``runtime/state/ui_settings.json``을 cwd 기준으로 찾는 방식과 일치한다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    return Path("config.json")


# 하위 호환용 별칭(import 시점 기준). 실제 로딩은 default_config_path()를 쓴다.
DEFAULT_CONFIG_PATH = default_config_path()

# 설정 파일이 없거나 잘못된 경우 사용할 fallback 기본값.
DEFAULT_KEYWORDS: list[str] = ["사고", "병원"]
DEFAULT_AUTO_MESSAGE: str = (
    "☎️KB생명 :1111-2222\n"
    "☎️삼성생명 : 2222-3333\n"
    "☎️한화생명 : 3333-4444\n"
    "☎️DB보험 : 8888-9999"
)
DEFAULT_COOLDOWN_SECONDS: int = 30

DEFAULT_CONFIG: dict[str, Any] = {
    "keywords": DEFAULT_KEYWORDS,
    "auto_message": DEFAULT_AUTO_MESSAGE,
    "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
}


@dataclass(frozen=True)
class KeywordConfig:
    keywords: list[str]
    auto_message: str
    cooldown_seconds: float


def load_keyword_config(config_path: Path | None = None) -> KeywordConfig:
    """``config.json``을 읽어 키워드/자동 메시지/쿨다운을 반환한다.

    파일이 없거나 JSON 형식이 잘못된 경우에도 예외를 처리하고 기본값을 fallback
    으로 반환한다. 누락된 개별 항목도 기본값으로 채운다.
    """
    path = config_path or default_config_path()
    try:
        if not path.exists():
            logger.warning("config.json이 없습니다(%s). 기본 키워드 설정을 사용합니다.", path)
            return _from_mapping({})

        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, dict):
            logger.warning("config.json 형식이 올바르지 않습니다(%s). 기본 설정을 사용합니다.", path)
            return _from_mapping({})
        return _from_mapping(raw)
    except Exception as exc:  # noqa: BLE001 - 어떤 오류든 봇이 죽지 않게 처리
        logger.error("config.json 읽기 실패: %s", exc)
        logger.warning("기본 키워드 설정을 사용합니다.")
        return _from_mapping({})


def _from_mapping(raw: dict[str, Any]) -> KeywordConfig:
    keywords = raw.get("keywords", DEFAULT_KEYWORDS)
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        logger.warning("config.json의 keywords 형식이 올바르지 않습니다. 기본값을 사용합니다.")
        keywords = list(DEFAULT_KEYWORDS)

    auto_message = raw.get("auto_message", DEFAULT_AUTO_MESSAGE)
    if not isinstance(auto_message, str):
        auto_message = DEFAULT_AUTO_MESSAGE

    cooldown = raw.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
    try:
        cooldown_seconds = float(cooldown)
    except (TypeError, ValueError):
        cooldown_seconds = float(DEFAULT_COOLDOWN_SECONDS)
    # 운영자가 직접 수정하는 파일이라 음수가 들어올 수 있다. 음수면 쿨다운이 무력화되어
    # 매 메시지마다 응답할 수 있으므로 0 미만은 허용하지 않고 기본값으로 되돌린다.
    if cooldown_seconds < 0:
        logger.warning(
            "config.json의 cooldown_seconds가 음수(%s)입니다. 기본값을 사용합니다.", cooldown
        )
        cooldown_seconds = float(DEFAULT_COOLDOWN_SECONDS)

    return KeywordConfig(
        keywords=list(keywords),
        auto_message=auto_message,
        cooldown_seconds=cooldown_seconds,
    )


def match_keyword(text: str, keywords: list[str]) -> bool:
    """메시지 텍스트에 키워드가 하나라도 포함되어 있으면 True.

    완전일치가 아닌 포함(substring) 검색 방식이다. 빈 키워드는 무시한다.
    """
    if not text:
        return False
    return any(keyword and keyword in text for keyword in keywords)


class KeywordResponder:
    """대상(채팅방/토픽)별 쿨다운을 적용해 키워드 자동응답 여부를 판단한다.

    설정은 매 호출마다 ``config.json``에서 다시 읽어 수정 사항을 즉시 반영한다.
    쿨다운 상태(대상별 마지막 응답 시각)는 인스턴스에 보관한다.

    쿨다운은 **전송 성공 후** :meth:`mark_sent`로 기록한다. :meth:`reply_for`는
    상태를 바꾸지 않으므로, 전송이 실패하면 쿨다운이 갱신되지 않아 다음 메시지에서
    다시 응답할 수 있다(전송 실패 시 메시지 유실 방지).
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path
        self._last_response_at: dict[Any, float] = {}
        self._lock = threading.Lock()

    def reply_for(self, target: Any, text: str, *, now: float | None = None) -> str | None:
        """이 메시지에 보낼 자동응답 문구를 반환한다. 응답하지 않으면 None.

        ``target``은 대상 식별자(예: (chat_id, thread_id))로, 같은 대상에서
        마지막 **전송 성공** 후 ``cooldown_seconds`` 이내 반복 키워드는 응답하지
        않는다. 이 메서드는 상태를 바꾸지 않는다. 전송에 성공하면 호출자가
        :meth:`mark_sent`를 호출해 쿨다운을 기록해야 한다.
        """
        config = load_keyword_config(self.config_path)
        if not match_keyword(text, config.keywords):
            return None

        current = time.time() if now is None else now
        with self._lock:
            last = self._last_response_at.get(target, 0.0)
            if (current - last) < config.cooldown_seconds:
                return None
        return config.auto_message

    def mark_sent(self, target: Any, *, now: float | None = None) -> None:
        """자동응답 **전송 성공** 후 대상의 쿨다운 시작 시각을 기록한다."""
        current = time.time() if now is None else now
        with self._lock:
            self._last_response_at[target] = current
