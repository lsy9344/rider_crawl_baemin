# done_작업 지시서: 설정 편의성 개선

작성일: 2026-06-19  
반영 상태: 반영완료  
대상 저장소: `rider_result_mornitoring`  
작업 목적: 관리자 웹 UI와 로컬 설정 UI의 세팅/설정 편의성을 개선한다. 핵심은 새 고객을 빠르게 추가하고, 수집/전송 테스트까지 안전하게 끝낼 수 있게 만드는 것이다.

## 1. 작업 목표

운영자가 새 고객을 추가할 때 다음 흐름을 한 화면 절차로 완료할 수 있어야 한다.

1. 고객 생성 또는 선택
2. 플랫폼 선택
3. 플랫폼 계정 입력
4. 업체/센터 정보 입력
5. 전송 채널 연결
6. 테스트 수집
7. 테스트 메시지 전송
8. 실발송 ON

완료 기준은 "폼 저장 성공"이 아니라 "수집 테스트와 전송 테스트가 통과했고, 실발송을 켤 수 있는 상태"다.

## 2. 작업 범위

### 포함

- 관리자 웹 UI `/admin`의 관리 탭 개선.
- 새 고객 세팅 마법사 추가.
- 설정 상태 표시 추가.
- 기본 설정과 고급 설정 분리.
- 화면 문구와 라벨 개선.
- 작은 글자와 mono 중심 타이포그래피 개선.
- DB 연결 실패 시 관리자에게 보이는 오류 화면 또는 안내 처리.
- 관련 테스트 추가 또는 수정.

### 제외

- 크롤러 파서 자체 변경.
- 텔레그램 전송 방식 교체.
- 카카오톡 자동화 구조 변경.
- DB 스키마 대규모 재설계.
- 로컬 Tkinter UI 전체 리디자인.

## 2.1 실행 원칙

이 작업은 한 번에 큰 wizard를 만드는 방식보다 작은 단위로 나누어 진행한다. 목표는 기존 운영 화면을 깨지 않으면서 세팅 실패를 줄이는 것이다.

권장 PR 단위:

1. 장애 회복 PR: `/admin` DB 연결 실패 안내와 테스트.
2. 빠른 UX 개선 PR: 용어 정리, 작은 글자 정리, 모바일 폼 상태 메시지 정리.
3. 세팅 체크리스트 PR: 관리 탭 상단에 `새 고객 세팅 시작` 진입점과 현재 폼으로 이동하는 단계 링크 추가.
4. 안전 게이트 PR: 수집 테스트, 전송 테스트, 실발송 ON을 하나의 완료 조건으로 묶기.
5. 마법사 PR: 기존 CRUD endpoint를 재사용해 HTMX 단계형 wizard로 확장.

처음부터 기존 폼을 삭제하지 않는다. 기존 방식은 `고급/기존 방식`으로 남겨 숙련 운영자가 계속 쓸 수 있게 한다.

## 3. 주요 근거 파일

| 파일 | 작업 이유 |
| --- | --- |
| `src/rider_server/admin/templates/dashboard.html` | 관리자 UI 전체 레이아웃, 관리 탭, CSS 수정 대상 |
| `src/rider_server/admin/templates/_entity_admin.html` | 고객/계정/업체/채널 설정 폼 수정 대상 |
| `src/rider_server/admin/templates/_targets.html` | 모니터링 row별 1차 액션, 테스트/활성화 진입점 참고 |
| `src/rider_server/admin/templates/_tenant_telegram.html` | 고객별 텔레그램 설정 상태 표시 수정 대상 |
| `src/rider_server/admin/crud_routes.py` | 관리 폼의 GET/POST, 옵션 목록, 엔티티 CRUD 연결 |
| `src/rider_server/admin/actions_routes.py` | 테스트 수집, dry-run, pause/activate 같은 운영 액션 |
| `src/rider_server/admin/routes.py` | 대시보드 렌더와 DB 실패 처리 검토 대상 |
| `src/rider_crawl/ui.py` | 로컬 설정 UX 참고, 검증 로직과 문구 참고 |
| `src/rider_crawl/ui_settings.py` | 탭별 기본값, 비밀값 ref 저장 정책 참고 |
| `tests/server/` | 관리자 UI, 라우트, 서비스 테스트 추가 위치 |

