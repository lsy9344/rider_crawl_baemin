# Chrome profile memory scaling diagnosis - 2026-07-01

## 검토 결론

문서의 큰 방향은 맞다. 현재 제품 코드에서 가장 효율적인 메모리 개선 방향은 탭 수를 줄이거나 인증 전 사전 탐지를 억지로 만드는 것이 아니라, **디스크 Chrome profile 보존**과 **live Chrome process 보존**을 분리하는 것이다. 다만 "디스크 profile이 있으니 live Chrome은 항상 닫아도 된다"는 뜻은 아니다. 특히 배민처럼 live Chrome을 닫으면 다음 수집 때 사람이 2차 인증을 다시 해야 하는 계정은 live process 자체가 운영 세션의 일부다. 따라서 RAM을 쓰는 Chrome process는 **플랫폼/계정별 lease 정책**으로 관리하고, 재인증 없이 재생성 가능한 경우에만 작업 후 닫는다.

다만 이 문서를 그대로 "한 번에 적용"하면 위험한 부분이 있다. 현재 crawler들은 CDP로 붙은 Chrome에 대해 `browser.close()`를 호출하지 않는 계약이 테스트로 잠겨 있다. `BrowserProfileManager.release()`와 `cleanup_idle_profiles()`는 registry에 들어온 tracked process handle을 닫을 수 있지만, 이 정리 경로가 모든 browser job에 공통 적용되지는 않는다. 따라서 작업 완료 후 Chrome을 닫는 구현은 crawler 내부가 아니라 Agent가 시작하거나 명시적으로 lease로 채택한 **OS Chrome process**를 닫는 공통 계층에서 해야 한다.

적용 판정은 다음과 같다.

- 바로 운영 설정으로 적용 가능: `RIDER_AGENT_MAX_JOBS=1` 유지, `RIDER_AGENT_MAX_PROFILES` 축소, `RIDER_AGENT_PROFILE_IDLE_TTL_SECONDS` 축소. orphan Chrome 수동 종료는 운영자가 해당 target이 live-session-hold/manual auth가 아니고 다음 수집에 재인증이 필요 없음을 확인한 경우에만 한다.
- 구조 변경으로 적용 가능: global browser inventory, browser lease/slot, heartbeat capacity 확장, scheduler/claim throttle.
- 그대로 적용하면 위험: crawler CDP 경로에서 `browser.close()` 추가, 탭을 1개로 단순 축소, manual auth lease 표식 없이 parent 없는 Chrome 자동 종료, account 단위 profile key 선변경.

## 선결 검증 — Phase 2 착수 전 반드시 (2026-07-02 보강)

이 문서 전체 전략은 **"tracked Chrome을 닫으면 그 프로필의 RAM이 실제로 회수된다"** 는 가정 하나에 걸려 있다. 이 가정이 깨지면 lease manager를 만들어도 메모리가 줄지 않는다. 가장 큰 실패 모드는 "lease를 회수했는데 RAM이 줄지 않는 상태"다. Phase 2 코드에 들어가기 전에 아래 두 가지를 실 Agent PC에서 먼저 측정/확정한다.

### 선결 검증 A — Chrome root 종료가 자식 프로세스 트리와 private memory를 회수하는가

현재 `BrowserProfileManager.release()`/`cleanup_idle_profiles()`/`close_all()`은 registry에 추적된 **핸들 하나**에만 `terminate()`를 호출한다(`src/rider_agent/browser_profile.py:696`). 자식 Chrome(renderer/GPU/utility) 트리를 명시적으로 정리하지 않는다. 관측값은 root 7개에 직계 자식 77개(root당 ~11개)다.

- Chromium은 자식이 browser(root) 프로세스 사망을 감지해 자멸하는 것이 정상이지만, psutil `terminate()`는 Windows에서 `TerminateProcess`(하드 킬)라 root의 graceful shutdown에 의존하지 않는다. 자식 정리가 보장되지 않을 수 있다.
- timeout 경로도 오해하면 안 된다. 자식 tree-kill(`taskkill /F /T`)은 **crawl 자식 Python** 트리를 죽일 뿐, Chrome은 부모(Agent)가 실행하므로 그 트리 밖이다. 실제 Chrome 종료자는 `release()`이고, 그 역시 핸들 하나만 닫는다.

측정: 추적된 root 하나를 terminate한 직후 (1) 해당 프로필의 chrome.exe 자식 수, (2) private/working-set memory가 실제로 사라지는지 확인한다. 실패하면 Phase 2는 lease manager만으로 부족하고, Windows **job object** 또는 process-group 기반 트리 종료가 필요하다.

2026-07-02 이 PC에서 사용자가 종료 허용한 `제이앤에이치플러스 의정부남부` target(`6b8fd18e-1799-4d39-9c13-f49794a6b5cf`)으로 선결 검증 A를 수행했다. root PID `14796`에 psutil `terminate()`를 호출했고 root는 5초 timeout 안에 종료됐다. 종료 전 해당 profile은 Chrome process 11개(root 1, typed child 10, child 중 CDP port flag 4), private 795,308,032 bytes, RSS 817,623,040 bytes였다. 종료 1초/3초/8초 후 같은 profile의 Chrome process는 0개, private/RSS는 0 bytes였다. 즉 이 PC의 이 sample에서는 **정확한 browser root handle을 terminate하면 child process와 RAM이 함께 회수된다**. 단, 선결 검증 B처럼 root가 아닌 child handle을 잡으면 이 결과가 보장되지 않으므로 root 식별 가드는 여전히 필수다.

### 선결 검증 B — 종료 대상이 진짜 browser root인가 (child 오종료 방지)

