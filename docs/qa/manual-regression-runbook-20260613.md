# 수동 회귀 런북 — 2026-06-13 (P0-05)

리팩토링 각 단계 후 **기존 활성 2탭(배민 1탭·쿠팡 1탭)** 의 수집·렌더링·전송 동작이 보존됐는지
운영자가 그대로 따라 검증하는 **수동 회귀·dry-run 런북**이다. 절차는 기존 `run_once` /
`render_current_screen_message` / 메신저 경계를 **있는 그대로 문서화**한 것이며, 제품 코드를
바꾸지 않는다(범위 경계는 §0).

이 문서에는 **실제 secret(토큰/비밀번호/OTP/`chat_id`/전화번호/이메일)이나 마스킹되지 않은
운영 식별자 원문을 적지 않는다.** 모두 placeholder(`<TELEGRAM_BOT_TOKEN>` 등)로만 기록한다.
(요구사항: P0-05 수동 회귀 시나리오, FR-1 절차 문서화, FR-3 dry-run 비교·실발송 없음,
NFR-5 secret 비노출, NFR-18 원본 보존, NFR-20 각 단계 회귀 시나리오 실행 가능,
NFR-24 cutover 동시 실발송 방지, ADD-15 secret 평문 저장 금지)

> **⛔ 실발송 절대 금지(최우선 가드레일).** 이 런북의 모든 dry-run/회귀 절차는 실제
> Telegram/Kakao로 **보내지 않는다**(FR-3, NFR-1 fail-closed). 발송이 본질인 "테스트 전송"
> 절차(③·④)는 운영자가 **지정 테스트 채널에 한해 수동으로만** 1회 수행하도록 격리해 기술한다.
> **자동화/CI에서의 실발송은 금지**다.

---

## 0. 범위 경계 (스코프 크립 방지 — 먼저 읽을 것)

- 이 스토리(1.4)는 **문서·산출물 스토리**다. 산출물은 신규 3파일뿐:
  ① 이 런북, ② `docs/qa/dry-run-baseline-20260613.md`(dry-run 기준선 기록),
  ③ `tests/test_manual_regression_runbook.py`(산출물 회귀 가드).
- **`src/rider_crawl/` 제품 코드는 한 줄도 바꾸지 않는다.** 기존 동작을 "구현"하는 게 아니라
  "문서화"한다. 새 dry-run 플래그·새 비교 도구·새 렌더 경로를 만들지 않는다 — 실제 dry-run
  경로 구현은 **Epic 3(FR-3)** 책임이다(epics.md FR-Coverage-Map).
- 결정적(deterministic) 렌더 형식 회귀는 이미 골든 테스트가 잠갔다(§5 참고). 이 런북의
  라이브 dry-run은 그것을 재작성하지 않고 **"수집→렌더 end-to-end가 여전히 동작하고 메시지가
  well-formed이며 실발송이 없음"** 을 확인하는 운영자 스모크다.

---

## 1. 사전 준비

- [ ] 운영 PC(Windows)에서 KakaoTalk PC 앱 검증이 필요한 경우 앱을 실행·로그인해 둔다(④ 한정).
- [ ] 회귀 대상 활성 탭을 확인한다: **배민 1탭, 쿠팡 1탭**. 활성 탭 설정은
      `runtime/state/ui_settings.json`의 **탭별(per-tab) 설정**에 있다(NFR-18: 원본 미변형).
- [ ] 각 탭의 `CDP 주소`가 `http://127.0.0.1:<포트>` 형태의 로컬 주소이고 탭마다 포트가 다른지
      확인한다(계정 격리).
- [ ] **쿠팡 탭 주의:** 쿠팡 탭에서도 `배민 센터명`(`baemin_center_name`)이 **기대 센터/상점명**
      으로 재사용된다. 비어 있거나 다른 계정값이면 오발송 위험이 있으므로 회귀 전에 확인한다.
      [Source: project-context §88, app.py `source_label`]

---

## 2. dry-run 비발송 실행 경로 정본 (모호함 제거 — 코드 근거)

`run_once(config, *, crawl_snapshot=None, send_message=None) -> RunResult`는 수집→렌더→
(중복 확인)→전송을 묶는 경계다. **`send_enabled=False`이면 sender를 호출하지 않고**
`RunResult(message=…, sent=False, skipped=False, message_hash=…)` 를 반환한다 — 이것이
**dry-run(비발송)의 정본 경로**다. [Source: src/rider_crawl/app.py(43-51), config.py(94)]