## 4. 구현 요구사항

### 4.1 `/admin` DB 연결 실패 안내

문제:

- 로컬 리뷰에서 `/admin` 접근 시 PostgreSQL 연결 거부로 500이 발생했다.
- 관리자 화면 전체가 JSON 내부 오류로 보이면 운영자가 조치하기 어렵다.

요구사항:

- dashboard repository 조회 중 DB 연결 실패가 발생하면 사용자용 HTML 오류 화면을 반환한다.
- 오류 화면에는 다음 정보가 있어야 한다.
  - "DB 연결 실패"
  - 현재 서버는 떠 있음
  - `DATABASE_URL` 또는 DB 실행 상태 확인 필요
  - 재시도 안내
- 민감한 DB URL이나 비밀번호는 표시하지 않는다.
- `/health`는 기존처럼 liveness로 유지한다.

검증:

- DB 연결이 거부되는 fake repository 또는 monkeypatch로 `/admin`이 500 JSON이 아니라 안내 HTML을 반환하는 테스트를 추가한다.

### 4.2 새 고객 세팅 마법사

문제:

- 현재 `_entity_admin.html`은 `새 업체 추가`, `전송 연결`, `등록된 업체 편집`이 먼저 보이고, 고객/계정/채널 생성은 접힌 보조 영역에 있다.
- 새 고객 세팅 순서와 화면 순서가 다르다.

요구사항:

- 관리 탭 상단에 `새 고객 세팅 시작` 버튼 또는 섹션을 추가한다.
- 마법사는 아래 단계를 가진다.
  1. 고객: 기존 고객 선택 또는 새 고객 생성
  2. 플랫폼: 배민 또는 쿠팡이츠 선택
  3. 로그인 계정: 플랫폼 계정 생성 또는 선택
  4. 업체/센터: 표시명, 센터/상점명, URL, 수집 주기
  5. 전송 채널: 텔레그램 또는 카카오 선택, 채널 생성 또는 선택
  6. 전송 규칙: 업체와 채널 연결
  7. 테스트: dry-run 또는 test-crawl 실행
  8. 실발송: sending_enabled 확인 및 ON
- 각 단계는 이전 단계의 결과를 다음 단계에 자동으로 넘긴다.
- 사용자가 raw id를 복사해서 넣는 흐름은 만들지 않는다.
- 단계별 결과는 inline status로 보여준다.
- 마지막 단계 전에는 실발송 ON을 바로 켤 수 없게 하거나, 최소한 수집 테스트와 전송 테스트 미완료 상태를 강하게 확인한다.
- 마법사 완료 화면에는 `수집 테스트 통과`, `전송 테스트 통과`, `실발송 OFF/ON` 상태가 같이 보여야 한다.

검증:

- 템플릿 테스트에서 마법사 단계 제목이 렌더되는지 확인한다.
- 옵션 select가 기존 HTMX 옵션 경로를 계속 사용하는지 확인한다.
- 새 고객 세팅 중 고객 선택이 없으면 다음 단계가 비활성 또는 안내 상태인지 확인한다.
- 테스트 완료 전 실발송 ON 단계가 비활성 또는 강한 확인 상태인지 확인한다.

### 4.3 기본 설정과 고급 설정 분리

문제:

- 첫 설정 화면에 내부/고급 필드가 많이 노출된다.

요구사항:

- 기본 화면에는 아래만 둔다.
  - 고객
  - 플랫폼
  - 계정 라벨 또는 로그인 ID
  - 업체 표시명
  - 센터/상점명
  - 수집 주기
  - 전송 채널
  - 테스트 버튼
  - 실발송 토글
- 아래 항목은 `고급 설정` 안으로 옮긴다.
  - `external_id`
  - raw ID성 값
  - `webhook secret`
  - `registration_code`
  - timeout류
  - lock류
  - CDP/Chrome profile 경로