`find_existing_chrome_debug_endpoint()`는 `runtime/agent-browser-profiles` 매칭 프로필 + `--remote-debugging-port` 를 가진 **첫 매칭 Chrome process**를 반환한다(`src/rider_crawl/browser_launcher.py:247`). CDP probe는 특정 PID가 아니라 포트(`127.0.0.1:<port>`)를 확인한다. 현재 코드에는 `--type=` 유무로 root/child를 구분하는 가드가 없다.

2026-07-02 이 PC에서 읽기 전용 psutil inventory를 수행한 결과, Agent profile Chrome 76개 중 `--type=` 없는 browser-root 후보 7개는 모두 CDP port flag를 갖고 있었고, `--type=` 있는 child 69개 중 31개도 `--remote-debugging-port` flag를 갖고 있었다. 즉 "포트 플래그가 있으면 root일 것"이라는 가정은 이 환경에서 성립하지 않는다. psutil 순회에서 child가 먼저 걸리면 **root가 아닌 child 핸들이 채택**되어 `release()`가 child만 죽일 수 있다.

확정: Phase 1 inventory는 root 식별을 **"프로필 매칭 + `--remote-debugging-port` 보유 + `--type=` 없음"** 으로 정의하고, lease 종료 대상도 이 root로 한정한다.

## 핵심 정정

### 1. "수동 로그인/2FA 때만 Chrome을 연다"는 현재 코드와 맞지 않는다

현재 scheduled crawl은 브라우저를 열기 전에 인증 필요 여부를 확정하지 않는다. `src/rider_agent/workers/crawl_worker.py`의 기본 경로는 `_prepare_process_boundary_job()`에서 `BrowserProfileManager.ensure_profile()`로 target profile/CDP Chrome을 준비하고, child process가 실제 수집을 수행한 뒤 crawler 내부 신호를 통해 `AUTH_REQUIRED`를 만든다.

`src/rider_agent/job_loop.py`의 preflight는 payload TTL 같은 stale job 차단용이다. 인증 상태를 브라우저 없이 확인하는 기능이 아니다.

`AUTH_CHECK`도 초경량 server-side cookie probe가 아니다. `src/rider_agent/auth/baemin_auth.py::default_login_probe()`는 기존 `reuse.crawl_snapshot()`을 호출하므로 현재 구현에서는 profile/CDP Chrome을 짧게 열어 확인한다.

따라서 목표 문장은 다음이 맞다.

- 일반 scheduled crawl이라도 플랫폼/계정별 live-session 정책을 따른다. 재인증 없이 profile에서 Chrome을 다시 열 수 있는 target은 browser lease를 짧게 잡고 수집 뒤 live Chrome을 닫는다. 닫으면 사람이 2FA를 다시 해야 하는 target은 closeable-after-job이 아니라 live-session-hold로 유지한다.
- 인증이 감지되면 쿠팡은 자동 email 2FA가 가능한 계정이면 `AUTH_COUPANG_2FA`를 먼저 수행한다.
- 자동 2FA 설정이 없거나 실패/차단/captcha/메일 인증 문제일 때만 `OPEN_AUTH_BROWSER`로 operator-facing Chrome을 길게 둔다.
- 배민은 자동 2FA가 없고, 일부 운영 계정은 live Chrome을 닫으면 다음 수집 때 사람이 다시 2차 인증을 해야 한다. 이런 target은 메모리 절감을 위해 자동 종료 대상으로 삼으면 안 되며, 별도 live-session-hold/manual-hold lease로 분류한다.

### 2. 현재 idle TTL 설정만으로 모든 Chrome이 닫히지는 않는다

