# 크롤링 탭 중지 트러블슈팅 & 인시던트 기록

크롤링 탭이 멈추거나 "자동 중지"될 때 **원인을 빠르게 좁히고 조치**하기 위한 참고 문서다.
실제로 발생한 사건은 아래 "인시던트 로그"에 날짜순으로 누적한다. 새 사건을 해결하면
같은 형식(증상 / 트레이스백 / 조사 / 원인 / 조치 / 검증 / 교훈)으로 한 건씩 추가할 것.

관련 운영 메모(탭→포트 매핑, 앱 실행/재시작, 2FA 등)는 사용자 auto-memory의
`app-run-and-coupang-2fa.md`에도 있다. 이 문서는 "탭이 멈췄다" 상황 전용 진단서다.

---

## 0. 30초 진단 체크리스트

탭이 멈추면 아래 순서로 좁힌다.

1. **어느 탭이, 무슨 에러로?** — 탭별 `log_dir`의 `run_errors.log` 마지막 트레이스백을 본다.
   탭마다 log_dir이 다르다(예: 크롤링1=`logs`, 크롤링3=`logs3`, 크롤링5=`logs5` —
   정확한 값은 `runtime/state/ui_settings.json` 확인).

2. **탭 → 포트 / 플랫폼 매핑 확인** — `runtime/state/ui_settings.json`의 `crawlings` 배열.
   인덱스 0 = 크롤링1. 각 항목의 `cdp_url`, `platform_name`, `performance_url`, `log_dir`.

3. **CDP 라이브 상태 확인** — 해당 탭 포트에 어떤 Chrome 탭이 열려 있는지 본다:
   ```bash
   curl -s http://127.0.0.1:<port>/json/list \
     | python -c "import sys,json;[print(p.get('type'),'|',p.get('url','')[:90]) for p in json.load(sys.stdin)]"
   ```
   - `xauth.coupang.com/...`(로그인/2FA 화면) → **세션 만료**. 2FA 자동복구 또는 수동 로그인.
   - 쿠팡: `rider-performance`만 있고 `peak-dashboard` 없음 → "대상 페이지 못 찾음"(인시던트 B).
   - 같은 URL 탭이 2개 이상 → "대상 탭이 여러 개" 오류. 중복 탭을 하나만 남긴다.
   - 배민: `deliverycenter.baemin.com/delivery/report` 탭이 떠 있고 로그인됐는지.

4. **`cannot import name ...` 류 import/모듈 에러** → 소스를 고치고 **앱을 재시작 안 함**.
   → 앱 재시작(아래 §A 교훈). 코드 문제 아님.

5. **`Executable doesn't exist at ...chromium-<build>`** → Playwright↔Chromium 빌드 불일치.
   → `C:\Users\dltnd\AppData\Local\Programs\Python\Python311\python.exe -m playwright install chromium`.

### 자주 나오는 에러 문구 → 원인 빠른표

| 에러 문구(일부) | 1차 원인 | 조치 |
|---|---|---|
| `cannot import name 'X' from 'rider_crawl....'` | 소스 수정 후 앱 미재시작(옛 모듈 캐시) | **앱 재시작** |
| `열려 있는 Chrome 탭에서 쿠팡이츠 대상 페이지를 찾지 못했습니다` | 대상 탭(보통 peak-dashboard) 부재 | §B(수정됨) / 대상 페이지 탭 열기 / 재시작 |
| `쿠팡이츠 대상 탭이 여러 개 열려 있습니다` | 같은 URL 중복 탭 | 중복 탭 하나만 남기기 |
| `Chrome CDP 연결 실패` / `ECONNREFUSED` | 그 포트에 Chrome 미기동 | '준비하기'로 해당 포트 Chrome 실행 |
| `세션이 만료되었습니다 / 다시 로그인` (xauth) | 진짜 로그인 만료 | 2FA 자동복구 또는 수동 로그인 |
| `Page.goto: Page crashed` / `Target crashed` | Chromium 렌더러 크래시(메모리 압박 등) | 다음 회차 새 탭 자동 복구(인시던트 C, 수정됨) |
| **로그가 어느 시각 이후 완전히 끊김**(에러도 성공도 없음) | 워커 스레드 사망 또는 행 | 인시던트 C 참고. 자동 재시작됨, 알림 확인 |

---

## 1. 재발하는 핵심 패턴 (먼저 의심할 것)

### A. editable 설치 + 실행 중 소스 수정 → **앱 재시작 필요**

