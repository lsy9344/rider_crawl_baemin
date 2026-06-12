"""Gmail 2차 인증번호 조회.

쿠팡이츠가 이메일 인증번호를 보내면, Gmail API(``gmail.readonly``)로 해당 메일을
찾아 본문에서 인증번호를 추출한다.

보안 규칙(문서 기준):

- ``requested_after`` 이후 도착한 메일만 사용한다(과거 인증번호 재사용 방지).
- 여러 메일이 있으면 가장 최신(``internalDate`` 큰) 메일만 본다.
- 인증번호 원문과 OAuth 토큰 값은 예외 메시지/로그에 절대 넣지 않는다.

테스트 가능성을 위해 Gmail 서비스 객체는 ``build_service`` 콜러블로 주입할 수 있게
했다. 본문 추출/코드 파싱/메일 필터링은 순수 함수라 네트워크 없이 단위 테스트한다.
"""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Gmail 읽기 전용 최소 권한. 다른 scope는 쓰지 않는다.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class Gmail2faError(RuntimeError):
    """Gmail 인증번호 조회 실패. 메시지에 인증번호/토큰 값을 넣지 않는다."""


def fetch_latest_verification_code(
    *,
    credentials_path: Path,
    token_path: Path,
    query: str,
    requested_after: datetime,
    poll_seconds: int,
    poll_interval_seconds: int,
    code_digits: int,
    build_service: Callable[[Path, Path], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Return the newest verification code that arrived after ``requested_after``.

    ``requested_after`` 이후 도착한 메일이 보일 때까지 ``poll_interval_seconds`` 간격
    으로 최대 ``poll_seconds`` 동안 Gmail을 재조회한다. 끝내 못 찾으면
    ``Gmail2faError``를 던진다.

    ``build_service``를 주입하면 실제 OAuth/네트워크 없이 fake 서비스로 테스트할 수
    있다. 기본값은 ``credentials_path``/``token_path``로 Gmail 서비스를 만든다.
    """

    requested_after_utc = _to_utc(requested_after)
    service_factory = build_service or _build_gmail_service
    service = service_factory(credentials_path, token_path)

    deadline = now() + _seconds_to_timedelta(max(0, poll_seconds))
    interval = max(0.0, float(poll_interval_seconds))

    last_error: Gmail2faError | None = None
    while True:
        try:
            code = _find_code_once(
                service,
                query=query,
                requested_after=requested_after_utc,
                code_digits=code_digits,
            )
        except Gmail2faError as exc:
            # 파싱/조회 단계 오류(본문 추출 실패, 일시적 Gmail API 오류 등)는 다음 폴링에서
            # 새 메일이 오거나 일시 오류가 풀리면 해소될 수 있으므로, 기록만 하고 deadline
            # 까지 계속 시도한다. 인증번호나 Google 클라이언트 세부 정보는 담기지 않는다.
            # 마지막 오류는 객체째 보관해, deadline 후 재발생 시 원인 체인을 보존한다.
            last_error = exc
            code = None

        if code is not None:
            return code

        if now() >= deadline:
            break
        sleep(interval)

    if last_error is not None:
        # 원인(원래 외부 예외 포함)을 from으로 이어 붙여 재발생한다. 메시지 자체는
        # 이미 안전한 Gmail2faError 메시지다.
        raise Gmail2faError(str(last_error)) from (last_error.__cause__ or last_error)
    raise Gmail2faError(
        "요청 시각 이후 도착한 쿠팡이츠 인증 메일을 찾지 못했습니다. "
        "Gmail 검색 조건(GMAIL_2FA_QUERY)이나 발신자/제목을 확인하세요."
    )


def _find_code_once(
    service: Any,
    *,
    query: str,
    requested_after: datetime,
    code_digits: int,
) -> str | None:
    """Search Gmail once and return the code from the single newest qualifying mail.

    ``requested_after`` 이전 메일은 버린다. 자격 있는 메일이 없으면 ``None``을 돌려준다.

    인증번호가 여러 번 발송된 경우(재시도 등)에는 **가장 최신 메일만** 코드 출처로
    쓴다. 오래된 메일로 내려가면 이미 무효가 된 과거 코드를 입력할 수 있기 때문이다.
    따라서 최신 메일에서 코드를 못 뽑으면 더 오래된 메일을 보지 않고 ``Gmail2faError``를
    올려 상위 폴링이 "아직 유효 코드 메일이 안 보임"으로 다루게 한다(다음 폴링에서 더
    새 메일이 오면 그때 사용).
    """

    messages = _list_messages(service, query)
    newest_internal: datetime | None = None
    newest_message: dict[str, Any] | None = None
    for meta in messages:
        message = _get_message(service, meta["id"])
        internal = _internal_date(message)
        if internal is None or internal < requested_after:
            continue
        if newest_internal is None or internal > newest_internal:
            newest_internal = internal
            newest_message = message

    if newest_message is None:
        return None

    body = extract_message_text(newest_message)
    code = extract_verification_code(body, code_digits=code_digits)
    if code is not None:
        return code

    raise Gmail2faError(
        "최신 인증 메일에서 인증번호를 추출하지 못했습니다. "
        "메일 본문 형식이나 인증번호 자리수(COUPANG_2FA_CODE_DIGITS)를 확인하세요."
    )


def _list_messages(service: Any, query: str) -> list[dict[str, Any]]:
    # Gmail API 호출 오류(HttpError·전송 오류 등)를 Gmail2faError로 감싸, Google 클라이언트
    # 내부 예외 타입/세부 메시지가 상위로 그대로 새어나가지 않게 한다. 원인은 from exc로
    # 보존한다. 검색어/인증번호 값은 메시지에 넣지 않는다.
    try:
        response = service.users().messages().list(userId="me", q=query).execute()
    except Gmail2faError:
        raise
    except Exception as exc:
        raise Gmail2faError("Gmail 메일 목록 조회에 실패했습니다.") from exc
    return list(response.get("messages") or [])


def _get_message(service: Any, message_id: str) -> dict[str, Any]:
    try:
        return service.users().messages().get(userId="me", id=message_id, format="full").execute()
    except Gmail2faError:
        raise
    except Exception as exc:
        raise Gmail2faError("Gmail 메일 본문 조회에 실패했습니다.") from exc


def _internal_date(message: dict[str, Any]) -> datetime | None:
    raw = message.get("internalDate")
    if raw is None:
        return None
    try:
        # Gmail internalDate는 epoch 밀리초 문자열이다.
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def extract_message_text(message: dict[str, Any]) -> str:
    """Return decoded plain/HTML body text from a Gmail message payload.

    Gmail 본문은 단일 part이거나 ``multipart/*`` 중첩 구조다. ``text/plain``을 우선
    수집하고, 없으면 ``text/html``에서 태그를 제거해 텍스트만 남긴다. 본문 데이터는
    base64url로 인코딩돼 있어 디코딩한다.
    """

    payload = message.get("payload") or {}
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _collect_body_parts(payload, plain_parts, html_parts)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return "\n".join(_strip_html(part) for part in html_parts)
    return ""


def _collect_body_parts(part: dict[str, Any], plain: list[str], html: list[str]) -> None:
    mime_type = str(part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data")
    if data:
        decoded = _decode_base64url(data)
        if mime_type == "text/plain":
            plain.append(decoded)
        elif mime_type == "text/html":
            html.append(decoded)
        elif not mime_type and decoded:
            # mimeType이 비어 있는 단일 part 메일은 plain으로 취급한다.
            plain.append(decoded)

    for child in part.get("parts") or []:
        _collect_body_parts(child, plain, html)


def _decode_base64url(data: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, TypeError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", without_tags).strip()


# 숫자 바로 옆에서 코드를 뽑을 때 쓰는, 좁고 정확한 단어 패턴. 잘못된 인접 매칭을
# 줄이려고 "인증번호/인증 코드/코드/verification code/code/otp"로 한정한다.
_CODE_CONTEXT_KEYWORDS = r"(?:인증번호|인증\s*코드|코드|verification\s*code|code|otp)"

# fallback(유일한 N자리 숫자) 채택 여부를 가르는, 조금 더 넓은 "이 메일이 인증 메일인가"
# 게이트. 본문 어딘가에 인증 의도 단어가 있을 때만 fallback을 허용해, 비인증 메일의
# 숫자를 코드로 오인하지 않게 한다. 단독 "인증"/"verify"까지 포함한다.
_VERIFICATION_INTENT_RE = re.compile(
    r"(?:인증|코드|verification|verify|\bcode\b|otp|일회용\s*비밀번호|one[-\s]*time)",
    flags=re.IGNORECASE,
)


def extract_verification_code(text: str, *, code_digits: int) -> str | None:
    """Extract the verification code from mail body text.

    먼저 "인증번호 …123456"처럼 주변 단어를 함께 본다. 실패하면 같은 자리수 독립 숫자
    (``\\b\\d{N}\\b``) fallback을 쓰되, **두 조건을 모두** 만족할 때만 채택한다.

    1. 본문 어딘가에 인증 관련 단어(인증번호/verification code/otp 등)가 있다. 넓은
       Gmail 검색에 비인증 쿠팡 메일이 섞여 들어와도, 인증 단어가 전혀 없는 메일의
       숫자를 코드로 오인하지 않게 한다.
    2. 같은 자리수 숫자가 본문에 정확히 하나다(여럿이면 어느 게 코드인지 알 수 없음).
    """

    if not text or code_digits <= 0:
        return None

    context_pattern = rf"{_CODE_CONTEXT_KEYWORDS}[^\d]{{0,20}}(\d{{{code_digits}}})"
    context_match = re.search(context_pattern, text, flags=re.IGNORECASE)
    if context_match:
        return context_match.group(1)

    # fallback: 인증 의도 단어가 본문에 있을 때만, 그리고 같은 자리수 숫자가 유일할 때만.
    if not _VERIFICATION_INTENT_RE.search(text):
        return None
    unique = set(re.findall(rf"\b\d{{{code_digits}}}\b", text))
    if len(unique) == 1:
        return next(iter(unique))
    return None


def _build_gmail_service(credentials_path: Path, token_path: Path) -> Any:
    """Build a Gmail API service from OAuth credential/token files.

    토큰이 만료되면 refresh하고, refresh 토큰이 없거나 자격 파일이 없으면 설정 오류로
    중단한다(이 단계에서 대화형 OAuth 승인을 새로 띄우지 않는다 — 무인 운영 환경이라
    최초 1회 승인은 운영 준비 단계에서 별도로 해 둔다).
    """

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not Path(token_path).is_file():
        raise Gmail2faError(
            "Gmail OAuth 토큰 파일이 없습니다. 최초 1회 로컬에서 Gmail 승인을 실행해 "
            f"토큰 파일을 만드세요: {token_path}"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), [GMAIL_READONLY_SCOPE])

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # google.auth.exceptions.RefreshError 등
                raise Gmail2faError(
                    "Gmail OAuth 토큰 갱신에 실패했습니다. Google 재승인이 필요합니다."
                ) from exc
            _save_token(creds, token_path)
        else:
            raise Gmail2faError(
                "Gmail OAuth 토큰이 유효하지 않습니다. Google 재승인이 필요합니다."
            )

    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _save_token(creds: Any, token_path: Path) -> None:
    Path(token_path).write_text(creds.to_json(), encoding="utf-8")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _seconds_to_timedelta(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)