`RIDER_AGENT_PROFILE_IDLE_TTL_SECONDS`는 `CrawlWorker._cleanup_profiles()`가 호출될 때만 적용된다. CRAWL job 완료 뒤에는 cleanup이 돌지만, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA`는 `worker_composition._with_profile_assignment()`로 profile을 확보한 뒤 동일한 cleanup을 호출하지 않는다.

즉 TTL을 낮추는 것은 scheduled crawl RAM에는 즉시 효과가 있지만, auth-only job이 연 Chrome은 다음 crawl cleanup, max profile eviction, 또는 agent shutdown까지 남을 수 있다. 또 registry 밖 live CDP Chrome은 같은 target이 다시 선택되어 채택되기 전까지 cleanup 대상이 아니다. 구조 변경 시 lease cleanup은 CRAWL뿐 아니라 브라우저를 여는 모든 job type에 공통 적용해야 한다.

### 3. 현재 capacity는 job 수 기반이다

서버 scheduler와 claim API는 이미 capacity를 보긴 한다.

- heartbeat `metrics.max_in_flight`가 `agents.capacity_json.max_in_flight`로 저장된다.
- scheduler는 online Agent의 `max_in_flight`, capability, active crawl job 수, job type별 in-flight를 이용해 enqueue를 throttle한다.
- `/v1/jobs/claim`은 해당 Agent의 남은 `max_in_flight`만큼 claim 수를 clamp한다.

하지만 이 capacity는 여전히 **job count** 기준이다. live Chrome 수, registry 밖 orphan Chrome 수, RAM pressure, manual auth browser slot은 반영하지 않는다. `max_jobs`를 늘리면 queue 처리량은 올라갈 수 있지만 Chrome 메모리 제어 없이 위험해진다.

### 4. DB `browser_profiles`와 heartbeat `browser_profiles`는 다른 의미다

DB의 `browser_profiles` 테이블은 Agent affinity와 배정 정보에 가깝다. Agent heartbeat의 `capacity_json.browser_profiles`는 `BrowserProfileManager` registry 안의 runtime projection이다. registry 밖 orphan live Chrome은 이 값에 보이지 않는다.

문서와 구현에서는 이 둘을 섞으면 안 된다. 메모리 제어에는 OS process/CDP inventory가 필요하다.

## 현재 PC 관찰

2026-07-01 로컬 PC에서 Agent 전용 `runtime/agent-browser-profiles` Chrome을 확인했다.

- RAM: 약 13.2GB 중 사용률 82.9%.
- Agent용 Chrome root: 7개.
- root 직계 Chrome 프로세스 합계: 77개.
- Chrome private memory 합계: 약 5.0GB.
- Chrome working set 합계: 약 5.6GB.
- 현재 살아 있는 `python.exe` parent를 가진 root는 1개뿐이고, 나머지 6개 root는 parent process가 사라진 live CDP Chrome이다.
- CDP target 기준 각 profile은 대체로 2개 page target을 갖는다.
  - 배민: `delivery/report` + `delivery/history`.
  - 쿠팡 정상: `peak-dashboard` + `rider-performance`.
  - 일부 쿠팡: `xauth` 로그인/인증 page 2개 또는 `rider-performance` 중복 2개.
- profile 디스크는 8개 leaf profile 합계 약 24GB이며, 상위 5개가 각각 4.4~4.9GB 수준이다.

2026-07-02 추가 읽기 전용 inventory:

- Agent profile Chrome process: 76개.
- `--type=` 없는 browser-root 후보: 7개.
- browser-root 후보 7개 모두 `--remote-debugging-port` 보유.
- `--type=` 있는 child process: 69개.
- `--type=` 있는 child 중 `--remote-debugging-port` 보유: 31개.
- profile 수: 7개.
- Chrome private memory 합계: 5,124,263,936 bytes.
- Chrome working set/RSS 합계: 5,713,829,888 bytes.
- 프로필별 process 수는 10~12개, profile별 root 후보는 각 1개.
- 이 관찰로 선결 검증 B의 root 식별 가드(`--type=` 없음)가 필수임이 확인됐다.
- 선결 검증 A는 사용자가 종료 허용한 `제이앤에이치플러스 의정부남부` target root PID `14796`으로 수행했다. root 종료 전 해당 profile은 process 11개/private 795,308,032 bytes/RSS 817,623,040 bytes였고, 종료 1초 후 process 0개/private 0/RSS 0으로 회수됐다.

이 형태는 Chromium의 multi-process 구조상 탭/iframe/worker/GPU/utility process가 분리되는 것까지는 정상이다. 문제는 고객 profile 여러 개가 동시에 live로 남는 운영 모델이다.

## 코드상 실제 흐름

### BrowserProfileManager의 현재 역할

`src/rider_agent/browser_profile.py`의 `BrowserProfileManager`는 target 단위 profile directory와 local CDP port를 배정하고, 같은 target의 live CDP Chrome이 있으면 재사용한다. 같은 profile을 쓰지만 CDP가 없는 stale Chrome은 닫을 수 있다.

중요한 점은 `release()`와 `cleanup_idle_profiles()` 자체는 tracked assignment의 process handle을 닫는다는 것이다. 현재 문제는 이 기능이 global orphan inventory가 아니고, 모든 job 완료 경로에서 호출되는 공통 lease 후처리도 아니라는 데 있다.

한계는 다음이다.

- `ensure_profile()`은 요청된 target profile만 본다. `runtime/agent-browser-profiles` 전체의 live Chrome을 scan하는 global janitor가 아니다.
- `cleanup_idle_profiles()`와 `close_all()`은 registry에 들어온 assignment만 대상으로 한다.
- registry 밖 live CDP Chrome은 해당 target이 다시 선택되어 채택되기 전까지 heartbeat에도 보이지 않고 cleanup 대상도 아니다.
- `max_profiles`는 registry/reservation 수 제한이다. OS에 남아 있는 orphan live Chrome 수 제한이 아니다.

### CRAWL job

1. Agent loop가 server preflight를 통과한 job을 실행한다.
2. `CrawlWorker`가 `_prepare_process_boundary_job()`에서 profile/CDP를 준비한다.
3. child process가 `reuse.crawl_snapshot()`을 호출한다.
4. 배민/쿠팡 crawler가 실제 페이지의 DOM/URL/context를 보고 성공, parser failure, `BrowserActionRequiredError`, `CdpUnavailableError` 등을 만든다.
5. `CrawlWorker`가 결과를 job result로 정규화하고 profile diagnostic을 registry에 기록한다.
6. 정상 완료 후에는 `cleanup_idle_profiles(max_idle_seconds=...)`가 호출된다. 이 호출은 TTL이 지난 tracked profile만 닫는다.
7. timeout 때는 child process tree를 강제 종료한 뒤 해당 target `release()`가 호출된다. timeout 경로는 비교적 강하지만, success/failure 경로는 live Chrome을 즉시 닫는 계약이 아니다.

### AUTH job

`AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA`는 `worker_composition`에서 profile assignment를 붙인 뒤 auth executor로 흐른다. 이 경로는 현재 CRAWL worker의 idle cleanup을 공유하지 않는다.

쿠팡 자동 2FA 계약은 다음과 같이 이미 분리되어 있다.

- scheduled crawl payload에 자동 2FA ref가 있으면 crawler 내부 inline recovery를 한 번 시도할 수 있다.
- 실패 후 account `auth_state`가 `AUTH_REQUIRED`가 되면 scheduler는 normal crawl이 아니라 `AUTH_COUPANG_2FA` job을 만든다.
- admin의 `start_auth`도 쿠팡 자동 2FA 정보가 완비되어 있으면 `OPEN_AUTH_BROWSER`가 아니라 `AUTH_COUPANG_2FA`를 enqueue한다.
- account/mailbox 중복 OTP는 `MailboxLockRegistry`, active/pending auth job gate, repository duplicate check, account cooldown, payload TTL로 줄이고 있다.

### 쿠팡 로그인/email 2FA 보호 계약 요약

이 메모리 개선 작업은 쿠팡 로그인/2FA selector, timeout, routing을 부수 수정 대상으로 삼으면 안 된다. 현재 보호 계약은 다음 흐름으로 고정되어 있다.

1. `AUTH_COUPANG_2FA`와 쿠팡 crawl session recovery는 모두 `recover_coupang_session_with_email_2fa()`를 사용한다.
2. 필요하면 primary login을 먼저 수행한다.
3. email 인증 방식을 선택한다.
4. send-code button을 누른다. 이 버튼은 보이지만 아직 actionable하지 않은 순간이 있으므로 interaction timeout을 짧게 줄이면 안 된다.
5. IMAP으로 OTP를 읽는다. OTP, Coupang password, email app password, plaintext secret은 로그/결과/heartbeat에 남기지 않는다.
6. OTP를 입력하고 제출한 뒤 target page를 다시 연다.
7. 2FA 화면에 visible "아이디" 또는 login 텍스트가 있어도 2FA signal이 있으면 primary login 화면으로 오분류하지 않는다.
8. hidden duplicate button이 있을 수 있으므로 visible role/text target 선호 정책을 유지한다.

관련 보호 runtime file은 `src/rider_crawl/auth/coupang_email_2fa.py`, `src/rider_agent/auth/coupang_gmail_2fa.py`, `src/rider_agent/worker_composition.py`, `src/rider_crawl/platforms/coupang/crawler.py`, `src/rider_server/services/admin_action_service.py`, `src/rider_server/scheduler/service.py`, `src/rider_server/queue/postgres_queue.py`이다. browser lease를 붙이기 위해 이 파일을 수정해야 한다면 AGENTS.md 절차대로 caller/payload trace, focused regression, 보호 테스트 세트, 실제 headed browser 검증이 필요하다.

## 탭 2개의 의미

탭 2개는 기본적으로 누수가 아니라 수집 계약에서 나온다.

쿠팡:

- `peak-dashboard`는 권위 페이지다. 센터 검증과 핵심 처리/배정 물량은 fail-closed로 읽는다.
- `rider-performance`는 수행중 인원 보조 페이지다. 실패해도 peak 수집은 계속하고 수행중 인원만 생략한다.
- target tab이 없을 때 같은 logged-in context에서 temporary tab을 열어 읽고 `finally`에서 닫는 경로가 있다.
- 중복 `rider-performance` 또는 `peak-dashboard` 탭은 `_select_page_by_url()`이 거부하고 operator 조치를 요구한다.

배민:

- `delivery/report`는 달성현황/주간 배달 현황 수집용이다.
- `delivery/history`는 배달현황 테이블과 취소/수행중 보조 수집용이다.
- 로그인된 `deliverycenter.baemin.com` page가 하나도 없으면 새 로그인 탭을 열지 않고 `BrowserActionRequiredError`로 멈춘다.

따라서 단순히 "탭을 1개로 줄인다"는 수정은 우선순위가 낮고 위험하다. 줄이려면 페이지별 수집을 한 탭의 순차 navigation으로 바꾸고, 쿠팡 peak authority, rider best-effort, center tab selection, stale rider temp-tab retry, 배민 report/history 계약을 모두 회귀 테스트해야 한다.

## 공식 문서 리서치 반영

공식 문서 기준으로도 현재 방향은 "crawler가 붙은 CDP browser를 닫는다"가 아니라 "Agent가 소유권을 가진 live process를 lease로 관리한다"가 맞다.

- Playwright `connect_over_cdp()`는 이미 떠 있는 Chromium browser에 Chrome DevTools Protocol로 연결한다. 이 방식은 Playwright protocol 연결보다 기능 충실도가 낮고, 기본 context는 `browser.contexts`로 접근한다. 현재 crawler의 CDP 경로가 외부 Chrome에 붙는 모델이라는 점과 맞다.
- Playwright `launch_persistent_context(user_data_dir)`는 persistent storage를 쓰는 browser context를 시작하고, 그 context를 닫으면 browser도 닫힌다. 다만 현재 Agent 기본 구조는 persistent context 직접 소유가 아니라 profile directory + local CDP Chrome + CDP attach 모델이다. 이 API로 전체 구조를 바꾸는 것은 별도 설계 대상이다.
- Chromium은 multi-process architecture를 사용한다. 따라서 profile 하나가 여러 Chrome child process를 갖는 것 자체는 정상이고, 메모리 문제의 핵심은 "프로세스 수가 많다"보다 "여러 profile root가 동시에 live로 오래 남는다"이다.
- Chrome Memory Saver는 inactive tab을 discard하는 사용자 설정 성격의 기능이다. 운영 PC 설정으로 도움은 될 수 있지만, 제품 코드의 deterministic lease/slot 제어를 대체하지 않는다.
- Chrome DevTools Protocol에는 target 생성/닫기와 `Browser.close`가 있다. 그러나 crawler CDP session에서 이를 직접 호출하면 현재 "사용자가 켜 둔 CDP Chrome은 닫지 않는다"는 테스트 계약과 운영 기대를 깨므로, 닫기는 lease 소유 계층에서만 해야 한다.

## 원인

### 1. live Chrome process에 대한 1급 resource model이 없다

현재 profile manager는 profile/port registry다. live Chrome process 전체를 fleet resource로 보지 않는다. Agent 재시작, parent process 종료, auth-only job 완료 뒤 남은 Chrome처럼 registry 밖 또는 cleanup 경로 밖에 있는 process가 RAM을 계속 사용한다.

### 2. live Chrome 보유 시간이 길고 cleanup 호출 범위가 좁다

기본 idle TTL은 3600초이고 max profiles는 20이다. `max_jobs=1`이어도 서로 다른 고객을 순차 수집하면 한 시간 동안 여러 profile이 live로 남을 수 있다. 또 auth job 경로는 현재 crawl cleanup을 공유하지 않는다.

### 3. scheduler/claim capacity가 browser 자원을 모른다

서버는 job 수와 capability는 본다. 그러나 browser slot, manual auth slot, orphan count, RAM pressure는 모른다.

### 4. 인증 탐지는 브라우저 내부 신호다

쿠팡/배민 모두 인증 상태를 신뢰할 수 있게 판단하는 신호는 실제 logged-in page/2FA/login page의 DOM/URL/context 안에 있다. 브라우저 없는 사전 탐지를 만들려면 플랫폼별 session API 또는 cookie validation contract가 새로 필요하다.

### 5. target 단위 profile은 안전하지만 확장성에는 불리하다

payload의 `browser_profile_ref`는 `profile:{target_id}`이다. 같은 platform account에 여러 target이 묶여도 profile은 target 단위로 열릴 수 있다. auth job dedup은 account 단위까지 올라가 있지만 crawl browser profile/lease는 target 단위다.

다만 account 단위 profile 공유는 센터 선택/화면 상태가 profile 안에 남는 플랫폼 특성 때문에 바로 바꾸면 위험하다.

## 권장 적용 순서

이 순서는 성능 개선 로드맵이 아니라 **현재 잘 동작하는 크롤링/인증 흐름을 유지하기 위한 안전 조건**이다. Phase 1의 관측과 root 식별 없이 Phase 2의 종료 로직을 먼저 넣으면 child process를 root로 오인하거나 수동 인증 창을 닫을 수 있다. Phase 2의 lease semantics 없이 Phase 3 claim gating을 먼저 넣으면 browser job 전체 또는 `KAKAO_SEND` 같은 비브라우저 job까지 멈출 수 있다. 따라서 운영 중인 crawler/2FA/process 상태를 보존하려면 아래 순서를 지킨다.

- Phase 0/1은 읽기 전용 관측과 운영 설정 축소다. 현재 Chrome 상태를 강제로 바꾸지 않는다.
- Phase 2는 root 식별, manual-hold/live-session-hold, closeable-after-job 구분이 검증된 뒤에만 켠다.
- Phase 3은 `browser_slots` heartbeat 계약과 browser-opening job 분류가 서버/Agent에서 일치한 뒤에만 켠다.
- Phase 4/5는 앞 단계가 안정화된 뒤 검토한다. account 단위 profile key나 탭 수 축소를 먼저 적용하지 않는다.

### Phase 0 - 운영 즉시 조치

- `RIDER_AGENT_MAX_JOBS=1`은 유지한다.
- `RIDER_AGENT_MAX_PROFILES`를 2~4로 낮춘다.
- `RIDER_AGENT_PROFILE_IDLE_TTL_SECONDS`를 60~180초로 낮춘다.
- 현재 orphan live Chrome은 운영자가 profile/화면/진행 중 인증 여부뿐 아니라 "닫아도 다음 수집이 사람 2FA 없이 재개되는 target인지"를 확인한 뒤에만 종료한다. 배민처럼 닫으면 사람이 다시 2차 인증해야 하는 창은 orphan이어도 유지한다.
- 수동 인증 진행 중인 창은 닫지 않는다.
- 관리자 Agents 화면의 `browser_profiles`만 믿지 말고, OS process/CDP port 기준 live Chrome 수도 같이 본다.

주의: 이 조치는 RAM을 즉시 낮추지만 완전한 해결은 아니다. TTL은 crawl cleanup 경로에만 확실히 적용되고, registry 밖 orphan live CDP Chrome은 자동으로 사라지지 않는다.

### Phase 1 - Browser process inventory와 안전한 관측

먼저 닫기보다 **보는 계층**을 만든다.

필수 정책:

- Agent 시작 시와 heartbeat 직전에 `runtime/agent-browser-profiles` 아래 Chrome process를 scan한다.
- registry 안 profile 수와 OS live Chrome root 수를 분리해 센다. **root 식별은 "프로필 매칭 + `--remote-debugging-port` 보유 + `--type=` 없음"** 으로 하고, `--type=` 있는 자식은 root 카운트/lease 대상에서 제외한다(선결 검증 B).
- parent 없는 live CDP Chrome 수를 `orphan_count`로 센다. 단, `orphan_count`는 자동 종료 수가 아니라 관측 지표다. live-session-hold target은 parent가 없어도 정리 대상이 아닐 수 있다.
- heartbeat에는 count와 slot 상태만 보낸다. raw path, URL, page title, email, secret, raw cmdline은 보내지 않는다.
- 새 값은 기존 `metrics` 버킷에 넣지 않는다. `metrics`는 전용 allowlist 없이 통과되기 때문이다(`src/rider_server/services/agent_registry.py:176`). `browser_slots`를 **top-level 필드**로 만들고 `browser_profiles`처럼 **전용 sanitizer/allowlist**(`src/rider_server/services/agent_registry.py:280`)를 새로 둔다.
- 단, 이 top-level 추가는 단순 provider 추가가 아니라 heartbeat API 계약 변경이다. 현재 Agent payload builder는 5개 표면(`metrics`/`capabilities`/`active_jobs`/`kakao_status`/`browser_profiles`)만 보낸다(`src/rider_agent/heartbeat.py:157-196`). 서버 `HeartbeatRequest`와 `HeartbeatInput`도 `browser_slots`를 받지 않는다(`src/rider_server/api/agents.py:67-79`, `src/rider_server/services/agent_registry.py:82-88`). 따라서 Phase 1 구현은 Agent payload, API request model, registry input, sanitizer, 저장된 `capacity_json`, heartbeat 테스트를 함께 확장해야 한다.

권장 heartbeat aggregate field:

```text
browser_slots.max
browser_slots.used
browser_slots.available
browser_slots.manual_auth_used
browser_slots.orphan_count
browser_slots.registry_profiles
browser_slots.ram_used_percent
```

현재 `agent_registry._sanitize_mapping()`은 nested mapping 자체는 처리할 수 있지만, `metrics` 서브객체에는 전용 allowlist가 없어 URL/파일경로/창 제목처럼 `redact()`가 걸러내지 못하는 값이 그대로 저장될 수 있다(`src/rider_server/services/agent_registry.py:176-206`). 따라서 `browser_slots`는 `metrics`가 아니라 `browser_profiles` sanitizer(`:280`)와 동형의 **top-level dedicated allowlist**로 정제한다. raw profile path, page URL, page title, email, login id, secret ref, raw cmdline 값은 capacity payload에 넣지 않는다. RAM pressure도 별도 `memory` free-form top-level로 두지 말고, 우선은 allowlist가 명확한 `browser_slots.ram_used_percent` 같은 숫자 필드로 제한한다. 나중에 `memory` namespace를 따로 만들 경우에도 dedicated allowlist/sanitizer를 별도로 둔다.

자동 종료는 이 단계에서 바로 켜지 않는다. 현재는 manual auth lease marker와 live-session-hold marker가 없어 parent 없는 Chrome이 정말 닫아도 되는 창인지 코드만으로 확정하기 어렵다.

### Phase 2 - BrowserLeaseManager 도입

`BrowserProfileManager`는 profile/port 준비와 target 검증 역할을 유지하고, live Chrome process는 별도 lease manager가 관리한다.

현재 `BrowserProfileManager.release()`가 가진 registry bookkeeping(assignment/port/profile-key 회수)은 재사용할 수 있다. 다만 process 종료 구현은 tracked handle 하나만 `terminate()`하는 형태라(`src/rider_agent/browser_profile.py:696`) 그대로 확장하면 안 된다. 새 lease manager의 목적은 registry 회수와 live Chrome root/tree 종료를 분리하고, 모든 browser job의 공통 after-hook에서 안전하게 호출할 수 있는 process ownership 정책을 추가하는 것이다.

필수 정책:

- `max_live_browsers`: RAM 기준으로 산정한다. 13GB PC는 1~2개부터 시작한다.
- browser를 여는 job type은 모두 slot을 획득해야 시작한다: `CRAWL_BAEMIN`, `CRAWL_COUPANG`, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA`. `AUTH_CHECK`는 preflight 대상은 아니지만 `default_login_probe`가 `crawl_snapshot`으로 실제 브라우저를 열므로 반드시 포함한다(`src/rider_agent/auth/baemin_auth.py:262`).
- lease type을 나눈다. **closeable-after-job**: 재인증 없이 disk profile에서 Chrome을 다시 열 수 있음이 확인된 target의 scheduled crawl, `AUTH_CHECK`, 자동 `AUTH_COUPANG_2FA` — terminal result 후 tracked live Chrome lease를 회수하고 disk profile만 유지한다. **live-session-hold**: 닫으면 사람이 2FA/로그인을 다시 해야 하는 target, 특히 배민 운영 profile — 수집에 쓰는 Chrome이라도 자동 종료하지 않는다. **manual-hold**: `OPEN_AUTH_BROWSER` — 사람이 인증 중이므로 TTL 또는 명시 완료/취소 전까지 닫지 않는다. 최대 1개, 긴 TTL, 명시 완료/취소/만료 정책이 필요하다.
- parent 없는 Chrome(orphan)도 운영상 "쓸데없는 창"이라고 단정하지 않는다. 현재 열려 있는 배민 report/history나 쿠팡 dashboard/rider 창은 다음 수집에서 같은 profile/CDP로 재채택될 수 있고, 외관상 실제 수집/운영 창일 수 있다. Phase 2 자동 정리는 closeable로 분류된 target에 한정하고, live-session-hold target은 orphan이어도 관측/재채택 대상으로 둔다.
- lease 종료는 tracked 핸들 하나가 아니라 **Chrome root의 프로세스 트리 전체**를 회수해야 한다(선결 검증 A). root 종료가 자식을 회수하지 못하면 job object/process-group 종료로 승격한다.
- timeout cleanup은 child process뿐 아니라 browser lease도 회수한다.
- lease manager는 Agent가 시작했거나 명시적으로 채택한 Agent profile root 아래 Chrome만 닫는다. 채택 시 root 식별(선결 검증 B)을 적용해 child를 root로 오인하지 않는다.
- crawler 내부 CDP connection 정책은 바꾸지 않는다. `fetch_page_html_via_cdp()`에 `browser.close()`를 넣지 않는다.

