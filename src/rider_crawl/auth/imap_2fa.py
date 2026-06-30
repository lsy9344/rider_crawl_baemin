"""이메일 2차 인증번호 조회 (IMAP, Gmail/Naver 공용)."""

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


class ImapAuthError(Imap2faError):
    """IMAP 로그인/이메일 설정 실패(앱 비밀번호·IMAP 사용 설정·미지원 도메인).

    "코드가 아직 안 옴(메일 지연)" 같은 일시적 실패와 구분되는 **운영자 조치형** 실패다 —
    상위 복구가 이걸 ``EMAIL_AUTH_REQUIRED`` 로 분류해 사람이 메일 설정을 고치게 한다. 메시지에
    인증번호/앱 비밀번호를 넣지 않는다(부모와 동일 정책)."""

    def __init__(self, *args: object, reason: str = "email_auth_required") -> None:
        super().__init__(*args)
        self.reason = reason


def classify_imap_auth_failure(exc: BaseException) -> str:
    """Return a safe reason code from an IMAP/provider auth failure."""

    text = re.sub(r"\s+", " ", f"{type(exc).__name__} {exc}".casefold()).strip()
    if any(
        signal in text
        for signal in (
            "imap access is disabled",
            "imap access disabled",
            "imap access not enabled",
            "imap is disabled",
            "imap not enabled",
            "imap has not been enabled",
            "enable imap",
        )
    ):
        return "imap_access_disabled"
    if any(
        signal in text
        for signal in (
            "too many login",
            "too many failed",
            "security block",
            "temporary auth block",
            "temporarily blocked",
            "authentication blocked",
            "account blocked",
            "account temporarily locked",
            "suspicious sign-in",
        )
    ):
        return "mailbox_auth_blocked"
    if any(
        signal in text
        for signal in (
            "invalid credentials",
            "invalid credential",
            "application-specific password",
            "application specific password",
            "app-specific password",
            "app password",
            "username and password not accepted",
            "authenticationfailed",
            "authentication failed",
            "auth failed",
            "bad credentials",
            "invalid password",
            "incorrect password",
        )
    ):
        return "mail_app_password_invalid"
    return "mailbox_login_failed"


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in (address or "") else ""


def imap_host_for_email(address: str) -> str:
    host = IMAP_HOST_BY_DOMAIN.get(domain_of(address))
    if not host:
        raise ImapAuthError(
            "지원하지 않는 인증 이메일 도메인입니다. naver.com 또는 gmail.com 주소를 입력하세요.",
            reason="unsupported_email_domain",
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
    diagnostics: dict[str, object] | None = None,
) -> str:
    """Return the newest verification code that arrived after ``requested_after``."""

    imap_host = host or imap_host_for_email(email_address)
    requested_after_utc = _to_utc(requested_after)
    if requested_after_utc is None:
        requested_after_utc = datetime.now(timezone.utc)
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
                    diagnostics=diagnostics,
                    now=now(),
                )
            except Imap2faError as exc:
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


# Gmail 의 "중요"(\\Important)·"별표"(\\Flagged)는 실제 메일함이 아니라 INBOX/전체메일의
# 가상 라벨(부분집합)이라, 순회하면 같은 인증 메일을 한 번 더 select/search/fetch 해
# 폴링 1회가 폴더당 ~1초씩 늘어난다(라이브 측정). 인증 메일이 이 라벨에만 있을 수는
# 없으므로 제외해도 코드를 놓치지 않는다.
_SKIP_FOLDER_SPECIAL = {"\\sent", "\\drafts", "\\trash", "\\all", "\\archive", "\\noselect", "\\important", "\\flagged"}
_SKIP_FOLDER_NAMES = {
    "sent",
    "sent messages",
    "sent mail",
    "drafts",
    "trash",
    "bin",
    "deleted messages",
    "deleted items",
    "보낸메일함",
    "발신함",
    "임시보관함",
    "지운메일함",
}


def _candidate_folders(server: Any) -> list[str]:
    try:
        listed = server.list_folders()
    except Exception:
        return ["INBOX"]
    folders = ["INBOX"]
    for entry in listed:
        try:
            flags, _delim, name = entry
        except (TypeError, ValueError):
            continue
        if not name or name.casefold() == "inbox":
            continue
        flagset = {
            (flag.decode("ascii", "ignore") if isinstance(flag, bytes) else str(flag)).casefold()
            for flag in (flags or ())
        }
        if flagset & _SKIP_FOLDER_SPECIAL:
            continue
        if name.strip().casefold() in _SKIP_FOLDER_NAMES:
            continue
        folders.append(name)
    return folders