비발송 dry-run 경로는 두 가지이며, **활성 탭 회귀에는 (기본) UI 경로를 쓴다.**

### (기본) UI 1회 실행 — 활성 탭 dry-run 정본

활성 배민/쿠팡 탭 설정은 `runtime/state/ui_settings.json`의 per-tab 값이다. UI `1회 실행`은
`settings.to_app_config(...)`로 그 탭 설정을 써서 `run_once`를 호출하므로, **활성 탭과 동일한
입력**으로 dry-run한다. [Source: src/rider_crawl/ui.py(533·613-616)]

- [ ] 앱 실행: `python -m rider_crawl`
- [ ] 회귀할 탭(배민 또는 쿠팡)을 선택한다.
- [ ] **`메시지 전송`(send_enabled) 체크를 해제**한다. ← 실발송 OFF의 핵심.
- [ ] `1회 실행`을 누른다.
- [ ] 미리보기 창에 렌더된 메시지가 뜬다. 실제 발송은 없다(`run_once`가 `sent=False`로 반환).

### (보조) CLI `python -m rider_crawl --once`

`run_cli_once()` → `run_once(AppConfig.from_env())`. 미발송이면 메시지를 **stdout으로 print**
한다(`SEND_ENABLED` 기본 False). [Source: ui.py(984-997), config.py from_env(94)]

> **⚠️ 중요 caveat:** 이 경로는 **`.env` 단일 설정만 읽고 UI 탭 설정을 읽지 않는다.**
> `.env`가 해당 활성 탭과 **동일하게 구성된 경우에만** 활성 탭과 동치다. 따라서 활성 탭 회귀의
> **정본은 UI 경로**이고, CLI는 `.env`가 탭과 동일할 때의 보조 수단으로만 쓴다.

### 캡처(sha256) 산출 보조 스니펫

`run_once`의 `RunResult.message_hash`는 `hashlib.sha256(message.encode("utf-8")).hexdigest()`
정의다. [Source: app.py(41)] UI 미리보기에서 해시를 직접 노출하지 않으면, 미리보기 메시지를
복사해 **동일 정의**로 해시를 산출한다(표준 라이브러리만):

```python
# capture_hash.py — 미리보기 메시지의 sha256(= RunResult.message_hash 동일 정의)
import hashlib, sys
message = sys.stdin.read()            # 미리보기 메시지를 그대로 붙여넣기(stdin)
print(hashlib.sha256(message.encode("utf-8")).hexdigest())
```

산출한 해시·sanitized 스켈레톤·메타데이터는 `docs/qa/dry-run-baseline-20260613.md`에 기록한다.

---

## 3. 4개 수동 회귀 절차 (AC1)

각 절차는 위 **(기본) UI 경로**를 dry-run 기본으로 따른다. 끝에 **"실발송 OFF 확인"** 단계가 있다.

### ① 배민 run 절차

- [ ] 활성 **배민 탭**을 선택한다(`전송 방식`은 그대로 두되 발송은 끈다).
- [ ] `메시지 전송`을 **해제**한 상태로 `1회 실행`.
- [ ] 미리보기에 배민 현재화면(`CurrentScreenSnapshot`) 렌더 메시지가 뜬다.
- [ ] **실발송 OFF 확인:** 발송 토스트/전송 로그가 없고 미리보기만 떴는지 확인한다
      (`run_once` → `sent=False`).

**기대 결과 — 수집 성공 판정:** 예외 없이 1회 실행이 끝나고 미리보기 `message`가 비어 있지
않다(내부적으로 `RunResult`가 반환되고 `message_hash`가 산출됨).

**기대 결과 — 배민 메시지 형태** [Source: message.py `_render_baemin_current_screen_message`(46-67), test_message.py]:

```
[실시간 실적봇]
[<센터명/탭 라벨>]            ← 센터명이 있을 때만 (없으면 줄 생략)
⏰{<월>월<일>일} <HH:MM> 기준
                              ← 빈 줄
오전오후피크 : <…>
오후논피크 : <…>
저녁피크 : <…>
저녁논피크 : <…>
                              ← (선택) 빈 줄 + 거절율
거절율 : <NUM>%               ← reject_rate 있을 때만
```

- 목표/달성률이 있으면 각 피크 줄은 `<건>건/<목표>건[<NUM>%]` 형태, 없으면 `<건>건`.
- `거절율` 줄은 `reject_rate`가 있을 때만 붙는다.

