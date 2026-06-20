"""Subprocess boundary for default crawl jobs."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from rider_agent.job_loop import ClaimedJob, JobResult, make_failure_result


def run_crawl_in_subprocess(
    job: ClaimedJob,
    *,
    timeout_seconds: float,
    target_id: str,
    platform: str,
    cleanup: Callable[[], None] | None = None,
) -> JobResult:
    """Run a crawl job in a child Python process and kill it on timeout."""

    with tempfile.TemporaryDirectory(prefix="rider-crawl-job-") as tmp:
        root = Path(tmp)
        input_path = root / "job.json"
        output_path = root / "result.json"
        payload = dict(job.payload)
        payload["timeout_seconds"] = 0
        input_path.write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "type": job.type,
                    "target_id": job.target_id,
                    "lease_expires_at": job.lease_expires_at,
                    "payload": payload,
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen(  # noqa: S603 - argv is fixed; paths are temp files.
            [
                sys.executable,
                "-m",
                "rider_agent.workers.crawl_process",
                str(input_path),
                str(output_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            if cleanup is not None:
                cleanup()
            return make_failure_result(
                "CRAWL_TIMEOUT",
                "crawl timed out",
                result_json={"target_id": target_id, "platform": platform},
            )
        if proc.returncode != 0 or not output_path.exists():
            return make_failure_result(
                "CRAWL_FAILURE",
                "crawl subprocess failed",
                result_json={"target_id": target_id, "platform": platform},
            )
        data = json.loads(output_path.read_text(encoding="utf-8"))
        return JobResult(**data)


def _run_child(input_path: Path, output_path: Path) -> int:
    from rider_agent.workers.crawl_worker import CrawlWorker

    data = json.loads(input_path.read_text(encoding="utf-8"))
    job = ClaimedJob(
        job_id=str(data["job_id"]),
        type=str(data.get("type") or ""),
        target_id=data.get("target_id"),
        lease_expires_at=data.get("lease_expires_at"),
        payload=dict(data.get("payload") or {}),
    )
    result = CrawlWorker(process_boundary_enabled=False).execute(job)
    output_path.write_text(
        json.dumps(dataclasses.asdict(result), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    return _run_child(Path(args[0]), Path(args[1]))


if __name__ == "__main__":
    raise SystemExit(main())
