# 작업 지시서: 등록된 설정 전송 준비 상태와 전송 규칙 UX 개선

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

작성일: 2026-06-29  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
근거: 2026-06-29 운영 Admin 확인. `팀100 남양주동부`의 `등록된 설정`에서 `전송 ON`이면서 `메신저 —`로 표시되는 상태 검토.

**Goal:** Admin UI가 "고객 전송 토글 ON"과 "대상별 실제 전송 준비 완료"를 구분해서 보여주고, 대상과 채널을 연결하는 전송 규칙 생성 누락을 운영자가 바로 알아차리고 해결할 수 있게 만든다.

**Architecture:** 현재 도메인 설계는 유지한다. `MessengerChannel`은 보낼 수 있는 목적지, `DeliveryRule`은 특정 `MonitoringTarget`을 특정 채널로 보내는 연결이다. 전송 안전성 때문에 채널 등록만으로 모든 대상에 자동 전송하지 않는다. 개선 범위는 이 의미를 UI와 작업 흐름에 명확히 드러내는 것이다.

**Tech Stack:** Python, FastAPI, Jinja admin templates, HTMX, small vanilla JavaScript, pytest.

---

## 1. 현재 확정 사실

- `팀100 남양주동부` tenant id: `fe63f809-b500-468c-9859-70500091300d`.
- `등록된 설정` fragment에서 해당 대상은 다음처럼 표시된다.
  - 수집: `ON`
  - 전송: `ON`
  - 메신저: `—`
  - 플랫폼: `쿠팡`
- 같은 tenant에는 메시지 채널이 1건 있다.
  - `KAKAO · 누나`
  - 상태: `ACTIVE`
- 같은 tenant의 모니터링 대상은 1건이다.
  - `팀100 남양주동부`
  - 상태: `ACTIVE`
- 그러나 해당 대상의 전송 규칙은 0건이다.
  - `/admin/delivery-rules?tenant=<team100>&target_id=<target>` 응답: `전송 규칙 목록 (0건)`
- 코드상 `등록된 설정`의 `전송` 열은 `tenant.sending_enabled`만 본다.
  - 위치: `src/rider_server/admin/routes.py`
  - `send_enabled = tenant_sending_enabled`
- 코드상 `등록된 설정`의 `메신저` 열은 대상별 활성 `DeliveryRule`이 연결한 채널만 본다.
  - 활성 규칙이 없으면 `messengers=()`가 되어 `—`가 표시된다.
- 실제 dispatch 경로도 전송 전에 활성 `DeliveryRule`과 `ACTIVE MessengerChannel`을 조인한다.
  - 규칙이 0건이면 delivery log와 전송 job을 만들지 않는다.

## 2. 설계 의도

채널 등록과 전송 규칙은 서로 다른 의미다.

| 개념 | 쉬운 설명 | 예시 |
| --- | --- | --- |
| 메시지 채널 | 보낼 수 있는 목적지 주소록 | `KAKAO · 누나`, Telegram chat/topic |
| 전송 테스트 | 그 목적지로 실제 메시지가 도착하는지 확인 | 카카오 테스트 job 성공, Telegram test 성공 |
| 전송 규칙 | 어떤 모니터링 대상 결과를 어느 채널로 보낼지 연결 | `팀100 남양주동부 -> KAKAO · 누나` |
| 고객 전송 토글 | 이 고객의 실전송을 전체적으로 허용하는 상위 스위치 | `tenant.sending_enabled=True` |

이 분리는 의도된 안전 장치다.

1. 한 고객 안에 대상이 여러 개일 수 있다.
   - A 센터는 사장님 방, B 센터는 매니저 방으로 보내야 할 수 있다.
2. 한 대상이 여러 채널로 fan-out될 수 있다.
   - 같은 실적을 카카오와 텔레그램 둘 다 보낼 수 있다.
3. 채널만 만들었다고 모든 대상에 자동 연결하면 오발송 위험이 생긴다.
   - 새 채널을 테스트하려고 만들었는데 기존 모든 대상 실적이 그 방으로 나가면 안 된다.
