# Test Automation Summary — Story 1.1 (기준선 branch/tag·설정 백업)

생성일: 2026-06-13 · 작성: QA 자동화 (bmad-qa-generate-e2e-tests) · 프레임워크: pytest

## 컨텍스트

Story 1.1은 제품 코드 변경이 아니라 **운영 기준선 고정 절차/산출물 스토리**다
(git tag, 백업 zip, sanitized 설정 샘플, `docs/qa/` 기록 문서). UI·API가 없어
전통적 E2E 대상은 아니지만, **AC가 보안·무결성 속성**(secret 비노출, 기록 완전성,
백업 zip 비추적)이라 회귀 가드로 자동화할 가치가 높다. 기존 수동 체크리스트를
durable pytest 회귀 테스트로 고정했다.

## Generated Tests

### Regression / Security Tests
- [x] `tests/test_baseline_artifacts.py` — 기준선 산출물 회귀 가드 (24 케이스)

## 적용한 갭 (auto-applied)

수동으로만 검증되던 AC들에 자동 회귀 가드가 없던 것이 핵심 갭이었다. 다음을 자동화로 고정:

| 갭 | AC / 요구사항 | 자동 테스트 |
| --- | --- | --- |
| sanitized 샘플에 실제 봇 토큰 형태 누출 검사 없음 | AC2/AC5, NFR-5, ADD-15 | `test_no_real_telegram_bot_token_in_artifacts` (5개 파일 파라미터화) |
| `ui_settings.sample.json` 민감 3필드 placeholder 미보장 | AC2 | `test_ui_settings_sample_telegram_fields_are_placeholders` |
| 운영 식별자(센터명/ID/카카오 방명) 마스킹 미보장 | AC2 | `test_ui_settings_sample_operating_identifiers_are_placeholders`, `test_env_operating_identifiers_are_placeholders` |
| `.env.*`의 TELEGRAM_* 빈값 미보장 | AC2/AC5 | `test_env_telegram_secrets_are_empty` |
| 보험사 실제 전화번호 placeholder 치환 미검증 | AC2 | `test_config_sample_phone_numbers_are_zero_placeholders` |
| 기록 문서 필수 메타데이터(tag·SHA·zip·sha256·일시) 완전성 미검증 | AC1 | `test_baseline_record_contains_required_metadata` |
| 백업 zip 비추적(`backups/` ignore) 회귀 가드 없음 | AC3, ADD-15 | `test_gitignore_excludes_backups_dir`, `test_backup_zip_is_git_ignored_if_present` |
| 기준선 tag annotated·기록 SHA 일치 미검증 | AC1 | `test_baseline_tag_is_annotated_and_matches_record` |
| JSON 스타일(2칸 들여쓰기, ensure_ascii=False)·대표 1탭 유지 | Task 4 정책 | `test_ui_settings_sample_*` |

설계 원칙: 프로젝트 규칙대로 **실제 secret 값을 테스트에 하드코딩하지 않는다.**
누출은 실제값이 아니라 secret '패턴'(텔레그램 봇 토큰 형태 `\d{6,}:[\w-]{30,}`,
0이 아닌 전화번호 숫자열)으로 검사한다. 로컬 전용 산출물(git tag, 백업 zip)은
존재할 때만 검증하고 fresh checkout/CI에서는 `skip`한다(가짜 실패 방지).

## Coverage

| Acceptance Criterion | 커버 |
| --- | --- |
| AC1 — tag·백업·기록 생성 (P0-01) | ✅ 기록 메타 완전성 + tag annotated/SHA 일치 (tag 있을 때) |
| AC2 — sanitized 샘플 placeholder (P0-02) | ✅ telegram 3필드·운영 식별자·전화번호 placeholder |
| AC2/AC5 — 실제 secret 비노출 (NFR-5/ADD-15) | ✅ 5개 커밋 산출물 봇토큰 패턴 0건 + TELEGRAM_* 빈값 |
| AC3 — 원본 보존 / 백업 비추적 (NFR-18) | ✅ `backups/` gitignore + zip git-ignored 확인 |

자동 검증 불가(설계상 skip 처리): 백업 zip의 sha256 무결성(zip이 gitignore라 CI에
없음), 원본 `runtime/`·`logs/` 미변경(로컬 mtime/내용 기준 — 1.1 dev 단계에서 수동 확인 완료).

## Validation Results

- 신규 모듈: **24 passed** (`pytest tests/test_baseline_artifacts.py`)
- 전체 스위트: **421 passed in 2.94s** (기존 397 + 신규 24, 회귀 없음)
- 누출 정규식 teeth 확인: 가짜 토큰 형태 탐지 ✅ / CDP URL·타임스탬프·placeholder 오탐 0건 ✅
- 실행: Windows venv `.venv/Scripts/python.exe -m pytest` (Python 3.11.9, pytest 9.0.3)

## Next Steps

- CI 파이프라인에 포함(이 가드는 외부 입력·네트워크 의존이 없어 CI 친화적).
- 후속 Story 1.2가 `docs/qa/`에 pytest 기준선 리포트를 추가하므로, 본 가드의
  기록-문서 컨벤션 검증과 충돌하지 않게 유지.
- 새 탭/플랫폼 추가로 샘플 구조가 바뀌면 placeholder 가드 케이스를 함께 갱신.
