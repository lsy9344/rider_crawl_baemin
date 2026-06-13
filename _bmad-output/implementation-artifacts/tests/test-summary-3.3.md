# 테스트 자동화 요약 — Story 3.3 (Message 정의와 안정적 렌더링 분리)

- **워크플로:** `bmad-qa-generate-e2e-tests`
- **대상 기능:** `MessageRenderService.render_message` / `domain.Message` (P2-03, FR-8, FR-2·FR-3 토대)
- **프레임워크:** pytest (`pyproject.toml`, `pythonpath=["src"]`) — UI/HTTP API 없는 순수 동기 도메인이라 서비스 레벨 테스트
- **실행:** `.venv/Scripts/python.exe -m pytest`
- **작성자:** Noah Lee (QA automate)
- **일자:** 2026-06-13

## 생성된 테스트

### 서비스 테스트 (`tests/server/test_message_render.py` — additive, +4건)

QA 커버리지 분석으로 발견한 4개 갭을 자동 적용했습니다(기존 9건 → 13건).

| 테스트 | 갭 | 검증 내용 |
|---|---|---|
| `test_render_message_coupang_without_current_screen` | A | 쿠팡 **일반 케이스**(`current_screen=None`, peak-dashboard만) 렌더 분기를 `render_message`가 통과 — '수행중인인원' 줄 생략·`template_version`=쿠팡·hash 정합·골든 동등 |
| `test_render_message_text_equals_render_str` | B | **텍스트 정본 동등** 불변식: `render_message(...).text == render(snapshot, source_label=...)` (3.1 `render` 본문 무변경 회귀 그물) |
| `test_text_redacted_preview_caps_text_over_limit` | C | `_PREVIEW_MAX_CHARS=500` **cap 잘림** 경계 — 긴 `source_label`로 텍스트를 500자 초과시켜 미리보기가 정확히 500자로 잘림을 확인 |
| `test_text_redacted_preview_masks_secret_shaped_text` | D | **방어적 심층(NFR-5)** — 비밀 형태 문자열이 렌더 텍스트에 섞여도 `text_redacted_preview`가 redact를 통과해 본문 무누출(기존 동어반복 단언 보완) |

### 기존 테스트 (변경 없음 — 회귀 보존)

`test_message_render.py`의 기존 9건(AC1~AC7: 배민/쿠팡 happy path·골든 동등, `text_hash`↔`DispatchService.message_hash` 정합, 결정성·재현성, `now` 의존성(배민 무관/쿠팡 분기), frozen, 예상 외 타입 방어, redaction 미리보기)은 모두 그대로 통과.

## 커버리지

- **`render_message` 분기:** 배민 / 쿠팡(`current_screen` 有) / 쿠팡(`current_screen=None`) / 예상 외 타입 `TypeError` — **4/4 경로 커버**
- **AC 매핑:** AC1(필드·`text_hash`)·AC1.2(hash 정합)·AC1.3·AC2(결정성·재현성)·AC3(렌더러 바이트 동등·정본 동등·타입 방어) + NFR-5(redaction·cap) — **전부 커버**
- **갭 해소:** 쿠팡 dashboard-only 분기, `render`↔`render_message` 정본 동등, 500자 cap 잘림, redaction 실효성 — **신규 4건으로 보강**

## 검증 결과

- **전체 스위트:** `846 passed` (기준선 `842` → +4 신규, **회귀 0**)
- **대상 파일:** `tests/server/test_message_render.py` `13 passed`
- **범위:** `src/rider_crawl/`·`pyproject.toml` **0줄 변경**, 제품 코드 무변경 — 테스트 파일만 additive
- **누출 스캔:** 신규 테스트에 실제 봇 토큰/`chat_id=digits`/한국 휴대폰/이메일 원문 **0건** (갭 D의 토큰은 명백한 가짜 — 진짜 봇 토큰 정규식 `[0-9]{6,}:[A-Za-z0-9_-]{30,}` 비해당)
- **의존성 방향:** `rider_crawl → rider_server` 역방향 import **0** (ast 가드 통과)

## 체크리스트 (`checklist.md`)

- [x] API/서비스 테스트 생성 (E2E UI 없음 — 해당 없음)
- [x] 표준 프레임워크 API(pytest) 사용
- [x] happy path 커버
- [x] 핵심 에러/경계 케이스 커버(예상 외 타입, cap 잘림, redaction 누출)
- [x] 전체 테스트 통과(846)
- [x] 의미 기반 단언(필드·바이트 동등·hash 정합)
- [x] 명확한 테스트 설명
- [x] 하드코딩 sleep/wait 없음
- [x] 테스트 간 독립(각자 fixture 구성, 순서 의존 0)
- [x] 요약 작성·커버리지 지표 포함

## 다음 단계

- CI에서 스위트 실행(기준선 846)
- Story 3.4(fan-out)·3.5(DeliveryLog dedup key: `…+template_version+text_hash`) 구현 시 이 Message 계약 위에 additive로 테스트 확장