- 단, 고급 설정은 운영 디버깅을 위해 접근 가능해야 한다.

검증:

- 기본 화면에서 고급 필드가 바로 보이지 않는지 확인한다.
- 고급 설정을 열면 기존 필드에 접근 가능한지 확인한다.

### 4.4 용어 정리

문제:

- 내부 용어가 화면에 그대로 노출된다.

요구사항:

아래 용어를 운영자 친화적인 문구로 바꾼다.

| 현재 | 변경 |
| --- | --- |
| Tenant | 고객 |
| Platform Account | 배민/쿠팡 로그인 계정 |
| external_id | 외부 관리 코드 |
| webhook secret | 텔레그램 webhook 보안키 |
| sending_enabled | 실제 메시지 보내기 |
| SETUP_PENDING | 세팅 중 |
| PAYMENT_ACTIVE | 결제 활성 |
| soft delete | 비활성화 |
| fail-closed | 기본 차단 |

검증:

- 화면 렌더 결과에 주요 내부 영문 용어가 남아 있지 않은지 테스트하거나 스냅샷으로 확인한다.
- 내부 enum 값은 서버 API와 DB에는 그대로 둔다. 화면 문구만 바꾼다.

### 4.5 타이포그래피와 가독성 개선

문제:

- 자동 탐지 결과 `dashboard.html`에 12px 미만 수준의 작은 텍스트가 여러 개 있다.
- Geist Mono가 본문과 라벨까지 넓게 쓰여 한국어 가독성이 떨어질 수 있다.

요구사항:

- 본문, 라벨, 버튼, 입력값의 기본 글자는 14px 이상으로 맞춘다.
- 표 보조 정보와 badge도 12px 미만으로 내려가지 않게 한다.
- 본문/라벨은 system sans 또는 Pretendard 계열로 바꾼다.
- 숫자, 시간, ID, code만 mono를 쓴다.
- 모바일에서 버튼 높이 44px 이상은 유지한다.
- 390px 폭에서 관리 탭 fieldset이 한 줄 입력으로 읽히고, 버튼과 상태 메시지가 옆으로 밀리지 않아야 한다.
- inline action status는 좁은 화면에서 버튼 옆 작은 pill이 아니라 다음 줄 전체 폭 안내로 보여도 된다.

검증:

- `npx impeccable --json src/rider_server/admin/templates src/rider_crawl/ui.py`에서 tiny-text 경고가 줄어야 한다.
- 주요 관리 폼을 1366px desktop, 390px mobile 폭에서 확인한다.
- 390px mobile에서 `새 고객 세팅 시작`, 계정 생성, 채널 생성, 텔레그램 설정 저장 버튼이 모두 44px 이상 touch target인지 확인한다.

### 4.6 세팅 완료 상태 표시

문제:

- 저장이 끝났는지, 테스트가 끝났는지, 실발송을 켜도 되는지 한눈에 알기 어렵다.

요구사항:

- 고객 또는 업체별 설정 상태를 보여준다.
- 최소 상태:
  - 세팅 필요
  - 로그인 필요
  - 수집 테스트 필요
  - 전송 테스트 필요
  - 실발송 대기
  - 운영 중
- 마법사 마지막에 체크리스트를 보여준다.
- 실발송 ON은 테스트 완료 후 켜는 흐름을 권장한다.
- 체크리스트에는 가능한 경우 아래 메타 정보를 표시한다.
  - 수집 테스트 통과 시각
  - 전송 테스트 통과 시각
  - 실발송 변경자
  - 실발송 변경 시각

검증:

- 테스트 데이터에서 상태별 badge 또는 안내 문구가 올바르게 렌더되는지 확인한다.

### 4.7 요청 상태와 접근성 기본기 보강

문제:

