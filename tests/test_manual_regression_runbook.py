"""Story 1.4 수동 회귀 런북·dry-run 기준선 산출물 회귀 가드 (QA 자동화, P0-05).

이 스토리(1.4)는 제품 코드 변경이 아니라 **기존 2탭(배민/쿠팡) 수동 회귀·dry-run 절차**를
런북과 기준선 기록으로 문서화하는 산출물 스토리다. 이 모듈은 그 수동 절차를 자동 회귀로 고정해,
앞으로의 커밋이 (1) 런북/기준선 산출물을 삭제하거나 필수 절차·비교 방법 섹션을 누락시키거나,
(2) 두 산출물에 실제 secret(텔레그램 봇 토큰/`chat_id`/전화/이메일)을 들이지 못하게 막는다.

검증 대상 AC:
- AC1 (P0-05, FR-1): 런북에 4개 절차(배민 run / 쿠팡 run / Telegram / Kakao 테스트 전송)와
  "실발송"(비발송) 단계가 모두 문서화됐는지.
- AC3 (FR-3, NFR-24): 재실행·비교 방법 섹션이 런북·기준선에 포함됐는지.
- AC4 (NFR-20): 두 산출물 파일이 존재하는지.
- AC4/AC2 (NFR-5, ADD-15): 두 산출물에 실제 secret 패턴이 없는지.

주의(테스트 철학):
- `tests/test_baseline_artifacts.py`와 동일하게 **실제 secret 값을 하드코딩하지 않는다**. 누출
  여부는 실제값이 아니라 secret '패턴'(봇 토큰 형태 등)으로 검사한다.
- **파일 전체에 `redact()`를 통과시켜 "변화 없음"을 단언하지 않는다.** 의도된 placeholder/가짜
  예시는 `redact`가 정상적으로 마스킹하므로 그 단언은 거짓 실패를 낸다. 검사는 **실제 secret
  패턴의 부재**로만 한다.
- 외부 브라우저/Telegram/Kakao/Gmail/네트워크를 호출하지 않는 **순수 파일 읽기 테스트**다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RUNBOOK = REPO_ROOT / "docs" / "qa" / "manual-regression-runbook-20260613.md"
DRY_RUN_BASELINE = REPO_ROOT / "docs" / "qa" / "dry-run-baseline-20260613.md"

# 외부에 커밋되는 산출물 텍스트. 어떤 파일에도 실제 secret 패턴이 없어야 한다.
COMMITTED_TEXT_ARTIFACTS = [RUNBOOK, DRY_RUN_BASELINE]

# 텔레그램 봇 토큰 형태: <6자리 이상 봇 id>:<30자 이상 영숫자/_-> (test_baseline_artifacts와 동일).
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")

# full email. example.* 테스트 도메인은 의도된 가짜 예시이므로 제외(거짓 실패 방지).
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
PLACEHOLDER_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

# 한국 휴대폰 형태(010/011/016/017/018/019 + 3~4 + 4자리). 모두 0인 placeholder는 제외.
KOREAN_PHONE_RE = re.compile(r"\b01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}\b")

# `chat_id=<digits>` 평문(placeholder `chat_id=<...>`는 매칭 안 됨 — 뒤가 숫자가 아님).
CHAT_ID_PLAINTEXT_RE = re.compile(r"chat_id\s*=\s*\d+", re.IGNORECASE)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC4 — 산출물 존재 (NFR-20)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_artifact_exists(artifact: Path):
    assert artifact.is_file(), f"산출물 누락: {artifact.relative_to(REPO_ROOT)}"


# ---------------------------------------------------------------------------
# AC1 — 런북 4개 절차 + 실발송(비발송) 단계 완전성 (P0-05, FR-1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    ["배민 run", "쿠팡 run", "Telegram", "Kakao"],
    ids=["baemin-run", "coupang-run", "telegram", "kakao"],
)
def test_runbook_documents_four_procedures(needle: str):
    """4개 수동 회귀 절차(배민 run / 쿠팡 run / Telegram / Kakao)가 런북에 모두 있어야 한다."""
    assert needle in _read_text(RUNBOOK), f"런북에 '{needle}' 절차 식별 문자열이 없다"


def test_runbook_documents_no_send_step():
    """각 절차의 '실발송 OFF/비발송' 확인 단계가 런북에 기술돼야 한다 (FR-3)."""
    text = _read_text(RUNBOOK)
    assert "실발송" in text, "런북에 '실발송'(비발송) 단계 서술이 없다"
    assert "send_enabled" in text, "런북에 비발송 경로(send_enabled=False) 근거가 없다"


def test_runbook_references_kakao_checklist_instead_of_duplicating():
    """Kakao 절차는 기존 정본 체크리스트를 재사용(교차참조)해야 한다 (AC1 #3, wheel 재발명 금지)."""
    assert "kakao-verification-checklist" in _read_text(RUNBOOK), (
        "Kakao 절차가 기존 docs/kakao-verification-checklist.md를 참조하지 않는다"
    )


# ---------------------------------------------------------------------------
# AC3 — 재실행·비교 방법 섹션 (FR-3, NFR-24)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_comparison_method_section_present(artifact: Path):
    """런북과 기준선 모두에 '비교 방법' 섹션 표제가 있어야 한다 (AC3)."""
    assert "비교 방법" in _read_text(artifact), (
        f"{artifact.name}에 비교 방법 섹션이 없다"
    )


def test_comparison_method_links_golden_tests_without_rewriting():
    """결정적 렌더 형식 회귀는 기존 골든 테스트가 잠금 — 비교 방법이 이를 연결해야 한다 (AC3 #8)."""
    text = _read_text(DRY_RUN_BASELINE)
    assert "test_message.py" in text and "test_coupang_message.py" in text, (
        "비교 방법이 기존 골든 테스트(test_message.py/test_coupang_message.py)를 연결하지 않는다"
    )


def test_dry_run_baseline_records_skeleton_and_hash_fields():
    """기준선 기록에 sanitized 스켈레톤과 sha256 항목이 표로 정의돼 있어야 한다 (AC2)."""
    text = _read_text(DRY_RUN_BASELINE)
    assert "스켈레톤" in text, "기준선에 sanitized 메시지 스켈레톤이 없다"
    assert "sha256" in text, "기준선에 sha256 기록 항목이 없다"


# ---------------------------------------------------------------------------
# AC4 / AC2 — secret 비노출 (NFR-5, ADD-15) — 실제 secret '패턴'의 부재로만 검사
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_real_telegram_bot_token(artifact: Path):
    match = TELEGRAM_BOT_TOKEN_RE.search(_read_text(artifact))
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 텔레그램 봇 토큰 형태 누출: {match.group() if match else ''!r}"
    )


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_real_email(artifact: Path):
    """example.* 가짜 도메인을 제외한 실제 full email이 없어야 한다."""
    for match in EMAIL_RE.finditer(_read_text(artifact)):
        email = match.group()
        if email.lower().endswith(PLACEHOLDER_EMAIL_DOMAINS):
            continue  # 의도된 가짜 예시 도메인은 누출이 아니다.
        pytest.fail(f"{artifact.relative_to(REPO_ROOT)} 에 실제 email 형태 누출: {email!r}")


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_real_korean_phone(artifact: Path):
    """가입자 자리가 모두 0인 placeholder(예: 010-0000-0000)를 제외한 실제 휴대폰 형태가 없어야 한다."""
    for match in KOREAN_PHONE_RE.finditer(_read_text(artifact)):
        digits = re.sub(r"\D", "", match.group())
        # 011/016 식 3자리 통신사 prefix를 떼고 가입자 번호만 본다. 가입자 자리가 모두 0이면
        # (010-0000-0000 등) 의도된 placeholder이므로 누출이 아니다.
        if set(digits[3:]) <= {"0"}:
            continue
        pytest.fail(f"{artifact.relative_to(REPO_ROOT)} 에 실제 휴대폰 형태 누출: {match.group()!r}")


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_plaintext_chat_id(artifact: Path):
    """`chat_id=<digits>` 평문이 없어야 한다(placeholder `chat_id=<...>`는 허용)."""
    match = CHAT_ID_PLAINTEXT_RE.search(_read_text(artifact))
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 chat_id 평문 누출: {match.group() if match else ''!r}"
    )


