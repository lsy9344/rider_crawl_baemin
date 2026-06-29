# done_Admin Manage Current Values Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

작성일: 2026-06-29  
상태: 작업 전  
대상 저장소: `rider_result_mornitoring`  
근거: 2026-06-29 관리자 웹앱 관리 탭 코드 확인, 운영자 질문 "드롭다운 선택 시 기존 설정값을 볼 수 있는가"

**Goal:** `/admin` 관리 탭에서 기존 항목을 선택하면 비밀값을 노출하지 않으면서 현재 설정 상태를 바로 확인하고 수정할 수 있게 만든다.

**Architecture:** 기존 partial-update 의미는 유지한다. 비밀값은 값 자체를 반환하지 않고 `설정됨`/`미설정` 상태와 교체 입력만 제공한다. 일반 설정값은 operator 전용 edit-state 조회 API로 안전하게 가져와 폼 또는 현재값 패널에 채운다.

**Tech Stack:** Python, FastAPI, Jinja admin templates, HTMX, small vanilla JavaScript, pytest.

---

## 현재 확정 사실

- `src/rider_server/admin/templates/_entity_admin.html`의 업체 편집 섹션은 "바꿀 칸만 입력하세요. 비운 칸은 기존 값을 유지합니다."라고 안내한다.
- `filledValues()`는 빈 값을 서버로 보내지 않는다. 따라서 현재 편집 방식은 "선택한 항목의 전체 편집 폼"이 아니라 "부분 수정 폼"이다.
- `/admin/*/options` 엔드포인트는 대부분 `<option value=id>label</option>`만 반환한다.
- 예외적으로 메시지 채널 옵션은 `data-chat`, `data-thread`, `data-kakao`를 내려주고 `populateChannelFields()`가 선택 시 입력칸을 채운다.
- 업체, 계정, 고객, 구독, 전송 규칙은 선택 시 현재값을 채우는 JavaScript와 읽기 API가 없다.
- 업체 목록/상세에서 볼 수 있는 값은 이름, 센터명, 플랫폼, 수집 주기 정도다. URL, 관리 코드, 전송 허용 시간창은 현재 UI에서 한눈에 확인하기 어렵다.
- 플랫폼 계정의 로그인 ID, 비밀번호, 2차인증 이메일 주소, 이메일 앱 비밀번호는 현재 코드상 DB에 값이 들어갈 수 있다. 그래도 관리자 화면/응답에서 raw 비밀번호, 이메일 앱 비밀번호, 토큰, OTP를 다시 보여주면 안 된다.
- 기존 문서 일부는 "SecretRef 핸들만 저장"이라고 되어 있지만, 현재 코드와 테스트는 평문 입력도 저장되는 "옵션 B"를 허용한다. 이 작업은 저장 정책을 바꾸지 않고 화면 노출 정책만 보강한다.

## 비범위

- Coupang 로그인/이메일 2FA 런타임 흐름은 수정하지 않는다.
- 보호 파일인 `src/rider_crawl/auth/coupang_email_2fa.py`, `src/rider_agent/auth/coupang_gmail_2fa.py`, `src/rider_agent/worker_composition.py`, `src/rider_crawl/platforms/coupang/crawler.py`, `src/rider_server/services/admin_action_service.py`, `src/rider_server/scheduler/service.py`, `src/rider_server/queue/postgres_queue.py`는 이 작업에서 건드리지 않는다.
- 플랫폼 계정 저장 정책을 SecretRef-only로 되돌리지 않는다.
- 비밀번호, 이메일 앱 비밀번호, 텔레그램 봇 토큰, webhook 보안키, OTP를 화면/JSON/HTML/data attribute에 반환하지 않는다.
- 전체 wizard 재설계, DB schema 변경, 권한 체계 재설계는 하지 않는다.

## 파일 구조

- Modify: `src/rider_server/admin/crud_routes.py`
  - edit-state JSON endpoint 추가.
  - endpoint는 tenant scope와 권한을 확인하고 safe view model만 반환한다.
- Modify: `src/rider_server/services/admin_entity_service.py`
  - route가 repository에 직접 닿지 않도록 scoped read method를 추가한다.