- `loadDeliveryRules()`는 HTMX 요청 직후 `조회 완료`를 표시한다. 실제 응답 실패 전에도 성공처럼 보일 수 있다.
- 모니터링 row는 `div onclick` 중심이라 키보드 사용자가 row 전체 affordance를 얻기 어렵다.
- 모니터링/관리 tab은 `role="tab"`은 있지만 `aria-controls`, `tabpanel`, focus 관리가 없다.
- 관리 폼 CSS는 `password` input을 포함하지 않아 비밀값 입력란이 다른 입력과 다르게 보일 수 있다.

요구사항:

- HTMX 요청에는 `요청 중`, `성공`, `실패`, `재시도` 상태를 둔다.
- 요청 중에는 해당 버튼 또는 status 영역에 `aria-busy="true"`를 설정한다.
- 성공 문구는 실제 성공 응답 이후에만 표시한다.
- 실패 시 같은 inline status 영역에 실패 문구와 다시 시도 안내를 표시한다.
- 모니터링 row는 keyboard로 상세를 열 수 있어야 한다. Enter/Space를 지원하거나, row 안의 `상세` 버튼을 항상 명확히 노출한다.
- tab 버튼에는 `aria-controls`를 연결하고, 모니터링/관리 main 영역은 `role="tabpanel"`을 가진다.
- tab 전환 후 선택된 tab의 `aria-selected`와 focus 상태가 일치해야 한다.
- `#view-manage input[type="password"]`는 text/number/select와 같은 글자 크기, padding, focus style, min-width 규칙을 가진다.
- 주요 액션 버튼은 데스크톱과 모바일 모두 최소 40-44px 높이를 목표로 한다.

검증:

- `loadDeliveryRules()` 실패 케이스에서 `조회 완료`가 먼저 나오지 않는지 테스트한다.
- `Tab`, `Enter`, `Space` 키만으로 모니터링 row 상세를 열 수 있는지 확인한다.
- 렌더된 HTML에 `aria-controls`, `role="tabpanel"`이 있는지 확인한다.
- password input이 관리 폼 CSS selector에 포함되는지 확인한다.
- desktop과 390px mobile에서 주요 버튼 touch target을 확인한다.

## 5. UX 수락 기준

작업 완료 후 다음이 가능해야 한다.

- 새 운영자가 문서를 보지 않고도 `새 고객 세팅 시작`에서 첫 고객을 추가할 수 있다.
- 세팅 중 어느 단계에 있는지 화면에 보인다.
- 필수 값이 빠졌을 때 다음 행동이 명확히 보인다.
- raw id 복사 입력 없이 고객, 계정, 업체, 채널을 연결할 수 있다.
- 실발송 전 테스트 수집과 테스트 전송을 확인할 수 있다.
- 설정 완료 상태가 고객별로 보인다.
- 관리자 화면의 일반 텍스트가 너무 작지 않다.
- DB 연결 실패 시 내부 trace나 JSON 500이 아니라 운영자용 안내가 보인다.
- HTMX 요청 실패가 성공처럼 보이지 않는다.
- keyboard만으로 모니터링 상세를 열고 관리 탭으로 이동할 수 있다.
- password 입력란도 다른 입력과 같은 테마와 focus 표시를 가진다.
- 주요 액션은 desktop과 390px mobile 모두에서 누르기 쉬운 크기다.

## 6. 테스트 지시

필수:

```powershell
.venv\Scripts\python.exe -m pytest tests/server
```

추가해야 할 테스트 예시:

