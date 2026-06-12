"""Story 1.5 재사용 경계·금지 행위 거버넌스 문서 산출물 회귀 가드 (P0, FR-2/ADD-15/NFR-5).

이 스토리(1.5)는 제품 코드 변경이 아니라 **어떤 기존 자산이 "보존·wrapping 재사용" 대상인지**와
**절대 하면 안 되는 금지 행위 7가지**·**권위 계층**·**예외 기록 절차**를 한 거버넌스 문서
(`docs/qa/reuse-boundaries-and-forbidden-behaviors-20260613.md`)로 못박는 산출물 스토리다.
이 모듈은 그 문서를 자동 회귀로 고정해, 앞으로의 커밋이 (1) 문서를 삭제하거나 필수 섹션
(7개 보존 자산·4개 공개 동작·7개 금지 행위·권위 계층·예외 기록)을 누락시키거나, (2) 문서에
실제 secret(텔레그램 봇 토큰/`chat_id`/전화/이메일)을 들이지 못하게 막는다.

검증 대상 AC:
- AC1 (FR-2): 7개 보존 자산 식별 문자열 + 4개 공개 동작 + "의도 없이 바꾸면 regression" 명문화,
  required change의 구현 책임이 후속 에픽임을 명시.
- AC2 (ADD-15): 7개 금지 행위 식별 문자열 + 올바른 대안·대안 구현 책임 에픽 연결.
- AC3 (NFR-5, governance): 권위 계층(project-context.md 56개 규칙 최우선) + 예외 기록(ADR/
  architecture.md)·위반 시 "실패로 보고".
- AC4 (NFR-20, NFR-5): 문서가 존재하고, 위 섹션이 모두 포함됐는지 + 실제 secret 패턴이 없는지.

주의(테스트 철학 — 1.4 AC4 교훈):
- `tests/test_baseline_artifacts.py`·`tests/test_manual_regression_runbook.py`와 동일하게 **실제
  secret 값을 하드코딩하지 않는다.** 누출 여부는 실제값이 아니라 secret '패턴'으로 검사한다.
- **파일 전체에 `redact()`를 통과시켜 "변화 없음"을 단언하지 않는다.** 의도된 placeholder/가짜
  예시는 `redact`가 정상적으로 마스킹하므로 그 단언은 거짓 실패를 낸다. 검사는 **실제 secret
  패턴의 부재**로만 한다.
- 외부 브라우저/Telegram/Kakao/Gmail/네트워크를 호출하지 않는 **순수 파일 읽기 테스트**다
  (project-context §55).

secret 패턴 정규식·placeholder 제외 목록은 정본인 `test_manual_regression_runbook.py`에서
재사용한다(wheel 재발명 금지).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# secret 패턴 정본 재사용 (test_manual_regression_runbook.py).
from test_manual_regression_runbook import (
    CHAT_ID_PLAINTEXT_RE,
    EMAIL_RE,
    KOREAN_PHONE_RE,
    PLACEHOLDER_EMAIL_DOMAINS,
    TELEGRAM_BOT_TOKEN_RE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

GOVERNANCE_DOC = (
    REPO_ROOT / "docs" / "qa" / "reuse-boundaries-and-forbidden-behaviors-20260613.md"
)

# 외부에 커밋되는 산출물 텍스트. 실제 secret 패턴이 없어야 한다.
COMMITTED_TEXT_ARTIFACTS = [GOVERNANCE_DOC]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC4 — 산출물 존재 (NFR-20)
# ---------------------------------------------------------------------------


def test_governance_doc_exists():
    assert GOVERNANCE_DOC.is_file(), (
        f"거버넌스 산출물 누락: {GOVERNANCE_DOC.relative_to(REPO_ROOT)}"
    )


# ---------------------------------------------------------------------------
# AC1 #1·#3 — 7개 보존·wrapping 재사용 자산 식별 (FR-2)
# ---------------------------------------------------------------------------

# 7개 보존 자산을 코드 위치/식별자로 못박는 needle (배민 parser/crawler, 쿠팡 parser,
# message renderer, Telegram/Kakao sender, 쿠팡 Gmail 2FA, run_once 경계, registry).
PRESERVED_ASSET_NEEDLES = [
    "src/rider_crawl/parser.py",            # ① 배민 parser
    "crawler.py",                            # ① 배민 crawler
    "src/rider_crawl/platforms/coupang/",   # ② 쿠팡 parser
    "render_current_screen_message",         # ③ message renderer
    "src/rider_crawl/sender.py",            # ④ Telegram/Kakao sender
    "Gmail 2FA",                             # ⑤ 쿠팡 Gmail 2FA
    "run_once",                              # ⑥ run_once 실행 경계
    "rider_crawl.platforms",                 # ⑦ platforms registry
    "rider_crawl.messengers",                # ⑦ messengers registry
]


@pytest.mark.parametrize("needle", PRESERVED_ASSET_NEEDLES, ids=lambda s: s)
def test_doc_identifies_preserved_assets(needle: str):
    """7개 보존·wrapping 재사용 자산의 코드 위치/식별자가 모두 문서에 있어야 한다 (AC1 #1)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"거버넌스 문서에 보존 자산 식별 문자열 '{needle}'이(가) 없다"
    )