이 단계가 메모리 문제의 핵심 수정이다. 구현 위치는 `src/rider_agent/browser_profile.py` 또는 새 `browser_lease.py`가 중심이고, 모든 browser job에 공통 후처리를 넣으려면 `src/rider_agent/worker_composition.py`도 변경 대상이 된다. `worker_composition.py`는 보호 runtime file이므로 AGENTS.md 절차가 필요하다.

### Phase 3 - Scheduler/claim capacity를 browser slot 기반으로 확장

서버는 `max_in_flight` 외에 browser slot을 capacity 입력으로 받아야 한다.

정책:

- **enqueue 제한보다 claim 제한이 더 중요하다.** 현재 scheduler capacity는 online agent들의 `max_in_flight`를 **합산**하고(`src/rider_server/scheduler/postgres_repository.py:137`) claim clamp도 agent별 job 수만 본다(`src/rider_server/api/jobs.py:358`). 그러나 Chrome RAM은 **PC(=agent)별 자원**이므로 fleet 합산이 아니라 **per-agent browser slot**으로 판단해야 한다.
- claim 제한은 "전체 claim 0"이 아니라 **browser-opening job에만 적용**해야 한다. 현재 `/v1/jobs/claim`은 `_claim_max_jobs()`가 숫자만 줄인 뒤 원래 capability 목록을 queue backend에 넘긴다(`src/rider_server/api/jobs.py:431-443`). backend는 `Job.type.in_(capabilities)`로 claim한다(`src/rider_server/queue/postgres_queue.py:375-385`). 따라서 browser slot이 0이라고 `max_jobs=0`만 반환하면 `KAKAO_SEND` 같은 비브라우저 job까지 같이 멈춘다. 구현은 browser-opening capability만 제거/분리하거나, backend claim이 browser/non-browser slot을 구분하도록 확장해야 한다.
- slot-gating 대상은 실제로 브라우저를 여는 모든 job이다: `CRAWL_BAEMIN`, `CRAWL_COUPANG`, `AUTH_CHECK`, `OPEN_AUTH_BROWSER`, `AUTH_COUPANG_2FA`. `AUTH_CHECK`는 preflight 대상이 아니어도 브라우저를 열므로 포함한다.
- 이 job type 집합은 Phase 3 구현에서 하드코딩을 흩뿌리지 말고 `opens_browser(job_type)` 같은 단일 helper/상수로 잠근다. 서버 claim, scheduler capacity, Agent lease acquisition이 같은 분류를 써야 한다.
- claim이 per-agent slot을 판단하려면 agent가 heartbeat로 보고한 free browser slot(Phase 1의 `browser_slots`)을 claim clamp가 입력으로 읽어야 한다.
- `AUTH_COUPANG_2FA`는 normal crawl보다 우선순위를 높일 수 있지만, mailbox/account lock과 payload TTL은 유지한다.
- manual auth browser는 normal crawl slot과 분리하거나 전체 Agent에서 최대 1개만 허용한다.
- 동일 platform account의 due target은 한 tick에서 coalesce하거나 순차화한다.
- overdue backlog는 target별 최신 due만 유지한다. 이미 active/pending이면 추가 enqueue하지 않는다.