| 테스트 파일 | 테스트 이름 | 확인 내용 |
| --- | --- | --- |
| `tests/server/test_admin_dashboard.py` | `test_admin_db_failure_returns_operator_recovery_html` | dashboard repository가 실패해도 `/admin`이 내부 trace 대신 DB 연결 실패 안내 HTML을 반환한다. |
| `tests/server/test_admin_dashboard.py` | `test_manage_tab_renders_new_customer_setup_entrypoint` | 관리 탭 상단에 `새 고객 세팅 시작` 또는 세팅 체크리스트가 렌더된다. |
| `tests/server/test_admin_dashboard.py` | `test_manage_tab_uses_operator_friendly_terms` | `Tenant`, `external_id`, `sending_enabled`, `fail-closed` 같은 내부 용어가 기본 화면에 직접 노출되지 않는다. |
| `tests/server/test_admin_dashboard.py` | `test_setup_gate_shows_test_before_live_send` | 실발송 ON 전에 수집 테스트와 전송 테스트 상태가 표시된다. |
| `tests/server/test_admin_dashboard.py` | `test_mobile_manage_styles_keep_touch_targets` | CSS에 390px 화면용 form/button/status 규칙이 있다. |
| `tests/server/test_admin_dashboard.py` | `test_delivery_rule_lookup_does_not_report_success_before_response` | delivery rule 조회가 성공 응답 전 `조회 완료`를 표시하지 않는다. |
| `tests/server/test_admin_dashboard.py` | `test_dashboard_tabs_have_aria_controls_and_panels` | 모니터링/관리 tab과 panel의 ARIA 연결이 완성되어 있다. |
| `tests/server/test_admin_dashboard.py` | `test_target_rows_are_keyboard_accessible_or_have_visible_detail_button` | keyboard만으로 업체 상세를 열 수 있다. |
| `tests/server/test_admin_dashboard.py` | `test_manage_password_inputs_share_form_control_styles` | password input이 text input과 같은 테마/focus 스타일을 가진다. |

권장:

```powershell
npx impeccable --json src/rider_server/admin/templates src/rider_crawl/ui.py
```

수동 확인:

```powershell
$env:PYTHONPATH="src"
$env:APP_ENV="review"
$env:RIDER_ADMIN_PUBLIC_ACCESS="true"
$env:RIDER_ADMIN_MFA_REQUIRED="false"
.venv\Scripts\python.exe -m rider_server
```

브라우저에서 확인:

- `http://127.0.0.1:8000/admin`
- 모니터링 탭
- 관리 탭
- 새 고객 세팅 마법사
- DB 연결 실패 안내 화면
- 390px 모바일 폭에서 관리 탭을 열고 fieldset, 버튼, 상태 메시지가 가로로 밀리지 않는지 확인
- 테스트 완료 전 실발송 ON이 바로 실행되지 않거나 강한 확인을 거치는지 확인

주의:

- 실제 토큰, 비밀번호, chat_id를 테스트 fixture나 문서에 넣지 않는다.
- `.env`에 운영 `DATABASE_URL`이 남아 있으면 로컬 확인이 PostgreSQL 경로를 탈 수 있다.

## 7. 작업 순서

1. `/admin` DB 실패 안내 처리와 테스트를 먼저 작성한다.
2. `_entity_admin.html`에 `새 고객 세팅 시작` 구조를 추가한다.
3. 기존 고객/계정/업체/채널 폼을 마법사 단계로 재배치한다.
4. 고급 설정 접힘 영역을 정리한다.
5. 화면 용어를 운영자 친화적으로 바꾼다.
6. `dashboard.html` CSS에서 작은 글자와 mono 본문 사용을 정리한다.
7. 세팅 상태 badge와 체크리스트를 추가한다.
8. 서버 테스트, 자동 탐지, 수동 화면 확인을 수행한다.

## 8. 리스크와 대응

| 리스크 | 대응 |
| --- | --- |
| 기존 운영자가 쓰던 폼 위치가 바뀌어 혼란 | 기존 엔티티 관리 섹션은 `고급/기존 방식`으로 한동안 유지 |
| 마법사 추가로 템플릿이 커짐 | partial을 나누고 단계별 HTMX fragment로 분리 |
| 실발송 토글 실수 | 테스트 완료 전에는 강한 확인 또는 비활성 처리 |
| 용어 변경으로 개발자가 디버깅 어려움 | 고급 모드에 내부 ID와 enum 표시 |
| DB 장애 안내가 실제 장애를 숨김 | 사용자용 안내와 서버 로그 trace를 분리해서 남김 |

## 9. 완료 산출물

- 수정된 관리자 UI 템플릿.
- 관련 route/service 테스트.
- 자동 탐지 결과 개선.
- 수동 확인 캡처 또는 확인 로그.
- README 또는 운영 문서의 세팅 절차 갱신.
