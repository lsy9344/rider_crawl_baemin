# 재사용 경계와 금지 행위 거버넌스 — 2026-06-13 (P0)

리팩토링 전 과정에서 **어떤 기존 코드가 "보존·wrapping 재사용" 대상인지**와 **절대 하면 안 되는
금지 행위 7가지**를, 이후 **모든 에픽(2~5)의 구현 에이전트**가 한 번에 참조할 수 있도록 한곳에
모아 못박는 **권위 거버넌스 문서**다. 이 문서는 새 규칙을 발명하지 않는다 — 이미 흩어져 있는 정본
(`project-context.md`, `implementation-contract.md`, `operations-security-test-contract.md`,
`architecture.md`)을 **취합·교차참조**하고, 권위 순서와 예외 절차를 고정한다(wheel 재발명 금지).

이 문서에는 **실제 secret(토큰/비밀번호/OTP/`chat_id`/전화번호/이메일)이나 마스킹되지 않은
운영 식별자 원문을 적지 않는다.** 예시가 필요하면 placeholder(`<TELEGRAM_BOT_TOKEN>`)·가짜값
(`010-0000-0000`, `rider@example.com`)만 쓴다.
(요구사항 추적: FR-2 기존 자산 재사용 보장, ADD-15 금지 행위, NFR-5 secret 비노출,
NFR-18 원본 보존, NFR-20 각 단계 회귀 시나리오 실행 가능, P0 기준선 가드레일)

> **⛔ 이 스토리는 경계 "문서화"이지 wrapping "구현"이 아니다(최우선 범위 가드레일).**
> 산출물은 신규 2파일(이 거버넌스 문서 + `tests/test_reuse_boundaries_doc.py` 회귀 가드)뿐이며,
> **제품 코드(`src/rider_crawl/`)는 한 줄도 바꾸지 않는다.** 아래 표의 "Required change"는
> **목표**이지 본 스토리의 작업이 아니다 — 실제 정규화 Snapshot wrapping·중앙 webhook 전환·
> token 격리·circuit breaker **구현**은 각 책임 에픽(Epic 2~5)에서 한다. 본 스토리에서 코드
> 이동·리네임·registry 변경을 하지 않는다.

---

## 0. 범위 경계 (스코프 크립 방지 — 먼저 읽을 것)

- **문서·산출물 스토리다(1.1/1.2/1.4 계열, 1.3 같은 코드 스토리 아님).** 이 문서는 "무엇을
  보존·재사용하고, 무엇을 절대 하면 안 되는가"의 **권위 기준**이다.
- **경계를 "문서화"하는 것이지 wrapping을 "구현"하는 게 아니다.** 코드 이동/리네임/registry
  변경을 본 스토리에서 하지 않는다. [Source: implementation-contract.md Reuse And Replace,
  FR-Coverage-Map(155)]
- 결정적 렌더 형식·저장 JSON 호환 등 공개 동작은 이미 골든 테스트·호환 테스트가 잠갔다(§3 참고).
  본 문서는 그것을 재작성하지 않고 **연결(교차참조)** 한다.

---

## 1. 권위 계층 (AC3 — 구현 에이전트가 따를 우선순위)

구현 에이전트는 **코드를 구현하기 전에 다음 순서의 상위 권위를 먼저 읽고 따른다.** 충돌 시 위가
이긴다.

1. **최우선: `_bmad-output/project-context.md`의 56개 규칙.** 에이전트는 코드 구현 전 이 파일을
   먼저 읽고, **일반 관례보다 우선**하며, **확신이 없으면 더 제한적인 선택**을 한다. 새 패턴/예외가
   생기면 이 파일을 함께 갱신한다. [Source: project-context.md(101-106)]
2. **본 거버넌스 문서** — 재사용 경계·공개 동작·금지 행위·예외 절차의 취합 정본.
3. **`architecture.md`** — 컴포넌트/데이터 경계와 ADR(아키텍처 결정 기록).
4. **spec 계약** — `implementation-contract.md`(Reuse And Replace)·
   `operations-security-test-contract.md`(Forbidden Behaviors).

> 위 4개는 서로 보강 관계다. 본 문서가 spec 계약을 옮길 때 원문과 어긋나면 spec 계약/코드를 정본으로
> 보고 본 문서를 고친다(project-context §69 문서 정합 원칙).

---

## 2. 보존·재사용 경계 표 (AC1 — 7개 자산)

다음 **7개 자산은 "보존·wrapping 재사용 대상"** 이다. 각 자산의 **현재 코드 위치**, **허용되는
변경(wrapping/추가)**, **금지되는 변경**, 그리고 목표인 **Required change(구현 책임 에픽)** 를 함께
명시한다. "Required change" 열은 `implementation-contract.md`의 Reuse And Replace 표를 정본으로
옮긴 것이며, **그 변경의 실제 구현은 해당 에픽 책임이다(본 스토리는 문서화만, wrapping 구현 아님).**