- Modify: `src/rider_server/admin/templates/_entity_admin.html`
  - 선택 시 현재값 로드 hook과 현재값 표시 영역을 추가한다.
  - 비밀값 입력란은 계속 빈 입력란으로 두고, 상태 라벨만 표시한다.
- Modify: `tests/server/test_admin_entity_crud.py`
  - endpoint, 템플릿, 보안 노출 회귀 테스트 추가.
- Optional Modify: `docs/operations/aws-product-setup-2026-06-18.md`
  - SecretRef-only로 읽히는 오래된 문구를 현재 코드 기준 설명으로 정리한다.

---

## 설계 원칙

1. 일반 설정값은 볼 수 있어야 한다.
   - 업체: 표시명, 센터/상점명, 관리 코드, URL, 수집 주기, 전송 허용 시간.
   - 고객: 고객명, 상태, 실제 메시지 보내기 ON/OFF, 전송 테스트 통과 여부.
   - 구독: 상태.
   - 전송 규칙: 변경시에만 전송 여부, 활성 여부.
   - 채널: 기존 `populateChannelFields()` 동작을 유지한다.

2. 비밀값은 볼 수 없어야 한다.
   - 플랫폼 계정 로그인 ID, 비밀번호, 2차인증 이메일 주소, 이메일 앱 비밀번호는 값 대신 `설정됨`/`미설정`만 표시한다.
   - 텔레그램 봇 토큰과 webhook 보안키도 값 대신 `설정됨`/`미설정`만 표시한다.
   - 제목 키워드와 발신자 키워드는 비밀값이 아니므로 현재값을 채울 수 있다.

3. options endpoint에 큰 현재값을 싣지 않는다.
   - `/options`는 viewer도 접근할 수 있고 여러 select가 공유한다.
   - URL이나 credential 상태를 generic option data로 흘리지 않는다.
   - edit-state endpoint를 operator 전용으로 만들고, 선택한 항목 하나만 가져온다.

4. "비우면 유지" 의미를 깨지 않는다.
   - 이번 작업의 핵심은 현재값을 알 수 있게 하는 것이다.
   - 값을 비워서 DB 값을 삭제하는 새 기능은 이 작업 범위가 아니다.
   - 입력을 비워 저장하면 기존 값이 유지된다는 안내는 계속 남긴다.

5. 실패는 같은 줄에서 보인다.
   - edit-state 조회 실패, 권한 부족, scope mismatch는 해당 편집 row의 inline status에 표시한다.
   - 실패 시 기존 입력값을 조용히 지우지 않는다.

---

## Task 0 - 기준선 확인

**Intent:** 구현 전에 현재 동작과 테스트 기준선을 확인한다.

**Files:** 없음

- [ ] 작업 전 git 상태를 확인한다.

```powershell
git status --short
```

Expected:

- 구현자가 만든 변경 외에 의도하지 않은 변경이 없어야 한다.
- 기존 변경이 있으면 되돌리지 말고 이번 작업과 충돌하는지만 판단한다.

- [ ] 관련 테스트 기준선을 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py tests/server/test_admin_dashboard.py tests/server/test_admin_security.py -q
```

Expected:

- 현재 main 기준으로 통과해야 한다.
- 기존 실패가 있으면 실패 테스트명과 이번 작업 영향 여부를 문서 또는 작업 로그에 적는다.

---

## Task 1 - edit-state scoped read service 추가

**Intent:** route가 repository에 직접 접근하지 않고, tenant scope가 걸린 단건 read를 재사용하게 만든다.

**Files:**

- Modify: `src/rider_server/services/admin_entity_service.py`
- Test: `tests/server/test_admin_entity_crud.py`

- [ ] 실패 테스트를 먼저 추가한다.

Add tests near existing service tests:

```python
def test_get_monitoring_target_for_edit_is_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_monitoring_target_for_edit("mt-1", tenant_id=_TENANT))