`rider-crawl`은 editable 설치(`pip install -e .`)다. `src/` 수정은 디스크에 즉시
반영되지만, **실행 중인 앱 프로세스는 시작 시점에 import한 모듈을 메모리에 그대로
유지**한다. 특히 앱 프로세스 안에서 도는 모듈(`crawler.py`, `parser.py`, `message.py`,
쿠팡 `platforms/coupang/crawler.py` 등)은 고쳐도 **재시작 전엔 옛 코드가 돈다.**

- 증상이 "조용히 옛 동작"일 수도 있고, 새 심볼이 얽히면 `cannot import name ...`으로
  **시끄럽게** 터질 수도 있다(인시던트 A).
- 판별: 실행 중 프로세스 기동 시각 vs 소스 수정 시각 비교.
  ```bash
  # 기동 시각
  powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | \
    Where-Object { \$_.CommandLine -match 'rider_crawl' } | \
    Select-Object ProcessId, CreationDate, CommandLine | Format-List"
  ```
- 재시작 방법(표준): 기존 `python -m rider_crawl` 종료 → 재실행
  `python -X utf8 -m rider_crawl` (작업 폴더 `C:\code\rider_crawl_baemin`,
  인터프리터 `C:\Users\dltnd\AppData\Local\Programs\Python\Python311\python.exe`).
  GUI는 사용자 데스크톱 세션에 떠야 하므로 가능하면 사용자가 직접 실행한다.
- 재시작하면 **모든 탭 크롤 루프가 멈춘다.** 탭마다 '시작'을 다시 눌러야 한다(자동 시작 없음).
  CDP Chrome(포트 9222~)은 별도 프로세스라 로그인 세션은 유지된다.

### B. 운영 Chrome 탭 상태 의존성

크롤러는 "이미 열린 탭"을 CDP로 읽는다. 그래서 **그 순간 어떤 탭이 떠 있느냐**에 강하게
의존한다. 탭이 다른 페이지로 이동했거나, 닫혔거나, 중복이면 "대상 페이지 못 찾음/여러 개"가
난다. 로그인은 멀쩡한데도(=세션 만료 아님) 이 이유로 "로그인 만료·조치 필요"로 표시될 수 있다.

> 2026-06-14 기준, 쿠팡은 주 페이지(peak-dashboard) 탭이 없고 로그인된 다른 쿠팡 탭
> (rider-performance 등)만 있어도 **같은 세션에 임시 탭을 열어 읽도록** 보강됨(인시던트 B 조치).

---

## 2. 인시던트 로그

### 2026-06-14 — 모든 크롤링 탭 중지 (두 건 동시)

배민 형식 변경 작업 후 앱을 재시작하는 과정에서, 서로 **다른 원인**의 두 사건이 겹쳐
"모든 탭이 멈춘" 것처럼 보였다. 원인이 다르므로 따로 기록한다.

---

#### 인시던트 A — 크롤링1(배민) `cannot import name 'has_today_delivery_status'`

- **증상**
  ```
  [오류] 크롤링1 실행 중 예외: cannot import name 'has_today_delivery_status'
  from 'rider_crawl.parser' (C:\code\rider_crawl_baemin\src\rider_crawl\parser.py)
  ```
- **배경**: 배민 달성현황 메시지를 "오늘 수행건수 / 주간 목표건수"로 합치는 작업에서
  `parser.py`에 `has_today_delivery_status`를 추가하고, `crawler.py`가 그것을
  **top-level import**(`from .parser import has_today_delivery_status`)하도록 바꿈.
- **조사**
  - 디스크 파일은 정상: 함수가 top-level에 정의(line 482), `ast.parse` OK,
    **새 인터프리터**에서 `from rider_crawl.parser import has_today_delivery_status` 성공,
    editable 설치가 바로 이 `src`를 가리킴(site-packages에 사본 없음).
  - 실행 중 앱 프로세스 **PID 7436, 01:48 기동** vs 소스 수정 시각 02:1x → 프로세스가 수정 이전.
- **원인**: §1.A 패턴. 앱 시작 시 `rider_crawl.parser`는 **eager import**되어 옛 버전이
  `sys.modules`에 캐시됨(그 심볼 없음). `rider_crawl.crawler`는 크롤마다
  `baemin.py::_crawl_current_screen`에서 **lazy import**되어 신규 코드가 로드되는데,
  그 신규 crawler의 top-level `from .parser import has_today_delivery_status`가
  캐시된 옛 parser에서 심볼을 못 찾아 ImportError. (lazy import도 `sys.modules`의 옛
  모듈을 재사용하므로 파일을 다시 읽지 않는다.)