4. fail-closed가 더 안전하다.
   - 연결 규칙이 없으면 "보내지 않음"이 맞다.
   - 잘못된 방으로 보내는 것보다 미전송이 낫다.

따라서 설계 자체는 유지한다. 문제는 현재 UI가 이 구분을 충분히 설명하지 못하는 것이다.

## 3. 문제 정의

현재 `등록된 설정`의 `전송 ON`은 운영자가 보기엔 "이 대상의 메시지가 전송된다"는 뜻으로 읽힌다. 하지만 실제 의미는 "고객 전체 실전송 토글이 켜져 있다"이다.

이 때문에 다음 오해가 생긴다.

- 메신저가 `—`인데 왜 전송이 `ON`인가?
- 채널 전송 세팅을 했는데 왜 대상에 메신저가 안 보이는가?
- 실제로 보내지는 상태인지, 연결이 빠진 상태인지 화면만 보고 알기 어렵다.

정확한 상태 표현은 다음에 가깝다.

- 고객 전송: `ON`
- 대상 연결: `필요`
- 메신저: `—`
- 실제 대상 전송 준비: `미완료`

## 4. 작업 범위

### 포함

- `등록된 설정` 카드의 전송 상태 표시 개선.
- 대상별 활성 전송 규칙이 없는 경우 명확한 상태 표시.
- 관리 탭에서 전송 규칙 생성 누락을 쉽게 발견하고 생성할 수 있는 UX 보강.
- 단일 대상 + 단일 활성 채널인 경우 운영자가 연결을 빠르게 완료할 수 있는 안전한 흐름 제공.
- 관련 테스트 추가.

### 제외

- 채널 생성만으로 모든 대상에 자동 연결하는 동작.
- 기존 dispatch fan-out 설계 변경.
- DB schema 변경.
- 카카오 전송 worker, Telegram 전송 worker 구조 변경.
- Coupang 로그인/이메일 2FA 런타임 변경.
- 보호 파일 변경:
  - `src/rider_crawl/auth/coupang_email_2fa.py`
  - `src/rider_agent/auth/coupang_gmail_2fa.py`
  - `src/rider_agent/worker_composition.py`
  - `src/rider_crawl/platforms/coupang/crawler.py`
  - `src/rider_server/services/admin_action_service.py`
  - `src/rider_server/scheduler/service.py`
  - `src/rider_server/queue/postgres_queue.py`

---

## 5. 요구사항

### 5.1 `등록된 설정` 상태 의미를 분리한다

`등록된 설정` 표에서 `전송` 한 칸이 모든 의미를 떠안지 않게 한다.

권장 표현:

- `고객 전송`: `ON` 또는 `OFF`
- `대상 연결`: `완료` 또는 `연결 필요`
- `메신저`: 연결된 메신저 목록. 없으면 `—`

대안:

- 열 수를 늘리지 않고 `전송` 칸에 복합 상태를 표시한다.
  - 고객 토글 ON + 규칙 있음: `ON`
  - 고객 토글 ON + 규칙 없음: `연결 필요`
  - 고객 토글 OFF: `OFF`

권장안은 열을 분리하는 것이다. 단어가 조금 늘어도 운영자가 덜 헷갈린다.

### 5.2 대상별 전송 준비 상태를 계산한다

`SettingsRow`에 다음 파생값을 추가한다.

- `customer_sending_enabled`: tenant의 실전송 토글.
- `has_active_delivery_rule`: 해당 대상에 활성 rule이 1건 이상 있음.
- `delivery_ready`: `customer_sending_enabled and has_active_delivery_rule`.
- `delivery_status_label`: 화면 표시용 라벨.
  - `OFF`: 고객 전송 토글 OFF
  - `연결 필요`: 고객 전송 ON, 활성 전송 규칙 없음
  - `ON`: 고객 전송 ON, 활성 전송 규칙 있음

주의:

- 이 표시는 운영 UI용 readiness다.
- 실제 dispatch 경로의 최종 gate는 계속 runtime이 본다.
- 전송 시간창, 전역 `RIDER_SENDING_ENABLED`, 채널 런타임 장애까지 모두 이 한 칸에 섞지 않는다.

### 5.3 메신저 `—`의 뜻을 화면에서 분명히 한다

