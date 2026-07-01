"""Sync-only worker composition for the Agent run loop.

This module assembles optional job executors without importing ``job_loop`` at
runtime. The run loop owns identity loading, token validation, heartbeat, and
shutdown order; this module owns only worker chaining.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from rider_crawl.config import app_state_root
from rider_crawl.redaction import redact

# auth wrapper 가 heartbeat profile 진단을 기록하는 job type(profile assignment 가 있는 경우만).
_AUTH_DIAGNOSTIC_JOB_TYPES = frozenset(
    {"AUTH_CHECK", "OPEN_AUTH_BROWSER", "AUTH_COUPANG_2FA"}
)
_AUTH_STATE_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WorkerComposition:
    execute_job: Callable[[Any], Any]
    browser_profiles_provider: Callable[[], object] | None = None
    kakao_status_provider: Callable[[], str] | None = None
    close_callbacks: tuple[Callable[[], None], ...] = ()
    kakao_worker: Any = None
    crawl_worker: Any = None


def _with_profile_assignment(
    job: Any,
    *,
    profile_manager: Any,
    secret_resolver: Callable[[Any], str | None] | None,
) -> Any:
    from rider_agent.workers.crawl_worker import _build_config, payload_from_job

    payload = payload_from_job(job)

    def build_config(
        *,
        tenant_id: str,
        target_id: str,
        cdp_url: str,
        user_data_dir: Path,
    ) -> Any:
        return _build_config(
            payload,
            cdp_url=cdp_url,
            user_data_dir=user_data_dir,
            secret_resolver=secret_resolver,
        )

    assignment = profile_manager.ensure_profile(
        payload.tenant_id,
        payload.target_id,
        build_config=build_config,
    )
    raw_payload = dict(getattr(job, "payload", {}) or {})
    raw_payload["cdp_url"] = str(
        getattr(assignment, "cdp_url", "http://127.0.0.1:9222")
    )
    raw_payload["browser_user_data_dir"] = str(
        getattr(
            assignment,
            "profile_dir",
            Path("runtime") / "agent-browser-profiles" / payload.target_id,
        )
    )
    return replace(job, payload=raw_payload)


def _diagnostic_iso(now: Callable[[], Any]) -> str:
    """주입 ``now`` (epoch float 또는 datetime)를 ISO-8601 UTC(``...Z``) 문자열로 만든다."""

    value = now()
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _record_auth_diagnostic_from_result(
    manager: Any,
    job: Any,
    result: Any,
    *,
    now: Callable[[], Any],
) -> None:
    """auth job 의 최종 ``JobResult`` 를 기존 profile assignment 에 진단으로 기록한다.

    - ``crawl_profile_manager`` 가 있을 때만 호출된다(auth-only 조합은 profile row 없음).
    - ``result_json.auth_state`` 가 있으면 그대로, 없으면 ``UNKNOWN`` 으로 둔다.
    - ``error_code`` 는 실패 시 원인 힌트로 기록한다.
    - secret/OTP/이메일/URL/HTML 은 담지 않는다(auth_state/error_code 텍스트만).
    - 진단 기록 실패는 흡수하며 job 결과를 바꾸지 않는다.
    - ``BrowserProfileManager`` 를 import 하지 않고 duck-typing 으로 호출한다(역방향 import 0).
    """

    record = getattr(manager, "record_profile_diagnostic", None)
    if not callable(record):
        return
    job_type = getattr(job, "type", None)
    if job_type not in _AUTH_DIAGNOSTIC_JOB_TYPES:
        return
    from rider_agent.workers.crawl_worker import payload_from_job

    try:
        payload = payload_from_job(job)
    except Exception:  # noqa: BLE001 - payload 파싱 실패는 진단을 건너뛰되 결과를 바꾸지 않는다.
        return
    result_json = getattr(result, "result_json", None)
    result_json = result_json if isinstance(result_json, dict) else {}
    auth_state = result_json.get("auth_state")
    if not isinstance(auth_state, str) or not auth_state:
        auth_state = _AUTH_STATE_UNKNOWN
    error_code = getattr(result, "error_code", None)
    try:
        record(
            payload.tenant_id,
            payload.target_id,
            auth_state=auth_state,
            last_error_code=error_code if isinstance(error_code, str) else None,
            last_probe_at=_diagnostic_iso(now),
        )
    except Exception:  # noqa: BLE001 - diagnostic recording must not change job result.
        # best-effort: 진단 기록 실패는 흡수한다(job 결과/실행에 영향 없음).
        pass


def compose_execute_job(
    *,
    identity: Any,
    capabilities: Sequence[str],
    fallback: Callable[[Any], Any],
    log: Callable[[str], None] | None,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    browser_profiles_provider: Callable[[], object] | None = None,
    kakao_status_provider: Callable[[], str] | None = None,
    on_status: Callable[[str], None] | None = None,
    start_auth_worker: bool = False,
    auth_login_probe: Callable[[Any], str] | None = None,
    auth_open_auth_browser: Callable[[Any], Any] | None = None,
    auth_detect_completion: Callable[[Any], bool] | None = None,
    auth_max_wait_seconds: float | None = None,
    auth_poll_interval_seconds: float | None = None,
    auth_max_attempts: int | None = None,
    start_crawl_worker: bool = False,
    crawl_profile_manager: Any = None,
    crawl_snapshot: Callable[..., Any] | None = None,
    crawl_auth_probe: Callable[[Any, Any], str] | None = None,
    secret_resolver: Callable[[Any], str | None] | None = None,
    profile_idle_ttl_seconds: float | None = 3600.0,
    max_profiles: int | None = 20,
    start_kakao_sender: bool = False,
    kakao_send: Callable[..., Any] | None = None,
    kakao_build_config: Callable[..., Any] | None = None,
    session_probe: Callable[[], bool] | None = None,
) -> WorkerComposition:
    effective_execute_job = fallback
    effective_browser_profiles = browser_profiles_provider
    effective_kakao_status = kakao_status_provider
    close_callbacks: list[Callable[[], None]] = []
    should_start_crawl_worker = start_crawl_worker and (
        "CRAWL_BAEMIN" in capabilities
        or "CRAWL_COUPANG" in capabilities
        or "RIDER_LOOKUP" in capabilities
    )

    if should_start_crawl_worker and crawl_profile_manager is None and crawl_snapshot is None:
        from rider_agent.browser_profile import BrowserProfileManager

        crawl_profile_manager = BrowserProfileManager(
            profiles_root=app_state_root() / "runtime" / "agent-browser-profiles",
            agent_id=identity.agent_id,
            max_profiles=max_profiles,
            log=log,
        )

    if start_auth_worker and (
        "AUTH_CHECK" in capabilities
        or "OPEN_AUTH_BROWSER" in capabilities
        or "AUTH_COUPANG_2FA" in capabilities
    ):
        from rider_agent.auth import baemin_auth

        auth_kwargs: dict[str, Any] = {
            "fallback": effective_execute_job,
            "now": now,
            "sleep": sleep,
            "log": log,
        }
        if crawl_profile_manager is not None:
            login_probe = auth_login_probe or (
                lambda job: baemin_auth.default_login_probe(
                    job, secret_resolver=secret_resolver
                )
            )
            open_auth_browser = auth_open_auth_browser or (
                lambda job: baemin_auth.default_open_auth_browser(
                    job, secret_resolver=secret_resolver
                )
            )
            detect_completion = auth_detect_completion or (
                lambda job: baemin_auth.default_detect_completion(
                    job, secret_resolver=secret_resolver
                )
            )
            auth_kwargs["login_probe"] = lambda job: login_probe(
                _with_profile_assignment(
                    job,
                    profile_manager=crawl_profile_manager,
                    secret_resolver=secret_resolver,
                )
            )
            auth_kwargs["open_auth_browser"] = lambda job: open_auth_browser(
                _with_profile_assignment(
                    job,
                    profile_manager=crawl_profile_manager,
                    secret_resolver=secret_resolver,
                )
            )
            auth_kwargs["detect_completion"] = lambda job: detect_completion(
                _with_profile_assignment(
                    job,
                    profile_manager=crawl_profile_manager,
                    secret_resolver=secret_resolver,
                )
            )
        else:
            if auth_login_probe is not None:
                auth_kwargs["login_probe"] = auth_login_probe
            if auth_open_auth_browser is not None:
                auth_kwargs["open_auth_browser"] = auth_open_auth_browser
            if auth_detect_completion is not None:
                auth_kwargs["detect_completion"] = auth_detect_completion
            if secret_resolver is not None:
                auth_kwargs["secret_resolver"] = secret_resolver
        if auth_max_wait_seconds is not None:
            auth_kwargs["max_wait_seconds"] = auth_max_wait_seconds
        if auth_poll_interval_seconds is not None:
            auth_kwargs["poll_interval_seconds"] = auth_poll_interval_seconds
        if auth_max_attempts is not None:
            auth_kwargs["max_attempts"] = auth_max_attempts
        effective_execute_job = baemin_auth.build_auth_execute_job(**auth_kwargs)

        # AUTH_COUPANG_2FA 는 별도 worker(coupang_gmail_2fa)로 라우팅한다. baemin auth 라우터
        # (AUTH_CHECK/OPEN_AUTH_BROWSER) 바깥에 합성해 그 둘은 그대로 흐르게 한다. profile
        # assignment 가 필요하므로(브라우저 page 획득) crawl_profile_manager 가 있을 때 wrap.
        if "AUTH_COUPANG_2FA" in capabilities:
            from rider_agent.auth import coupang_gmail_2fa

            inner_execute = effective_execute_job
            if crawl_profile_manager is not None:
                def _coupang_2fa_execute(job: Any, _inner=inner_execute) -> Any:
                    assigned = _with_profile_assignment(
                        job,
                        profile_manager=crawl_profile_manager,
                        secret_resolver=secret_resolver,
                    )
                    router = coupang_gmail_2fa.build_coupang_auth_execute_job(
                        secret_resolver=secret_resolver,
                        fallback=lambda _j: _inner(job),
                        now=now,
                        sleep=sleep,
                        log=log,
                    )
                    return router(assigned)

                effective_execute_job = _coupang_2fa_execute
            else:
                effective_execute_job = coupang_gmail_2fa.build_coupang_auth_execute_job(
                    secret_resolver=secret_resolver,
                    fallback=inner_execute,
                    now=now,
                    sleep=sleep,
                    log=log,
                )

        # auth 결과를 heartbeat profile 진단으로 기록한다(profile assignment 가 있을 때만).
        # router 가 만든 최종 JobResult 를 검사만 하고 원본을 그대로 반환한다(결과 불변).
        if crawl_profile_manager is not None:
            _auth_inner = effective_execute_job

            def _auth_with_diagnostic(job: Any, _inner=_auth_inner) -> Any:
                result = _inner(job)
                _record_auth_diagnostic_from_result(
                    crawl_profile_manager, job, result, now=now
                )
                return result

            effective_execute_job = _auth_with_diagnostic

    crawl_worker = None
    if should_start_crawl_worker:
        from rider_agent.workers.crawl_worker import (
            CrawlWorker,
            build_execute_job as build_crawl_execute_job,
        )

        crawl_worker = CrawlWorker(
            profile_manager=crawl_profile_manager,
            crawl_snapshot=crawl_snapshot,
            auth_probe=crawl_auth_probe,
            secret_resolver=secret_resolver,
            profile_idle_ttl_seconds=profile_idle_ttl_seconds,
            log=log,
        )
        effective_execute_job = build_crawl_execute_job(
            crawl_worker=crawl_worker, fallback=effective_execute_job
        )
        if (
            effective_browser_profiles is None
            and crawl_profile_manager is not None
            and hasattr(crawl_profile_manager, "browser_profiles")
        ):
            effective_browser_profiles = crawl_profile_manager.browser_profiles
        close_all_profiles = getattr(crawl_profile_manager, "close_all", None)
        if callable(close_all_profiles):
            close_callbacks.append(close_all_profiles)

    if (
        should_start_crawl_worker
        and "RIDER_LOOKUP" in capabilities
        and crawl_profile_manager is not None
    ):
        # RIDER_LOOKUP(카카오 인바운드 라이더 조회)을 전용 worker 로 라우팅한다. 배민 crawl 과
        # 같은 CDP profile 을 재사용(fetcher 가 crawl-worker config 준비를 조립)하되 결과는
        # snapshot 이 아니라 command reply 다. 이 worker 는 RIDER_LOOKUP 만 잡고 나머지는 fallback
        # 으로 흘리므로 AUTH_COUPANG_2FA/CRAWL_COUPANG/KAKAO_SEND 라우팅 순서는 바뀌지 않는다.
        from rider_agent.workers.rider_lookup import (
            RiderLookupWorker,
            build_execute_job as build_rider_lookup_execute_job,
            make_baemin_rider_rows_fetcher,
        )

        rider_lookup_worker = RiderLookupWorker(
            fetch_rider_rows=make_baemin_rider_rows_fetcher(
                profile_manager=crawl_profile_manager,
                secret_resolver=secret_resolver,
            ),
            log=log,
        )
        effective_execute_job = build_rider_lookup_execute_job(
            rider_lookup_worker=rider_lookup_worker,
            fallback=effective_execute_job,
        )

    kakao_worker = None
    if start_kakao_sender:
        from rider_agent.autostart import kakao_session_allowed

        allowed, reason = kakao_session_allowed(
            capabilities, session_probe=session_probe
        )
        if not allowed:
            if log is not None:
                log(
                    redact(
                        f"kakao sender disabled: non-interactive session ({reason})"
                    )
                )
            if on_status is not None:
                on_status(reason)
        else:
            from rider_agent.workers.kakao_sender import (
                build_execute_job,
                start_kakao_sender_worker_if_enabled,
            )

            kakao_worker = start_kakao_sender_worker_if_enabled(
                capabilities=capabilities,
                send=kakao_send,
                build_config=kakao_build_config,
                sleep=sleep,
                now=now,
                log=log,
            )
            if kakao_worker is not None:
                effective_execute_job = build_execute_job(
                    kakao_worker=kakao_worker, fallback=effective_execute_job
                )
                if effective_kakao_status is None:
                    effective_kakao_status = kakao_worker.kakao_status
                close_callbacks.append(kakao_worker.stop)

    return WorkerComposition(
        execute_job=effective_execute_job,
        browser_profiles_provider=effective_browser_profiles,
        kakao_status_provider=effective_kakao_status,
        close_callbacks=tuple(close_callbacks),
        kakao_worker=kakao_worker,
        crawl_worker=crawl_worker,
    )
