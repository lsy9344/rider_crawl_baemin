"""Story 1.1 기준선 산출물 회귀 가드 (QA E2E 자동화).

이 스토리(1.1)는 제품 코드 변경이 아니라 운영 기준선 고정 절차/산출물 스토리이며,
원래는 수동 체크리스트로 검증됐다. 이 모듈은 그 수동 검증을 자동 회귀 테스트로 고정해
앞으로의 커밋이 sanitized 샘플·기록 문서에 실제 secret(토큰/chat_id/전화번호)이나
누락을 다시 들이지 못하게 막는다.

검증 대상 AC:
- AC1 (P0-01): 기준선 tag·백업 메타·기록 문서 완전성.
- AC2/AC5 (P0-02, NFR-5, ADD-15): sanitized 샘플에 실제 secret 없음 + placeholder 존재.
- AC3 (NFR-18): 백업 zip이 git에 추적되지 않도록 `.gitignore`에 `backups/` 존재.

주의: 프로젝트 규칙에 따라 이 테스트는 **실제 secret 값을 하드코딩하지 않는다**.
누출 여부는 실제값이 아니라 secret '패턴'(예: 텔레그램 봇 토큰 형태)으로 검사한다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

CONFIG_SAMPLES_DIR = REPO_ROOT / "docs" / "config-samples"
UI_SETTINGS_SAMPLE = CONFIG_SAMPLES_DIR / "ui_settings.sample.json"
CONFIG_SAMPLE = CONFIG_SAMPLES_DIR / "config.sample.json"
ENV_SAMPLE = CONFIG_SAMPLES_DIR / ".env.sample"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BASELINE_RECORD = REPO_ROOT / "docs" / "qa" / "baseline-record-20260613.md"
GITIGNORE = REPO_ROOT / ".gitignore"

TAG_NAME = "baseline-local-ui-20260613"
ZIP_PATH = "backups/baseline-config-backup-20260613.zip"

# 외부에 커밋되는 모든 텍스트 산출물. 어떤 파일에도 실제 봇 토큰 형태가 없어야 한다.
COMMITTED_TEXT_ARTIFACTS = [
    UI_SETTINGS_SAMPLE,
    CONFIG_SAMPLE,
    ENV_SAMPLE,
    ENV_EXAMPLE,
    BASELINE_RECORD,
]

# 텔레그램 봇 토큰 형태: <8자리 이상 봇 id>:<35자 내외 영숫자/_- 토큰>.
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")

# .env 파일에서 마스킹돼야 하는 운영 식별자.
MASKED_ENV_IDENTIFIERS = ("BAEMIN_CENTER_NAME", "BAEMIN_CENTER_ID", "KAKAO_CHAT_NAME")
# .env 파일에서 비어 있어야 하는 secret 키.
EMPTY_ENV_SECRETS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_MESSAGE_THREAD_ID",
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _is_placeholder(value: str) -> bool:
    """`<...>` 형태의 placeholder인지."""
    return value.startswith("<") and value.endswith(">") and len(value) > 2


def _parse_env(path: Path) -> dict[str, str]:
    """주석을 제외한 KEY=VALUE 라인만 dict로 파싱."""
    env: dict[str, str] = {}
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value.strip()
    return env


# ---------------------------------------------------------------------------
# AC2/AC5 — sanitized 샘플 누출 가드 (핵심 회귀 가드)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_committed_artifact_exists(artifact: Path):
    assert artifact.is_file(), f"기준선 산출물 누락: {artifact.relative_to(REPO_ROOT)}"


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_real_telegram_bot_token_in_artifacts(artifact: Path):
    """커밋되는 어떤 산출물에도 실제 텔레그램 봇 토큰 형태가 없어야 한다 (ADD-15)."""
    match = TELEGRAM_BOT_TOKEN_RE.search(_read_text(artifact))
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 텔레그램 봇 토큰 형태 누출: {match.group() if match else ''!r}"
    )


def test_ui_settings_sample_is_valid_json():
    json.loads(_read_text(UI_SETTINGS_SAMPLE))


def test_ui_settings_sample_uses_two_space_indent():
    """프로젝트 JSON 규칙(ensure_ascii=False, indent=2) — 2칸 들여쓰기 유지."""
    text = _read_text(UI_SETTINGS_SAMPLE)
    assert '\n  "crawlings"' in text, "최상위 키가 2칸 들여쓰기가 아니다"
    # 비ASCII placeholder가 \uXXXX 로 escape되지 않았는지 (ensure_ascii=False 확인).
    assert "\\u" not in text


def test_ui_settings_sample_telegram_fields_are_placeholders():
    """탭별 민감 필드 3종이 모두 placeholder여야 한다 (AC2)."""
    data = json.loads(_read_text(UI_SETTINGS_SAMPLE))
    crawlings = data.get("crawlings")
    assert isinstance(crawlings, list) and crawlings, "crawlings 배열이 비어 있다"
    for tab in crawlings:
        for field in ("telegram_bot_token", "telegram_chat_id", "telegram_message_thread_id"):
            assert field in tab, f"민감 필드 누락: {field}"
            assert _is_placeholder(tab[field]), f"{field}가 placeholder가 아니다: {tab[field]!r}"


def test_ui_settings_sample_operating_identifiers_are_placeholders():
    """운영 식별자(센터명/ID/카카오 방명)도 보수적으로 마스킹돼야 한다 (AC2)."""
    data = json.loads(_read_text(UI_SETTINGS_SAMPLE))
    for tab in data["crawlings"]:
        for field in ("baemin_center_name", "baemin_center_id", "kakao_chat_name"):
            assert _is_placeholder(tab[field]), f"{field} 마스킹 누락: {tab[field]!r}"


def test_ui_settings_sample_keeps_single_representative_tab():
    """실제 9개 탭 전체를 복사하지 않고 대표 1개 탭만 남긴다 (Task 4 정책)."""
    data = json.loads(_read_text(UI_SETTINGS_SAMPLE))
    assert len(data["crawlings"]) == 1


def test_config_sample_phone_numbers_are_zero_placeholders():
    """auto_message의 실제 보험사 전화번호가 0 placeholder로 치환됐는지 (AC2)."""
    data = json.loads(_read_text(CONFIG_SAMPLE))
    auto_message = data["auto_message"]
    digit_runs = re.findall(r"\d{3,}", auto_message)
    assert digit_runs, "전화번호 placeholder 형태가 없다"
    non_zero = [run for run in digit_runs if set(run) != {"0"}]
    assert not non_zero, f"실제 전화번호로 보이는 숫자 누출: {non_zero}"


# ---------------------------------------------------------------------------
# AC2/AC5 — .env.sample / .env.example 마스킹 가드
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_path", [ENV_SAMPLE, ENV_EXAMPLE], ids=lambda p: p.name)
def test_env_telegram_secrets_are_empty(env_path: Path):
    env = _parse_env(env_path)
    for key in EMPTY_ENV_SECRETS:
        assert key in env, f"{env_path.name}에 {key} 키가 없다"
        assert env[key] == "", f"{env_path.name}의 {key}가 비어 있지 않다: {env[key]!r}"


@pytest.mark.parametrize("env_path", [ENV_SAMPLE, ENV_EXAMPLE], ids=lambda p: p.name)
def test_env_operating_identifiers_are_placeholders(env_path: Path):
    env = _parse_env(env_path)
    for key in MASKED_ENV_IDENTIFIERS:
        assert key in env, f"{env_path.name}에 {key} 키가 없다"
        assert _is_placeholder(env[key]), f"{env_path.name}의 {key}가 마스킹되지 않음: {env[key]!r}"


# ---------------------------------------------------------------------------
# AC1 — 기준선 기록 문서 완전성
# ---------------------------------------------------------------------------


def test_baseline_record_contains_required_metadata():
    """기록 문서에 tag명·full commit SHA·zip 경로·sha256·생성 일시가 모두 있어야 한다."""
    text = _read_text(BASELINE_RECORD)
    assert TAG_NAME in text, "tag 이름 누락"
    assert ZIP_PATH in text, "백업 zip 경로 누락"
    assert re.search(r"\b[0-9a-f]{40}\b", text), "full commit SHA(40 hex) 누락"
    assert re.search(r"\b[0-9a-f]{64}\b", text), "백업 zip sha256(64 hex) 누락"
    # 생성 일시(KST) 기록.
    assert re.search(r"20\d{2}-\d{2}-\d{2}.*KST", text), "생성 일시(KST) 누락"


# ---------------------------------------------------------------------------
# AC3 / ADD-15 — 백업 zip 누출 방지 (.gitignore)
# ---------------------------------------------------------------------------


def test_gitignore_excludes_backups_dir():
    """secret을 포함한 백업 zip이 git에 올라가지 않도록 backups/가 ignore돼야 한다."""
    lines = {line.strip() for line in _read_text(GITIGNORE).splitlines()}
    assert "backups/" in lines, ".gitignore에 backups/ 규칙이 없다 (secret 평문 커밋 위험)"


def test_backup_zip_is_git_ignored_if_present():
    """백업 zip이 로컬에 있으면 git이 실제로 무시하는지 확인 (AC3)."""
    if shutil.which("git") is None:
        pytest.skip("git 미설치")
    if not (REPO_ROOT / ZIP_PATH).exists():
        pytest.skip("로컬에 백업 zip 없음 (gitignore라 fresh checkout에는 없음)")
    result = subprocess.run(
        ["git", "check-ignore", ZIP_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "백업 zip이 git에 의해 무시되지 않는다 (커밋 위험)"


# ---------------------------------------------------------------------------
# AC1 — 기준선 tag (로컬 전용 산출물 → 없으면 skip)
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    return shutil.which("git") is not None and (REPO_ROOT / ".git").exists()


def test_baseline_tag_is_annotated_and_matches_record():
    """로컬에 기준선 tag가 있으면 annotated이고 기록 문서의 SHA와 일치해야 한다."""
    if not _git_available():
        pytest.skip("git 저장소 아님")
    listed = subprocess.run(
        ["git", "tag", "-l", TAG_NAME], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if listed.stdout.strip() != TAG_NAME:
        pytest.skip(f"로컬에 {TAG_NAME} tag 없음 (로컬 전용 기준선 산출물)")

    kind = subprocess.run(
        ["git", "cat-file", "-t", TAG_NAME], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert kind.stdout.strip() == "tag", "기준선 tag가 annotated tag가 아니다"

    sha = subprocess.run(
        ["git", "rev-list", "-n1", TAG_NAME], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    record_shas = set(re.findall(r"\b[0-9a-f]{40}\b", _read_text(BASELINE_RECORD)))
    assert sha in record_shas, f"tag가 가리키는 commit({sha})이 기록 문서에 없다"