def test_get_platform_account_for_edit_is_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_platform_account_for_edit("pa-1", tenant_id=_TENANT))
```

Also add scoped happy-path tests for subscription, delivery rule, tenant if helpers exist in the file:

```python
def test_get_delivery_rule_for_edit_is_tenant_scoped_through_target() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target("mt-other", tenant=_OTHER))
    repo.seed_delivery_rule(
        DeliveryRule(id="rule-1", target_id="mt-other", channel_id="ch-1")
    )
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_delivery_rule_for_edit("rule-1", tenant_id=_TENANT))
```

- [ ] 실패를 확인한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py::test_get_monitoring_target_for_edit_is_tenant_scoped tests/server/test_admin_entity_crud.py::test_get_platform_account_for_edit_is_tenant_scoped -q
```

Expected before implementation:

```text
FAILED
```

- [ ] `AdminEntityService`에 scoped read method를 추가한다.

Implementation shape:

```python
    async def get_platform_account_for_edit(
        self, account_id: str, *, tenant_id: str
    ) -> PlatformAccount:
        return await self._scoped_platform_account(account_id, tenant_id=tenant_id)

    async def get_monitoring_target_for_edit(
        self, target_id: str, *, tenant_id: str
    ) -> MonitoringTarget:
        return await self._scoped_target(target_id, tenant_id=tenant_id)

    async def get_subscription_for_edit(
        self, subscription_id: str, *, tenant_id: str
    ) -> Subscription:
        return await self._scoped_subscription(subscription_id, tenant_id=tenant_id)

    async def get_tenant_for_edit(self, tenant_id: str) -> Tenant:
        return await self._scoped_tenant(tenant_id)

    async def get_delivery_rule_for_edit(
        self, rule_id: str, *, tenant_id: str
    ) -> DeliveryRule:
        rule, _target = await self._scoped_rule(rule_id, tenant_id=tenant_id)
        return rule
```

Rules:

- Do not expose repository methods to routes.
- Do not return dicts from service in this task. Keep service domain-oriented.
- Do not add write behavior.

