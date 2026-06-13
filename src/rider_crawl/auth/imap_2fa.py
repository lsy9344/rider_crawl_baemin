"""이메일 2차 인증번호 조회 (IMAP, Gmail/Naver 공용).

도메인으로 IMAP 호스트만 분기한다. ``requested_after`` 이후(서버 수신시각 INTERNALDATE
기준) 도착한 가장 최신 메일에서 인증번호를 추출한다.

운영 검증 메모(네이버·Gmail 양쪽 실측 확인):
- **앱 비밀번호의 공백 제거 필수.** Gmail은 4자리씩 공백으로 보여준다("nuda vmiy gtfr ggeg").
  공백 포함 입력은 로그인 실패, 제거 시 성공 → ``_imap_connect``에서 strip.
- **시각 컷오프는 Date(발신자값)가 아니라 서버 수신시각 INTERNALDATE로 비교.**
- **두 공급자 모두 바운드 폴링으로 통일(IDLE 미사용).** 2FA 조회는 "코드 전송 직후
  ~120초 동안만 기다리는" 짧고 끝이 있는 작업이라 폴링이 단순·충분하다. Naver는 IDLE을
  지원하지 않으므로 양쪽을 같은 코드로 다루기 위해 폴링으로 맞춘다.
- **네이버는 선택된 세션이 NOOP로 새 메일을 갱신하지 않는다** → 폴링마다 INBOX 재-SELECT
  (``_find_code_once``마다 ``select_folder``). Gmail도 무해.
- **INBOX만 본다.** 인증 메일은 INBOX로 도착하므로 Gmail의 한글 특수폴더를 다룰 필요가 없다.
- 한글 SUBJECT 서버검색 불안정 → ``SINCE``로 후보만 줄이고 **제목은 클라이언트 필터**.
- 기존 Gmail API 검색식의 ``from:`` 안전장치는 **FROM 헤더 클라이언트 필터**로 유지한다
  (기본 ``verification_email_sender_keyword="coupang"``).

보안: 인증번호·앱 비밀번호를 예외 메시지/로그에 넣지 않는다. ``BODY.PEEK[]`` + readonly
SELECT로 메일을 읽음 처리하지 않는다.
"""

from __future__ import annotations

import email
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Any, Callable

from rider_crawl.auth.codes import extract_verification_code

IMAP_HOST_BY_DOMAIN = {
    "naver.com": "imap.naver.com",
    "mail.naver.com": "imap.naver.com",
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
}


class Imap2faError(RuntimeError):
    """이메일 인증번호 조회 실패. 메시지에 인증번호/앱 비밀번호를 넣지 않는다."""


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""


def imap_host_for_email(address: str) -> str:
    host = IMAP_HOST_BY_DOMAIN.get(domain_of(address))
    if not host:
        raise Imap2faError(
            "지원하지 않는 인증 이메일 도메인입니다. naver.com 또는 gmail.com 주소를 입력하세요."
        )
    return host