메신저가 비어 있으면 단순 `—`만 표시하지 않는다.

권장:

- `—` 아래 또는 옆에 작은 상태 텍스트 `전송 규칙 없음`.
- `연결 필요` badge와 함께 표시.

단, 화면에 설명문을 길게 넣지 않는다. 운영자가 스캔할 수 있는 짧은 라벨이어야 한다.

### 5.4 관리 탭 전송 규칙 생성 흐름을 보강한다

현재 운영자는 채널을 만든 뒤 별도로 `전송 연결`에서 대상과 채널을 골라 규칙을 만들어야 한다. 이 단계가 빠지면 지금 같은 상태가 된다.

보강 요구:

- 채널 생성/전송 테스트 성공 후에도 "대상과 채널 연결이 필요합니다"가 보이게 한다.
- `전송 연결` 섹션에서 대상 선택과 채널 선택을 더 명확히 한다.
- 대상 1개 + 활성 채널 1개인 tenant에서는 빠른 연결 버튼을 제공한다.

빠른 연결 버튼 예시:

- 버튼 라벨: `이 대상에 채널 연결`
- 동작: 선택된 대상과 선택된 채널로 `DeliveryRule` 생성.
- 전제: operator 권한, tenant scope 통과, target/channel 둘 다 같은 tenant.
- 생성 후 `admin-entity-changed` 이벤트로 `등록된 설정` 카드가 갱신되어야 한다.

자동 생성은 신중하게 다룬다.

- 채널 생성만으로 자동 규칙 생성하지 않는다.
- 전송 테스트 성공만으로 자동 규칙 생성하지 않는다.
- 운영자가 명시적으로 "연결"을 누르는 흐름을 기본으로 한다.
- 단, 향후 세팅 wizard에서는 "대상 1개 + 채널 1개" 조건에서 확인 단계와 함께 자동 제안을 할 수 있다.

### 5.5 전송 테스트 통과와 규칙 연결을 구분한다

현재 `tenant.sending_enabled`는 전송 테스트 통과 후 켤 수 있다. 그러나 전송 테스트 통과는 "채널이 동작한다"는 뜻이지 "특정 대상이 그 채널로 연결됐다"는 뜻이 아니다.

UI는 두 조건을 모두 보여야 한다.

- 채널 전송 테스트: 통과/미통과
- 대상 전송 연결: 완료/연결 필요

운영 판단:

- 테스트 통과 + 연결 없음: 아직 대상 전송 준비 미완료.
- 테스트 통과 + 연결 있음 + 고객 전송 ON: 대상 전송 준비 완료.

---

## 6. 구현 작업

## Task 0 - 기준선 확인

**Intent:** 기존 동작과 관련 테스트 기준선을 확인한다.

**Files:** 없음

- [ ] 작업 전 git 상태를 확인한다.

```powershell
git status --short
```