def _find_code_once(
    server: Any,
    *,
    subject_keyword: str,
    sender_keyword: str,
    requested_after: datetime,
    code_digits: int,
    diagnostics: dict[str, object] | None = None,
    now: datetime | None = None,
) -> str | None:
    best_dt: datetime | None = None
    best_folder: str | None = None
    best_uid: Any = None
    latest_candidate_dt: datetime | None = None
    latest_candidate_folder: str | None = None
    latest_candidate_uid: Any = None
    msgs_found = 0
    for folder in _candidate_folders(server):
        try:
            server.select_folder(folder, readonly=True)
            uids = server.search(["SINCE", requested_after.date()])
        except Exception:
            continue
        if not uids:
            continue
        try:
            meta = server.fetch(uids, ["INTERNALDATE", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)]"])
        except Exception:
            continue
        for uid, data in meta.items():
            internal = _to_utc(data.get(b"INTERNALDATE"))
            if internal is None:
                continue
            headers = data.get(b"BODY[HEADER.FIELDS (SUBJECT FROM)]", b"")
            subject = _decode_header_value(headers, "Subject")
            sender = _decode_header_value(headers, "From")
            if subject_keyword and subject_keyword.casefold() not in subject.casefold():
                continue
            if sender_keyword and sender_keyword.casefold() not in sender.casefold():
                continue
            msgs_found += 1
            if latest_candidate_dt is None or internal > latest_candidate_dt:
                latest_candidate_dt = internal
                latest_candidate_folder = folder
                latest_candidate_uid = uid
            if internal < requested_after:
                continue
            if best_dt is None or internal > best_dt:
                best_dt, best_folder, best_uid = internal, folder, uid

    if best_folder is None:
        code_found = False
        if diagnostics is not None:
            code_found = _candidate_has_code(
                server,
                folder=latest_candidate_folder,
                uid=latest_candidate_uid,
                code_digits=code_digits,
            )
        _record_diagnostics(
            diagnostics,
            msgs_found=msgs_found,
            code_found=code_found,
            latest_code_dt=latest_candidate_dt if code_found else None,
            requested_after=requested_after,
            now=now,
        )
        return None

    server.select_folder(best_folder, readonly=True)
    raw = server.fetch([best_uid], ["BODY.PEEK[]"])[best_uid][b"BODY[]"]
    body = _message_text(email.message_from_bytes(raw))
    code = extract_verification_code(body, code_digits=code_digits)
    if code is not None:
        _record_diagnostics(
            diagnostics,
            msgs_found=msgs_found,
            code_found=True,
            latest_code_dt=best_dt,
            requested_after=requested_after,
            now=now,
        )
        return code
    _record_diagnostics(
        diagnostics,
        msgs_found=msgs_found,
        code_found=False,
        latest_code_dt=None,
        requested_after=requested_after,
        now=now,
    )
    raise Imap2faError("최신 인증 메일에서 인증번호를 추출하지 못했습니다(자리수/형식 확인).")


def _candidate_has_code(
    server: Any,
    *,
    folder: str | None,
    uid: Any,
    code_digits: int,
) -> bool:
    if folder is None or uid is None:
        return False
    try:
        server.select_folder(folder, readonly=True)
        raw = server.fetch([uid], ["BODY.PEEK[]"])[uid][b"BODY[]"]
        body = _message_text(email.message_from_bytes(raw))
        return extract_verification_code(body, code_digits=code_digits) is not None
    except Exception:
        return False


def _record_diagnostics(
    diagnostics: dict[str, object] | None,
    *,
    msgs_found: int,
    code_found: bool,
    latest_code_dt: datetime | None,
    requested_after: datetime,
    now: datetime | None,
) -> None:
    if diagnostics is None:
        return
    latest_code_age_s: int | None = None
    if latest_code_dt is not None and now is not None:
        latest_code_age_s = max(0, int((now - latest_code_dt).total_seconds()))
    diagnostics.update(
        {
            "code_found": bool(code_found),
            "msgs_found": int(msgs_found),
            "latest_code_age_s": latest_code_age_s,
            "within_poll_window": bool(
                code_found
                and latest_code_dt is not None
                and latest_code_dt >= requested_after
            ),
        }
    )


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

    app_password = re.sub(r"\s+", "", app_password or "")
    server = IMAPClient(host, port=port, ssl=True, use_uid=True)
    server.normalise_times = False
    try:
        server.login(email_address, app_password)
    except Exception as exc:
        raise ImapAuthError(
            "IMAP 로그인 실패. 메일의 IMAP 사용 설정과 앱 비밀번호를 확인하세요.",
            reason=classify_imap_auth_failure(exc),
        ) from exc
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
