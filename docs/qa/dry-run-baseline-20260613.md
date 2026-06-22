# dry-run 기준선 기록 — 2026-06-13 (P0-05 / FR-3)

기존 활성 2탭(**배민 1탭·쿠팡 1탭**)의 **수집·렌더링 dry-run을 실발송 없이 1회** 캡처한
기준선 기록이다. 향후 리팩토링 각 단계 후 동일 절차로 재캡처해 형태 회귀를 비교하는 기준점이다.
캡처·비교 **절차**는 [`docs/qa/manual-regression-runbook-20260613.md`](manual-regression-runbook-20260613.md)에 있다.

이 문서에는 **실제 secret(토큰/비밀번호/OTP/`chat_id`/전화번호/이메일)이나 마스킹되지 않은
운영 식별자 원문을 적지 않는다.** 렌더 메시지 본문은 **sanitized 스켈레톤**(라벨/구조 보존,
숫자·센터명·시각 등 변동/식별 값 마스킹)으로만 기록하고, 커밋 전
`rider_crawl.redaction.redact(text, mask_operational_ids=True)`를 통과시킨다.
(요구사항: FR-3 dry-run 비교·실발송 없음, FR-1 기준선 저장, NFR-5 secret 비노출,
NFR-18 원본 보존, NFR-24 cutover, ADD-15 secret 평문 저장 금지)

> **sha256는 secret이 아니다.** 메시지 본문에서 산출한 불투명 해시라 원문 복원이 불가능하므로
> 그대로 커밋한다. 마스킹 대상은 **본문 스켈레톤**뿐이다.

---

## 1. 캡처 메타데이터

| 항목 | 값 |
| --- | --- |
| 문서 작성 일시 (KST) | 2026-06-13 KST |
| 기준선 commit (작성 시점 HEAD) | `a820a85045ecbc0ec1355b30a0d6705167de9e6b` |
| dry-run 실행 경로(정본) | UI `1회 실행`, `메시지 전송`(send_enabled) **해제** → `run_once`가 `sent=False` 반환 |
| 실발송 여부 | **없음** (FR-3: `send_enabled=False` 경로, sender 미호출) |
| sha256 정의 | `hashlib.sha256(message.encode("utf-8")).hexdigest()` (= `RunResult.message_hash`) |

### 라이브 캡처 가능 여부에 대한 주의 (실측 vs placeholder)

- **실측 캡처는 로그인된 Chrome(CDP)으로 활성 탭 dry-run을 수행할 수 있는 운영 환경에서만** 가능하다.
  본 문서 작성 환경(헤드리스 dev)에서는 라이브 수집을 수행하지 않았으므로, 아래 표의 **렌더 메시지
  sha256**과 **캡처 일시**는 `<운영자 캡처 필요>` placeholder로 둔다. **절차 자체는 완비**돼 있으며
  (런북 §2~§4), 운영자가 그 절차로 채운다("문서화" AC 충족).
- **sanitized 스켈레톤(형태/라벨)** 은 코드(`message.py`)와 골든 테스트로 결정되는 **기대 형태**이므로
  미리 기록한다. 이것이 라이브 dry-run의 **형태 비교 기준선**(숫자 제외)이다.
- 산출물 회귀 가드 테스트(`tests/test_manual_regression_runbook.py`)는 **형식·secret 비노출만**
  강제하며, 실측값(sha256) 유무로 깨지지 않게 설계됐다.

---

## 2. 대상별 dry-run 기준선

### 2-1. 배민 1탭

| 항목 | 값 |
| --- | --- |
| 플랫폼 | 배민 (`platform_name=baemin`) |
| 탭 라벨 | `<배민 탭 라벨>` (센터명/탭명, 커밋 전 마스킹) |
| 캡처 일시 (KST) | `<운영자 캡처 필요>` |
| 렌더 메시지 sha256 (64 hex) | `<운영자 캡처 필요>` |
| dry-run 실행 방식 | UI `1회 실행` (`메시지 전송` 해제) |
| 실발송 없음 | ✅ (`run_once` → `sent=False`) |

**sanitized 메시지 스켈레톤 (배민 — 형태 비교 기준선):**

```
[실시간 실적봇]
[<센터명 마스킹>]
⏰{<월>월<일>일} <HH:MM> 기준

오전오후피크 : <NUM>건/<NUM>건[<NUM>%]
오후논피크 : <NUM>건/<NUM>건[<NUM>%]
저녁피크 : <NUM>건/<NUM>건[<NUM>%]
저녁논피크 : <NUM>건/<NUM>건[<NUM>%]

거절율 : <NUM>%
```

- `[<센터명 마스킹>]` 줄은 센터명이 있을 때만(없으면 생략). 목표/달성률이 없으면 각 피크 줄은
  `<NUM>건` 형태. `거절율` 줄은 `reject_rate`가 있을 때만.
  [Source: message.py(46-67), test_message.py]