### ② 쿠팡 run 절차

- [ ] 활성 **쿠팡 탭**(`platform_name=coupang`)을 선택한다.
- [ ] **쿠팡 오발송 방지 확인:** 그 탭의 `배민 센터명`(`baemin_center_name`)이 **기대 센터/
      상점명**과 일치하는지 먼저 확인한다(비었거나 다른 값이면 중단). [Source: project-context §88]
- [ ] `메시지 전송`을 **해제**한 상태로 `1회 실행`.
- [ ] 미리보기에 쿠팡 `PerformanceSnapshot` → 피크 대시보드 실적 메시지가 뜬다.
- [ ] **실발송 OFF 확인:** 미리보기만 뜨고 발송이 없는지 확인한다(`sent=False`).

**기대 결과 — 쿠팡 메시지 형태** [Source: message.py `_render_performance_message`(70-98), test_coupang_message.py]:

```
[실시간 실적봇]
[<센터명/탭 라벨>]            ← 센터명이 있을 때만
⏰ <HH:MM> 기준
                              ← 빈 줄
아침 : <완료|<건>건/<건>건> (<시간대>)
점심 피크 : … (<시간대>)
점심 논피크 : … (<시간대>)
저녁 피크 : … (<시간대>)
저녁 논피크 : … (<시간대>)
                              ← 빈 줄
배정 <건>건 / 처리 <건>건
🚨거절률: <NUM>%🚨
🌇수행중인인원 : <N>명         ← current_screen이 있을 때만 (쿠팡 단일 페이지면 생략)
```

- 시간대 표기는 주중/주말(`weekday()>=5`)에 따라 다른 표를 쓴다. [Source: message.py `_peak_times`(27-32)]

### ③ Telegram 테스트 전송 절차 (지정 테스트 채널 한정 수동 1회)

> **이 절차만 실제 발송을 한다.** 운영자가 **지정 테스트 `chat_id`/채널에 한해** 수동으로 1회
> 발송해 라우팅을 확인한다. 운영 채널/CI 자동 발송 금지.

- [ ] 별도 **테스트 탭**(또는 테스트 chat_id로 구성한 탭)을 준비한다. 봇 토큰/`chat_id`/
      `message_thread_id`는 운영자 로컬 설정(`ui_settings.json`/`.env`)에만 두고 **이 문서에는
      placeholder만** 적는다:
      `TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>`, `chat_id=<TELEGRAM_CHAT_ID>`,
      `message_thread_id=<TELEGRAM_MESSAGE_THREAD_ID>`.
- [ ] 테스트 채널에 한해서만 `메시지 전송`을 **켜고** `1회 실행`으로 1회 발송한다.
- [ ] 테스트 채널이 **자기 탭 메시지만** 받았는지 확인한다.
- [ ] **주의(제약):** 텔레그램 수신 폴러는 봇 토큰별 단일 큐다. 같은 봇 토큰을 여러 프로세스에서
      동시에 polling하지 않는다. 활성 텔레그램 탭끼리 같은 `chat_id + topic_id` 조합을 공유하지
      않는다(오발송/라우팅 혼선 방지). [Source: project-context §90·91]
- [ ] **확인 후 즉시 `메시지 전송`을 다시 끈다(실발송 OFF 복귀).**

### ④ Kakao 테스트 전송 절차 (기존 체크리스트 재사용 — 중복 서술 금지)

Kakao 테스트 전송·다중 창 라우팅·안전 실패 검증은 **이미 정본 문서가 있다.** 여기서 다시 서술하지
않고 그대로 따른다(정확한 채팅방명 일치, 모호하면 안전 실패, `logs/kakao_diagnostics.log` 확인):

- **정본:** [`docs/kakao-verification-checklist.md`](../kakao-verification-checklist.md)
  - 1단계: 메시지 생성만 확인(전송 끄기) — `[크롤링N]` 라벨 확인.
  - 2단계: 정상 라우팅 확인(테스트 방에 한해 전송 켜기).
  - 3단계: 안전 실패 확인(모호한 대상 → 임의 전송 안 함).
  - 진단 로그: `logs/kakao_diagnostics.log`.
- **전제:** KakaoTalk PC 앱이 실행·로그인된 **Windows 환경**에서만 수행 가능하다.
- [ ] 위 정본 절차를 수행하고, 메시지 생성만 확인하는 단계에서는 **`메시지 전송`을 끈다(실발송 OFF).**