# ===========================================================================
# 아래는 QA E2E 자동화(qa-generate-e2e-tests)로 보강한 AC 추적 커버리지다.
# 기존 테스트는 4개 절차의 '존재'와 secret 비노출만 단언했고, 다음 AC 항목은
# 회귀 가드에서 비어 있었다:
#   - AC1 #2: 각 절차의 '기대 결과'(렌더 메시지 형태) 문서화
#   - AC2 #4·#5: dry-run 기준선 캡처 메타데이터 + '실발송 없음' 표시
#   - AC3 #7: 비교 방법 세부(동일 입력 sha256 일치 + 숫자 제외 형태/라벨 비교)
#   - AC3 #9 / NFR-24: cutover 규칙(운영자 승인 후에만 활성화, 자동 활성화 금지)
# 모두 순수 파일 읽기 단언이며 외부 호출이 없다(project-context §55).
# ===========================================================================


# ---------------------------------------------------------------------------
# AC1 #2 — 각 절차의 기대 결과(렌더 메시지 형태) 문서화 (P0-05, FR-1)
# ---------------------------------------------------------------------------

# 배민 현재화면 렌더의 4개 피크 줄 라벨 (message.py `_render_baemin_current_screen_message`).
BAEMIN_PEAK_LABELS = ("오전오후피크", "오후논피크", "저녁피크", "저녁논피크")