### 2-2. 쿠팡 1탭

| 항목 | 값 |
| --- | --- |
| 플랫폼 | 쿠팡 (`platform_name=coupang`) |
| 탭 라벨 | `<쿠팡 탭 라벨>` (기대 센터/상점명 = `baemin_center_name`, 커밋 전 마스킹) |
| 캡처 일시 (KST) | `<운영자 캡처 필요>` |
| 렌더 메시지 sha256 (64 hex) | `<운영자 캡처 필요>` |
| dry-run 실행 방식 | UI `1회 실행` (`메시지 전송` 해제) |
| 실발송 없음 | ✅ (`run_once` → `sent=False`) |

**sanitized 메시지 스켈레톤 (쿠팡 — 형태 비교 기준선):**

```
[실시간 실적봇]
[<센터명 마스킹>]
⏰ <HH:MM> 기준

아침 : <상태> (<시간대>)
점심 피크 : <상태> (<시간대>)
점심 논피크 : <상태> (<시간대>)
저녁 피크 : <상태> (<시간대>)
저녁 논피크 : <상태> (<시간대>)

배정 <NUM>건 / 처리 <NUM>건
🚨거절률: <NUM>%🚨
🌇수행중인인원 : <N>명
```

- `🌇수행중인인원` 줄은 `current_screen`이 있을 때만(쿠팡 단일 페이지면 생략). 시간대 표기는
  주중/주말에 따라 다른 표를 쓴다. [Source: message.py(70-98), test_coupang_message.py]
- **오발송 방지 주의:** 쿠팡 탭의 라벨은 `baemin_center_name`(기대 센터/상점명)을 재사용한다.
  비었거나 다른 계정값이면 회귀 전에 중단한다. [Source: project-context §88]

> 위 두 스켈레톤은 커밋 전 `redact(text, mask_operational_ids=True)`를 통과해도 변화가 없음을
> 확인했다(이미 placeholder만 포함 → 누출 없음).

---

## 3. 비교 방법 (AC3 — 런북 §5와 공유)

향후 단계에서 기준선 메시지와 신규 메시지를 비교하는 정본 방법이다.

| # | 비교 방법 | 적용 대상 | 판정 |
| --- | --- | --- | --- |
| (a) | **동일 입력 sha256 일치** | 동일 snapshot/대상(동일 입력) | 해시 일치 → 형태 동일성 보장 |
| (b) | **형태/라벨 단위 비교(숫자 제외)** | 라이브 dry-run(실시간 수치 변동) | 헤더·라벨·구조 일치 → 회귀 없음 |
| (c) | **골든 테스트 연결(중복 작성 금지)** | 결정적 렌더 형식 회귀 | `tests/test_message.py`·`tests/test_coupang_message.py`가 이미 잠금 |
| (d) | **cutover 규칙(FR-3/NFR-24)** | old/new path 전환 | dry-run 실발송 없음 + **승인 후에만** 활성화, 차이 시 자동 활성화 금지 |

- **(c)** 결정적(고정 snapshot→정확한 메시지) 형식 회귀는 골든 테스트가 이미 잠그고 있으므로 본
  기준선은 그것을 **재작성하지 않는다.** 라이브 dry-run은 "수집→렌더 end-to-end가 여전히 동작하고
  메시지가 well-formed이며 실발송이 없음"을 확인하는 운영자 스모크로 구분한다.
- **(d)** old path(기존 UI/`run_once`)와 new path(Epic 3 신규 경로)의 **동시 실제 전송을 막는**
  cutover의 전제는 "dry-run은 실발송 없음 + 운영자 승인 후에만 활성화"다(FR-3).

---

## 4. 산출물 목록 (git 추적 대상)

| 경로 | 종류 |
| --- | --- |
| `docs/qa/manual-regression-runbook-20260613.md` | 수동 회귀·dry-run 런북(절차 정본) |
| `docs/qa/dry-run-baseline-20260613.md` | dry-run 기준선 기록(이 문서) |
| `tests/test_manual_regression_runbook.py` | 산출물 회귀 가드 테스트 |

## 5. 후속 호환

- 본 문서는 Story 1.1(`baseline-record-20260613.md`)·1.2(`pytest-baseline-20260613.md`)의
  `docs/qa/` 헤더·보안 주의·메타데이터 표 컨벤션을 따른다.
- 실제 dry-run 경로(FR-3) 구현과 자동 비교 하네스(`tests/regression/`)는 **Epic 3/5** 책임이다.
  본 스토리는 사람-가독 런북·기준선 + 산출물 가드로 한정한다. [Source: architecture.md Source Tree, epics.md FR-Coverage-Map]