def fetch_latest_verification_code(
    *,
    email_address: str,
    app_password: str,
    subject_keyword: str,
    sender_keyword: str,
    requested_after: datetime,
    poll_seconds: int,
    poll_interval_seconds: int,
    code_digits: int,
    host: str | None = None,
    port: int = 993,
    connect: Callable[[str, int, str, str], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Return the newest verification code that arrived after ``requested_after``.

    ``requested_after`` 이후(INTERNALDATE 기준) 도착한 메일이 보일 때까지
    ``poll_interval_seconds`` 간격으로 최대 ``poll_seconds`` 동안 재조회한다. 끝내 못
    찾으면 ``Imap2faError``를 던진다.

    ``connect``를 주입하면 실제 네트워크 없이 fake 서버로 단위 테스트할 수 있다. 기본값은
    ``imapclient.IMAPClient``로 접속한다(도메인으로 호스트 자동 결정).
    """

    imap_host = host or imap_host_for_email(email_address)
    requested_after_utc = _to_utc(requested_after)
    server = (connect or _imap_connect)(imap_host, port, email_address, app_password)

    deadline = now() + timedelta(seconds=max(0, poll_seconds))
    interval = max(0.0, float(poll_interval_seconds))
    last_error: Imap2faError | None = None
    try:
        while True:
            try:
                code = _find_code_once(
                    server,
                    subject_keyword=subject_keyword,
                    sender_keyword=sender_keyword,
                    requested_after=requested_after_utc,
                    code_digits=code_digits,
                )
            except Imap2faError as exc:
                # 일시 오류(검색 실패, 최신 메일 파싱 실패 등)는 다음 폴링에서 새 메일이
                # 오거나 일시 오류가 풀리면 해소될 수 있다. 기록만 하고 deadline까지 계속.
                last_error, code = exc, None
            if code is not None:
                return code
            if now() >= deadline:
                break
            sleep(interval)
    finally:
        _safe_logout(server)

    if last_error is not None:
        raise Imap2faError(str(last_error)) from (last_error.__cause__ or last_error)
    raise Imap2faError(
        "요청 시각 이후 도착한 인증 메일을 찾지 못했습니다. 제목 키워드/메일 도착 여부를 확인하세요."
    )


def _find_code_once(
    server: Any,
    *,
    subject_keyword: str,
    sender_keyword: str,
    requested_after: datetime,
    code_digits: int,
) -> str | None:
    # 네이버는 선택 세션에 새 메일이 반영 안 되므로 매 폴링 재-SELECT. readonly로 읽음 방지.
    server.select_folder("INBOX", readonly=True)
    try:
        uids = server.search(["SINCE", requested_after.date()])  # SINCE는 '일' 단위까지만
    except Exception as exc:
        raise Imap2faError("메일 검색에 실패했습니다.") from exc
    if not uids:
        return None

    meta = server.fetch(uids, ["INTERNALDATE", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)]"])
    newest_uid, newest_dt = None, None
    for uid, data in meta.items():
        internal = _to_utc(data.get(b"INTERNALDATE"))
        if internal is None or internal < requested_after:
            continue
        headers = data.get(b"BODY[HEADER.FIELDS (SUBJECT FROM)]", b"")
        subject = _decode_header_value(headers, "Subject")
        sender = _decode_header_value(headers, "From")
        if subject_keyword and subject_keyword.casefold() not in subject.casefold():
            continue
        if sender_keyword and sender_keyword.casefold() not in sender.casefold():
            continue
        if newest_dt is None or internal > newest_dt:
            newest_uid, newest_dt = uid, internal

    if newest_uid is None:
        return None

    raw = server.fetch([newest_uid], ["BODY.PEEK[]"])[newest_uid][b"BODY[]"]
    body = _message_text(email.message_from_bytes(raw))
    code = extract_verification_code(body, code_digits=code_digits)
    if code is not None:
        return code
    raise Imap2faError("최신 인증 메일에서 인증번호를 추출하지 못했습니다(자리수/형식 확인).")


def _message_text(msg: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            plain.append(text)
        elif part.get_content_type() == "text/html":
            html.append(text)
    return "\n".join(plain) if plain else _strip_html("\n".join(html))


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _decode_header_value(raw: bytes, header_name: str) -> str:
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(email.message_from_bytes(raw).get(header_name, ""))))
    except Exception:
        return ""


def _imap_connect(host: str, port: int, email_address: str, app_password: str) -> Any:
    from imapclient import IMAPClient

    # Gmail은 앱 비밀번호를 4자리씩 공백으로 끊어 보여준다("nuda vmiy gtfr ggeg").
    # 사용자가 화면 그대로 붙여넣어도 로그인되도록 공백을 제거한다(네이버도 무해).
    # ★ 실측 검증: 공백 포함 입력은 로그인 실패, 공백 제거 시 성공.
    app_password = re.sub(r"\s+", "", app_password or "")
    server = IMAPClient(host, port=port, ssl=True, use_uid=True)
    # ★ INTERNALDATE는 timezone-aware로 받는다(normalise_times=False). 기본값(True)은
    # INTERNALDATE를 "naive 로컬 시각"으로 돌려주는데, requested_after는 aware UTC라
    # KST(+9) 머신에서 컷오프가 9시간 어긋난다(과거 만료 코드를 채택할 위험). aware로
    # 받으면 _to_utc의 astimezone(UTC)가 올바르게 변환한다.
    server.normalise_times = False
    try:
        server.login(email_address, app_password)
    except Exception as exc:
        raise Imap2faError(
            "IMAP 로그인 실패. 메일의 IMAP 사용 설정과 앱 비밀번호를 확인하세요."
        ) from exc  # 앱 비밀번호 값은 메시지에 넣지 않는다.
    return server


def _safe_logout(server: Any) -> None:
    try:
        server.logout()
    except Exception:
        pass


def _to_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    return None