- [ ] service tests pass.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py -q
```

Expected:

```text
passed
```

---

## Task 2 - operator-only edit-state JSON endpoints 추가

**Intent:** 선택한 항목 하나의 현재 설정을 안전한 JSON으로 가져온다.

**Files:**

- Modify: `src/rider_server/admin/crud_routes.py`
- Test: `tests/server/test_admin_entity_crud.py`

- [ ] route 테스트를 먼저 추가한다.

Add route tests:

```python
def test_monitoring_target_edit_state_returns_safe_current_values() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    repo.seed_monitoring_target(
        MonitoringTarget(
            id="mt-1",
            tenant_id="tn-1",
            platform_account_id="pa-1",
            name="H&J",
            center_name="제이앤에이치플러스 의정부남부",
            external_id="store-77",
            url="https://example.test/dashboard",
            interval_minutes=2,
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
            status=MonitoringTargetStatus.ACTIVE,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {
        "id": "mt-1",
        "name": "H&J",
        "center_name": "제이앤에이치플러스 의정부남부",
        "external_id": "store-77",
        "url": "https://example.test/dashboard",
        "interval_minutes": 2,
        "schedule_enabled": True,
        "start_time": "09:00",
        "stop_time": "22:00",
        "status": "ACTIVE",
    }
```

```python
def test_platform_account_edit_state_never_returns_raw_credentials() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.BAEMIN,
            label="쿠팡 운영 계정",
            username="real-login-id",
            password="plain-password",
            verification_email_address="owner@example.test",
            verification_email_app_password="mail-app-password",
            verification_email_subject_keyword="인증번호",
            verification_email_sender_keyword="coupang",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/platform-accounts/pa-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    body = resp.text
    assert "real-login-id" not in body
    assert "plain-password" not in body
    assert "owner@example.test" not in body
    assert "mail-app-password" not in body
    assert resp.json() == {
        "id": "pa-1",
        "platform": "BAEMIN",
        "label": "쿠팡 운영 계정",
        "username_label": "설정됨",
        "password_label": "설정됨",
        "verification_email_address_label": "설정됨",
        "verification_email_app_password_label": "설정됨",
        "verification_email_subject_keyword": "인증번호",
        "verification_email_sender_keyword": "coupang",
        "auth_state": "UNKNOWN",
    }
```

```python
def test_edit_state_routes_require_operator() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.FORBIDDEN
```

```python
def test_edit_state_routes_are_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.NOT_FOUND
```

Add similar focused tests for:

- `/admin/customers/{tenant_id}/edit-state`
- `/admin/subscriptions/{subscription_id}/edit-state`
- `/admin/delivery-rules/{rule_id}/edit-state`

Required JSON shapes:

```json
{
  "id": "tn-1",
  "name": "H&J",
  "status": "PAYMENT_ACTIVE",
  "telegram_bot_token_label": "설정됨",
  "telegram_webhook_secret_label": "미설정",
  "sending_enabled": false,
  "send_test_passed": false
}
```

```json
{
  "id": "sub-1",
  "status": "PAYMENT_ACTIVE"
}
```

```json
{
  "id": "rule-1",
  "enabled": true,
  "send_only_on_change": true
}
```

- [ ] 실패를 확인한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py::test_monitoring_target_edit_state_returns_safe_current_values tests/server/test_admin_entity_crud.py::test_platform_account_edit_state_never_returns_raw_credentials tests/server/test_admin_entity_crud.py::test_edit_state_routes_require_operator tests/server/test_admin_entity_crud.py::test_edit_state_routes_are_tenant_scoped -q
```

Expected before implementation:

```text
FAILED
```

- [ ] `crud_routes.py`에 JSONResponse와 helper를 추가한다.

Implementation shape:

```python
from fastapi.responses import HTMLResponse, JSONResponse
```

```python
def _configured_label(value: str | None) -> str:
    return "설정됨" if (value or "").strip() else "미설정"
```

- [ ] edit-state endpoints를 추가한다.

Implementation shape:

```python
@router.get("/monitoring-targets/{target_id}/edit-state")
async def monitoring_target_edit_state(
    request: Request, target_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        target = await _service(request).get_monitoring_target_for_edit(
            target_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": target.id,
            "name": target.name,
            "center_name": target.center_name,
            "external_id": target.external_id,
            "url": target.url,
            "interval_minutes": target.interval_minutes,
            "schedule_enabled": target.schedule_enabled,
            "start_time": target.start_time,
            "stop_time": target.stop_time,
            "status": target.status.value,
        }
    )
```

```python
@router.get("/platform-accounts/{account_id}/edit-state")
async def platform_account_edit_state(
    request: Request, account_id: str, _principal=Depends(require_operator)
) -> JSONResponse:
    try:
        account = await _service(request).get_platform_account_for_edit(
            account_id, tenant_id=_tenant_id(request)
        )
    except (LookupError, ValueError) as exc:
        _raise_for(exc)
    return JSONResponse(
        {
            "id": account.id,
            "platform": account.platform.value,
            "label": account.label,
            "username_label": _configured_label(account.username),
            "password_label": _configured_label(account.password),
            "verification_email_address_label": _configured_label(
                account.verification_email_address
            ),
            "verification_email_app_password_label": _configured_label(
                account.verification_email_app_password
            ),
            "verification_email_subject_keyword": account.verification_email_subject_keyword,
            "verification_email_sender_keyword": account.verification_email_sender_keyword,
            "auth_state": account.auth_state.value,
        }
    )
```

Also add customer, subscription, delivery-rule endpoints using the JSON shapes above.

Rules:

- Keep all edit-state endpoints operator-only.
- Do not return raw credential fields.
- Do not include values in response headers.
- Do not add these values to `/options` fragments.

- [ ] route tests pass.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py -q
```

Expected:

```text
passed
```

---

## Task 3 - 관리 폼에 현재값 로드 UI 추가

**Intent:** 드롭다운 선택 시 운영자가 현재 설정을 즉시 볼 수 있게 한다.

**Files:**

- Modify: `src/rider_server/admin/templates/_entity_admin.html`
- Test: `tests/server/test_admin_entity_crud.py`

- [ ] 템플릿 source 테스트를 먼저 추가한다.

Add tests:

```python
def test_entity_admin_has_current_value_load_hooks() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert 'onchange="loadTargetEditState(this)"' in template
    assert 'onchange="loadPlatformAccountEditState(this)"' in template
    assert 'onchange="loadCustomerEditState(this)"' in template
    assert 'onchange="loadSubscriptionEditState(this)"' in template
    assert 'onchange="loadDeliveryRuleEditState(this)"' in template
    assert "function fetchEditState(" in template
    assert "/edit-state?tenant=" in template
```

```python
def test_entity_admin_secret_current_values_are_status_labels_only() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert 'id="acc-current-username"' in template
    assert 'id="acc-current-password"' in template
    assert 'id="acc-current-email"' in template
    assert 'id="acc-current-email-password"' in template
    assert "username_label" in template
    assert "password_label" in template
    assert "verification_email_app_password_label" in template
    assert "document.getElementById('acc-edit-password').value = data.password" not in template
    assert "document.getElementById('acc-edit-email-password').value = data.verification_email_app_password" not in template
```

- [ ] 실패를 확인한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py::test_entity_admin_has_current_value_load_hooks tests/server/test_admin_entity_crud.py::test_entity_admin_secret_current_values_are_status_labels_only -q
```

Expected before implementation:

```text
FAILED
```

- [ ] target select에 현재값 로드 hook을 추가한다.

Change:

```html
<select id="tgt-edit-id"
        hx-get="/admin/monitoring-targets/options?tenant={{ tenant_id | urlencode }}"
        hx-trigger="load, admin-entity-changed from:body" hx-target="this" hx-swap="innerHTML">
```

To:

```html
<select id="tgt-edit-id"
        hx-get="/admin/monitoring-targets/options?tenant={{ tenant_id | urlencode }}"
        hx-trigger="load, admin-entity-changed from:body" hx-target="this" hx-swap="innerHTML"
        onchange="loadTargetEditState(this)">
```

- [ ] platform account, customer, subscription, delivery rule select에도 같은 방식의 hook을 추가한다.

Required hook mapping:

| select id | function |
| --- | --- |
| `acc-edit-id` | `loadPlatformAccountEditState(this)` |
| `cust-edit-id` | `loadCustomerEditState(this)` |
| `sub-edit-id` | `loadSubscriptionEditState(this)` |
| `rule-edit-id` | `loadDeliveryRuleEditState(this)` |

- [ ] secret status labels를 플랫폼 계정 편집 row에 추가한다.

Add near account edit inputs:

```html
<span class="muted current-value" id="acc-current-username">로그인 ID: 항목을 선택하세요</span>
<span class="muted current-value" id="acc-current-password">로그인 비밀번호: 항목을 선택하세요</span>
<span class="muted current-value" id="acc-current-email">2차인증 이메일: 항목을 선택하세요</span>
<span class="muted current-value" id="acc-current-email-password">앱 비밀번호: 항목을 선택하세요</span>
```

Rules:

- These are status labels, not inputs.
- Do not place raw credential value in any label.
- Password and email app password inputs remain blank.

- [ ] shared fetch helper와 loader 함수를 추가한다.

Implementation shape:

```javascript
  function fetchEditState(url, row) {
    var status = row ? row.querySelector('.inline-action-status') : null;
    if (status) {
      status.className = 'inline-action-status action-result warn';
      status.textContent = '현재값 불러오는 중';
    }
    return fetch(url, {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function(response) {
      if (!response.ok) {
        throw new Error('현재값을 불러오지 못했습니다');
      }
      return response.json();
    }).then(function(data) {
      if (status) {
        status.className = 'inline-action-status action-result ok';
        status.textContent = '현재값 불러옴';
      }
      return data;
    }).catch(function() {
      if (status) {
        status.className = 'inline-action-status action-result err';
        status.textContent = '현재값 조회 실패 · 권한 또는 대상을 확인하세요';
      }
      throw new Error('edit state load failed');
    });
  }
```

```javascript
  function loadTargetEditState(select) {
    if (!select.value) { return; }
    var row = select.closest('.edit-row');
    fetchEditState(
      '/admin/monitoring-targets/' + encodeURIComponent(select.value) + '/edit-state?tenant=' + CRUD_TENANT,
      row
    ).then(function(data) {
      document.getElementById('tgt-edit-name').value = data.name || '';
      document.getElementById('tgt-edit-center').value = data.center_name || '';
      document.getElementById('tgt-edit-external').value = data.external_id || '';
      document.getElementById('tgt-edit-url').value = data.url || '';
      document.getElementById('tgt-edit-interval').value = data.interval_minutes || '';
      document.getElementById('tgt-edit-schedule').value = data.schedule_enabled ? 'true' : 'false';
      document.getElementById('tgt-edit-start').value = data.start_time || '';
      document.getElementById('tgt-edit-stop').value = data.stop_time || '';
      syncEntityFormButtons();
    }).catch(function() {});
  }
```

```javascript
  function loadPlatformAccountEditState(select) {
    if (!select.value) { return; }
    var row = select.closest('.edit-row');
    fetchEditState(
      '/admin/platform-accounts/' + encodeURIComponent(select.value) + '/edit-state?tenant=' + CRUD_TENANT,
      row
    ).then(function(data) {
      document.getElementById('acc-edit-label').value = data.label || '';
      document.getElementById('acc-edit-username').value = '';
      document.getElementById('acc-edit-password').value = '';
      document.getElementById('acc-edit-email').value = '';
      document.getElementById('acc-edit-email-password').value = '';
      document.getElementById('acc-edit-email-subject').value = data.verification_email_subject_keyword || '';
      document.getElementById('acc-edit-email-sender').value = data.verification_email_sender_keyword || '';
      document.getElementById('acc-current-username').textContent = '로그인 ID: ' + (data.username_label || '미설정');
      document.getElementById('acc-current-password').textContent = '로그인 비밀번호: ' + (data.password_label || '미설정');
      document.getElementById('acc-current-email').textContent = '2차인증 이메일: ' + (data.verification_email_address_label || '미설정');
      document.getElementById('acc-current-email-password').textContent = '앱 비밀번호: ' + (data.verification_email_app_password_label || '미설정');
      syncEntityFormButtons();
    }).catch(function() {});
  }
```

Also implement:

- `loadCustomerEditState(select)`
- `loadSubscriptionEditState(select)`
- `loadDeliveryRuleEditState(select)`

Required behavior:

- Customer loader fills `cust-edit-name`, `cust-edit-status`, `tg-edit-sending` when the selected customer is the same control context.
- Subscription loader fills `sub-edit-status`.
- Delivery rule loader sets `rule-edit-change-only.checked`.
- Existing `populateChannelFields()` remains unchanged except for style/status cleanup if needed.

- [ ] 기존 "비우면 유지" 안내를 더 정확히 바꾼다.

Target wording:

```text
항목을 선택하면 현재 일반 설정값을 불러옵니다. 비밀값은 값 대신 설정 여부만 표시됩니다. 비밀값을 바꾸려면 새 값을 입력하세요.
```

Keep this separate:

```text
입력값을 비우고 저장하면 기존 값을 유지합니다.
```

- [ ] 템플릿 테스트가 통과한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py::test_entity_admin_has_current_value_load_hooks tests/server/test_admin_entity_crud.py::test_entity_admin_secret_current_values_are_status_labels_only -q
```

Expected:

```text
passed
```

---

## Task 4 - no-secret exposure regression 강화

**Intent:** 새 JSON endpoint와 HTML이 비밀값을 노출하지 않는지 고정한다.

**Files:**

- Modify: `tests/server/test_admin_entity_crud.py`

- [ ] route response 전체에 raw secret이 없는지 테스트를 추가한다.

Add test:

```python
def test_platform_account_edit_state_redacts_all_secret_like_values() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.COUPANG,
            label="계정",
            username="coupang-real-user",
            password="coupang-real-password",
            verification_email_address="mail-owner@example.test",
            verification_email_app_password="real-mail-app-password",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/platform-accounts/pa-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    text = resp.text
    for forbidden in (
        "coupang-real-user",
        "coupang-real-password",
        "mail-owner@example.test",
        "real-mail-app-password",
    ):
        assert forbidden not in text
    for expected in (
        '"username_label":"설정됨"',
        '"password_label":"설정됨"',
        '"verification_email_address_label":"설정됨"',
        '"verification_email_app_password_label":"설정됨"',
    ):
        assert expected in text
```

- [ ] HTML template에 raw secret field mapping이 없는지 source guard를 추가한다.

Add assertions to the template test:

```python
for forbidden_snippet in (
    ".value = data.username",
    ".value = data.password",
    ".value = data.verification_email_address",
    ".value = data.verification_email_app_password",
    "data-password",
    "data-verification-email-app-password",
):
    assert forbidden_snippet not in template
```

- [ ] 보안 테스트를 실행한다.

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py::test_platform_account_edit_state_redacts_all_secret_like_values tests/server/test_admin_entity_crud.py::test_entity_admin_secret_current_values_are_status_labels_only -q
```

Expected:

```text
passed
```

---

## Task 5 - UI 동작 회귀 확인

**Intent:** 기존 채널 자동 채움, 버튼 활성화, partial update 흐름이 깨지지 않았는지 확인한다.

**Files:**

- Modify: `tests/server/test_admin_entity_crud.py`

- [ ] 기존 채널 자동 채움 계약을 명시 테스트로 고정한다.

Add or extend existing test:

```python
def test_entity_admin_channel_autofill_contract_stays_in_place() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert 'onchange="populateChannelFields(this)"' in template
    assert "function populateChannelFields(select)" in template
    assert "option.dataset.chat" in template
    assert "option.dataset.thread" in template
    assert "option.dataset.kakao" in template
```

- [ ] edit-state 조회 실패가 같은 row에 표시되는지 source test를 추가한다.

Add test:

```python
def test_entity_admin_edit_state_failure_uses_inline_status() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert "현재값 조회 실패 · 권한 또는 대상을 확인하세요" in template
    assert "select.closest('.edit-row')" in template
    assert "row.querySelector('.inline-action-status')" in template
```

- [ ] partial update 의미가 유지되는지 source test를 유지하거나 추가한다.

Expected source facts:

- `filledValues(` is still used for edit submit.
- Secret credential edit still uses blank input as replacement value only.
- No route treats omitted values as clear.

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py -q
```

Expected:

```text
passed
```

---

## Task 6 - 운영 문구와 오래된 문서 정리

**Intent:** 운영자가 "값을 볼 수 없는 것"과 "값이 없는 것"을 혼동하지 않게 한다.

**Files:**

- Modify: `src/rider_server/admin/templates/_entity_admin.html`
- Optional Modify: `docs/operations/aws-product-setup-2026-06-18.md`

- [ ] 계정 관리 안내 문구를 현재 코드 기준으로 정리한다.

Required message meaning:

```text
비밀번호, 이메일 앱 비밀번호, 토큰 같은 비밀값은 다시 표시하지 않습니다. 설정 여부만 표시되고, 새 값을 입력하면 교체됩니다.
```

Keep this meaning:

```text
값은 DB 또는 설정된 secret resolver 경로에 있을 수 있으므로 DB 접근/백업을 민감정보로 관리하세요.
```

- [ ] 업체 편집 안내 문구를 현재값 로드 기준으로 정리한다.

Required message meaning:

```text
업체를 선택하면 현재 일반 설정값을 불러옵니다. 비밀값은 표시하지 않습니다.
```

- [ ] stale 문서가 구현과 반대로 말하는 경우 현재 코드 기준으로 고친다.

Specific file:

- `docs/operations/aws-product-setup-2026-06-18.md`

Required update:

- "SecretRef 핸들만 저장"처럼 단정하는 문구를 현재 구현 기준으로 바꾼다.
- Plain meaning: Admin web app may accept direct values or handles. The UI must not re-display raw secrets. DB and backups must be treated as sensitive.

- [ ] 문서/템플릿 source 확인.

```powershell
rg -n "SecretRef 핸들만|plaintext values belong|비밀값은 다시 표시하지 않습니다|설정 여부만" docs src/rider_server/admin/templates/_entity_admin.html
```

Expected:

- 새 안내 문구가 보인다.
- 오래된 단정 문구가 남아 있으면 현재 코드와 맞는 문맥인지 확인하고 정리한다.

---

## Task 7 - 수동 화면 검증

**Intent:** 운영자가 실제로 드롭다운 선택 후 현재 설정을 볼 수 있는지 확인한다.

**Files:** 없음

- [ ] 로컬 admin server를 실행한다.

```powershell
$env:PYTHONPATH="src"
$env:RIDER_ADMIN_PUBLIC_ACCESS="true"
$env:RIDER_ADMIN_MFA_REQUIRED="false"
.venv\Scripts\python.exe -m rider_server
```

Expected:

- `http://127.0.0.1:8000/admin` 접속 가능.
- DB가 없으면 기존 DB 실패 안내가 보인다. 이 경우 seeded/dev admin 경로로 확인하거나 테스트 클라이언트 검증으로 대체한다.

- [ ] 관리 탭에서 업체 선택을 확인한다.

Manual expected:

- 업체 선택 시 표시명, 센터/상점명, 관리 코드, URL, 수집 주기, 전송 허용 시간이 채워진다.
- inline status에 `현재값 불러옴`이 표시된다.
- 비밀값은 표시되지 않는다.

- [ ] 플랫폼 계정 선택을 확인한다.

Manual expected:

- 라벨, 제목 키워드, 발신자 키워드는 채워진다.
- 로그인 ID, 로그인 비밀번호, 2차인증 이메일, 앱 비밀번호는 raw 값이 채워지지 않는다.
- 각 credential 옆에는 `설정됨` 또는 `미설정` 상태가 보인다.

- [ ] 구독, 고객, 전송 규칙 선택을 확인한다.

Manual expected:

- 구독 상태 select가 현재 상태로 맞춰진다.
- 고객명/고객 상태가 현재값으로 맞춰진다.
- 전송 규칙의 `변경시에만 전송` 체크박스가 현재값으로 맞춰진다.

- [ ] 실패 경로를 확인한다.

Manual expected:

- 권한 부족 또는 잘못된 tenant scope에서는 같은 edit row에 `현재값 조회 실패 · 권한 또는 대상을 확인하세요`가 표시된다.
- 실패가 다른 입력칸을 조용히 지우지 않는다.

---

## 통합 검증

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/server/test_admin_entity_crud.py tests/server/test_admin_dashboard.py tests/server/test_admin_security.py -q
```

Expected:

```text
passed
```

If any protected Coupang login/2FA runtime file is changed by mistake, stop and run the protected test set from `AGENTS.md` before claiming completion:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

Expected:

```text
passed
```

---

## 완료 기준

- [ ] 업체 선택 시 현재 일반 설정값이 보인다.
- [ ] 플랫폼 계정 선택 시 라벨/키워드는 보이고 credential은 `설정됨`/`미설정`만 보인다.
- [ ] 고객, 구독, 전송 규칙 선택 시 현재 상태가 보인다.
- [ ] 채널 선택 시 기존 채팅 ID/토픽/카카오 방명 자동 채움이 유지된다.
- [ ] `/options` endpoint에는 현재 상세값을 새로 싣지 않는다.
- [ ] edit-state endpoint는 operator-only이고 tenant scope를 지킨다.
- [ ] 비밀번호, 이메일 앱 비밀번호, 토큰, OTP, raw credential은 HTML/JSON/data attribute에 노출되지 않는다.
- [ ] "비우면 유지" 부분 업데이트 의미가 유지된다.
- [ ] 조회 실패가 같은 row의 inline status에 보인다.
- [ ] 관련 server tests가 통과한다.

---

## 구현자가 주의할 점

- "기존값을 보여준다"와 "비밀값을 다시 보여준다"는 다르다. 비밀값은 설정 여부만 보여준다.
- `response_remote_admin.html` 같은 저장된 HTML은 참고 자료일 뿐이다. 현재 코드와 테스트가 정본이다.
- `crud_routes.py`의 기존 `require_viewer` options endpoint를 확장해서 현재값을 넣지 않는다. operator-only edit-state endpoint를 사용한다.
- 템플릿에 secret-looking field 이름이 있어도 raw value assignment는 금지한다.
- source guard 테스트는 완전한 보안 장치가 아니므로 route response test와 함께 둔다.
- 기존 사용자가 익숙한 부분 업데이트 흐름을 바꾸지 않는다.
