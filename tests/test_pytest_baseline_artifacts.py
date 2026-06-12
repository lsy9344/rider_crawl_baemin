"""Story 1.2 pytest 기준선 리포트 회귀 가드 (QA E2E 자동화).

Story 1.2(P0-03)는 제품 코드 변경이 아니라, 리팩토링 시작 시점의 전체 pytest
결과를 1회 실행해 통과/실패/스킵으로 분류·보관하는 절차/산출물 스토리다.
산출물은 ``docs/qa/`` 아래 ① 분류 리포트(md), ② JUnit XML raw 결과,
③ ``-v`` per-test 텍스트 로그 3종이며, 원래는 수동 체크리스트로 검증됐다.
이 모듈은 그 수동 검증을 durable 회귀 테스트로 고정해, 앞으로의 커밋이 기준선
산출물에 실제 secret을 들이거나(누출) 필수 메타데이터·머신리더블 비교 키를
누락·훼손하지 못하게 막는다.

검증 대상 AC:
- AC1 (P0-03): 분류·집계 리포트 + 필수 메타데이터 + 머신리더블 raw 산출물의 존재/완전성.
- AC2 (NFR-20, FR-2): 회귀 비교 기준선으로서의 사용성 — 안정적 비교 키(JUnit
  ``<testcase>`` nodeid)와 향후 비교 절차가 문서화돼 있어야 함.
- AC3: "이미 실패/skip 집합" vs "must-not-break 통과 집합" 분류가 기록돼 있어야 함.
- NFR-5 / ADD-15: 산출물(md/xml/txt)에 실제 secret 평문이 비노출이어야 함.

설계 원칙(test_baseline_artifacts.py와 동일): 프로젝트 규칙에 따라 **실제 secret
값을 하드코딩하지 않는다.** 누출은 실제값이 아니라 secret '패턴'으로 검사한다.
기대 수치(passed 수 등)는 베껴 쓰지 않고 JUnit XML(authoritative)에서 파싱해
리포트(md)·텍스트 로그(txt)와 교차검증한다 — 기준선이 재생성돼도 함께 따라간다.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

QA_DIR = REPO_ROOT / "docs" / "qa"
BASELINE_MD = QA_DIR / "pytest-baseline-20260613.md"
BASELINE_XML = QA_DIR / "pytest-baseline-20260613.xml"
BASELINE_TXT = QA_DIR / "pytest-baseline-20260613.txt"

# 커밋되는 기준선 산출물 3종. 어떤 파일에도 실제 secret이 없어야 한다.
ALL_ARTIFACTS = [BASELINE_MD, BASELINE_XML, BASELINE_TXT]

# 텔레그램 봇 토큰 형태: <8자리 이상 봇 id>:<30자 이상 영숫자/_- 토큰>. (test_baseline_artifacts.py와 동일)
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
# 이메일 주소 형태(쿠팡 Gmail 2FA 계정 등 실제 메일이 traceback에 혼입되는 것 방지).
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# 한국 휴대폰/대표번호 형태(보험사 번호·운영 연락처 혼입 방지).
KOREAN_PHONE_RE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")

SECRET_PATTERNS = [
    ("telegram_bot_token", TELEGRAM_BOT_TOKEN_RE),
    ("email_address", EMAIL_RE),
    ("korean_phone_number", KOREAN_PHONE_RE),
]


def _read_lenient(path: Path) -> str:
    """산출물 텍스트를 인코딩에 관대하게 읽는다.

    ``-v`` 텍스트 로그(txt)는 Windows 콘솔 출력이라 cp949로 저장된다(경로의 한글
    포함). md/xml은 utf-8이다. 탐지 대상 secret 패턴은 모두 ASCII라 어떤 인코딩으로
    디코딩해도 동일하게 검출된다.
    """
    data = path.read_bytes()
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="ignore")


def _parse_testsuite(path: Path) -> ET.Element:
    """JUnit XML을 파싱해 authoritative ``<testsuite>`` 노드를 돌려준다."""
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None, "JUnit XML에 <testsuite> 노드가 없다"
    return suite


def _suite_counts(suite: ET.Element) -> dict[str, int]:
    counts = {k: int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


# ---------------------------------------------------------------------------
# AC1 / AC3 — 산출물 존재 (분류 리포트 + 머신리더블 raw 2종)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=lambda p: p.name)
def test_baseline_artifact_exists(artifact: Path):
    assert artifact.is_file(), f"기준선 산출물 누락: {artifact.relative_to(REPO_ROOT)}"


# ---------------------------------------------------------------------------
# NFR-5 / ADD-15 — secret 비노출 (핵심 회귀 가드)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=lambda p: p.name)
@pytest.mark.parametrize("pattern_name,pattern", SECRET_PATTERNS, ids=[n for n, _ in SECRET_PATTERNS])
def test_no_secret_pattern_in_artifacts(artifact: Path, pattern_name: str, pattern: re.Pattern[str]):
    """기준선 산출물 어디에도 실제 secret 형태가 남으면 안 된다 (NFR-5, ADD-15).

    all-green 기준선에는 traceback이 없어 현재는 공집합이지만, 향후 실패가 생겨
    assert 메시지·traceback이 raw 산출물에 캡처될 때 운영 secret이 혼입되는 회귀를
    이 가드가 막는다. nodeid에 ``token``/``chat_id`` 같은 *단어*는 있을 수 있으나
    실제 secret *값*은 없어야 한다.
    """
    text = _read_lenient(artifact)
    match = pattern.search(text)
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 {pattern_name} 형태 누출: "
        f"{match.group() if match else ''!r}"
    )


# ---------------------------------------------------------------------------
# AC1 / AC2 — JUnit XML 정합성과 안정적 비교 키(nodeid) 보존
# ---------------------------------------------------------------------------


def test_junit_xml_is_wellformed_and_self_consistent():
    """raw JUnit XML이 파싱 가능하고 testcase 수가 testsuite 집계와 일치해야 한다."""
    suite = _parse_testsuite(BASELINE_XML)
    counts = _suite_counts(suite)
    testcases = suite.findall("testcase")
    assert len(testcases) == counts["tests"], (
        f"testcase 노드 수({len(testcases)})가 testsuite tests 속성({counts['tests']})과 불일치"
    )


def test_junit_testcases_preserve_stable_comparison_key():
    """모든 testcase가 안정적 비교 키(classname+name = nodeid)를 보존해야 한다 (AC2/NFR-20)."""
    suite = _parse_testsuite(BASELINE_XML)
    testcases = suite.findall("testcase")
    assert testcases, "비교 키로 쓸 testcase 노드가 없다"
    for tc in testcases:
        assert tc.get("classname"), "testcase에 classname(비교 키) 누락"
        assert tc.get("name"), "testcase에 name(비교 키) 누락"


# ---------------------------------------------------------------------------
# AC1 — 리포트 집계가 머신리더블 raw(authoritative)와 일치
# ---------------------------------------------------------------------------


def test_report_aggregate_matches_junit_xml():
    """분류 리포트(md)의 집계가 JUnit XML 집계와 일치해야 한다 (AC1, authoritative)."""
    counts = _suite_counts(_parse_testsuite(BASELINE_XML))
    md = _read_lenient(BASELINE_MD)
    for attr in ("tests", "failures", "errors", "skipped"):
        token = f'{attr}="{counts[attr]}"'
        assert token in md, f"리포트가 JUnit 집계 {token} 를 인용/일치하지 않는다"
    assert str(counts["passed"]) in md, f"리포트에 passed 집계({counts['passed']})가 없다"
    # 실패/에러/스킵 0이면 리포트가 'all-green'으로 명시해야 한다 (AC3 분류 정책).
    if counts["failures"] == 0 and counts["errors"] == 0 and counts["skipped"] == 0:
        assert "all-green" in md, "실패/에러/스킵 0인데 리포트에 'all-green' 명시가 없다"


# ---------------------------------------------------------------------------
# AC1 — 리포트 필수 메타데이터 완전성
# ---------------------------------------------------------------------------


def test_report_contains_required_metadata():
    """리포트에 실행 일시(KST)·commit SHA·브랜치·Python/pytest 버전·환경·명령·raw 경로가 있어야 한다."""
    md = _read_lenient(BASELINE_MD)
    assert re.search(r"20\d{2}-\d{2}-\d{2}.*KST", md), "실행 일시(KST) 누락"
    assert re.search(r"\b[0-9a-f]{40}\b", md), "기준선 commit SHA(40 hex full) 누락"
    assert "refactoring" in md, "기준선 브랜치(refactoring) 누락"
    assert "Python" in md and re.search(r"\b3\.\d+\.\d+\b", md), "Python 버전 누락"
    assert "pytest" in md and re.search(r"\b\d+\.\d+\.\d+\b", md), "pytest 버전 누락"
    assert re.search(r"win32|Windows|venv", md), "실행 환경(OS/런타임) 누락"
    assert "--junit-xml" in md, "실행 명령(pytest --junit-xml) 누락"
    assert BASELINE_XML.name in md, "raw JUnit XML 경로 누락"
    assert BASELINE_TXT.name in md, "raw -v 텍스트 로그 경로 누락"


# ---------------------------------------------------------------------------
# AC2 / AC3 — 회귀 비교 방법과 분류 정책 문서화
# ---------------------------------------------------------------------------


def test_report_documents_regression_comparison_contract():
    """리포트가 must-not-break 집합·비교 키·재실행 명령·skip 분류를 문서화해야 한다 (AC2/AC3)."""
    md = _read_lenient(BASELINE_MD)
    assert "must-not-break" in md, "must-not-break 통과 집합 명시 누락"
    assert "nodeid" in md, "안정적 비교 키(test nodeid) 명시 누락"
    assert re.search(r"pytest[^\n]*--junit-xml", md), "재실행/비교용 pytest 명령 누락"
    assert "skip" in md.lower(), "이미 실패/skip 분류 기록 누락"


# ---------------------------------------------------------------------------
# AC1 — -v 텍스트 로그가 per-test outcome 로그로 유효
# ---------------------------------------------------------------------------


def test_txt_log_is_valid_verbose_per_test_log():
    """txt가 per-test outcome 라인을 담은 ``-v`` 로그이고 passed 요약이 JUnit과 일치해야 한다."""
    txt = _read_lenient(BASELINE_TXT)
    counts = _suite_counts(_parse_testsuite(BASELINE_XML))
    # per-test outcome 라인 존재(머신리더블 대안 산출물). 긴 nodeid는 줄바꿈될 수 있어
    # 줄 수 일치 대신 '존재'만 본다 — 권위 있는 수치는 JUnit/요약 라인으로 교차검증한다.
    per_test = re.findall(
        r"^tests/\S+::\S+\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)", txt, re.MULTILINE
    )
    assert per_test, "txt에 per-test outcome 라인이 없다(-v 로그가 아님)"
    assert re.search(rf"\b{counts['passed']} passed\b", txt), (
        f"txt 요약의 passed 수가 JUnit 집계({counts['passed']})와 불일치"
    )
    # all-green이면 txt 요약에 failed/error 흔적이 없어야 한다.
    if counts["failures"] == 0 and counts["errors"] == 0:
        assert re.search(r"\d+ failed", txt) is None, "all-green인데 txt 요약에 failed가 있다"
        assert re.search(r"\d+ error", txt) is None, "all-green인데 txt 요약에 error가 있다"