- **조치**: 앱 재시작(PID 7436 종료 → 4528 기동, 10:18 이전). 재시작 후 정상.
  - 라이브 검증(CDP 9222): `오전오후피크 : 471건/363건[100%]` 등 정상 출력.
  - 관련 커밋: `456d9a6` (feat(baemin): 달성현황 메시지 '오늘 수행건수/주간 목표건수' 결합).
- **교훈**: in-process 모듈 수정은 **반드시 앱 재시작**. parser에 새 심볼 추가 + crawler에서
  top-level import하면, "옛 parser 캐시 + 새 crawler lazy import" 조합에서 ImportError로
  드러난다(조용히 옛 로직 도는 것보다 차라리 안전 신호 — 재시작하라는 뜻).

---

#### 인시던트 B — 크롤링2~5(쿠팡) "쿠팡이츠 대상 페이지를 찾지 못했습니다"

- **증상**
  ```
  [오류] 크롤링N 로그인 만료·조치 필요로 자동 중지: 열려 있는 Chrome 탭에서
  쿠팡이츠 대상 페이지를 찾지 못했습니다.
  https://partner.coupangeats.com/page/peak-dashboard 페이지를 로그인된 상태로 열어두세요.
  ```
- **트레이스백 핵심**
  ```
  platforms/coupang/crawler.py:59  crawl_performance_snapshot  (peak-dashboard 조회)
  platforms/coupang/crawler.py:406 fetch_page_html_via_cdp
  platforms/coupang/crawler.py:518 _fetch_target_page_content
  platforms/coupang/crawler.py:802 _raise_coupang_page_action_required
  → BrowserActionRequiredError: ... 대상 페이지를 찾지 못했습니다 (peak-dashboard)
  ```
- **조사 (CDP 라이브 probe, `/json/list`)**
  - **9223(탭2)**: `xauth.coupang.com/.../login-actions/authenticate` — **진짜 세션 만료**.
    (사용자가 직접 로그인 처리하기로 함 → 본 건에서 제외.)
  - **9224(탭3), 9225(탭4), 9226(탭5)**: **로그인됨**, 단 `rider-performance` 탭만 있고
    `peak-dashboard` 탭 **없음**. (처음엔 탭4만 정상으로 보였으나, 곧 모두 동일 상태로 실패 —
    탭4는 그 시점에 아직 크롤이 안 돌았을 뿐.)
- **원인**: 쿠팡 주 페이지는 peak-dashboard. `_select_page_by_url(pages, peak-dashboard)`가
  peak-dashboard 탭 0개라 `None`. 로그인 페이지가 없어 `_recover_login_page_to_target`도
  `None`. 임시탭 폴백 `_open_rider_performance_in_new_tab`은 **rider-performance 타깃만**
  처리(가드: `if not _path_is(target_url, "/page/rider-performance"): return None`)하고
  peak-dashboard는 의도적으로 제외 → 폴백 없이 `_raise_coupang_page_action_required`로 raise.
  즉 **"로그인은 됐는데 peak-dashboard 탭만 없는" 상태의 폴백 부재**가 직접 원인.
  - "왜 peak-dashboard 없이 rider-performance만?": 운영 Chrome이 rider-performance에
    머물러 있었음(사용자/준비하기/이전 내비게이션 등 외부 요인). 크롤 자체는 임시
    rider-performance 탭을 **열고 닫으므로** peak 탭을 없애지 않는다. CDP Chrome은 앱
    재시작에도 살아남아 그 상태가 유지됨.
- **조치 (코드 수정, `platforms/coupang/crawler.py`)**
  1. `_open_rider_performance_in_new_tab` → **`_open_target_in_new_tab`** 로 일반화.
     가드를 `rider-performance OR peak-dashboard`로 확장. **단 `_coupang_logged_in_context`**
     (= `partner.coupangeats.com` 페이지가 떠 있는 컨텍스트)가 있을 때만 임시 탭을 연다.
     → 진짜 만료(로그인 탭만 있고 쿠팡 페이지 없음)는 임시 탭을 안 열고 기존 복구/조치
     흐름 그대로. 임시 peak-dashboard 탭이 로그인으로 리다이렉트되면
     `BrowserActionRequiredError`가 그대로 올라온다.
  2. **추적 로깅 `_log_page_selection_failure`** 추가: raise 직전,
     `<log_dir>/run_errors.log`에 한 줄 —
     `open_tabs=[host+path...], exact_match=, path_match=, login_page=, logged_in_context=`.
     (쿼리의 토큰/state/execution 등 민감값은 host+path만 남겨 제외.)
     → 다음에 또 못 찾으면 "탭 없음 vs 다른 페이지 vs 중복 vs 로그아웃"을 즉시 구분 가능.
  3. 테스트 갱신: `_does_not_open_temp_tab_for_missing_peak`(의도적 누락을 강제하던 테스트)
     → `_opens_temp_tab_for_missing_peak_when_logged_in`으로 교체 +
     `_skips_temp_tab_for_peak_when_no_logged_in_context` 추가.
