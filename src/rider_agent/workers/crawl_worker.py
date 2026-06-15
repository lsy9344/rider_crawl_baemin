"""CRAWL_BAEMIN / CRAWL_COUPANG executor.

The worker reuses ``rider_crawl`` through the Agent reuse seam and returns the
snapshot-shaped ``result_json`` expected by server ingest. Browser/profile,
auth, and crawl calls are injectable so tests do not open browsers or networks.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rider_crawl.config import (
    DEFAULT_EMAIL_2FA_SENDER_KEYWORD,
    DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD,
    AppConfig,
)
from rider_crawl.redaction import redact

from rider_agent.heartbeat import CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG
from rider_agent.job_loop import (
    ClaimedJob,
    JobResult,
    default_execute_job,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import (
    BrowserActionRequiredError,
    BrowserLaunchError,
    CdpUnavailableError,
    crawl_snapshot as reuse_crawl_snapshot,
)

AUTH_STATE_ACTIVE = "ACTIVE"
AUTH_STATE_AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_STATE_USER_ACTION_PENDING = "USER_ACTION_PENDING"

ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"
ERROR_USER_ACTION_PENDING = "USER_ACTION_PENDING"
ERROR_PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
ERROR_CDP_UNREACHABLE = "CDP_UNREACHABLE"
ERROR_PARSER_MISSING_DATA = "PARSER_MISSING_DATA"
ERROR_CENTER_MISMATCH = "CENTER_MISMATCH"
ERROR_CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
ERROR_CRAWL_FAILURE = "CRAWL_FAILURE"
ERROR_PLAINTEXT_SECRET_NOT_ALLOWED = "PLAINTEXT_SECRET_NOT_ALLOWED"

QUALITY_OK = "OK"
SCHEMA_VERSION = 1
_PLAINTEXT_SECRET_KEYS = frozenset(
    {
        "coupang_login_password",
        "verification_email_app_password",
    }
)


@dataclass(frozen=True)
class CrawlJobPayload:
    target_id: str
    tenant_id: str
    platform: str
    platform_account_id: str
    primary_url: str
    expected_display_name: str
    browser_profile_ref: str
    timeout_seconds: int
    parser_version: str
    username_ref: str = ""
    password_ref: str = ""
    coupang_login_id: str = ""
    coupang_login_password: str = ""
    verification_email_address_ref: str = ""
    verification_email_app_password_ref: str = ""
    verification_email_address: str = ""
    verification_email_app_password: str = ""
    verification_email_subject_keyword: str = ""
    verification_email_sender_keyword: str = ""
    coupang_auto_email_2fa_enabled: bool = False


class CrawlWorker:
    """Execute crawl jobs by calling existing crawler/parser seams."""

    def __init__(
        self,
        *,
        profile_manager: Any = None,
        crawl_snapshot: Callable[..., Any] | None = None,
        auth_probe: Callable[[ClaimedJob, AppConfig], str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._profile_manager = profile_manager
        self._crawl_snapshot = crawl_snapshot or reuse_crawl_snapshot
        self._auth_probe = auth_probe
        self._secret_resolver = secret_resolver
        self._log = log

    def execute(self, job: ClaimedJob) -> JobResult:
        """Execute a supported crawl job or return unsupported for other types."""

        if job.type not in {CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG}:
            return self.make_unsupported(job)

        raw_payload = _raw_payload(job)
        payload = payload_from_job(job)
        if _plaintext_secret_keys(raw_payload):
            return make_failure_result(
                ERROR_PLAINTEXT_SECRET_NOT_ALLOWED,
                "crawl job plaintext secrets are not allowed",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        if payload.platform not in {"baemin", "coupang"}:
            return make_failure_result(
                ERROR_CRAWL_FAILURE,
                "unsupported crawl platform",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )

        try:
            config = self._prepare_config(job, payload)
            auth_state = self._auth_probe(job, config) if self._auth_probe else AUTH_STATE_ACTIVE
            if auth_state != AUTH_STATE_ACTIVE:
                return _auth_failure(payload, auth_state)

            raw = self._crawl_snapshot(config, platform_name=payload.platform)
            mismatch = _display_name_mismatch(raw, payload.expected_display_name)
            if mismatch:
                return make_failure_result(
                    ERROR_CENTER_MISMATCH,
                    "crawl result display name mismatch",
                    result_json={
                        "target_id": payload.target_id,
                        "platform": payload.platform,
                        "mismatch": ERROR_CENTER_MISMATCH,
                    },
                )
            return make_success_result(result_json=_snapshot_payload(payload, raw))
        except BrowserActionRequiredError:
            return _auth_failure(payload, AUTH_STATE_AUTH_REQUIRED)
        except CdpUnavailableError:
            return make_failure_result(
                ERROR_CDP_UNREACHABLE,
                "CDP endpoint unavailable",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        except BrowserLaunchError:
            return make_failure_result(
                ERROR_PROFILE_UNAVAILABLE,
                "browser profile unavailable",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        except TimeoutError:
            return make_failure_result(
                ERROR_CRAWL_TIMEOUT,
                "crawl timed out",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        except Exception as exc:  # noqa: BLE001 - classify known parser failures, fail closed otherwise.
            if _is_missing_data_error(exc):
                return make_failure_result(
                    ERROR_PARSER_MISSING_DATA,
                    "required crawl data missing",
                    result_json={"target_id": payload.target_id, "platform": payload.platform},
                )
            return make_failure_result(
                ERROR_CRAWL_FAILURE,
                "crawl failed",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )

    def make_unsupported(self, job: ClaimedJob) -> JobResult:
        return default_execute_job(job)

    def _prepare_config(self, job: ClaimedJob, payload: CrawlJobPayload) -> AppConfig:
        def build_config(
            *,
            tenant_id: str,
            target_id: str,
            cdp_url: str,
            user_data_dir: Path,
        ) -> AppConfig:
            return _build_config(
                payload,
                cdp_url=cdp_url,
                user_data_dir=user_data_dir,
                secret_resolver=self._secret_resolver,
            )

        if self._profile_manager is None:
            return build_config(
                tenant_id=payload.tenant_id,
                target_id=payload.target_id,
                cdp_url=str(_raw_payload(job).get("cdp_url") or "http://127.0.0.1:9222"),
                user_data_dir=Path("runtime") / "agent-browser-profiles" / payload.target_id,
            )

        try:
            assignment = self._profile_manager.ensure_profile(
                payload.tenant_id,
                payload.target_id,
                build_config=build_config,
            )
        except (BrowserLaunchError, CdpUnavailableError, BrowserActionRequiredError):
            raise
        except Exception as exc:  # noqa: BLE001 - raw profile paths may be in exception text.
            if self._log is not None:
                self._log(redact("profile unavailable"))
            raise BrowserLaunchError("browser profile unavailable") from exc

        return build_config(
            tenant_id=payload.tenant_id,
            target_id=payload.target_id,
            cdp_url=getattr(assignment, "cdp_url", "http://127.0.0.1:9222"),
            user_data_dir=getattr(
                assignment,
                "profile_dir",
                Path("runtime") / "agent-browser-profiles" / payload.target_id,
            ),
        )


def build_execute_job(
    *,
    crawl_worker: CrawlWorker,
    fallback: Callable[[ClaimedJob], JobResult] = default_execute_job,
) -> Callable[[ClaimedJob], JobResult]:
    """Route crawl job types to ``crawl_worker`` and all other jobs to fallback."""

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type in {CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG}:
            return crawl_worker.execute(job)
        return fallback(job)

    return _execute


def payload_from_job(job: ClaimedJob) -> CrawlJobPayload:
    raw = _raw_payload(job)
    platform = str(raw.get("platform") or _platform_from_type(job.type)).strip().casefold()
    target_id = str(raw.get("target_id") or job.target_id or "").strip()
    tenant_id = str(raw.get("tenant_id") or "").strip()
    timeout_seconds = _positive_int(raw.get("timeout_seconds"), default=60)
    return CrawlJobPayload(
        target_id=target_id,
        tenant_id=tenant_id,
        platform=platform,
        platform_account_id=str(raw.get("platform_account_id") or "").strip(),
        primary_url=str(raw.get("primary_url") or raw.get("url") or "").strip(),
        expected_display_name=str(raw.get("expected_display_name") or "").strip(),
        browser_profile_ref=str(raw.get("browser_profile_ref") or "").strip(),
        timeout_seconds=timeout_seconds,
        parser_version=str(raw.get("parser_version") or f"{platform}-v1").strip(),
        username_ref=_text(raw, "username_ref", "coupang_login_id_ref"),
        password_ref=_text(raw, "password_ref", "coupang_login_password_ref"),
        coupang_login_id=_text(raw, "coupang_login_id"),
        coupang_login_password=_text(raw, "coupang_login_password"),
        verification_email_address_ref=_text(raw, "verification_email_address_ref"),
        verification_email_app_password_ref=_text(
            raw, "verification_email_app_password_ref"
        ),
        verification_email_address=_text(raw, "verification_email_address"),
        verification_email_app_password=_text(raw, "verification_email_app_password"),
        verification_email_subject_keyword=_text(raw, "verification_email_subject_keyword"),
        verification_email_sender_keyword=_text(raw, "verification_email_sender_keyword"),
        coupang_auto_email_2fa_enabled=_truthy(
            raw.get("coupang_auto_email_2fa_enabled")
        ),
    )


def _raw_payload(job: ClaimedJob) -> dict[str, Any]:
    raw = dict(job.payload or {})
    nested = raw.get("payload")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update(raw)
        raw = merged
    return raw


def _platform_from_type(job_type: str) -> str:
    if job_type == CAPABILITY_CRAWL_COUPANG:
        return "coupang"
    return "baemin"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on"}


def _plaintext_secret_keys(raw: dict[str, Any]) -> list[str]:
    return [
        key
        for key in sorted(_PLAINTEXT_SECRET_KEYS)
        if str(raw.get(key) or "").strip()
    ]


def _resolved_or_value(
    *,
    value: str,
    ref: str,
    secret_resolver: Callable[[str], str | None] | None,
) -> str:
    if value:
        return value
    if not ref or secret_resolver is None:
        return ""
    try:
        return str(secret_resolver(ref) or "").strip()
    except Exception:  # noqa: BLE001 - secret resolver failures fail closed.
        return ""


def _build_config(
    payload: CrawlJobPayload,
    *,
    cdp_url: str,
    user_data_dir: Path,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> AppConfig:
    coupang_login_id = _resolved_or_value(
        value=payload.coupang_login_id,
        ref=payload.username_ref,
        secret_resolver=secret_resolver,
    )
    coupang_login_password = _resolved_or_value(
        value=payload.coupang_login_password,
        ref=payload.password_ref,
        secret_resolver=secret_resolver,
    )
    verification_email_address = _resolved_or_value(
        value=payload.verification_email_address,
        ref=payload.verification_email_address_ref,
        secret_resolver=secret_resolver,
    )
    verification_email_app_password = _resolved_or_value(
        value=payload.verification_email_app_password,
        ref=payload.verification_email_app_password_ref,
        secret_resolver=secret_resolver,
    )
    enable_email_2fa = bool(
        payload.coupang_auto_email_2fa_enabled
        and coupang_login_id
        and coupang_login_password
        and verification_email_address
        and verification_email_app_password
    )
    return AppConfig(
        coupang_eats_url=payload.primary_url,
        baemin_center_name=payload.expected_display_name,
        baemin_center_id=payload.target_id,
        browser_mode="cdp",
        cdp_url=cdp_url,
        browser_user_data_dir=user_data_dir,
        headless=False,
        kakao_chat_name="",
        log_dir=Path("logs"),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=max(60, payload.timeout_seconds * 2),
        page_timeout_seconds=payload.timeout_seconds * 1000,
        messenger_name="telegram",
        crawl_name=payload.expected_display_name or payload.target_id,
        state_subdir=payload.target_id,
        platform_name=payload.platform,
        coupang_auto_email_2fa_enabled=enable_email_2fa,
        coupang_login_id=coupang_login_id,
        coupang_login_password=coupang_login_password,
        verification_email_address=verification_email_address,
        verification_email_app_password=verification_email_app_password,
        verification_email_subject_keyword=payload.verification_email_subject_keyword
        or DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD,
        verification_email_sender_keyword=payload.verification_email_sender_keyword
        or DEFAULT_EMAIL_2FA_SENDER_KEYWORD,
    )


def _auth_failure(payload: CrawlJobPayload, auth_state: str) -> JobResult:
    code = (
        ERROR_USER_ACTION_PENDING
        if auth_state == AUTH_STATE_USER_ACTION_PENDING
        else ERROR_AUTH_REQUIRED
    )
    return make_failure_result(
        code,
        "crawl authentication required",
        result_json={
            "target_id": payload.target_id,
            "platform": payload.platform,
            "auth_state": auth_state,
        },
    )


def _display_name_mismatch(raw: Any, expected: str) -> bool:
    if not expected:
        return False
    actual = getattr(raw, "center_name", "")
    if not actual and getattr(raw, "current_screen", None) is not None:
        actual = getattr(raw.current_screen, "center_name", "")
    return bool(actual and actual != expected)


def _snapshot_payload(payload: CrawlJobPayload, raw: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "snapshot",
        "target_id": payload.target_id,
        "tenant_id": payload.tenant_id,
        "platform_account_id": payload.platform_account_id,
        "platform": payload.platform,
        "collected_at": _iso_utc(datetime.now(timezone.utc)),
        "parser_version": payload.parser_version,
        "quality_state": QUALITY_OK,
        "normalized_json": _sanitize_snapshot_value(raw),
        "artifact_refs": [],
    }


def _iso_utc(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _is_missing_data_error(exc: BaseException) -> bool:
    return exc.__class__.__name__ == "MissingPerformanceDataError"


_SENSITIVE_SNAPSHOT_KEY_PARTS = frozenset(
    {
        "token",
        "secret",
        "password",
        "credential",
        "otp",
        "cookie",
        "html",
        "raw",
        "path",
        "screenshot",
        "clipboard",
    }
)


def _is_sensitive_snapshot_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_SNAPSHOT_KEY_PARTS)


def _sanitize_snapshot_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _sanitize_snapshot_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_snapshot_key(key):
                continue
            cleaned[key] = _sanitize_snapshot_value(raw_value)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_snapshot_value(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value