---

## 4. 캡처·기준선 기록 연계

- [ ] §2의 (기본) UI 경로로 배민 1·쿠팡 1 각각 dry-run을 1회 수행한다(실발송 OFF).
- [ ] 미리보기 메시지로 sha256(§2 스니펫)과 sanitized 스켈레톤을 산출한다.
- [ ] 결과를 [`docs/qa/dry-run-baseline-20260613.md`](dry-run-baseline-20260613.md)의 표에 기록한다.
- [ ] **커밋 전 sanitize:** 스켈레톤은 숫자·센터명·시각 등 변동/식별 값을 마스킹하고, 커밋 전
      `rider_crawl.redaction.redact(text, mask_operational_ids=True)`를 통과시킨다. 실제 토큰/
      전화/이메일/`chat_id`는 **절대 넣지 않는다.** [Source: redaction.py, Story 1.3]

---

## 5. 재실행·비교 방법 (AC3)

기준선 메시지와 신규 메시지를 비교하는 **저장 위치와 비교 방법**이다. 상세 비교 데이터는
[`docs/qa/dry-run-baseline-20260613.md`](dry-run-baseline-20260613.md) §비교 방법과 공유한다.

1. **(a) 동일 입력 sha256 일치 비교:** 동일 snapshot/대상(동일 입력)이라면 렌더 메시지의 sha256
   해시가 일치해야 형태 동일성이 보장된다(`message_hash` = `hashlib.sha256(...).hexdigest()`).
2. **(b) 라이브 dry-run은 형태/라벨 단위 비교:** 실시간 수치는 매 실행 달라지므로, 라이브 dry-run
   회귀는 **숫자를 제외한 형태/라벨 단위**(헤더·라벨 줄·구조)로 판정한다.
3. **(c) 결정적 렌더 형식 회귀는 골든 테스트가 이미 잠금:** `tests/test_message.py`·
   `tests/test_coupang_message.py`(고정 snapshot→정확한 메시지 단언, Story 1.2 pytest 기준선
   포함). **이 골든 테스트를 중복 재작성하지 않는다.** 라이브 dry-run은 그것과 구분되는 end-to-end
   운영자 스모크다.
4. **(d) cutover 규칙(FR-3, NFR-24):** dry-run은 **실발송이 없고**, old path(기존 UI/`run_once`)와
   new path(Epic 3 신규 경로)의 동시 실제 전송은 막는다. 비교에서 차이가 발생해도 **자동으로
   활성화하지 않으며, 운영자 승인 후에만** 대상 전송을 활성화한다.

---

## 6. 마무리 점검

- [ ] 모든 dry-run 절차에서 `메시지 전송`이 꺼져 있었고 실발송이 없었음을 확인한다(③ 테스트 전송
      제외, 그것도 테스트 채널 한정·확인 후 OFF 복귀).
- [ ] 이 런북·기준선 파일에 실제 secret/운영 식별자 원문이 없는지 확인한다(placeholder만).
- [ ] `runtime/`·`logs/`·`ui_settings.json` 원본이 변형되지 않았는지 확인한다(NFR-18). dry-run
      (`send_enabled=False`)은 `last_message` 해시를 쓰지 않으므로 중복 상태를 오염시키지 않는다.
      [Source: app.py(46-51)]

---

## 참고 (References)

- [Source: src/rider_crawl/app.py(15-51)] — `RunResult`, `run_once` 분기(`send_enabled=False`=비발송), `message_hash`, `source_label`.
- [Source: src/rider_crawl/ui.py(533·613-616·984-997)] — UI `1회 실행`(per-tab dry-run 정본), CLI `--once`/`run_cli_once`(`.env` caveat).
- [Source: src/rider_crawl/config.py(94)] — `send_enabled` 기본 False.
- [Source: src/rider_crawl/message.py(35-98)] — 배민/쿠팡 렌더 형태 정본.
- [Source: tests/test_message.py, tests/test_coupang_message.py] — 결정적 렌더 형식 골든 테스트(재작성 금지).
- [Source: docs/kakao-verification-checklist.md] — Kakao 테스트 전송 정본(재사용 대상).
- [Source: docs/qa/baseline-record-20260613.md, docs/qa/pytest-baseline-20260613.md] — `docs/qa/` 헤더·보안 주의 컨벤션.
- [Source: project-context §88·90·91] — 쿠팡 기대 센터명 재사용, 텔레그램 토큰/`chat_id+topic` 정책.
