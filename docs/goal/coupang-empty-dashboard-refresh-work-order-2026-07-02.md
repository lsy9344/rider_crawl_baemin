# 작업 지시서: 쿠팡 빈 대시보드 shell 재시도와 Admin 표시 분리

작성일: 2026-07-02
상태: 코드 구현 및 자동 테스트 완료 / 실제 headed Chrome 검증 전
대상: `해운대이로움 남구중앙` 반복 `PARSER_MISSING_DATA`

## 목표

쿠팡 `peak-dashboard`가 로그인된 화면 형태와 섹션은 보여주지만 실제 수집 수치가 비어 있는 경우를 일반 “수집 기간 초과”처럼 보이지 않게 분리 표시한다. 같은 상황에서는 2FA OTP 복구를 호출하지 않고 대상 URL을 한 번 reload 한 뒤 재파싱한다.

## 현재 원인

- `parse_peak_dashboard_html()`은 `업데이트`, `배정 물량`, `처리 물량`, `거절률`, 피크별 `목표/완료` 값이 없으면 `MissingPerformanceDataError`를 던진다.
- `_content_with_post_load_recovery()`는 데이터 누락 화면이 로그인처럼 보일 때만 email 2FA 복구를 시도한다.
- 로그인처럼 보이지 않는 빈 대시보드 shell은 OTP 낭비를 막기 위해 바로 `PARSER_MISSING_DATA`로 끝났다.
- Admin은 이 실패를 마지막 성공 시각 기반 신선도와 함께 보여줘 운영자가 “수집 시간만 초과된 상태”로 오해할 수 있었다.

## 구현 방침

1. Admin 표시
   - DB enum이나 job error code는 추가하지 않는다.
   - 최신 실패가 `PARSER_MISSING_DATA`이고 마지막 성공 이후 발생한 경우 Admin 표시 severity를 `CRAWL_DATA_MISSING`으로 변환한다.
   - 라벨은 `크롤링 데이터 누락`으로 표시한다.
   - 사유 문구는 “쿠팡 화면 형태는 열렸지만 실제 수집 데이터가 비어 있음”으로 시작해, 로그인 만료와 구분한다.

2. Coupang crawler
   - `post_load_validate`가 `MissingPerformanceDataError`를 던졌고 화면이 로그인처럼 보이지 않으면 email 2FA 복구를 호출하지 않는다.
   - 대신 `_reload_target_page()`로 target URL을 한 번 다시 연다.
   - 다시 readiness와 센터 탭 선택을 확인한 뒤 재파싱한다.
   - reload 후에도 데이터가 없으면 기존처럼 `MissingPerformanceDataError`를 유지한다.
   - reload 후 화면이 로그인으로 바뀌어 보이면 기존 `AUTH_REQUIRED` escalator를 유지한다.

3. 보호 계약
   - Coupang email 2FA selector, OTP 입력, IMAP 흐름은 변경하지 않는다.
   - 새 동작은 authenticated empty shell에 대한 reload 1회로 제한한다.
   - OTP, Coupang password, email app password, plaintext secret은 로그/DB/화면에 남기지 않는다.

## 테스트 계획

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_crawler.py tests\server\test_admin_dashboard.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

운영 claim 전에는 실제 headed Chrome 또는 Agent PC의 Coupang profile에서 `peak-dashboard` 수집 흐름을 한 번 확인한다.

## 2026-07-02 적용 결과

- Admin은 최신 활성 실패가 `PARSER_MISSING_DATA`이면 `CRAWL_DATA_MISSING` 표시 상태로 변환한다.
- 카드/드로어 라벨은 `크롤링 데이터 누락`으로 보이고, 사유는 “쿠팡 화면 형태는 열렸지만 실제 수집 데이터가 비어 있음”으로 표시한다.
- Coupang crawler는 로그인처럼 보이지 않는 빈 대시보드 shell에서 email 2FA 복구를 호출하지 않고 target URL reload를 한 번 수행한다.
- reload 후에도 데이터가 없으면 기존처럼 `MissingPerformanceDataError`를 유지한다.

검증:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_crawler.py -q
# 91 passed

.\.venv\Scripts\python.exe -m pytest tests\server\test_admin_dashboard.py -q
# 130 passed

.\.venv\Scripts\python.exe -m pytest tests\server\test_dashboard_severity.py -q
# 49 passed

.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
# 428 passed, 22 skipped
```

남은 수동 확인:

- Agent PC 또는 로컬 headed Chrome에서 실제 `peak-dashboard`가 빈 shell을 만났을 때 reload 1회 후 정상 수집되는지 확인한다.

2026-07-02 headed 확인 시도:

- 로컬 CDP `http://127.0.0.1:61222`에 연결은 됐다.
- `/json/list` 기준 대상 탭은 `https://xauth.coupang.com/...redirect_uri=...peak-dashboard` 로그인 화면이었다.
- 이 상태에서는 수집 경로가 `BrowserActionRequiredError`로 끝나므로, 로그인 완료 후 다시 headed 수집 검증이 필요하다.
