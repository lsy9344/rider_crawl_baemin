# Test Automation Summary — Story 2.3 (플랫폼 중립 Target 필드 통일)

- 작성자: QA 자동화 (bmad-qa-generate-e2e-tests)
- 대상 스토리: `_bmad-output/implementation-artifacts/2-3-플랫폼-중립-target-필드-통일.md`
- 날짜: 2026-06-13
- 테스트 프레임워크: pytest (`pyproject.toml` `pythonpath=["src"]`, `testpaths=["tests"]`)
- 실행 커맨드: `.venv/Scripts/python.exe -m pytest -q`
- 성격: 순수 단위/계약 테스트(외부 브라우저·텔레그램·Gmail 미호출). UI/네트워크가 없는 dataclass 접근자·순수 함수라 별도 E2E 런타임 없이 단위 경계로 검증.

## 기준선

| 항목 | 값 |
| --- | --- |
| QA 진입 전 전체 스위트 | **632 passed** |
| QA 갭 테스트 추가 후 | **642 passed** (신규 10) |
| 회귀 | 0건 (기존 통과 무변동) |

## 갭 분석 결과

구현(dev-story)이 AC1~AC7 전반에 14개 테스트를 이미 보유해 커버리지가 넓었다. AC 대비 정밀 분석으로 **의미 있는 갭 4종**을 식별해 모두 자동 적용했다(중복/저가치 추가는 의도적으로 배제).

| # | 갭 | AC 근거 | 추가 위치 |
| --- | --- | --- | --- |
| 1 | raise 경로(`_require_coupang_center`)와 flag 경로(`coupang_center_name_risk`)가 **동일 조건 단일 소스에 합의**함을 잠그는 드리프트 방지 테스트 부재 | AC3 / Task 3 (조건 단일 소스화) | `tests/test_config.py` |
| 2 | empty vs 배민-기본값의 **사유(reason) 구분** 미검증(둘 다 truthy만 확인) | AC3 #5 | `tests/test_config.py` |
| 3 | AppConfig 중립 접근자가 **dataclass 필드/`asdict`에 안 잡힘 + read-only(frozen)** 임을 잠그는 테스트 부재(UiSettings엔 존재) | AC2/AC4 | `tests/test_config.py` |
| 4 | `to_app_config()` **변환 경계**를 거친 뒤에도 중립 필드가 동일 값임을 잠그는 테스트 부재 | AC1 (동일 Target 필드 집합) | `tests/test_ui_settings.py` |

## 생성된 테스트 (신규 10 케이스)

### `tests/test_config.py` (+9)

- `test_coupang_center_name_risk_distinguishes_empty_from_baemin_default_reason` — 두 위험 사유가 서로 다름(운영자 식별 가능).
- `test_risk_classifier_agrees_with_require_coupang_center_single_source` — 6 파라미터(빈값/공백/배민-기본값/공백패딩/실제센터/회사명포함). raise 입력 ↔ 위험 분류가 항상 일치(드리프트 시 실패).
- `test_app_config_neutral_accessors_are_not_dataclass_fields` — 4개 중립 이름이 `dataclasses.fields`·`asdict` 어디에도 없음(직렬화 불변).
- `test_app_config_neutral_accessors_are_read_only_on_frozen_config` — frozen에서도 읽기 가능, 덮어쓰기는 `AttributeError`(읽기 전용 별칭).

### `tests/test_ui_settings.py` (+1)

- `test_to_app_config_preserves_neutral_target_fields` — UiSettings→AppConfig 변환 후 `primary_url`/`center_name`/`target_external_id` 동일 값 유지(AC1 양쪽을 변환 경로로 연결).

## 커버리지 (Story 2.3 AC 기준)

| AC | 의미 | 상태 |
| --- | --- | --- |
| AC1 | 중립 필드 4종 도입·동일 Target 집합·center_name 쿠팡 검증 유지 | ✅ 기존 + 갭4(변환 경계) |
| AC2 | legacy alias 매핑·임의 rename 금지 | ✅ 기존 + 갭3(필드 아님 잠금) |
| AC3 | 쿠팡 empty/배민-기본값 → 비차단 위험 분류 | ✅ 기존 + 갭1·2(단일 소스/사유 구분) |
| AC4 | 직렬화·JSON·마이그레이션 불변 | ✅ 기존(UiSettings) + 갭3(AppConfig) |
| AC5 | 위험 필드 상태 노출(is_risky+사유) | ✅ 기존 |
| AC6 | 분류만·차단 미약화(raise 유지) | ✅ 기존(test_config·test_ui_helpers raise 테스트) |
| AC7 | enum 신설 금지(bool+사유) | ✅ 기존(튜플 형태 단언) |

## 범위·보안 검증

- 변경: `tests/test_config.py`, `tests/test_ui_settings.py` (**테스트 전용**). 제품 코드(`src/`)는 dev-story 구현물 그대로 — QA가 추가 변경 0.
- 신규 테스트 값: 가짜/placeholder만(`"강남센터"`·`"DP000"`·`"다른센터"`·공개 쿠팡 URL). 실제 토큰/전화/이메일 0.
- 누출 grep(봇 토큰/`chat_id=<digits>`/한국 휴대폰): 신규 diff에서 **0건**.

## 다음 단계

- 추가 작업 불필요. 분류기의 실제 작업 차단·상태 전이(Epic 4 FR-14/FR-20)와 도메인 enum(Story 2.5)은 본 스토리 범위 밖이며 그 시점에 별도 테스트 대상.