변경 후보:

- `src/rider_agent/heartbeat.py`: browser slot metrics 제공.
- `src/rider_server/api/agents.py`: `HeartbeatRequest`에 `browser_slots` 입력 표면 추가.
- `src/rider_server/services/agent_registry.py`: heartbeat sanitizer/allowlist 확장.
- `src/rider_server/scheduler/policy.py`, `src/rider_server/scheduler/postgres_repository.py`, `src/rider_server/scheduler/service.py`: capacity model 확장.
- `src/rider_server/api/jobs.py`: claim clamp에 browser slot 반영.
- `src/rider_server/queue/postgres_queue.py`: priority/run_after/status 정책을 바꿀 경우 영향.
- `tests/agent/test_heartbeat.py`, `tests/server/test_agents_api.py`: heartbeat payload/API 계약 확장 회귀.

`scheduler/service.py`와 `postgres_queue.py`는 보호 runtime file이다. 추가로 `src/rider_server/scheduler/policy.py`(auth 라우팅 결정 `decide_auth_gate`)와 `src/rider_server/scheduler/postgres_repository.py`(계정 단위 OTP 중복방지 `enqueue_auth_coupang_2fa_job`)는 AGENTS 보호 목록에는 없지만 인증 라우팅과 OTP 중복방지 계약을 담고 있다. Phase 3에서 이 둘을 건드리면 보호 테스트가 자동으로 잡지 못하므로 **사실상 보호 대상으로 취급**하고 `tests/server/test_scheduler_tick.py` 등 scheduler 회귀를 반드시 함께 돌린다.

