"""Job complete result ingest boundary.

This service validates Agent ``complete`` snapshot payloads before the queue
marks a job succeeded, then commits the prepared record after lease ownership
checks pass. Persistence is injected so this slice can stay independent from a
specific database repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Callable

from rider_crawl.redaction import redact

from rider_server.queue.states import JOB_STATUS_SUCCEEDED


class JobResultIngestError(ValueError):
    """Snapshot result payload is unsafe or incomplete."""


@dataclass(frozen=True)
class SnapshotIngestRecord:
    job_id: str
    agent_id: str
    target_id: str
    tenant_id: str
    platform: str
    platform_account_id: str
    collected_at: datetime
    parser_version: str
    quality_state: str
    normalized_json: dict[str, Any]
    artifact_refs: list[Any]
    completed_at: datetime


class JobResultIngestService:
    """Prepare and commit snapshot-shaped Agent complete results."""

    def __init__(
        self,
        *,
        save_snapshot: Callable[[SnapshotIngestRecord], Any] | None = None,
    ) -> None:
        self._save_snapshot = save_snapshot

    def prepare_complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        status: str,
        result_json: dict[str, Any] | None,
        completed_at: datetime,
        expected_target_id: str | None = None,
    ) -> SnapshotIngestRecord | None:
        if status != JOB_STATUS_SUCCEEDED or not result_json:
            return None
        if result_json.get("result_type") != "snapshot":
            return None
        if result_json.get("schema_version") != 1:
            raise JobResultIngestError("unsupported snapshot result schema_version")

        normalized_json = result_json.get("normalized_json")
        if not isinstance(normalized_json, dict):
            raise JobResultIngestError("snapshot normalized_json is required")

        target_id = _required_text(result_json, "target_id")
        if expected_target_id is not None and target_id != expected_target_id:
            raise JobResultIngestError("snapshot target_id does not match claimed job")

        return SnapshotIngestRecord(
            job_id=job_id,
            agent_id=agent_id,
            target_id=target_id,
            tenant_id=str(result_json.get("tenant_id") or ""),
            platform=_required_text(result_json, "platform"),
            platform_account_id=str(result_json.get("platform_account_id") or ""),
            collected_at=_required_datetime(result_json, "collected_at"),
            parser_version=_required_text(result_json, "parser_version"),
            quality_state=_required_text(result_json, "quality_state"),
            normalized_json=_sanitize_json(normalized_json),
            artifact_refs=_artifact_refs(result_json.get("artifact_refs")),
            completed_at=completed_at,
        )

    async def commit(self, record: SnapshotIngestRecord) -> None:
        if self._save_snapshot is None:
            return
        result = self._save_snapshot(record)
        if isawaitable(result):
            await result


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobResultIngestError(f"snapshot {key} is required")
    return value.strip()


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobResultIngestError(f"snapshot {key} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JobResultIngestError(f"snapshot {key} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise JobResultIngestError(f"snapshot {key} must include timezone")
    return parsed.astimezone(timezone.utc)


def _artifact_refs(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise JobResultIngestError("snapshot artifact_refs must be a list")
    return _sanitize_json(value)


_SENSITIVE_KEY_PARTS = frozenset(
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


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                continue
            cleaned[key] = _sanitize_json(raw_value)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value
