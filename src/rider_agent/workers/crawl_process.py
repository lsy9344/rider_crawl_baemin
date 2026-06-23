"""Subprocess boundary for default crawl jobs."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from rider_agent.job_loop import ClaimedJob, JobResult, make_failure_result
from rider_crawl.redaction import redact

_IS_WINDOWS = os.name == "nt"
_MAX_STREAM_TAIL_CHARS = 4000


def _new_process_group_kwargs() -> dict[str, Any]:
    """child 를 **자체 프로세스 그룹/세션**으로 띄우는 Popen kwargs.

    timeout 시 child Python 만이 아니라 그 child 가 spawn 한 Chrome 트리까지 한 번에
    종료하기 위함이다(고아 Chrome 방지). Windows 는 CREATE_NEW_PROCESS_GROUP,
    POSIX 는 setsid(start_new_session=True).
    """

    if _IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags = flags | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": creationflags}
    return {"start_new_session": True}


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """child 와 그 자손(Chrome 포함)을 강제 종료한다(플랫폼별 트리 kill).

    Windows: ``taskkill /T /F`` 로 PID 트리 전체. POSIX: 프로세스 그룹에 SIGTERM→SIGKILL.
    어떤 단계가 실패해도(이미 종료 등) best-effort 로 진행한다 — timeout 정리는 멈추면 안 된다.
    """

    if proc.poll() is not None:
        return
    if _IS_WINDOWS:
        try:
            subprocess.run(  # noqa: S603,S607 - fixed argv, PID is ours.
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except Exception:  # noqa: BLE001 - best-effort; fall back to proc.kill below.
            pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - already gone.
            pass
        return
    # POSIX: 그룹 전체에 SIGTERM 후 짧게 기다렸다 SIGKILL.
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            continue


def run_crawl_in_subprocess(
    job: ClaimedJob,
    *,
    timeout_seconds: float,
    target_id: str,
    platform: str,
    cleanup: Callable[[], None] | None = None,
) -> JobResult:
    """Run a crawl job in a child Python process and kill its tree on timeout."""

    with tempfile.TemporaryDirectory(prefix="rider-crawl-job-") as tmp:
        root = Path(tmp)
        input_path = root / "job.json"
        output_path = root / "result.json"
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
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
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            proc = subprocess.Popen(  # noqa: S603 - argv is fixed; paths are temp files.
                [
                    sys.executable,
                    "-m",
                    "rider_agent.workers.crawl_process",
                    str(input_path),
                    str(output_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                **_new_process_group_kwargs(),
            )
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                # child Python 만이 아니라 그 자손(Chrome 트리)까지 종료한다(고아 방지).
                _terminate_process_tree(proc)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
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
                result_json={
                    "target_id": target_id,
                    "platform": platform,
                    "diagnostics": {
                        "subprocess": _subprocess_diagnostics(
                            proc.returncode, stdout_path=stdout_path, stderr_path=stderr_path
                        )
                    },
                },
            )
        data = json.loads(output_path.read_text(encoding="utf-8"))
        return JobResult(**data)


def _subprocess_diagnostics(
    returncode: int | None,
    *,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {"returncode": returncode}
    stdout_tail = _redacted_tail(stdout_path)
    stderr_tail = _redacted_tail(stderr_path)
    if stdout_tail:
        diagnostics["stdout_tail"] = stdout_tail
    if stderr_tail:
        diagnostics["stderr_tail"] = stderr_tail
    return diagnostics


def _redacted_tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # **redact 먼저, truncate 나중.** redact 는 ``key=value``/OTP 문맥처럼 키와 값이 인접해야
    # 매칭되는 정규식이라, 4000자로 먼저 자르면 민감 키가 tail 앞에서 잘리고 값만 남아 마스킹
    # 문맥을 잃어 secret 이 누출될 수 있다(검토 High). 전체를 redact 한 뒤 마지막 N 자만 취한다.
    redacted = redact(text)
    if len(redacted) > _MAX_STREAM_TAIL_CHARS:
        redacted = redacted[-_MAX_STREAM_TAIL_CHARS:]
    return redacted.strip()


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
