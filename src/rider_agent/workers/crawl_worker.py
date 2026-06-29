"""CRAWL_BAEMIN / CRAWL_COUPANG executor.

The worker reuses ``rider_crawl`` through the Agent reuse seam and returns the
snapshot-shaped ``result_json`` expected by server ingest. Browser/profile,
auth, and crawl calls are injectable so tests do not open browsers or networks.
"""

from __future__ import annotations

import dataclasses
import threading
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
    JOB_STATUS_SUCCESS,
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
AUTH_STATE_CENTER_MISMATCH = "CENTER_MISMATCH"
# heartbeat 진단 투영에서 인증 상태를 단정할 수 없을 때의 fail-safe 요약(parser/CDP/timeout 등).
AUTH_STATE_UNKNOWN = "UNKNOWN"

ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"
ERROR_USER_ACTION_PENDING = "USER_ACTION_PENDING"
ERROR_PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
ERROR_CDP_UNREACHABLE = "CDP_UNREACHABLE"
ERROR_PARSER_MISSING_DATA = "PARSER_MISSING_DATA"
ERROR_CENTER_MISMATCH = "CENTER_MISMATCH"
ERROR_TARGET_VALIDATION_FAILURE = "TARGET_VALIDATION_FAILURE"
ERROR_CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
ERROR_CRAWL_FAILURE = "CRAWL_FAILURE"
ERROR_PLAINTEXT_SECRET_NOT_ALLOWED = "PLAINTEXT_SECRET_NOT_ALLOWED"
ERROR_SECRET_REF_UNRESOLVED = "SECRET_REF_UNRESOLVED"
# payload TTL 이 지난 stale job 을 profile/browser 준비 전에 거르는 defensive 가드(server
# preflight 가 우회돼도 브라우저를 열지 않게 — Task 5 defense-in-depth). secret 0.
ERROR_PAYLOAD_EXPIRED = "PAYLOAD_EXPIRED"
REASON_PAYLOAD_EXPIRED = "payload_expired"

QUALITY_OK = "OK"
SCHEMA_VERSION = 1
_MAX_DIAGNOSTIC_MESSAGE_LENGTH = 800
_BAEMIN_PAGE_TIMEOUT_GRACE_SECONDS = 5.0


class SecretRefUnresolved(RuntimeError):
    pass


@dataclass(frozen=True)
class CrawlJobPayload:
    target_id: str
    tenant_id: str
    platform: str
    platform_account_id: str
    primary_url: str
    expected_display_name: str
    browser_profile_ref: str
    timeout_seconds: float
    parser_version: str
    login_id_ref: str = ""
    login_password_ref: str = ""
    coupang_login_id_ref: str = ""
    coupang_login_password_ref: str = ""
    verification_email_address_ref: str = ""
    verification_email_app_password_ref: str = ""
    verification_email_subject_keyword: str = ""
    verification_email_sender_keyword: str = ""
    coupang_auto_email_2fa_enabled: bool = False
    expires_at: str = ""
    target_external_id: str = ""