- **검증**: 라이브 9224/9225/9226에서 `crawl_performance_snapshot` 성공 —
  해운대이로움 남구중앙 / 팀100 남양주동부 / 남구인라이더스 남구중앙 메시지 정상 출력,
  센터 검증 통과, 임시 탭은 읽은 뒤 닫힘(사용자 rider-performance 탭은 그대로).
  전체 테스트 **442 passed**.
- **적용**: `platforms/coupang/crawler.py`는 in-process → §1.A에 따라 **앱 재시작 필요**
  (PID 4528 → 18584, 10:18 기동). 이후 탭별 '시작'.
- **교훈**:
  - "로그인 만료·조치 필요"로 표시돼도 **실제 만료가 아닐 수 있다** — CDP로 실제 탭 상태를
    먼저 확인할 것(로그인됐는데 탭이 다른 페이지일 뿐).
  - CDP로 "열린 탭 읽기"는 탭 상태에 의존하므로, **로그인된 컨텍스트가 있으면 임시 탭으로
    대상을 직접 여는 패턴**이 견고하다(주/보조 페이지 모두).
  - 실패 지점엔 "그 순간 무엇이 열려 있었는지"를 남기는 진단 로깅이 사후 추적에 결정적.

---

### 2026-06-21 — 크롤링1(배민) 08:59 이후 13시간 무인 정지

- **증상**: 크롤링1이 오전 8시대 이후 카톡 전송이 끊김. 앱·다른 탭(크롤링2)은 정상.
  `logs/run_errors.log`의 **마지막 항목이 08:59:05**이고 그 이후로 에러도 성공도 전무.
  마지막 정상 전송 08:58:54(`runtime/state/crawling1/last_message.*.sha256` mtime / kakao_diagnostics).
- **트레이스백 핵심(08:59:05, 마지막 줄)**
  ```
  crawler.py _open_baemin_delivery_history_page → _goto_page → page.goto(.../center/change)
  playwright ... Error: Page.goto: Page crashed   (= Chromium 렌더러 크래시)
  → RuntimeError: Chrome CDP 연결 또는 배민 달성현황 수집 실패
  ```
- **조사**
  - 앱 프로세스(PID 15740)는 **장애 내내 06-17 기동 그대로**(오늘 재시작 안 됨), 9222 Chrome도 생존.
    `/json`상 페이지는 저녁엔 `/delivery/report`로 정상 복구돼 있었음(크래시 후 Chrome이 탭을 재로드).
  - "Page crashed"는 `except Exception`→`return False`→**5초 재시도**라야 하는데, 08:59:05 단 1건뿐.
    5초마다 같은 에러가 안 쌓였다 = 다음 회차가 에러도 안 남기고 멈춤 → **워커 루프가 더 안 돈다.**
  - 결정타: 같은 프로세스 안에서 ~22:08 수동 '시작'이 먹혀(`ui_settings.json` 저장 22:08, 재전송 22:18)
    크롤링1이 부활. `start()`는 워커가 살아 있으면 `_has_live_workers`로 **거부**하므로, 시작이
    먹혔다 = **옛 워커 스레드가 이미 죽어 있었다**는 직접 증거.
- **원인**: 렌더러 크래시 직후 회차에서 `Exception`이 아닌 예외(`asyncio.CancelledError` 등
  `BaseException`)가 `_run_once_background`의 `except Exception`과 `scheduler.run_loop`의 동기
  `self.run_job()` 밖으로 전파되어 **데몬 워커 스레드를 traceback 없이 종료**시켰다. 워커가 죽으니
  재시도·로그·전송이 전부 멈춤 → 무인 정지. 기여요인: 아침 내내 datastudio 달성현황 렌더가 느려
  `MissingPerformanceDataError`가 반복되던 스트레스 상태 + 장시간 떠 있는 무거운 페이지 +
  `eb39b7b`의 배달현황 다중 페이지(최대 20 goto/회차)로 같은 Chrome 부하 증가.