def test_doc_scopes_required_change_to_later_epics():
    """required change(목표 변경)의 구현 책임이 후속 에픽(Epic 2~5)이고, 본 스토리는 경계
    "문서화"이지 wrapping "구현"이 아님을 명시해야 한다 (AC1 #3)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "Required change" in text, "문서에 'Required change'(목표 변경) 열/서술이 없다"
    assert "Epic 3" in text, "required change 구현 책임 에픽(Epic 3)이 표기되지 않았다"
    assert "문서화" in text and "구현" in text, (
        "본 스토리가 경계 '문서화'(wrapping '구현' 아님)임을 명시하지 않았다"
    )


# ---------------------------------------------------------------------------
# AC1 #2 — 보존해야 할 공개 동작 4종 + "의도 없이 바꾸면 regression"
# ---------------------------------------------------------------------------

# (a) 렌더링 결과 골든, (b) 저장 JSON 호환, (c) 탭 9개 로딩, (d) 쿠팡 플랫폼 추론.
PUBLIC_BEHAVIOR_NEEDLES = [
    "test_message.py",          # (a) 렌더링 결과 골든 테스트 (재작성 금지, 연결만)
    "ensure_ascii=False",        # (b) 저장 JSON 호환
    "load_all(max_tabs=9)",      # (c) 탭 9개 로딩
    "test_architecture.py",      # (d) 쿠팡 플랫폼 추론 registry
]


@pytest.mark.parametrize("needle", PUBLIC_BEHAVIOR_NEEDLES, ids=lambda s: s)
def test_doc_lists_four_public_behaviors(needle: str):
    """보존해야 할 공개 동작 4종이 모두 문서에 나열돼야 한다 (AC1 #2)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"거버넌스 문서에 공개 동작 식별 문자열 '{needle}'이(가) 없다"
    )


def test_doc_marks_unintended_public_behavior_change_as_regression():
    """공개 동작을 의도 없이 바꾸는 변경을 '실패(regression)'로 취급한다고 못박아야 한다 (AC1 #2)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "regression" in text, "공개 동작 변경을 regression으로 취급한다는 명문화가 없다"
    assert "의도 없이" in text, "'의도 없이 바꾸면 실패'라는 표현이 없다"


# ---------------------------------------------------------------------------
# AC2 #4·#5 — 금지 행위 7가지 + 올바른 대안·대안 구현 책임 에픽 (ADD-15)
# ---------------------------------------------------------------------------

# 7개 금지 행위를 못박는 needle.
FORBIDDEN_BEHAVIOR_NEEDLES = [
    "탭 9→100",                   # ① 탭 9→100 확장 스케일링
    "배민 휴대폰 인증",            # ② 배민 휴대폰 인증 자동화/우회
    "Kakao 2건 병렬",             # ③ 같은 Windows session Kakao 2건 병렬 전송
    "Gmail token 공유",           # ④ 고객 간 Gmail token 공유
    "secret(token/password/OTP)",  # ⑤ secret 평문 저장
    "CDP 포트",                   # ⑥ 클라우드가 로컬 Chrome CDP 포트 직접 접속
    "circuit breaker",            # ⑦ backoff/circuit breaker 없는 빠른 재시도
]


@pytest.mark.parametrize("needle", FORBIDDEN_BEHAVIOR_NEEDLES, ids=lambda s: s)
def test_doc_lists_seven_forbidden_behaviors(needle: str):
    """7개 금지 행위 식별 문자열이 모두 문서에 있어야 한다 (AC2 #4)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"거버넌스 문서에 금지 행위 식별 문자열 '{needle}'이(가) 없다"
    )