### Phase 4 - Profile key 재검토

많은 고객에서 profile을 빠르게 교체하려면 target 단위 profile이 항상 최선은 아니다. 그러나 account 단위 profile 공유는 우선순위가 낮다.

권장 순서:

1. target 단위 disk profile은 유지한다.
2. live Chrome slot만 공유/제한한다.
3. 같은 platform account의 due target을 scheduler에서 coalesce/순차화한다.
4. 그 뒤 account 단위 profile key를 검토한다.

account 단위 profile로 바꾸려면 center selection이 완전히 idempotent인지, 배민/쿠팡의 화면 상태가 target 간에 오염되지 않는지 실브라우저로 확인해야 한다.

### Phase 5 - 탭 canonical cleanup

현재 2탭은 허용하되 profile당 canonical tab set을 강제하는 것이 현실적이다.

- 쿠팡 live profile: 최대 `peak-dashboard` 1개 + `rider-performance` 1개 + recovery 중 login/xauth 1개.
- 배민 live profile: 최대 `delivery/report` 1개 + `delivery/history` 1개 + center-change/login flow 중 필요한 page.
- temporary tab은 읽은 뒤 반드시 닫는다. 쿠팡 `_open_target_in_new_tab()`은 이미 `finally close`가 있다.
- duplicate exact/path tabs는 자동으로 임의 선택하지 않는다. 지금처럼 거부하거나, 별도 cleanup job에서 안전한 canonical close 규칙을 만든다.

