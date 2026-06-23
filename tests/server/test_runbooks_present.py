"""Story 5.9 / AC3 — 7종 운영 runbook 파일 존재 + FailureCategory 참조(always-run).

"파일 없이 done 처리"를 차단한다(완료 위조 방지 — "lying about completion"). 각 runbook 이
해당 정본 ``FailureCategory`` 코드 문자열을 명시 참조하는지(NFR-15 분류 계약) 확인하고, 7
runbook 이 정본 7 카테고리를 모두 커버하는지 잠근다.
"""

from __future__ import annotations

from pathlib import Path

from rider_server.domain import FailureCategory

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK_DIR = _REPO_ROOT / "docs" / "runbooks"
_QUEUE_BACKLOG_POLICY = (
    _REPO_ROOT / "docs" / "operations" / "queue-backlog-handling-policy.md"
)

# runbook 파일 → 명시 참조해야 하는 정본 FailureCategory 코드(NFR-15 분류 계약).
_REQUIRED: dict[str, tuple[str, ...]] = {
    "agent_offline.md": ("CRAWL_FAILURE",),
    "queue_lag.md": ("KAKAO_FAILURE",),
    "api_error_rate.md": ("CRAWL_FAILURE", "RENDER_FAILURE", "TELEGRAM_FAILURE"),
    "auth_required.md": ("AUTH_REQUIRED",),
    "profile_mismatch.md": ("TARGET_VALIDATION_FAILURE",),
    "kakao_ambiguous_room.md": ("KAKAO_FAILURE",),
    "duplicate_blocked.md": ("DUPLICATE_BLOCKED",),
}


def test_all_seven_runbooks_exist_as_files() -> None:
    for name in _REQUIRED:
        path = _RUNBOOK_DIR / name
        assert path.is_file(), f"누락된 runbook: {name}"
        assert path.read_text(encoding="utf-8").strip(), f"빈 runbook: {name}"


def test_each_runbook_references_its_failure_category_codes() -> None:
    for name, codes in _REQUIRED.items():
        text = (_RUNBOOK_DIR / name).read_text(encoding="utf-8")
        for code in codes:
            assert code in text, f"{name} 가 {code} 를 참조하지 않음(NFR-15 분류 누락)"


def test_required_codes_are_canonical_failure_category_members() -> None:
    # 참조 코드가 정본 FailureCategory 값에서 벗어나지 않는지(오타/임의 코드 차단).
    canonical = {m.value for m in FailureCategory}
    used = {code for codes in _REQUIRED.values() for code in codes}
    assert used <= canonical, used - canonical


def test_seven_runbooks_cover_all_seven_failure_categories() -> None:
    # 7 runbook 이 정본 7 카테고리를 모두 커버(NFR-15 — 모든 원인이 조치 가능하게 분류).
    covered = {code for codes in _REQUIRED.values() for code in codes}
    assert covered == {m.value for m in FailureCategory}
    assert len(_REQUIRED) == 7


def _ec2_memory_runbook() -> str:
    return (_RUNBOOK_DIR / "ec2-memory-hardening-plan.md").read_text(
        encoding="utf-8"
    )


def test_ec2_memory_runbook_uses_repo_root_env_for_production_deploy() -> None:
    """Production deploy sources /opt/rider-server/repo/.env, not deploy/.env."""

    runbook = _ec2_memory_runbook()

    assert "/opt/rider-server/repo/.env" in runbook
    assert "/opt/rider-server/repo/deploy/.env`에" not in runbook
    assert "deploy/env/*.env" in runbook
    assert "compose service env files" in runbook
    assert "not the root deploy variable file" in runbook


def test_ec2_memory_runbook_blocks_runner_shutdown_without_deploy_replacement() -> None:
    """Runner shutdown requires confirmed production deploy replacement."""

    runbook = _ec2_memory_runbook()

    assert ".github/workflows/test.yml" in runbook
    assert "deploy-production" in runbook
    assert "runs-on: [self-hosted, Linux, ARM64, rider-prod]" in runbook
    assert "systemctl disable" in runbook
    assert (
        "blocked until manual deploy or GitHub-hosted + SSH/SSM deploy is verified"
        in runbook
    )


def test_ec2_memory_runbook_has_idempotent_swap_commands() -> None:
    """Swap setup checks existing swapfile and fstab before writing."""

    runbook = _ec2_memory_runbook()

    assert "test -f /swapfile" in runbook
    assert "sudo file /swapfile" in runbook
    assert "grep -q '^/swapfile none swap sw 0 0$' /etc/fstab" in runbook
    assert "sudo fallocate -l 2G /swapfile\nsudo chmod 600 /swapfile" not in runbook
    assert "\necho '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab" not in runbook


def test_ec2_memory_runbook_keeps_required_services_enabled() -> None:
    """Runbook names services that must not be disabled."""

    runbook = _ec2_memory_runbook()

    assert "do not disable amazon-ssm-agent" in runbook
    assert "do not disable docker" in runbook
    assert "do not disable containerd" in runbook
    assert "do not disable ssh" in runbook
    assert "sudo systemctl enable --now fwupd.service fwupd-refresh.timer ModemManager.service udisks2.service" in runbook


def test_ec2_memory_runbook_aborts_on_terraform_instance_replacement() -> None:
    """Local PostgreSQL on root volume makes EC2 replacement a stop condition."""

    runbook = _ec2_memory_runbook()

    assert "-/+ aws_instance.app" in runbook
    assert "root volume local PostgreSQL data" in runbook
    assert "delete_on_termination = true" in runbook


def test_ec2_memory_runbook_success_criteria_include_metrics_and_oom_checks() -> None:
    """Runbook success includes host metrics, alarms, and OOM log checks."""

    runbook = _ec2_memory_runbook()

    assert "journalctl -k --since" in runbook
    assert "HostMemAvailablePercent" in runbook
    assert "HostSwapUsedPercent" in runbook
    assert "no new OOM log" in runbook
    assert "rider-metrics.service" in runbook


def test_queue_backlog_policy_mentions_implemented_and_target_behavior_sections() -> None:
    """Runbook separates present behavior from future policy."""

    text = _QUEUE_BACKLOG_POLICY.read_text(encoding="utf-8")
    assert "Current Implemented Behavior" in text
    assert "Target Permanent Behavior" in text
    assert "Emergency Operator Action" in text
    assert "Verification Matrix" in text
    # 안전한 reason 코드(secret 없는 분류 코드)가 문서에 명시돼 운영자가 의미를 안다.
    assert "stale_auth_job_expired" in text
    assert "stale_crawl_skipped" in text