def test_doc_links_forbidden_alternatives_and_responsibility_epics():
    """각 금지 행위에 올바른 대안과 구현 책임 에픽이 연결돼야 한다 (AC2 #5)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "redact" in text, "⑤ 대안으로 Story 1.3 `redact()` 재사용이 연결되지 않았다"
    for epic in ("Epic 2", "Epic 4", "Epic 5"):
        assert epic in text, f"대안 구현 책임 에픽 '{epic}'이(가) 표기되지 않았다"


# ---------------------------------------------------------------------------
# AC3 #6 — 권위 계층 (project-context.md 56개 규칙 최우선)
# ---------------------------------------------------------------------------


def test_doc_states_authority_hierarchy():
    """권위 계층: project-context.md 56개 규칙이 최우선 상위 권위임을 명시해야 한다 (AC3 #6)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "project-context.md" in text, "권위 계층에 project-context.md 참조가 없다"
    assert "56" in text, "project-context.md '56개 규칙' 수치가 명시되지 않았다"
    assert "더 제한적" in text, "'확신 없으면 더 제한적 선택' 원칙 인용이 없다"


# ---------------------------------------------------------------------------
# AC3 #7 — 예외 기록 절차 + 위반 시 실패 보고
# ---------------------------------------------------------------------------


def test_doc_states_exception_and_violation_procedure():
    """경계/금지 변경은 임의 변경 금지 → ADR/architecture.md 기록, 위반은 실패로 보고 (AC3 #7)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "ADR" in text, "예외 기록 절차에 ADR 언급이 없다"
    assert "architecture.md" in text, "예외 기록 위치(architecture.md)가 없다"
    assert "실패로 보고" in text, "위반 발견 시 '실패로 보고'하라는 명문화가 없다"


# ---------------------------------------------------------------------------
# AC4 #9 — secret 비노출 (NFR-5, ADD-15) — 실제 secret '패턴'의 부재로만 검사
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_real_telegram_bot_token(artifact: Path):
    match = TELEGRAM_BOT_TOKEN_RE.search(_read_text(artifact))
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 텔레그램 봇 토큰 형태 누출: "
        f"{match.group() if match else ''!r}"
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
    """가입자 자리가 모두 0인 placeholder(010-0000-0000)를 제외한 실제 휴대폰 형태가 없어야 한다."""
    for match in KOREAN_PHONE_RE.finditer(_read_text(artifact)):
        digits = re.sub(r"\D", "", match.group())
        if set(digits[3:]) <= {"0"}:
            continue
        pytest.fail(f"{artifact.relative_to(REPO_ROOT)} 에 실제 휴대폰 형태 누출: {match.group()!r}")


@pytest.mark.parametrize("artifact", COMMITTED_TEXT_ARTIFACTS, ids=lambda p: p.name)
def test_no_plaintext_chat_id(artifact: Path):
    """`chat_id=<digits>` 평문이 없어야 한다(placeholder `chat_id=<...>`는 허용)."""
    match = CHAT_ID_PLAINTEXT_RE.search(_read_text(artifact))
    assert match is None, (
        f"{artifact.relative_to(REPO_ROOT)} 에 chat_id 평문 누출: "
        f"{match.group() if match else ''!r}"
    )


# ===========================================================================
# 아래는 QA E2E 자동화(qa-generate-e2e-tests)로 보강한 AC 추적 커버리지다.
# 기존 30개 케이스는 보존 자산/공개 동작/금지 행위의 '존재'와 secret 비노출만
# 단언했고, 다음 AC 하위 절은 회귀 가드에서 비어 있었다:
#   - AC1 #1: 보존 자산 표가 '허용되는 변경'·'금지되는 변경' 열을 갖췄는지
#   - AC1 #3: 핵심 required change 목표(정규화 Snapshot/webhook/getUpdates/template_version)
#   - AC1 #2: 쿠팡 렌더 골든(test_coupang_message.py) 연결 + 저장 JSON 호환(indent=2) 완전형
#   - AC2 #4: 금지 행위 표가 '사유(코드/운영 근거)' 열을 갖췄는지
#   - AC2 #5: 구체 대안(KakaoSendJob FIFO queue + sender lock / backoff / `*_ref` 분리)
#   - AC3 #6: 권위 계층 4단(architecture.md·spec 계약)·"코드 구현 전 먼저"·"일반 관례보다 우선"
#   - AC3 #7: '임의 변경 금지' 명문화
# 모두 기존과 동일한 순수 파일 읽기 단언이며 외부 호출이 없다(project-context §55).
# ===========================================================================


# ---------------------------------------------------------------------------
# AC1 #1 — 보존 자산 표가 허용/금지 변경 열을 갖췄는지 (FR-2)
# ---------------------------------------------------------------------------


def test_doc_preserved_asset_table_has_allowed_and_forbidden_change_columns():
    """보존 자산은 코드 위치뿐 아니라 '허용되는 변경'·'금지되는 변경'과 함께 명시돼야 한다 (AC1 #1)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "허용되는 변경" in text, "보존 자산 표에 '허용되는 변경(wrapping/추가)' 열이 없다"
    assert "금지되는 변경" in text, "보존 자산 표에 '금지되는 변경' 열이 없다"


# ---------------------------------------------------------------------------
# AC1 #3 — 핵심 required change 목표가 표에 명시됐는지 (FR-2, 구현 책임은 후속 에픽)
# ---------------------------------------------------------------------------

# AC1 #3 예시로 못박은 목표 변경(배민 parser 정규화 Snapshot wrapping, Telegram 중앙 webhook
# 전환·per-Agent getUpdates 제거, renderer template_version)을 표에 옮겼는지 확인한다.
REQUIRED_CHANGE_NEEDLES = [
    "정규화 Snapshot",   # 배민 parser → 정규화 Snapshot으로 wrapping
    "webhook",           # Telegram → 중앙 webhook 전환
    "getUpdates",        # Telegram → per-Agent getUpdates 제거
    "template_version",  # renderer → template_version·tenant 템플릿
]


@pytest.mark.parametrize("needle", REQUIRED_CHANGE_NEEDLES, ids=lambda s: s)
def test_doc_records_required_change_targets(needle: str):
    """required change 목표(정규화 Snapshot/webhook/getUpdates/template_version)가 표에 있어야 한다 (AC1 #3)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"거버넌스 문서 보존 자산 표에 required change 목표 '{needle}'이(가) 없다"
    )


# ---------------------------------------------------------------------------
# AC1 #2 — 공개 동작 4종의 나머지 정본(쿠팡 골든 + 저장 JSON indent=2)
# ---------------------------------------------------------------------------


def test_doc_links_coupang_golden_and_json_indent():
    """(a) 쿠팡 렌더 골든(test_coupang_message.py) 연결과 (b) 저장 JSON `indent=2` 완전형이 있어야 한다 (AC1 #2)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "test_coupang_message.py" in text, (
        "공개 동작 (a)에 쿠팡 렌더 골든(test_coupang_message.py)이 연결되지 않았다"
    )
    assert "indent=2" in text, "공개 동작 (b) 저장 JSON 호환에 `indent=2`가 명시되지 않았다"


# ---------------------------------------------------------------------------
# AC2 #4 — 금지 행위 표가 사유(코드/운영 근거) 열을 갖췄는지 (ADD-15)
# ---------------------------------------------------------------------------


def test_doc_forbidden_table_has_rationale_column():
    """각 금지 행위가 '사유(코드/운영 근거)'와 함께 나열돼야 한다 (AC2 #4)."""
    assert "사유" in _read_text(GOVERNANCE_DOC), (
        "금지 행위 표에 '사유(코드/운영 근거)' 열이 없다"
    )


# ---------------------------------------------------------------------------
# AC2 #5 — 금지 행위별 구체 대안이 표에 연결됐는지 (ADD-15)
# ---------------------------------------------------------------------------

# AC2 #5가 예시로 든 구체 대안: ③ KakaoSendJob FIFO queue + sender lock, ⑦ exponential
# backoff·circuit breaker, ⑤ secret_ref(`*_ref`만 저장) 분리.
FORBIDDEN_ALTERNATIVE_NEEDLES = [
    "FIFO queue",   # ③ Kakao 직렬 전송 대안
    "sender lock",  # ③ 전역 sender lock
    "backoff",      # ⑦ exponential backoff 대안
    "*_ref",        # ⑤ secret 값 분리(secret_ref) 대안
]


@pytest.mark.parametrize("needle", FORBIDDEN_ALTERNATIVE_NEEDLES, ids=lambda s: s)
def test_doc_records_specific_forbidden_alternatives(needle: str):
    """금지 행위별 구체 대안(FIFO queue/sender lock/backoff/`*_ref`)이 표에 있어야 한다 (AC2 #5)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"거버넌스 문서 금지 행위 표에 구체 대안 '{needle}'이(가) 없다"
    )


# ---------------------------------------------------------------------------
# AC3 #6 — 권위 계층 4단 전체 + "코드 구현 전 먼저"·"일반 관례보다 우선" 원칙
# ---------------------------------------------------------------------------

# 권위 계층: project-context.md → 본 문서 → architecture.md → spec 계약. 하위 3·4단(
# architecture.md, implementation-contract.md, operations-security-test-contract.md)이
# 모두 명시돼야 계층이 완전하다(기존 테스트는 최우선 project-context.md만 단언).
AUTHORITY_TIER_NEEDLES = [
    "architecture.md",                        # 3단: 컴포넌트/데이터 경계·ADR
    "implementation-contract.md",             # 4단: Reuse And Replace
    "operations-security-test-contract.md",   # 4단: Forbidden Behaviors
]


@pytest.mark.parametrize("needle", AUTHORITY_TIER_NEEDLES, ids=lambda s: s)
def test_doc_authority_hierarchy_names_lower_tiers(needle: str):
    """권위 계층이 하위 3·4단(architecture.md·spec 계약)까지 모두 명시해야 한다 (AC3 #6)."""
    assert needle in _read_text(GOVERNANCE_DOC), (
        f"권위 계층에 '{needle}' 단계가 명시되지 않았다"
    )


def test_doc_authority_principle_read_before_coding():
    """권위 원칙: '코드 구현 전 먼저 읽고', '일반 관례보다 우선'을 인용해야 한다 (AC3 #6)."""
    text = _read_text(GOVERNANCE_DOC)
    assert "코드 구현 전" in text or "코드를 구현하기 전" in text, (
        "'코드 구현 전 먼저 읽는다' 원칙 인용이 없다"
    )
    assert "일반 관례보다 우선" in text, "'일반 관례보다 우선' 원칙 인용이 없다"


# ---------------------------------------------------------------------------
# AC3 #7 — '임의 변경 금지' 명문화 (governance)
# ---------------------------------------------------------------------------


def test_doc_forbids_arbitrary_change():
    """경계/금지 규칙을 바꿔야 할 때 '임의 변경 금지'를 명문화해야 한다 (AC3 #7)."""
    assert "임의 변경" in _read_text(GOVERNANCE_DOC), (
        "'임의 변경 금지'(→ ADR/예외 기록) 명문화가 없다"
    )