이 단계는 메모리 개선의 핵심이 아니라 안정화 후 품질 개선이다.

## 적용 금지/주의 항목

- CDP crawler 내부에서 `browser.close()`를 호출하지 않는다. 기존 테스트가 "사용자가 켜 둔 CDP Chrome은 닫지 않는다"는 계약을 잠근다.
- 쿠팡 login/email 2FA selector, timeout, routing은 lease 구조 변경의 부수 수정으로 건드리지 않는다.
- `src/rider_crawl/auth/coupang_email_2fa.py`의 send-code interaction timeout, 2FA/login 분류, visible target 선호 정책은 별도 회귀/실브라우저 검증 없이 바꾸지 않는다.
- parent process가 죽은 Chrome을 manual auth lease 표식 없이 자동 종료하지 않는다.
- `browser_profile_ref`를 `platform_account_id` 단위로 바로 변경하지 않는다.
- 탭 2개를 1개로 줄이는 것을 메모리 대책 1순위로 잡지 않는다.

## 검증 계획

보호 대상 Coupang 로그인/2FA 계약을 건드리지 않는 변경이면 먼저 profile/lease 계층 테스트부터 추가한다.

필수 신규/확장 테스트:

- Agent startup/global inventory가 registry 밖 orphan live Chrome을 발견한다.
- heartbeat API가 `browser_slots` top-level 입력을 받고 Agent payload builder가 해당 필드를 전송한다(`tests/agent/test_heartbeat.py`, `tests/server/test_agents_api.py` 확장).
- heartbeat가 browser slot/orphan/RAM count를 저장하되 path/URL/secret 계열 key를 버린다.
- `BrowserProfileManager.release()`와 `cleanup_idle_profiles()`가 tracked process handle을 실제 종료한다.
- closeable-after-job target의 scheduled crawl success/failure 후 live Chrome process가 닫히고 disk profile은 남는다.
- live-session-hold target, 특히 닫으면 수동 2FA가 필요한 배민 profile은 scheduled crawl success/failure 후에도 자동 종료하지 않는다.
- auth-only job, 특히 `AUTH_COUPANG_2FA`와 `AUTH_CHECK`, 완료 후 live Chrome cleanup이 실행된다.
- `OPEN_AUTH_BROWSER` manual auth lease는 janitor가 닫지 않는다.
- timeout cleanup이 child process와 browser lease를 모두 회수한다.
- per-agent browser slot이 0이면 browser-opening job(`AUTH_CHECK` 포함)만 claim 대상에서 제외된다(fleet 합산이 아니라 agent 단위). 같은 claim 요청에 `KAKAO_SEND` 같은 비브라우저 capability가 있으면 계속 claim 가능해야 한다.
- root 식별이 fake cmdline에서 `--type=` 없는 root만 채택/종료하고, 포트 플래그를 가진 `--type=` child는 종료하지 않는다(선결 검증 B).
- `browser_slots`는 top-level allowlist로 정제되어 path/URL/title/raw cmdline 계열 key를 버린다(`metrics` 통과 경로로 새지 않는다). `ram_used_percent` 같은 RAM 값은 숫자 allowlist 필드로만 저장된다.
- (실 PC 측정, unit test 아님) 추적 root terminate 직후 자식 chrome.exe 수와 private/working-set memory가 실제로 회수되는지 확인한다(선결 검증 A).
- `AUTH_REQUIRED + Coupang auto 2FA complete`는 manual browser가 아니라 `AUTH_COUPANG_2FA`를 먼저 만든다.
- 같은 account의 여러 target이 동시에 due여도 auth job은 1개만 생긴다.
- CDP crawler 테스트에서 `browser.close()`가 호출되지 않는 기존 회귀가 유지된다.
- duplicate tab cleanup 정책을 넣는다면 쿠팡/배민 기존 tab-selection 테스트를 확장한다.