| # | Keep (보존·wrapping) | 현재 코드 위치 | 허용되는 변경 (wrapping/추가) | 금지되는 변경 | Required change (구현 책임 에픽) |
| --- | --- | --- | --- | --- | --- |
| ① | 배민 parser | `src/rider_crawl/parser.py` (레거시, import/테스트 호환 유지) | 정규화 Snapshot으로 **wrapping**, fixture 테스트 추가 | parser 내부 로직 재작성·골든 출력 변경·import 경로 파괴 | 정규화 Snapshot으로 wrapping + fixture 테스트 유지 (Epic 3) |
| ① | 배민 crawler | `src/rider_crawl/crawler.py` (레거시, import/테스트 호환 유지) | 주입 가능한 adapter로 감싸기 | 쿠팡 전용 로직을 여기에 섞기 | `run_once` 분해 시 crawler 주입 경계 유지 (Epic 3) |
| ② | 쿠팡 peak-dashboard parser | `src/rider_crawl/platforms/coupang/` | store/center 검증 추가, parser version 기록 | 배민 legacy에 쿠팡 로직 섞기 | store/center 검증 + parser version 기록 (Epic 4) |
| ③ | message renderer | `src/rider_crawl/message.py` `render_current_screen_message` | `template_version`·tenant 템플릿·렌더 결과 저장 추가 | 골든 테스트가 잠근 메시지 형태 변경 | `template_version`·tenant 템플릿·렌더 결과 저장 (Epic 3) |
| ④ | Telegram/Kakao sender | `src/rider_crawl/sender.py` + `rider_crawl.messengers` registry | `Messenger.send_text` 경계 유지하며 전송 방식 확장 | `Messenger.send_text(config, message)` 경계 파괴 | Telegram 중앙 webhook/sendMessage 전환·per-Agent getUpdates 제거 (Epic 3/5); Kakao 직렬 queue+lock (Epic 4) |
| ⑤ | 쿠팡 Gmail 2FA 로직 | 쿠팡 플랫폼 경로 (`google-api-python-client` 읽기 전용 흐름) | customer/mailbox token 격리, mailbox lock 추가 | scope 확대·token 공유·secret 평문화 | customer/mailbox token 격리·mailbox lock·제한 scope (Epic 4) |
| ⑥ | `run_once` 실행 경계 | `src/rider_crawl/app.py` `run_once` | 주입 가능한 crawler/sender 하위 adapter로 연결 | 이 경계를 우회하는 새 실행 경로 추가 | CrawlJob→Snapshot→Message→DispatchJob→DeliveryLog로 분해 (Epic 3) |
| ⑦ | platforms/messengers registry | `rider_crawl.platforms`, `rider_crawl.messengers` | 기존 계약 구현으로 신규 플랫폼/메신저 등록 | `PerformancePlatform`/`Messenger.send_text` 계약 변경 | 신규 플랫폼/메신저는 기존 계약 구현으로 추가 (Epic 2~4) |

[Source: implementation-contract.md Reuse And Replace(5-12), project-context §42-45, epics.md FR-2(24)]

---

## 3. 보존해야 할 공개 동작 4종 (AC1 #2)

다음 **공개 동작 4종**을 **의도 없이 바꾸는 변경은 "실패(regression)"로 취급**한다. 이 동작들은
이미 테스트가 잠갔으므로 **재작성하지 말고 연결(교차참조)** 만 한다.

- **(a) 렌더링 결과** — 골든 테스트 `tests/test_message.py`·`tests/test_coupang_message.py`가
  고정 snapshot → 정확한 메시지로 잠근다. **재작성 금지, 연결만.**
- **(b) 저장 JSON 호환** — `ui_settings.json`은 `ensure_ascii=False, indent=2` 스타일과 legacy
  카카오 설정을 유지한다. 설정 마이그레이션·기본값 변경은 기존 호환 테스트를 추가한다.
- **(c) 탭 9개 로딩** — `UiSettingsStore.load_all(max_tabs=9)` / `runtime/state/ui_settings.json`
  구조를 깨지 않는다.
- **(d) 쿠팡 플랫폼 추론** — `tests/test_architecture.py` / `rider_crawl.platforms` registry가
  잠근 추론 경로를 유지한다.

> **위 4종 중 어느 하나라도 의도 없이 바뀌면 그 작업은 regression(실패)다.** 의도된 변경이라면
> 반드시 §1 권위·§5 예외 절차를 거친다. [Source: project-context §47·54·59·68, epics.md AC(342)]

---

## 4. 금지 행위 7가지 (AC2 — ADD-15 Forbidden Behaviors)

다음 **7개 금지 행위**는 사유(코드/운영 근거)와 함께 **절대 하지 않는다.** 각 행위에는 **올바른
대안**과 그 대안의 **구현 책임 에픽**을 연결한다(대안 구현은 본 스토리가 아니라 해당 에픽 책임).

