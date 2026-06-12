"""공용 redaction 유틸 (P0-04).

토큰·비밀번호·OTP·전화번호·이메일 등 민감값을 로그/예외 메시지에서 가리는 한 곳의
일관된 마스킹 정책이다. Cloud(`rider_server`)/Agent(`rider_agent`)/기존 `rider_crawl`이
모두 ``from rider_crawl.redaction import redact`` 형태로 재사용하도록 표준 라이브러리
``re`` 만 쓰고 무거운 의존성을 끌어오지 않는다.

세 가지 진입점:

* :func:`redact` — 자유 텍스트용. 알려진 패턴(Telegram 토큰, email, phone, OTP 문맥,
  ``key=value`` 형태의 민감 키)에 대한 **best-effort** 마스킹이다.
* :func:`redact_mapping` — 구조화된 dict/list용. **키 이름 기반**이라 자유 텍스트 정규식보다
  신뢰도가 높다. 구조를 아는 호출자는 이쪽을 쓴다.
* :func:`redacted_error_event` — 에러 이벤트(ADD-6 job 이벤트 / ADD-13 에러 응답)에 그대로
  끼울 수 있는 평면 dict를 만든다.

정책 메모(코드만으로 알기 어려운 부분):

* **완전 치환.** secret 은 끝 4자리 같은 부분 노출 없이 통째로 :data:`REDACTED` 로 바꾼다
  (operations-security-test-contract: 원본 secret 의 어떤 연속 부분 문자열도 남지 않을 것).
* **``*_ref`` 는 secret 이 아니다.** architecture 는 평문 secret 대신 ``password_ref`` 같은
  참조만 로그에 남기는 정책이므로 ``*_ref`` 키 값은 보존한다(추적용).
* **운영 식별자는 조건부.** ``customer_name``/``center_name``(쿠팡 ``baemin_center_name``
  포함)/카카오 방명은 운영자 로그엔 기본 보존, 외부 진단 산출물용으로
  ``mask_operational_ids=True`` 일 때만 마스킹한다.
* **idempotent.** :data:`REDACTED` 자체가 어떤 secret 패턴에도 다시 매칭되지 않으므로
  ``redact(redact(x)) == redact(x)`` 가 성립한다.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "REDACTED",
    "redact",
    "redact_mapping",
    "redacted_error_event",
]

#: 모든 마스킹 결과에 쓰는 단일 placeholder. 숫자/``:``/``@`` 가 없어 어떤 secret 패턴에도
#: 다시 매칭되지 않는다(idempotency 보장).
REDACTED = "***REDACTED***"


# --- 자유 텍스트 패턴 (best-effort) -------------------------------------------

# Telegram 봇 토큰 등 ``<digits>:<token>`` 형태. 선두 숫자 개수에 관대하게 두어 가짜 fixture
# (``8:AAE-...``)와 실제 토큰(``123456789:AAH...``)을 모두 잡는다. 콜론 뒤 6자 이상을 요구해
# ``10:30:00`` 같은 시각/포트는 건드리지 않는다.
_TOKEN_SHAPE_RE = re.compile(r"\d+:[A-Za-z0-9_-]{6,}")

# full email (local@domain 전체).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# full phone: 한국 휴대폰(010-XXXX-XXXX / 하이픈 없는 형태) + 국제 ``+82…``.
_PHONE_RE = re.compile(
    r"(?<![\w])"
    r"(?:\+?82[-\s.]?)?"            # 선택적 국가번호
    r"0?1[016789]"                  # 010/011/016/017/018/019 (국제표기 시 선두 0 생략 가능)
    r"[-\s.]?\d{3,4}[-\s.]?\d{4}"
    r"(?![\w])"
)

# OTP/인증번호: 문맥 키에 인접한 4–8자리 코드만 마스킹(임의 숫자 오탐 방지).
_OTP_RE = re.compile(
    r"(?P<label>otp|code|인증\s*번호|인증\s*코드|verification\s*code|auth\s*code)"
    r"(?P<mid>[\"']?\s*[:=]?\s*[\"']?)"
    r"(?P<digits>\d{4,8})(?!\d)",
    re.IGNORECASE,
)

# 민감 키 어간. 선두 식별자 prefix(``coupang_``/``telegram_`` 등)는 허용하되, 값 분리자
# ``[:=]`` 가 바로 뒤따라야 하므로 ``password_ref`` 처럼 ``_ref`` 가 붙은 키는 매칭되지 않는다.
_SENSITIVE_KEY = (
    r"(?:[A-Za-z0-9]+_)*"
    r"(?:password|passwd|pwd"
    r"|telegram_bot_token|bot_token|access_token|refresh_token|id_token|token"
    r"|client_secret|secret"
    r"|refresh"
    r"|authorization_code|authorization|auth_code"
    r"|otp|verification_code"
    r"|telegram_chat_id|chat_id"
    r"|telegram_message_thread_id|message_thread_id|thread_id"
    r"|credentials|credential"
    r"|api_key|apikey)"
)

# ``key=value`` / ``key: value`` / JSON ``"key": "value"`` 에서 값만 마스킹.
_KEY_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<key>[\"']?" + _SENSITIVE_KEY + r"[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<val>\"[^\"]*\"|'[^']*'|[^\s,;}{]+)",
    re.IGNORECASE,
)


def _mask_kv(match: "re.Match[str]") -> str:
    """``key=value`` 매치에서 값만 placeholder 로 치환(키·분리자·따옴표 보존)."""

    key = match.group("key")
    sep = match.group("sep")
    val = match.group("val")
    if val[:1] in ("\"", "'"):
        quote = val[0]
        return f"{key}{sep}{quote}{REDACTED}{quote}"
    return f"{key}{sep}{REDACTED}"


# ``Authorization: Bearer <token>`` / ``Authorization=<token>`` HTTP 인증 헤더. 일반
# ``key=value`` 규칙은 값 캡처가 첫 공백에서 끊겨 ``Bearer`` 스킴 단어만 가리고 토큰 본문을
# 흘리므로(AC2 위반), authorization 키 뒤 구분자부터 줄 끝(또는 ``,``/``;``)까지를 통째로
# 가린다. ``authorization_code`` 는 sep 가 ``_`` 라 여기 안 걸리고 ``key=value`` 규칙이 처리한다.
_AUTH_HEADER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<key>(?:proxy-)?authorization)"
    r"(?P<sep>\s*[:=]\s*)"
    r"[^\r\n,;]+",
    re.IGNORECASE,
)


def _mask_auth_header(match: "re.Match[str]") -> str:
    """``Authorization`` 헤더의 자격증명 본문 전체(스킴 포함)를 placeholder 로 치환."""

    return f"{match.group('key')}{match.group('sep')}{REDACTED}"


def redact(text: str, *, mask_operational_ids: bool = False) -> str:
    """자유 텍스트에서 알려진 민감 패턴을 :data:`REDACTED` 로 완전 치환한다.

    Telegram 토큰, full email, full phone, OTP/인증번호 문맥, 그리고
    ``token``/``password``/``secret``/``refresh``/``authorization``/``code``/``otp``/
    ``chat_id``/``thread_id``/``credential`` 류 키의 ``key=value`` 값이 대상이다. 구조를
    아는 호출자는 더 신뢰도 높은 :func:`redact_mapping` 을 쓰는 게 좋다(이 함수는 best-effort).

    ``mask_operational_ids`` 는 시그니처 일관성을 위해 받지만, 자유 텍스트에는 운영 식별자를
    안전하게 식별할 키 맥락이 없으므로 현재 동작에 영향을 주지 않는다. 운영 식별자 마스킹은
    :func:`redact_mapping` 의 키 기반 분기에서 처리한다.
    """

    if not isinstance(text, str):
        text = str(text)

    # 순서: email → 토큰 형태 → phone → Authorization 헤더 → key=value → OTP 문맥.
    text = _EMAIL_RE.sub(REDACTED, text)
    text = _TOKEN_SHAPE_RE.sub(REDACTED, text)
    text = _PHONE_RE.sub(REDACTED, text)
    text = _AUTH_HEADER_RE.sub(_mask_auth_header, text)
    text = _KEY_VALUE_RE.sub(_mask_kv, text)
    text = _OTP_RE.sub(lambda m: f"{m.group('label')}{m.group('mid')}{REDACTED}", text)
    return text


# --- 구조화 입력 (키 이름 기반) -----------------------------------------------

# 키가 이 어간으로 끝나면 값을 마스킹한다(``_ref`` 는 아래에서 먼저 제외).
# 주의: 일반 ``code`` 는 여기 없다 — 에러 이벤트의 ``code`` (UPPER_SNAKE 에러 코드)는 보존
# 대상이라 키 기반 마스킹에서 빼고, OTP 는 ``otp``/``verification_code`` 로만 잡는다.
_SECRET_KEY_SUFFIXES = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "otp",
    "chat_id",
    "thread_id",
    "credential",
    "credentials",
    "authorization",
    "authorization_code",
    "auth_code",
    "verification_code",
    "api_key",
    "apikey",
    "refresh",
)

# 운영 식별자: 기본 보존, ``mask_operational_ids=True`` 일 때만 마스킹.
# 쿠팡 탭의 ``baemin_center_name`` 은 실제 기대 센터/상점명으로 재사용되므로 secret 이 아니라
# 운영 식별자군으로 본다(project-context §88).
_OPERATIONAL_KEY_SUFFIXES = (
    "customer_name",
    "center_name",
    "store_name",
    "shop_name",
    "room_name",
    "kakao_room",
)


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    if k.endswith("_ref") or k == "ref":
        return False  # ``*_ref`` 는 참조일 뿐 secret 이 아니다(추적용 보존).
    return k.endswith(_SECRET_KEY_SUFFIXES)


def _is_operational_key(key: str) -> bool:
    return key.lower().endswith(_OPERATIONAL_KEY_SUFFIXES)


def _redact_value(value: Any, *, mask_operational_ids: bool) -> Any:
    if isinstance(value, dict):
        return {
            k: _redact_field(k, v, mask_operational_ids=mask_operational_ids)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted = [
            _redact_value(item, mask_operational_ids=mask_operational_ids)
            for item in value
        ]
        return type(value)(redacted) if isinstance(value, tuple) else redacted
    if isinstance(value, str):
        return redact(value, mask_operational_ids=mask_operational_ids)
    return value


def _redact_field(key: Any, value: Any, *, mask_operational_ids: bool) -> Any:
    key_str = str(key)
    if _is_secret_key(key_str):
        return REDACTED
    if _is_operational_key(key_str):
        return REDACTED if mask_operational_ids else value
    return _redact_value(value, mask_operational_ids=mask_operational_ids)


def redact_mapping(data: Any, *, mask_operational_ids: bool = False) -> dict:
    """구조화된 dict 를 **키 이름 기반**으로 재귀 마스킹한 새 객체를 반환한다.

    민감 키(``password``/``*token``/``secret``/``otp``/``chat_id``/``*thread_id``/
    ``credential`` 류) 값은 통째로 :data:`REDACTED` 로 바꾸고, ``*_ref`` 키는 보존한다.
    운영 식별자(``customer_name``/``center_name`` 등)는 기본 보존, ``mask_operational_ids``
    가 ``True`` 면 마스킹한다. 민감/운영/ref 어디에도 안 걸리는 문자열 값은 :func:`redact`
    (자유 텍스트 best-effort)를 통과시킨다. 중첩 dict/list 도 재귀 처리한다.
    원본은 변경하지 않는다(새 dict/list 반환).
    """

    return _redact_value(data, mask_operational_ids=mask_operational_ids)


# --- 에러 이벤트 헬퍼 (ADD-6 / ADD-13) ----------------------------------------


def redacted_error_event(
    code: str,
    message: str,
    error: BaseException | None = None,
) -> dict:
    """redaction 통과된 에러 이벤트 평면 dict 를 만든다.

    반환 형태::

        {"code": code, "message_redacted": redact(message),
         "error_message_redacted": redact(str(error))}  # error 가 있을 때만 후자

    architecture 에러 응답 ``{"error": {"code", "message_redacted"}}`` (ADD-13)과 job 이벤트
    ``event_type/severity/message_redacted`` (ADD-6)에 **그대로 합성 가능한 평면 dict** 다.
    응답 envelope(``{"error": …}``)나 이벤트 메타(``event_type``/``severity``)까지는 만들지
    않는다 — 그건 호출 레이어(P4 API / P3 Agent 이벤트)의 책임이다.

    ``code`` 는 secret 이 아니라고 가정하고 그대로 둔다(UPPER_SNAKE 보장은 호출자 책임).
    ``message``/``error`` 의 본문은 내부적으로 :func:`redact` 를 통과시켜 secret/OTP 부분
    문자열이 남지 않도록 자동 보장한다(별도 마스킹 로직 중복 구현 금지).
    """

    event: dict = {"code": code, "message_redacted": redact(message)}
    if error is not None:
        event["error_message_redacted"] = redact(str(error))
    return event
