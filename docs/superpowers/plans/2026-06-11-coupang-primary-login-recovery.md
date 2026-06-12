# Coupang Primary Login Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 쿠팡 Vendor Portal 1차 로그인 화면이 떠도 저장된 계정 파일로 로그인한 뒤 기존 Gmail 이메일 2FA 자동 복구를 이어간다.

**Architecture:** `AppConfig`에 쿠팡 계정 JSON 경로를 추가하고, `auth/coupang_email_2fa.py`의 복구 흐름 안에서 비밀번호 로그인 화면이면 계정 파일을 읽어 아이디/비밀번호를 채운 뒤 로그인 버튼을 누른다. 로그인 후 2단계 인증 화면에서 기존 이메일 인증 흐름을 그대로 사용한다.

**Tech Stack:** Python dataclass config, Playwright sync locator API, pytest.

---

### Task 1: Credential Path And Loader

**Files:**
- Modify: `src/rider_crawl/config.py`
- Modify: `tests/test_config.py`
- Create: `secrets/google/coupang.credentials.json`

- [ ] **Step 1: Write failing tests**

Add tests that assert `AppConfig.from_env()` exposes `coupang_credentials_path` with default `secrets/google/coupang.credentials.json` and env override `COUPANG_CREDENTIALS_PATH`.

- [ ] **Step 2: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q`
Expected: fail because `coupang_credentials_path` does not exist yet.

- [ ] **Step 3: Implement config field**

Add `DEFAULT_COUPANG_CREDENTIALS_PATH`, `coupang_credentials_path`, and env reading.

- [ ] **Step 4: Create local credential file**

Write `secrets/google/coupang.credentials.json` with `username` and `password`.

### Task 2: Primary Login Recovery

**Files:**
- Modify: `src/rider_crawl/auth/coupang_email_2fa.py`
- Modify: `tests/test_coupang_email_2fa.py`

- [ ] **Step 1: Write failing tests**

Add tests that simulate the Vendor Portal login screen and assert the recovery function fills username/password, clicks login, then continues to email 2FA.

- [ ] **Step 2: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_coupang_email_2fa.py -q`
Expected: fail because primary login is currently treated as out of scope.

- [ ] **Step 3: Implement minimal login helper**

Read JSON credentials, fill visible username/password fields, click the login button, wait briefly, then continue with existing email 2FA flow. Return `False` if credential file is missing or the screen is not a password login screen.

- [ ] **Step 4: Verify**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_coupang_email_2fa.py tests/test_gmail_2fa.py tests/test_coupang_crawler.py -q`
Expected: pass.

### Task 3: Operational Verification

**Files:**
- No source change.

- [ ] **Step 1: Run full tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Verify with real Chrome session**

Use the configured 쿠팡 CDP tab, force `send_enabled=False`, and run `run_once(config)`.
Expected: `RUN_ONCE_OK`, `sent: False`, and a generated message.