보호 runtime file을 수정하는 경우 AGENTS.md 계약에 따라 caller trace, focused regression test, 보호 테스트 세트, 실제 headed browser 검증이 필요하다. 특히 selector, wait, login, 2FA, CDP, agent-routing 변경은 실제 headed browser flow 검증 없이 완료로 주장하면 안 된다.

이번 문서 검토에서는 보호 runtime file을 수정하지 않는다. 구조 변경 구현 때 보호 파일을 건드리면 최소한 다음 보호 테스트 세트를 실행해야 한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coupang_email_2fa.py tests\agent\test_coupang_gmail_2fa.py tests\agent\test_job_loop.py tests\test_coupang_crawler.py tests\server\test_admin_actions.py tests\server\test_scheduler_tick.py tests\server\test_queue_backend.py tests\server\test_queue_recovery.py -q
```

## 참고 자료

- Chromium multi-process architecture: https://www.chromium.org/developers/design-documents/multi-process-architecture/
- Playwright BrowserType, `connect_over_cdp()` and `launch_persistent_context()`: https://playwright.dev/python/docs/api/class-browsertype
- Playwright BrowserContext pages/lifecycle: https://playwright.dev/python/docs/api/class-browsercontext
- Chrome Memory Saver: https://developer.chrome.com/blog/memory-and-energy-saver-mode
- Chrome Memory Saver help: https://support.google.com/chrome/answer/12929150
- Chrome DevTools Protocol Target domain: https://chromedevtools.github.io/devtools-protocol/tot/Target/
- Chrome DevTools Protocol Browser domain: https://chromedevtools.github.io/devtools-protocol/tot/Browser/