- **조치 (코드 수정)**
  1. **스케줄러 생존 가드** (`scheduler.py`): `run_loop`이 `run_job()`을 `try/except BaseException`으로
     감싸 `KeyboardInterrupt`/`SystemExit`만 재발생, 그 외엔 흔적 남기고(실패 회차→재시도) 루프 유지.
     `on_job_error` 훅 추가 → `ui._on_worker_job_error`가 `run_errors.log`에 한 줄.
  2. **UI 핸들러 완전화** (`ui.py` `_run_once_background`): `except Exception` 뒤에 `except BaseException`
     절(KeyboardInterrupt/SystemExit 재발생) → CancelledError 등도 로그+`False`. 이중 안전망.
  3. **렌더러 크래시 복구** (`crawler.py`, `platforms/coupang/crawler.py`): `_page_is_crashed`로
     `page.is_crashed()`인 탭을 선택 후보에서 제외 → 새 탭으로 자동 복구. `_is_page_crash`로
     "Page crashed"를 CDP연결오류와 구분해 재시도 가능 RuntimeError로.
  4. **실행 워치독** (`crawler.py`): 배민 async open+collect를 `asyncio.wait_for`(예산
     `page_timeout_seconds/1000*2+30`)로 감싸 크래시 페이지의 무한 대기를 Runtimeable로 끊음.
  5. **무인 정지 가시화 + 자동 재시작** (`ui.py`): `_poll_schedule`(전탭, 스케줄 무관)이 ①중지요청
     없는데 워커 사망 시 `_handle_dead_worker`로 **최대 3회 자동 재시작**(초과 시 '수동 점검' 알림),
     ②N주기(기본 3배, 최소 10분) 무전송 시 1회 침묵 알림. `last_success_by_tab`로 성공 추적.
- **검증**: 전체 테스트 **502 passed**(신규 18: 스케줄러 BaseException 생존/훅/재발생, UI BaseException·
  죽은워커 자동재시작·캡·침묵알림, crawler 크래시 페이지 스킵·워치독 타임아웃 매핑, 쿠팡 크래시 스킵).
  운영 적용 시 §1.A에 따라 **앱 재시작 필요**(scheduler/ui/crawler 모두 in-process).
- **교훈**:
  - **로그가 특정 시각 이후 완전히 끊겼다(에러도 성공도 없다)** = 흔한 "자동 중지"와 다른 신호.
    데몬 워커 스레드 사망/행을 의심하라. `_has_live_workers`가 죽은 워커를 살아있다 보지 않으므로
    그 시점 이후 그 탭은 영영 안 돈다(과거엔 13시간).
  - `except Exception`은 `asyncio.CancelledError` 같은 `BaseException`을 **안 잡는다.** 워커 루프의
    최상단은 `except BaseException`으로 감싸 한 회차 예외가 스레드를 죽이지 못하게 해야 한다.
  - in-process 모듈이 죽으면 앱은 멀쩡해 보여도 그 탭만 조용히 정지한다 — 탭별 "마지막 성공 시각"
    하트비트/무전송 알림이 없으면 장시간 아무도 모른다.

---

## 3. 참고: 진단에 쓴 명령 모음

```bash
# 탭 → 포트/플랫폼/로그 매핑 (ui_settings.json)
python -c "import json;d=json.load(open('runtime/state/ui_settings.json',encoding='utf-8'));\
[print(i+1,t.get('platform_name'),t.get('cdp_url'),t.get('log_dir'),t.get('performance_url')) \
for i,t in enumerate(d['crawlings'])]"

# 특정 포트의 열린 탭 URL
curl -s http://127.0.0.1:9224/json/list \
  | python -c "import sys,json;[print(p.get('type'),'|',p.get('url','')[:90]) for p in json.load(sys.stdin)]"

# 탭별 최신 에러 트레이스백
tail -n 30 logs/run_errors.log     # 크롤링1·2·4
tail -n 30 logs3/run_errors.log    # 크롤링3
tail -n 30 logs5/run_errors.log    # 크롤링5

# 실행 중 앱 프로세스 기동 시각(소스 수정 후 재시작 여부 판별)
powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | \
  Where-Object { \$_.CommandLine -match 'rider_crawl' } | \
  Select-Object ProcessId, CreationDate | Format-List"
```

> 주의: `ui_settings.json`에는 쿠팡 로그인/이메일 자격증명이 평문으로 들어 있다.
> 진단 출력에 자격증명 필드를 찍지 말 것(위 명령은 비민감 필드만 출력).