| # | 금지 행위 | 사유 (코드/운영 근거) | 올바른 대안 (구현 책임 에픽) |
| --- | --- | --- | --- |
| ① | **탭 9→100 확장**으로 스케일링 | UI는 최대 9탭 모델 전제(§47). 100+는 중앙 서버/Agent 모델이 필요하다 | Tenant/Target ID 모델 + 중앙 scheduler/queue (Epic 2/Epic 5) |
| ② | **배민 휴대폰 인증** 자동화/우회 | 배민은 휴대폰 인증 때문에 완전 자동 로그인을 전제하지 않는다. 사용자 조치 필요 상태 감지가 우선(§87) | AUTH_REQUIRED 감지 + 사람 개입형 재인증 (Epic 4) |
| ③ | 같은 Windows session에서 **Kakao 2건 병렬** 전송 | Kakao=PC 앱 UI 자동화라 전역 lock과 정확한 채팅방명 검증이 필요(§49·94) | KakaoSendJob FIFO queue + sender lock (Epic 4) |
| ④ | 고객 간 **Gmail token 공유** | 계정/메일함 격리 위반 시 다른 계정 실적 오발송·누출 위험 | customer/mailbox token 격리 + mailbox lock (Epic 4) |
| ⑤ | **secret(token/password/OTP)** 을 로그·DB text 필드·스크린샷·config 파일·에러 메시지에 평문 저장 | NFR-5·ADD-15 — secret은 로컬 파일·`*_ref`만(§81·89). 인증번호/OAuth token/비밀번호를 로그·예외에 남기지 않는다 | Story 1.3 `redaction.redact()` 재사용 + `*_ref`만 저장(secret 값 분리) (Epic 2) |
| ⑥ | 클라우드가 로컬 Chrome **CDP 포트**에 직접 접속 | CDP 포트와 Chrome 프로필은 계정 격리 장치(§93). 클라우드 직접 접속은 격리를 약화한다 | outbound-only Agent + token-auth job API (Epic 4) |
| ⑦ | backoff/**circuit breaker** 없이 parser 실패를 빠르게 재시도 | 사이트 부하·차단·job storm 위험 | jitter·exponential backoff·platform circuit breaker (Epic 5) |

[Source: operations-security-test-contract.md Forbidden Behaviors(87-94), epics.md ADD-15(143),
project-context §49·81·87·89·93]

---

## 5. 예외/위반 처리 절차 (AC3 #7)

- **임의 변경 금지.** §2 경계나 §4 금지 규칙을 바꿔야 할 **정당한 사유**가 생기면, 코드에서 임의로
  바꾸지 말고 먼저 **`architecture.md`의 ADR(아키텍처 결정 기록)** 또는 **`project-context.md`의
  예외 항목**에 변경과 **근거**를 남긴다. [Source: project-context.md(106), epics.md AC(351)]
- **위반은 실패로 보고.** 보존 동작(§3 공개 동작 4종)을 **의도 없이 깨거나** §4 금지 행위를
  도입한 작업을 발견하면, 그 작업을 **실패로 보고**하고 되돌린다. "그럴듯하지만 경계를 흔드는"
  변경을 통과시키지 않는다.
- 확신이 없으면 §1 원칙대로 **더 제한적인 선택**을 한다.

---

## 부록 A. References (정본 출처)

- [Source: _bmad-output/project-context.md(101-106·42-49·54·59·68·81·87·89·93)] — 권위 계층·
  보존 자산 코드 경계·공개 동작 호환·secret/식별자 정책·CDP 격리·금지 행위 근거.
- [Source: implementation-contract.md Reuse And Replace(3-20)] — Keep/Required change 정본.
- [Source: operations-security-test-contract.md Forbidden Behaviors(87-94)] — 금지 행위 7항목 정본.
- [Source: epics.md FR-2(24), ADD-15(143), FR-Coverage-Map(154-159·168·186), AC(331-351)] —
  재사용 보장·금지 행위·대안 구현 책임 에픽(secret_ref Epic 2, Kakao queue Epic 4,
  circuit breaker Epic 5).
- [Source: architecture.md Source Tree(458-464)] — `docs/qa/` 위치·컴포넌트/데이터 경계.
- [Source: tests/test_message.py, tests/test_coupang_message.py, tests/test_architecture.py] —
  공개 동작을 잠근 골든/registry 테스트(재작성 금지, 연결만).
- [Source: src/rider_crawl/redaction.py, 1-3 스토리] — 금지 행위 ⑤ 대안(`redact()` 재사용).
- [Source: tests/test_reuse_boundaries_doc.py] — 본 문서 산출물 회귀 가드(존재·필수 섹션·
  secret 패턴 비노출, 순수 파일 읽기).