- [ ] 관련 테스트 기준선을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py tests/server/test_admin_entity_crud.py tests/server/test_channel_lifecycle.py tests/server/test_channel_send_test.py -q
```

Expected:

- 현재 main 기준으로 통과한다.
- 기존 실패가 있으면 이번 작업 영향인지 기록하고, unrelated 실패는 되돌리지 않는다.

## Task 1 - 등록된 설정 row model에 readiness 필드 추가

**Intent:** 고객 전송 토글과 대상별 전송 연결 상태를 분리해서 표현한다.

**Files:**

- Modify: `src/rider_server/admin/routes.py`
- Modify: `tests/server/test_admin_dashboard.py`

작업:

- [ ] `SettingsRow`에 파생 필드를 추가한다.
  - `customer_sending_enabled`
  - `has_active_delivery_rule`
  - `delivery_ready`
  - 필요하면 `delivery_status`
- [ ] `_settings_rows_for_tenant()`에서 대상별 활성 rule 수를 계산한다.
- [ ] 기존 `send_enabled` 의미를 유지할지, 새 이름으로 바꿀지 결정한다.
  - 추천: 템플릿 표시는 새 필드 사용.
  - 기존 테스트 호환 때문에 내부 `send_enabled`는 당장 유지 가능.
- [ ] `tenant.sending_enabled=True`, 활성 rule 0건이면 `delivery_ready=False`가 되게 한다.

검증:

- [ ] tenant 전송 ON + rule 0건이면 row가 `연결 필요` 상태가 되는 테스트 추가.
- [ ] tenant 전송 ON + active rule 1건이면 row가 `ON` 상태가 되는 테스트 추가.
- [ ] tenant 전송 OFF + active rule 1건이면 row가 `OFF` 상태가 되는 테스트 추가.

## Task 2 - 등록된 설정 템플릿 표시 개선

**Intent:** 운영자가 한눈에 "전송 토글 ON이지만 대상 연결이 빠졌다"는 사실을 알게 한다.

**Files:**

- Modify: `src/rider_server/admin/templates/_registered_settings.html`
- Modify: `tests/server/test_admin_dashboard.py`

작업:

- [ ] `전송` 열 라벨을 바꾸거나 보조 열을 추가한다.
  - 권장: `고객 전송`, `대상 연결` 두 열.
  - 단순 대안: `전송` 열에 `연결 필요` badge 표시.
- [ ] 활성 rule이 없으면 메신저 칸에 `—`와 함께 `전송 규칙 없음`을 표시한다.
- [ ] `연결 필요`는 warning tone으로 보이게 한다.
- [ ] 기존 정상/주의/위험 severity 색과 충돌하지 않게 CSS class를 재사용하거나 최소 추가한다.

검증:

- [ ] `등록된 설정` HTML에 `연결 필요`가 렌더되는 테스트 추가.
- [ ] 활성 rule이 있으면 `텔레그램`/`카카오` 라벨이 기존처럼 보이는지 유지 테스트.
- [ ] 전체 고객(`tenant=all`)에서도 고객명과 readiness가 같이 보이는지 확인한다.

## Task 3 - 관리 탭에서 전송 규칙 누락을 쉽게 해결하게 한다

**Intent:** 채널은 있는데 대상 연결이 빠진 상태를 운영자가 바로 고칠 수 있게 한다.

**Files:**

- Modify: `src/rider_server/admin/templates/_entity_admin.html`
- Modify: `src/rider_server/admin/crud_routes.py` if a new helper endpoint is needed.
- Modify: `tests/server/test_admin_entity_crud.py`

작업:

- [ ] `전송 연결` 섹션의 문구를 정리한다.
  - "채널 생성"과 "대상 연결"이 다른 단계임을 짧게 표시한다.
- [ ] 대상 select와 채널 select가 모두 선택되면 생성 버튼이 명확히 활성화되게 한다.
- [ ] 대상 1개 + 활성 채널 1개이면 빠른 연결 버튼 또는 미리 선택 상태를 제공한다.
- [ ] 빠른 연결은 기존 `POST /admin/delivery-rules`를 재사용한다.
- [ ] 생성 성공 후 `admin-entity-changed`가 발생해 `등록된 설정` 카드가 갱신되게 한다.

검증:

- [ ] `POST /admin/delivery-rules`가 target/channel 같은 tenant일 때 성공하는 기존 테스트를 유지한다.
- [ ] cross-tenant target/channel 연결이 계속 차단되는지 테스트한다.
- [ ] duplicate active rule 생성이 허용되는지 여부를 확인한다.
  - 현재 정책이 중복 허용이면 이번 작업에서 바꾸지 않는다.
  - 중복 방지가 필요하면 별도 요구사항으로 분리한다.

## Task 4 - 채널 전송 테스트 결과와 대상 연결 상태를 함께 보여준다

**Intent:** "전송 테스트 통과"가 "대상 연결 완료"로 오해되지 않게 한다.

**Files:**

- Modify: `src/rider_server/admin/templates/_tenant_telegram.html`
- Modify: `src/rider_server/admin/templates/_entity_admin.html`
- Modify: `tests/server/test_admin_entity_crud.py`
- Optional Modify: `tests/server/test_admin_dashboard.py`

작업:

- [ ] 고객별 텔레그램 설정 표의 `전송 테스트`와 `실제 메시지 보내기`는 기존 의미를 유지한다.
- [ ] 관리 탭 전송 연결 영역에 "테스트 통과 후에도 대상 연결이 필요"하다는 짧은 상태 라벨을 추가한다.
- [ ] 단일 대상/단일 채널 누락 상태에서는 `연결 필요` CTA를 보여준다.

검증:

- [ ] 전송 테스트 통과 tenant라도 delivery rule 0건이면 `등록된 설정`은 `연결 필요`를 표시한다.
- [ ] 전송 테스트 미통과 tenant는 기존처럼 실발송 ON을 막는다.

## Task 5 - 운영 문서 보강

**Intent:** 운영자가 세팅 순서를 문서에서 확인할 수 있게 한다.

**Files:**

- Modify: `README.md` or `docs/operations/*` 중 현행 Admin 세팅 절차 문서

작업:

- [ ] 새 고객 세팅 순서를 문서화한다.
  1. 고객 생성 또는 선택
  2. 플랫폼 계정 생성
  3. 모니터링 대상 생성
  4. 메시지 채널 생성
  5. 채널 전송 테스트
  6. 전송 규칙 생성: 대상 -> 채널
  7. 수집 테스트
  8. 고객 실전송 ON
- [ ] "채널 생성만으로 대상에 자동 전송되지 않는다"는 설명을 추가한다.
- [ ] "등록된 설정에서 메신저 `—`는 전송 규칙 없음"이라는 설명을 추가한다.

검증:

- [ ] 문서에 비밀번호, 토큰, OTP, 실제 webhook secret이 포함되지 않는다.

---

## 7. 수용 기준

- `팀100 남양주동부`와 같은 상태에서 `전송 ON / 메신저 —`만 보이지 않는다.
- 고객 전송 토글이 ON이어도 대상별 전송 규칙이 없으면 `연결 필요`가 표시된다.
- 대상별 활성 전송 규칙이 생기면 `등록된 설정`의 메신저 칸에 `카카오` 또는 `텔레그램`이 표시된다.
- 전송 테스트 통과와 대상 연결 완료가 UI에서 구분된다.
- 기존 dispatch fail-closed 동작은 바뀌지 않는다.
- 기존 보호된 Coupang 로그인/2FA 흐름은 변경하지 않는다.
- 관련 pytest가 통과한다.

최소 검증 명령:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py tests/server/test_admin_entity_crud.py tests/server/test_channel_lifecycle.py tests/server/test_channel_send_test.py -q
```

필요 시 전체 server admin 관련 검증:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/server/test_admin_dashboard.py tests/server/test_admin_entity_crud.py tests/server/test_admin_security.py tests/server/test_channel_lifecycle.py tests/server/test_channel_send_test.py tests/server/test_snapshot_telegram_runtime.py -q
```

---

## 8. 운영상 즉시 조치

코드 개선 전이라도 `팀100 남양주동부`를 실제 전송 준비 상태로 만들려면 관리 탭에서 전송 규칙을 생성해야 한다.

필요 연결:

- 대상: `팀100 남양주동부`
- 채널: `KAKAO · 누나`
- 규칙: 활성, 필요 시 `변경시에만 전송` 선택

생성 후 기대 상태:

- `등록된 설정`의 메신저 칸에 `카카오` 표시.
- 고객 전송 ON 상태라면 다음 정상 수집 이후 해당 채널로 전송 대상이 된다.

단, 실제 전송은 runtime의 최종 gate를 계속 따른다.

- 고객 전송 토글
- 전역 발송 게이트
- 대상 전송 허용 시간창
- 활성 전송 규칙
- 활성 채널
- 메시지 변경 조건
- dispatch worker/agent 상태

---

## 9. 설계 결정

이번 작업의 결정은 다음과 같다.

- 채널 등록만으로 전송 규칙을 자동 생성하지 않는다.
- 채널 전송 테스트 통과만으로 전송 규칙을 자동 생성하지 않는다.
- `DeliveryRule`은 대상별 라우팅의 정본으로 유지한다.
- UI는 `고객 전송 ON`과 `대상 연결 완료`를 분리해서 보여준다.
- fail-closed 원칙을 유지한다. 애매하면 보내지 않는다.