def test_runbook_documents_message_header_and_collection_success():
    """기대 결과: 공용 헤더(`[실시간 실적봇]`)와 수집 성공 판정 기준이 런북에 명시돼야 한다 (AC1 #2)."""
    text = _read_text(RUNBOOK)
    assert "[실시간 실적봇]" in text, "기대 메시지 공용 헤더(`[실시간 실적봇]`)가 런북에 없다"
    assert "수집 성공" in text, "수집 성공 판정 기준 서술이 런북에 없다"


@pytest.mark.parametrize("label", BAEMIN_PEAK_LABELS, ids=list(BAEMIN_PEAK_LABELS))
def test_runbook_documents_baemin_four_peak_labels(label: str):
    """배민 기대 메시지 형태: 4개 피크 줄 라벨이 모두 문서화돼야 한다 (AC1 #2)."""
    assert label in _read_text(RUNBOOK), f"배민 기대 형태에 '{label}' 줄 라벨이 없다"


def test_runbook_documents_baemin_optional_reject_rate():
    """배민 기대 형태의 선택적 '거절율' 줄이 문서화돼야 한다 (AC1 #2)."""
    assert "거절율" in _read_text(RUNBOOK), "배민 기대 형태에 '거절율' 줄 서술이 없다"


def test_runbook_documents_coupang_dashboard_form():
    """쿠팡 기대 메시지 형태: 피크 대시보드 실적(배정/처리·거절률)이 문서화돼야 한다 (AC1 #2)."""
    text = _read_text(RUNBOOK)
    assert "배정" in text and "거절률" in text, (
        "쿠팡 피크 대시보드 실적 형태(배정/처리·거절률) 서술이 런북에 없다"
    )


# ---------------------------------------------------------------------------
# AC2 #4·#5 — dry-run 기준선 캡처 메타데이터 + 비발송 표시 (FR-3, FR-1)
# ---------------------------------------------------------------------------

# 캡처 메타데이터 최소 항목 (일시 KST, 플랫폼, 탭 라벨, 실행 방식).
CAPTURE_METADATA_FIELDS = ("플랫폼", "탭 라벨", "캡처 일시", "실행 방식")


@pytest.mark.parametrize("field", CAPTURE_METADATA_FIELDS, ids=list(CAPTURE_METADATA_FIELDS))
def test_dry_run_baseline_records_capture_metadata(field: str):
    """기준선 표에 캡처 메타데이터(플랫폼/탭 라벨/캡처 일시/실행 방식)가 있어야 한다 (AC2 #4)."""
    assert field in _read_text(DRY_RUN_BASELINE), f"기준선에 '{field}' 캡처 메타데이터 항목이 없다"


def test_dry_run_baseline_capture_time_is_kst():
    """캡처 일시는 KST 기준으로 기록돼야 한다 (AC2 #4)."""
    assert "KST" in _read_text(DRY_RUN_BASELINE), "기준선 캡처 일시에 KST 표기가 없다"


def test_dry_run_baseline_marks_no_real_send():
    """기준선 각 대상에 '실발송 없음'(`sent=False`) 표시가 있어야 한다 (AC2 #5, FR-3)."""
    text = _read_text(DRY_RUN_BASELINE)
    assert "실발송 없음" in text, "기준선에 '실발송 없음' 표시가 없다"
    assert "sent=False" in text, "기준선에 비발송 근거(`sent=False`)가 없다"


# ---------------------------------------------------------------------------
# AC3 #7 — 비교 방법 세부(동일 입력 sha256 일치 + 숫자 제외 형태/라벨 비교)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_comparison_method_documents_sha256_equality(artifact: Path):
    """비교 방법 (a): 동일 입력일 때 sha256 일치로 형태 동일성을 확인함을 명시해야 한다 (AC3 #7)."""
    text = _read_text(artifact)
    assert "sha256" in text and "일치" in text, (
        f"{artifact.name} 비교 방법에 동일 입력 sha256 일치 비교가 없다"
    )


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_comparison_method_documents_shape_compare_excluding_numbers(artifact: Path):
    """비교 방법 (b): 라이브 dry-run은 숫자 제외 형태/라벨 비교로 판정함을 명시해야 한다 (AC3 #7)."""
    text = _read_text(artifact)
    assert "형태" in text and "숫자" in text, (
        f"{artifact.name} 비교 방법에 숫자 제외 형태/라벨 비교 서술이 없다"
    )


# ---------------------------------------------------------------------------
# AC3 #9 / NFR-24 — cutover 규칙(운영자 승인 후에만 활성화, 자동 활성화 금지)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_cutover_rule_requires_operator_approval(artifact: Path):
    """cutover 규칙(NFR-24): dry-run 실발송 없음 + 운영자 승인 후에만 활성화가 명시돼야 한다 (AC3 #9)."""
    text = _read_text(artifact)
    assert "NFR-24" in text, f"{artifact.name}에 NFR-24 cutover 근거가 없다"
    assert "승인" in text, f"{artifact.name} cutover 규칙에 '운영자 승인 후에만 활성화'가 없다"
    assert "자동" in text, f"{artifact.name} cutover 규칙에 '차이 시 자동 활성화 금지'가 없다"
