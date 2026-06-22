"""Sync-only worker composition for the Agent run loop.

This module assembles optional job executors without importing ``job_loop`` at
runtime. The run loop owns identity loading, token validation, heartbeat, and
shutdown order; this module owns only worker chaining.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from rider_crawl.config import app_state_root
from rider_crawl.redaction import redact


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
        "CRAWL_BAEMIN" in capabilities or "CRAWL_COUPANG" in capabilities
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
        "AUTH_CHECK" in capabilities or "OPEN_AUTH_BROWSER" in capabilities
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