class CrawlWorker:
    """Execute crawl jobs by calling existing crawler/parser seams."""

    def __init__(
        self,
        *,
        profile_manager: Any = None,
        crawl_snapshot: Callable[..., Any] | None = None,
        auth_probe: Callable[[ClaimedJob, AppConfig], str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
        profile_idle_ttl_seconds: float | None = None,
        log: Callable[[str], None] | None = None,
        process_boundary_enabled: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._profile_manager = profile_manager
        self._uses_default_crawl_snapshot = crawl_snapshot is None
        self._crawl_snapshot = crawl_snapshot or reuse_crawl_snapshot
        self._auth_probe = auth_probe
        self._secret_resolver = secret_resolver
        self._profile_idle_ttl_seconds = profile_idle_ttl_seconds
        self._log = log
        self._process_boundary_enabled = process_boundary_enabled
        self._now = now or (lambda: datetime.now(timezone.utc))

    def execute(self, job: ClaimedJob) -> JobResult:
        """Execute a supported crawl job or return unsupported for other types."""

        if job.type not in {CAPABILITY_CRAWL_BAEMIN, CAPABILITY_CRAWL_COUPANG}:
            return self.make_unsupported(job)

        payload = payload_from_job(job)
        # 지원 crawl job 의 최종 JobResult 를 한 곳에서 받아 profile 진단을 기록한다(process
        # boundary 결과·_execute_payload 결과 모두 이 경로를 통과). timeout 은 release 순서가
        # 중요해 _cleanup_after_timeout 안에서 release 전에 따로 기록하므로, 여기서의 재기록은
        # assignment 가 이미 사라진 no-op 이다(중복 기록이 결과를 바꾸지 않는다).
        result = self._execute_supported(job, payload)
        self._record_crawl_diagnostic_from_result(payload, result)
        return result

    def _execute_supported(self, job: ClaimedJob, payload: CrawlJobPayload) -> JobResult:
        raw_payload = _raw_payload(job)
        # payload TTL 이 지났으면 profile/browser 를 준비하기 전에 fail-fast(server preflight 가
        # 우회돼도 stale job 이 브라우저를 열지 않게 — Task 5 defense-in-depth).
        if _payload_expired(payload.expires_at, now=self._now()):
            return make_failure_result(
                ERROR_PAYLOAD_EXPIRED,
                "crawl payload expired before profile prepare",
                result_json={
                    "target_id": payload.target_id,
                    "platform": payload.platform,
                    "reason": REASON_PAYLOAD_EXPIRED,
                },
            )
        # 옵션 B: 평문 자격증명을 허용하므로 bare-key 평문 거부 가드를 두지 않는다.
        if payload.timeout_seconds > 0:
            if self._should_use_process_boundary():
                from rider_agent.workers.crawl_process import run_crawl_in_subprocess

                try:
                    process_job = self._prepare_process_boundary_job(
                        job, raw_payload, payload
                    )
                except BrowserActionRequiredError:
                    return _auth_failure(payload, AUTH_STATE_AUTH_REQUIRED)
                except CdpUnavailableError:
                    return make_failure_result(
                        ERROR_CDP_UNREACHABLE,
                        "CDP endpoint unavailable",
                        result_json={"target_id": payload.target_id, "platform": payload.platform},
                    )
                except BrowserLaunchError as exc:
                    return _profile_unavailable_failure(payload, exc)
                except SecretRefUnresolved:
                    return make_failure_result(
                        ERROR_SECRET_REF_UNRESOLVED,
                        "secret ref could not be resolved",
                        result_json={"target_id": payload.target_id, "platform": payload.platform},
                    )
                except Exception as exc:  # noqa: BLE001 - process setup is fail-closed.
                    if _is_target_validation_error(exc):
                        return _target_validation_failure(payload, exc)
                    if _is_missing_data_error(exc):
                        return make_failure_result(
                            ERROR_PARSER_MISSING_DATA,
                            "required crawl data missing",
                            result_json={"target_id": payload.target_id, "platform": payload.platform},
                        )
                    return _crawl_failure(payload, exc)

                timeout_cleanup_ran = False

                def timeout_cleanup() -> None:
                    nonlocal timeout_cleanup_ran
                    timeout_cleanup_ran = True
                    self._cleanup_after_timeout(payload)

                result = run_crawl_in_subprocess(
                    process_job,
                    timeout_seconds=float(payload.timeout_seconds),
                    target_id=payload.target_id,
                    platform=payload.platform,
                    cleanup=timeout_cleanup if self._profile_manager is not None else None,
                )
                if not timeout_cleanup_ran:
                    self._cleanup_profiles()
                return result
            return _run_with_timeout(
                lambda: self._execute_payload(job, raw_payload, payload),
                timeout_seconds=float(payload.timeout_seconds),
                payload=payload,
                cleanup=lambda: self._cleanup_after_timeout(payload),
            )
        return self._execute_payload(job, raw_payload, payload)

    def _record_crawl_diagnostic_from_result(
        self, payload: CrawlJobPayload, result: JobResult
    ) -> None:
        """최종 JobResult 를 기준으로 기존 profile assignment 에 진단값을 기록한다.

        auth_state 는 result_json 이 명시한 값(성공/인증필요/center mismatch)을 그대로 쓰고,
        없으면 ``UNKNOWN`` 으로 둔다(parser/CDP/timeout 등을 인증 문제로 단정하지 않는다).
        last_error_code 는 실패 시 result.error_code(진단 힌트)다. 진단 기록 실패는 흡수하며
        job 결과를 바꾸지 않는다.
        """

        if self._profile_manager is None:
            return
        record = getattr(self._profile_manager, "record_profile_diagnostic", None)
        if not callable(record):
            return
        result_json = result.result_json if isinstance(result.result_json, dict) else {}
        auth_state = result_json.get("auth_state")
        if not isinstance(auth_state, str) or not auth_state:
            auth_state = (
                AUTH_STATE_ACTIVE
                if result.status == JOB_STATUS_SUCCESS
                else AUTH_STATE_UNKNOWN
            )
        last_error_code = (
            None if result.status == JOB_STATUS_SUCCESS else result.error_code
        )
        try:
            record(
                payload.tenant_id,
                payload.target_id,
                auth_state=auth_state,
                last_error_code=last_error_code,
                last_probe_at=_iso_utc(self._now()),
            )
        except Exception:  # noqa: BLE001 - diagnostic recording must not change job result.
            if self._log is not None:
                self._log(redact("profile diagnostic record failed"))

    def _should_use_process_boundary(self) -> bool:
        return (
            self._process_boundary_enabled
            and self._uses_default_crawl_snapshot
            and self._auth_probe is None
        )

    def _prepare_process_boundary_job(
        self,
        job: ClaimedJob,
        raw_payload: dict[str, Any],
        payload: CrawlJobPayload,
    ) -> ClaimedJob:
        child_payload = dict(raw_payload)
        if self._profile_manager is not None:
            config = self._prepare_config(job, payload)
            child_payload["cdp_url"] = str(config.cdp_url)
            child_payload["browser_user_data_dir"] = str(config.browser_user_data_dir)
        return dataclasses.replace(job, payload=child_payload)

    def _execute_payload(
        self,
        job: ClaimedJob,
        raw_payload: dict[str, Any],
        payload: CrawlJobPayload,
    ) -> JobResult:
        # 옵션 B: 평문 자격증명 허용 — bare-key 평문 거부 가드 없음.
        try:
            if payload.platform not in {"baemin", "coupang"}:
                return make_failure_result(
                    ERROR_CRAWL_FAILURE,
                    "unsupported crawl platform",
                    result_json={"target_id": payload.target_id, "platform": payload.platform},
                )
            config = self._prepare_config(job, payload)
            auth_state = self._auth_probe(job, config) if self._auth_probe else AUTH_STATE_ACTIVE
            if auth_state != AUTH_STATE_ACTIVE:
                return _auth_failure(payload, auth_state)

            raw = self._crawl_snapshot(config, platform_name=payload.platform)
            mismatch = _display_name_mismatch(raw, payload.expected_display_name)
            if mismatch:
                return make_failure_result(
                    ERROR_TARGET_VALIDATION_FAILURE,
                    "crawl result display name mismatch",
                    result_json={
                        "target_id": payload.target_id,
                        "platform": payload.platform,
                        "auth_state": AUTH_STATE_CENTER_MISMATCH,
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
        except BrowserLaunchError as exc:
            return _profile_unavailable_failure(payload, exc)
        except SecretRefUnresolved:
            return make_failure_result(
                ERROR_SECRET_REF_UNRESOLVED,
                "secret ref could not be resolved",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        except TimeoutError:
            return make_failure_result(
                ERROR_CRAWL_TIMEOUT,
                "crawl timed out",
                result_json={"target_id": payload.target_id, "platform": payload.platform},
            )
        except Exception as exc:  # noqa: BLE001 - classify known parser failures, fail closed otherwise.
            if _is_target_validation_error(exc):
                return _target_validation_failure(payload, exc)
            if _is_missing_data_error(exc):
                return make_failure_result(
                    ERROR_PARSER_MISSING_DATA,
                    "required crawl data missing",
                    result_json={"target_id": payload.target_id, "platform": payload.platform},
                )
            return _crawl_failure(payload, exc)
        finally:
            self._cleanup_profiles()

    def _cleanup_profiles(self) -> None:
        if self._profile_idle_ttl_seconds is None or self._profile_manager is None:
            return
        cleanup = getattr(self._profile_manager, "cleanup_idle_profiles", None)
        if cleanup is None:
            return
        try:
            cleanup(max_idle_seconds=self._profile_idle_ttl_seconds)
        except Exception:  # noqa: BLE001 - cleanup must not change job result.
            if self._log is not None:
                self._log(redact("profile cleanup failed"))

    def _cleanup_after_timeout(self, payload: CrawlJobPayload) -> None:
        if self._profile_manager is not None:
            # release 가 assignment 를 없애므로 timeout 진단은 release 전에 기록한다(이후
            # 일반 result 기반 기록은 assignment 가 사라져 no-op 이 된다).
            record = getattr(self._profile_manager, "record_profile_diagnostic", None)
            if callable(record):
                try:
                    record(
                        payload.tenant_id,
                        payload.target_id,
                        auth_state=AUTH_STATE_UNKNOWN,
                        last_error_code=ERROR_CRAWL_TIMEOUT,
                        last_probe_at=_iso_utc(self._now()),
                    )
                except Exception:  # noqa: BLE001 - diagnostic must not change timeout result.
                    if self._log is not None:
                        self._log(redact("profile timeout diagnostic record failed"))
            release = getattr(self._profile_manager, "release", None)
            if callable(release):
                try:
                    release(payload.tenant_id, payload.target_id)
                except Exception:  # noqa: BLE001 - cleanup must not change timeout result.
                    if self._log is not None:
                        self._log(redact("profile release after timeout failed"))
        self._cleanup_profiles()

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
            raw = _raw_payload(job)
            return build_config(
                tenant_id=payload.tenant_id,
                target_id=payload.target_id,
                cdp_url=str(raw.get("cdp_url") or "http://127.0.0.1:9222"),
                user_data_dir=Path(
                    raw.get("browser_user_data_dir")
                    or (Path("runtime") / "agent-browser-profiles" / payload.target_id)
                ),
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


def _run_with_timeout(
    call: Callable[[], JobResult],
    *,
    timeout_seconds: float,
    payload: CrawlJobPayload,
    cleanup: Callable[[], None],
) -> JobResult:
    result: dict[str, JobResult] = {}
    error: dict[str, BaseException] = {}
    done = threading.Event()

    def _target() -> None:
        try:
            result["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raise in parent after join.
            error["value"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_target, name="crawl-worker-timeout", daemon=True)
    thread.start()
    if not done.wait(timeout_seconds):
        cleanup()
        return make_failure_result(
            ERROR_CRAWL_TIMEOUT,
            "crawl timed out",
            result_json={"target_id": payload.target_id, "platform": payload.platform},
        )
    if error:
        raise error["value"]
    return result["value"]


def payload_from_job(job: ClaimedJob) -> CrawlJobPayload:
    raw = _raw_payload(job)
    platform = str(raw.get("platform") or _platform_from_type(job.type)).strip().casefold()
    target_id = str(raw.get("target_id") or job.target_id or "").strip()
    tenant_id = str(raw.get("tenant_id") or "").strip()
    timeout_seconds = _positive_float(raw.get("timeout_seconds"), default=60.0)
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
        login_id_ref=_text(raw, "login_id_ref", "baemin_login_id_ref"),
        login_password_ref=_text(raw, "login_password_ref", "baemin_login_password_ref"),
        coupang_login_id_ref=_text(raw, "coupang_login_id_ref"),
        coupang_login_password_ref=_text(raw, "coupang_login_password_ref"),
        verification_email_address_ref=_text(raw, "verification_email_address_ref"),
        verification_email_app_password_ref=_text(
            raw, "verification_email_app_password_ref"
        ),
        verification_email_subject_keyword=_text(raw, "verification_email_subject_keyword"),
        verification_email_sender_keyword=_text(raw, "verification_email_sender_keyword"),
        coupang_auto_email_2fa_enabled=(
            _truthy(raw.get("coupang_auto_email_2fa_enabled"))
            if "coupang_auto_email_2fa_enabled" in raw
            else False
        ),
        expires_at=str(raw.get("expires_at") or "").strip(),
        target_external_id=_text(
            raw, "external_id", "target_external_id", "baemin_center_id"
        ),
    )


def _payload_expired(expires_at: str, *, now: datetime) -> bool:
    """payload ``expires_at``(ISO 8601 ``…Z``) 가 지났는가(없거나 파싱 실패면 False — 보수적)."""

    text = str(expires_at or "").strip()
    if not text:
        return False
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now >= parsed


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


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
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


def _resolve_secret_ref(
    ref: str,
    *,
    secret_resolver: Callable[[str], str | None] | None,
) -> str:
    value = ref.strip()
    if not value:
        return ""
    # 운영 결정(옵션 B): 핸들 모양(env:/dpapi:/vault: 또는 "://")이 아니면 평문 실값으로 보고
    # 그대로 사용한다. 웹앱에 입력한 쿠팡 ID/PW/이메일이 페이로드에 평문으로 흘러 들어온다.
    if not _looks_like_secret_ref(value):
        return value
    if secret_resolver is not None:
        resolved = _safe_resolve(secret_resolver, value)
        if resolved:
            return resolved
    resolved = _safe_resolve(_default_secret_resolver, value)
    if resolved:
        return resolved
    raise SecretRefUnresolved(value)


def _looks_like_secret_ref(value: str) -> bool:
    text = str(value or "").strip().casefold()
    return "://" in text or text.startswith(("env:", "dpapi:", "vault:", "local:"))


def _safe_resolve(resolver: Callable[[str], str | None], ref: str) -> str:
    try:
        return str(resolver(ref) or "").strip()
    except Exception:
        return ""


def _default_secret_resolver(ref: str) -> str | None:
    try:
        from rider_agent.secure_store import DpapiSecretStore, default_secret_store_path

        resolved = DpapiSecretStore(default_secret_store_path()).resolve(ref)
        if resolved:
            return resolved
    except Exception:
        pass
    try:
        from rider_agent.secure_store import default_secret_store_path
        from rider_crawl.secret_store import default_secret_store

        return default_secret_store(default_secret_store_path()).resolve(ref)
    except Exception:
        return None


def _page_timeout_ms(payload: CrawlJobPayload) -> int:
    timeout_ms = max(1_000, int(float(payload.timeout_seconds) * 1000))
    if str(payload.platform or "").strip().casefold() != "baemin":
        return timeout_ms
    grace_ms = int(_BAEMIN_PAGE_TIMEOUT_GRACE_SECONDS * 1000)
    if timeout_ms <= grace_ms + 1_000:
        return timeout_ms
    return timeout_ms - grace_ms


def _build_config(
    payload: CrawlJobPayload,
    *,
    cdp_url: str,
    user_data_dir: Path,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> AppConfig:
    coupang_login_id = _resolve_secret_ref(
        payload.coupang_login_id_ref, secret_resolver=secret_resolver
    )
    coupang_login_password = _resolve_secret_ref(
        payload.coupang_login_password_ref, secret_resolver=secret_resolver
    )
    baemin_login_id = _resolve_secret_ref(
        payload.login_id_ref, secret_resolver=secret_resolver
    )
    baemin_login_password = _resolve_secret_ref(
        payload.login_password_ref, secret_resolver=secret_resolver
    )
    verification_email_address = _resolve_secret_ref(
        payload.verification_email_address_ref, secret_resolver=secret_resolver
    )
    verification_email_app_password = _resolve_secret_ref(
        payload.verification_email_app_password_ref,
        secret_resolver=secret_resolver,
    )
    is_coupang = str(payload.platform or "").strip().casefold() == "coupang"
    enable_email_2fa = bool(
        is_coupang
        and payload.coupang_auto_email_2fa_enabled
        and coupang_login_id
        and coupang_login_password
        and verification_email_address
        and verification_email_app_password
    )
    return AppConfig(
        coupang_eats_url=payload.primary_url,
        baemin_center_name=payload.expected_display_name,
        baemin_center_id=payload.target_external_id or payload.target_id,
        browser_mode="cdp",
        cdp_url=cdp_url,
        browser_user_data_dir=user_data_dir,
        headless=False,
        kakao_chat_name="",
        log_dir=Path("logs"),
        send_enabled=False,
        send_only_on_change=False,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=max(60, int(payload.timeout_seconds * 2)),
        page_timeout_seconds=_page_timeout_ms(payload),
        messenger_name="telegram",
        crawl_name=payload.expected_display_name or payload.target_id,
        state_subdir=payload.target_id,
        platform_name=payload.platform,
        coupang_auto_email_2fa_enabled=enable_email_2fa,
        baemin_login_id=baemin_login_id,
        baemin_login_password=baemin_login_password,
        coupang_login_id=coupang_login_id,
        coupang_login_password=coupang_login_password,
        verification_email_address=verification_email_address,
        verification_email_mailbox_lock_id=(
            payload.verification_email_address_ref or verification_email_address
        ),
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


def _target_validation_failure(payload: CrawlJobPayload, exc: BaseException) -> JobResult:
    return make_failure_result(
        ERROR_TARGET_VALIDATION_FAILURE,
        _exception_summary(exc),
        result_json={
            "target_id": payload.target_id,
            "platform": payload.platform,
            "auth_state": AUTH_STATE_CENTER_MISMATCH,
            "mismatch": ERROR_CENTER_MISMATCH,
            "diagnostics": {"agent_error": _exception_diagnostics(exc)},
        },
    )


def _profile_unavailable_failure(payload: CrawlJobPayload, exc: BaseException) -> JobResult:
    return make_failure_result(
        ERROR_PROFILE_UNAVAILABLE,
        "browser profile unavailable",
        result_json={
            "target_id": payload.target_id,
            "platform": payload.platform,
            "diagnostics": {
                "agent_error": _profile_unavailable_diagnostics(exc),
            },
        },
    )


def _profile_unavailable_diagnostics(exc: BaseException) -> dict[str, str]:
    root = _root_cause(exc)
    return {
        "type": root.__class__.__name__,
        "reason": _profile_unavailable_reason(exc, root),
    }


def _root_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while current.__cause__ is not None and id(current.__cause__) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def _profile_unavailable_reason(exc: BaseException, root: BaseException) -> str:
    text_parts = [str(exc), str(root)]
    seen: set[int] = set()
    current = exc.__cause__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text_parts.append(str(current))
        current = current.__cause__
    text = "\n".join(text_parts).casefold()
    if "cdp 디버깅 포트는 열려 있지" in text and "이미 실행 중" in text:
        return "chrome_profile_in_use_without_cdp"
    if "cdp 주소가 이미 사용 중" in text:
        return "cdp_endpoint_in_use"
    if "chrome cdp 포트가 준비되지" in text:
        return "chrome_cdp_not_ready"
    if "chrome 실행 실패" in text:
        return "chrome_launch_failed"
    if "cdp 주소에는 포트" in text:
        return "cdp_url_missing_port"
    if "cdp 주소는 ipv4 로컬 주소" in text:
        return "remote_cdp_rejected"
    if root is not exc:
        return "profile_prepare_exception"
    return "browser_launch_error"


def _crawl_failure(payload: CrawlJobPayload, exc: BaseException) -> JobResult:
    return make_failure_result(
        ERROR_CRAWL_FAILURE,
        _exception_summary(exc),
        result_json={
            "target_id": payload.target_id,
            "platform": payload.platform,
            "diagnostics": {"agent_error": _exception_diagnostics(exc)},
        },
    )


def _exception_summary(exc: BaseException) -> str:
    text = _redacted_exception_text(exc)
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


def _exception_diagnostics(exc: BaseException) -> dict[str, str]:
    diagnostics = {"type": exc.__class__.__name__}
    text = _redacted_exception_text(exc)
    if text:
        diagnostics["message_redacted"] = text
    return diagnostics


def _redacted_exception_text(exc: BaseException) -> str:
    text = redact(str(exc)).strip()
    if len(text) <= _MAX_DIAGNOSTIC_MESSAGE_LENGTH:
        return text
    return text[:_MAX_DIAGNOSTIC_MESSAGE_LENGTH] + "...<truncated>"


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
        "auth_state": AUTH_STATE_ACTIVE,
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


def _is_target_validation_error(exc: BaseException) -> bool:
    if exc.__class__.__name__ == "CoupangCenterValidationError":
        return True
    return "센터 검증 실패" in str(exc)


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
