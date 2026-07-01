"""RIDER_LOOKUP executor — Baemin rider cancel-rate lookup for Kakao commands.

The worker parses a ``RIDER_LOOKUP`` job, fetches Baemin delivery-history rows
(row-level, via the shared ``rider_crawl`` accessor — never aggregate snapshot
JSON), runs the shared matcher/renderer, and completes with
``result_type="rider_lookup"`` and a ``reply_text``. The server turns that into
one scoped ``KAKAO_SEND`` reply; this worker never sends Kakao itself and never
enters snapshot ingest/fanout.

Browser profile/config preparation is intentionally an injected seam
(``fetch_rider_rows``) so this module reuses existing crawl-worker patterns at
wiring time without opening a browser in tests and without duplicating the crawl
worker. ``reply_text`` contains the parsed name + phone suffix and therefore
lives only in job result scope — never in heartbeat or free-text logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from rider_agent.heartbeat import CAPABILITY_RIDER_LOOKUP
from rider_agent.job_loop import (
    ClaimedJob,
    JobResult,
    default_execute_job,
    make_failure_result,
    make_success_result,
)
from rider_agent.reuse import (
    COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
    BrowserActionRequiredError,
    BrowserLaunchError,
    CdpUnavailableError,
    RiderLookupCommand,
    find_rider_cancel_matches,
    render_lookup_reply,
)
from rider_agent.workers.crawl_worker import (
    _payload_expired,
    _positive_float,
    _raw_payload,
    _text,
)

RESULT_TYPE = "rider_lookup"
RESULT_TYPE_FAILED = "rider_lookup_failed"
SCHEMA_VERSION = 1

AUTH_STATE_ACTIVE = "ACTIVE"
AUTH_STATE_AUTH_REQUIRED = "AUTH_REQUIRED"

ERROR_UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
ERROR_PAYLOAD_EXPIRED = "PAYLOAD_EXPIRED"
ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"
ERROR_CDP_UNREACHABLE = "CDP_UNREACHABLE"
ERROR_PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
ERROR_LOOKUP_TIMEOUT = "LOOKUP_TIMEOUT"
ERROR_PARSER_MISSING_DATA = "PARSER_MISSING_DATA"
ERROR_LOOKUP_FAILURE = "LOOKUP_FAILURE"
REASON_PAYLOAD_EXPIRED = "payload_expired"

DEFAULT_SOURCE_LABEL = "배민"

# (job, payload) -> rider rows. The wiring layer composes crawl-worker browser
# profile/config prep + the shared Baemin row fetch behind this seam.
FetchRiderRows = Callable[["ClaimedJob", "RiderLookupJobPayload"], list[dict[str, str]]]


@dataclass(frozen=True)
class RiderLookupJobPayload:
    target_id: str
    tenant_id: str
    platform: str
    platform_account_id: str
    primary_url: str
    expected_display_name: str
    name: str
    phone_last4: str
    command_type: str
    reply_channel_id: str
    reply_kakao_room_name: str
    origin_event_key: str
    timeout_seconds: float
    expires_at: str
    target_external_id: str


def rider_lookup_payload_from_job(job: ClaimedJob) -> RiderLookupJobPayload:
    raw = _raw_payload(job)
    command = raw.get("command") if isinstance(raw.get("command"), dict) else {}
    return RiderLookupJobPayload(
        target_id=_text(raw, "target_id") or str(job.target_id or "").strip(),
        tenant_id=_text(raw, "tenant_id"),
        platform=(_text(raw, "platform") or "baemin").casefold(),
        platform_account_id=_text(raw, "platform_account_id"),
        primary_url=_text(raw, "primary_url", "url"),
        expected_display_name=_text(raw, "expected_display_name"),
        name=_text(command, "name"),
        phone_last4=_text(command, "phone_last4"),
        command_type=_text(command, "type") or COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
        reply_channel_id=_text(raw, "reply_channel_id"),
        reply_kakao_room_name=_text(raw, "reply_kakao_room_name"),
        origin_event_key=_text(raw, "origin_event_key"),
        timeout_seconds=_positive_float(raw.get("timeout_seconds"), default=60.0),
        expires_at=_text(raw, "expires_at"),
        target_external_id=_text(raw, "external_id", "target_external_id"),
    )


class RiderLookupWorker:
    """Execute RIDER_LOOKUP jobs; other job types fall through to ``fallback``."""

    def __init__(
        self,
        *,
        fetch_rider_rows: FetchRiderRows,
        now: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._fetch_rider_rows = fetch_rider_rows
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._log = log

    def execute(self, job: ClaimedJob) -> JobResult:
        if job.type != CAPABILITY_RIDER_LOOKUP:
            return default_execute_job(job)

        payload = rider_lookup_payload_from_job(job)
        # Defense-in-depth: fail fast on a stale payload before opening a browser.
        if _payload_expired(payload.expires_at, now=self._now()):
            return _failure(
                ERROR_PAYLOAD_EXPIRED,
                "rider lookup payload expired before fetch",
                payload,
                reason=REASON_PAYLOAD_EXPIRED,
            )
        # The server already gates non-Baemin as unsupported; fail closed here too.
        if payload.platform != "baemin":
            return _failure(
                ERROR_UNSUPPORTED_PLATFORM, "rider lookup platform is not baemin", payload
            )

        command = RiderLookupCommand(
            type=COMMAND_TYPE_RIDER_CANCEL_RATE_LOOKUP,
            name=payload.name,
            phone_last4=payload.phone_last4,
        )
        try:
            rows = self._fetch_rider_rows(job, payload)
        except BrowserActionRequiredError:
            return _failure(
                ERROR_AUTH_REQUIRED, "rider lookup auth required", payload,
                auth_state=AUTH_STATE_AUTH_REQUIRED,
            )
        except CdpUnavailableError:
            return _failure(ERROR_CDP_UNREACHABLE, "CDP endpoint unavailable", payload)
        except BrowserLaunchError:
            return _failure(ERROR_PROFILE_UNAVAILABLE, "browser profile unavailable", payload)
        except TimeoutError:
            return _failure(ERROR_LOOKUP_TIMEOUT, "rider lookup timed out", payload)
        except Exception as exc:  # noqa: BLE001 - classify parser miss, fail closed otherwise.
            if exc.__class__.__name__ == "MissingPerformanceDataError":
                return _failure(ERROR_PARSER_MISSING_DATA, "required lookup data missing", payload)
            return _failure(ERROR_LOOKUP_FAILURE, "rider lookup failed", payload, error=exc)

        matches = find_rider_cancel_matches(
            rows, command=command, source_label=payload.expected_display_name or DEFAULT_SOURCE_LABEL
        )
        reply_text = render_lookup_reply(command, matches)
        return make_success_result(result_json=_success_result(payload, reply_text))


def build_execute_job(
    *,
    rider_lookup_worker: RiderLookupWorker,
    fallback: Callable[[ClaimedJob], JobResult] = default_execute_job,
) -> Callable[[ClaimedJob], JobResult]:
    """Route RIDER_LOOKUP to ``rider_lookup_worker`` and all other jobs to fallback."""

    def _execute(job: ClaimedJob) -> JobResult:
        if job.type == CAPABILITY_RIDER_LOOKUP:
            return rider_lookup_worker.execute(job)
        return fallback(job)

    return _execute


def make_baemin_rider_rows_fetcher(
    *,
    profile_manager: Any,
    secret_resolver: Callable[[str], str | None] | None = None,
    fetch_rows: Callable[[Any], list[dict[str, str]]] | None = None,
) -> FetchRiderRows:
    """Compose crawl-worker browser-profile prep with the shared Baemin row fetch.

    Reuses the crawl worker's ``payload_from_job``/``_build_config`` and the same
    ``profile_manager.ensure_profile`` assignment so a RIDER_LOOKUP job opens the
    exact CDP profile crawl jobs use (no second copy of profile/config logic),
    then fetches rider rows through the shared accessor. ``fetch_rows`` is an
    injectable seam so wiring/tests can substitute a fake without a browser. This
    is the production ``fetch_rider_rows`` the worker composition injects.
    """

    from pathlib import Path

    from rider_agent.reuse import fetch_baemin_delivery_history_rows
    from rider_agent.workers.crawl_worker import _build_config, payload_from_job

    fetch = fetch_rows or fetch_baemin_delivery_history_rows

    def fetch_rider_rows(job: ClaimedJob, payload: RiderLookupJobPayload) -> list[dict[str, str]]:
        crawl_payload = payload_from_job(job)

        def build_config(*, tenant_id: str, target_id: str, cdp_url: str, user_data_dir: Path) -> Any:
            return _build_config(
                crawl_payload,
                cdp_url=cdp_url,
                user_data_dir=user_data_dir,
                secret_resolver=secret_resolver,
            )

        assignment = profile_manager.ensure_profile(
            crawl_payload.tenant_id, crawl_payload.target_id, build_config=build_config
        )
        config = build_config(
            tenant_id=crawl_payload.tenant_id,
            target_id=crawl_payload.target_id,
            cdp_url=getattr(assignment, "cdp_url", "http://127.0.0.1:9222"),
            user_data_dir=getattr(
                assignment,
                "profile_dir",
                Path("runtime") / "agent-browser-profiles" / crawl_payload.target_id,
            ),
        )
        return fetch(config)

    return fetch_rider_rows


def _reply_scope(payload: RiderLookupJobPayload) -> dict[str, Any]:
    # The scope the server needs to enqueue a KAKAO_SEND reply to the right room.
    return {
        "target_id": payload.target_id,
        "tenant_id": payload.tenant_id,
        "reply_channel_id": payload.reply_channel_id,
        "reply_kakao_room_name": payload.reply_kakao_room_name,
        "origin_event_key": payload.origin_event_key,
    }


def _success_result(payload: RiderLookupJobPayload, reply_text: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": RESULT_TYPE,
        **_reply_scope(payload),
        "reply_text": reply_text,
        "auth_state": AUTH_STATE_ACTIVE,
    }


def _failure(
    code: str,
    message: str,
    payload: RiderLookupJobPayload,
    *,
    auth_state: str | None = None,
    reason: str | None = None,
    error: BaseException | None = None,
) -> JobResult:
    # Failure keeps the reply scope (but no reply_text) so the server can enqueue
    # one fixed failure reply when the send gate is on.
    result_json: dict[str, Any] = {"result_type": RESULT_TYPE_FAILED, **_reply_scope(payload)}
    if auth_state:
        result_json["auth_state"] = auth_state
    if reason:
        result_json["reason"] = reason
    return make_failure_result(code, message, error=error, result_json=result_json)
