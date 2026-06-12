# Input Reconciliation: docs/refactoring/research.md

## 요약 판정

`docs/refactoring/research.md`의 핵심 방향은 PRD와 addendum에 대부분 반영되어 있다. 특히 "클라우드 중앙 서버 + Windows Local Agent/작업 노드", "수집/렌더링/전송 분리", "ID 기반 운영", "배민은 사람 개입형 인증", "쿠팡은 Gmail token 분리", "KakaoTalk은 직렬 sender queue"라는 큰 줄기는 보존되어 있다.

다만 research가 담고 있던 일부 전략적/질적 의도는 PRD의 FR 구조로 들어가며 약해졌다. 주요 누락은 채널 온보딩 UX, 구독/결제 실패 lifecycle, 규모별 운영 전략, 법무/약관 리스크, 플랫폼 인증 실패 taxonomy다.

## 비교 범위

- Source input: `docs/refactoring/research.md`
- Compared against:
  - `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/prd.md`
  - `_bmad-output/planning-artifacts/prds/prd-rider_result_mornitoring-2026-06-12/addendum.md`
- Product code edit: 없음

## 잘 반영된 핵심 의도

### 1. 최종 구조 방향

Research의 추천 방향인 "클라우드 중앙 서버 + 여러 작업 노드/로컬 에이전트 + 메신저 전송 큐"는 PRD 비전, FR-12~FR-16, FR-27~FR-28, addendum의 Technical Direction에 반영되어 있다.

PRD는 특히 중앙 서버가 운영 상태를 통제하고, Windows Local Agent가 Chrome/KakaoTalk 같은 로컬 제약 작업을 담당한다는 책임 분리를 유지한다.

### 2. 현재 구조의 핵심 문제

Research가 지적한 탭 기반 고객 모델, `crawlingN` 상태 꼬임, 탭별 thread, Chrome profile/CDP 운영 부담, 평문 secret, 중앙 관측성 부족, KakaoTalk UI 자동화 위험은 PRD의 문제 정의, 제품 원칙, FR-4~FR-16, NFR에 반영되어 있다.

### 3. 수집과 전송 분리

Research의 `CrawlJob -> SnapshotStore -> MessageRenderJob -> DispatchJob -> DeliveryLog` 흐름은 PRD의 FR-7~FR-11, addendum의 data flow 항목에 반영되어 있다.

Fan-out, 채널별 실패 분리, idempotency, DeliveryLog도 요구사항으로 살아 있다.

### 4. 플랫폼별 인증 전략

배민을 완전 자동 로그인 대상으로 보지 않고 인증 필요 감지와 사람 개입형 재인증으로 다루는 방향은 FR-17~FR-18에 반영되어 있다.

쿠팡 Gmail 2FA는 고객/메일함/token 분리, mailbox 충돌 방지, 민감값 redaction 요구로 FR-19와 NFR에 반영되어 있다.

### 5. Telegram/KakaoTalk 분리 전략

Telegram은 중앙 서버 중심으로 관리하고, KakaoTalk은 Windows Local Agent의 직렬 queue 및 정확한 채팅방 검증을 요구하는 구조가 FR-15, FR-24~FR-26에 반영되어 있다.

KakaoTalk을 무제한/강한 SLA 채널로 팔지 말아야 한다는 상품 리스크도 FR-25와 Open Questions에 남아 있다.

### 6. MVP 경계

Research의 "고성능 서버부터 사지 말고 현재 PC를 Agent #1로 쓰며 구조를 먼저 잡는다"는 방향은 FR-28, MVP In Scope/Out of Scope, 출시와 마이그레이션 요구에 반영되어 있다.

Kubernetes, 1000개 이상 자동화, 완전 셀프서비스, 결제 자동화, 고성능 서버 즉시 구매는 MVP 밖으로 잘 분리되어 있다.

## PRD/Addendum Gap

### Gap 1: Telegram/KakaoTalk 채널 온보딩 UX가 약해졌다

**Source intent**

Research는 Telegram과 KakaoTalk 채널 등록을 단순 설정값 입력이 아니라 고객 확인이 필요한 activation flow로 설명한다.

- Telegram: 고객이 봇을 그룹에 추가하고 `/register ABC123`를 입력하면 서버가 `chat_id`, `topic_id`를 저장하고 테스트 메시지 후 확인한다.
- KakaoTalk: 고객이 서비스용 카카오 계정을 단톡방에 초대하고, 방 이름을 고유하게 맞춘 뒤 등록코드로 검증하고 테스트 메시지를 확인한다.

**Current coverage**

PRD는 FR-24에서 Telegram 채널 등록, topic ID 관리, test message를 언급한다. FR-15와 FR-25는 KakaoTalk 직렬 전송과 제한 운영을 다룬다.

**Gap**

등록코드 기반 채널 매핑, 고객 확인 단계, 고유 방명 정책, 테스트 메시지 승인 후 활성화라는 activation UX가 PRD 본문에서 명확한 요구사항으로 살아 있지 않다. 이 부분은 오발송 방지와 고객 셀프/반셀프 온보딩의 핵심이라 단순 구현 세부가 아니다.

**Recommended PRD action**

FR-24 또는 별도 FR로 "채널 등록/검증/활성화" 요구를 추가한다. KakaoTalk에는 고유 방명 검증과 등록코드 기반 방 매핑을, Telegram에는 register code 기반 chat/topic 자동 저장과 테스트 확인을 명시한다.

### Gap 2: 구독/결제 실패 lifecycle이 너무 단순화되었다

**Source intent**

Research는 고객 상태와 결제 상태를 운영 state machine으로 다룬다.

- `LEAD -> SIGNED_UP -> PAYMENT_ACTIVE -> SETUP_PENDING -> PLATFORM_AUTH_PENDING -> MESSENGER_VERIFY_PENDING -> TEST_RUNNING -> ACTIVE -> DEGRADED -> AUTH_REQUIRED -> SUSPENDED`
- `payment_failed + grace_period`에는 경고와 알림을 보내고, `grace_period_expired`에는 신규 수집을 중지하되 기존 설정은 보존한다.

**Current coverage**

PRD는 FR-6에서 구독 상태에 따라 작업 실행을 제어한다고 한다. 다만 MVP는 수동 구독 상태 관리로 가정되어 있고, payment failure/grace period/설정 보존 정책은 구체화되어 있지 않다.

**Gap**

Research의 "판매용 구독 시스템" 의도 중 고객 lifecycle과 결제 실패 운영 정책이 PRD에서 축약되어, 후속 아키텍처/에픽 작성 시 단순 active/inactive flag로 축소될 위험이 있다.

**Recommended PRD action**

MVP가 수동 구독 상태를 유지하더라도, "구독 lifecycle 상태와 결제 실패 grace 정책은 후속 범위로 보존한다"는 요구 또는 addendum 항목을 추가한다. 특히 결제 실패 시 신규 job 중지, 설정 보존, 결제 복구 시 재개 조건은 제품 정책으로 남겨야 한다.

### Gap 3: 규모별 운영 전략과 target-count 사고가 충분히 보존되지 않았다

**Source intent**

Research는 고객 수보다 "모니터링 대상 수"가 핵심 단위라고 강조하고, 10개 이하, 30~100개, 100~500개, 1000개 이상으로 운영 모델을 나눈다. 각 구간마다 현재 PC, Windows 작업 서버, worker pool, sharding, sender pool, circuit breaker의 필요 수준이 달라진다.

**Current coverage**

PRD는 Open Question에서 목표 규모를 모니터링 대상 수 기준으로 묻고, MVP 성능 요구로 100 fake target scheduling smoke를 둔다. addendum은 worker capacity 측정 항목과 queue lag 기준을 보존한다.

**Gap**

Research의 단계별 운영 판단 기준이 PRD/Addendum에서 일부 흩어져 있다. "언제 현재 PC로 충분한지", "언제 고성능 Windows 작업 서버가 필요한지", "언제 작업 노드 풀로 가야 하는지"라는 전략적 판단 구조가 약하다.

**Recommended PRD action**

PRD의 출시/마이그레이션 요구 또는 addendum에 규모별 운영 단계 표를 보존한다. MVP 범위를 늘릴 필요는 없지만, 이후 capacity planning과 에픽 분할의 기준으로 남기는 것이 좋다.

### Gap 4: 법무/약관/계정 위임 리스크가 명시적 PRD 리스크로 빠졌다

**Source intent**

Research의 리스크 표는 "약관/계정 위임 이슈"를 높은 심각도로 두고, 출시 전 약관, 동의서, 보안정책 검토가 필요하다고 한다.

**Current coverage**

Addendum에는 KakaoTalk 안전/정책 자료상 고신뢰 SLA 약속 전 공식 채널/API 평가가 필요하다는 외부 패턴 노트가 있다. 그러나 PRD의 리스크와 NFR에는 약관, 계정 위임, 고객 동의, 운영자 대행 인증의 정책 리스크가 명시되어 있지 않다.

**Gap**

이 제품은 고객 계정으로 플랫폼 화면을 읽고, 배민 휴대폰 인증을 사람이 처리하며, KakaoTalk PC 앱 UI를 자동화한다. 따라서 법무/약관/동의 리스크는 PRD-level risk로 남기는 편이 안전하다.

**Recommended PRD action**

PRD §10 리스크와 Open Questions에 "플랫폼 약관, 계정 위임, 고객 동의서, 인증 대행 정책, KakaoTalk 자동화 정책 검토"를 추가한다.

### Gap 5: 플랫폼 인증 실패 taxonomy가 축약되었다

**Source intent**

Research는 배민과 쿠팡 인증 실패를 여러 상태로 나눈다.

- 배민: `ACTIVE -> AUTH_REQUIRED -> USER_ACTION_PENDING -> AUTH_VERIFIED -> ACTIVE`
- 쿠팡: Gmail token 만료/폐기, 비밀번호 오류, 인증메일 지연, CAPTCHA, 같은 Gmail의 여러 인증메일 충돌, 최신 메일 오인식

**Current coverage**

PRD는 인증 필요 감지, 사람 개입형 배민 재인증, 쿠팡 token/mailbox 분리와 redaction을 담고 있다.

**Gap**

CAPTCHA, 비밀번호 오류, 인증메일 지연, token 폐기, 최신 메일 오인식 같은 실패 사유가 PRD나 addendum에 별도 taxonomy로 남아 있지 않다. 이 상태 분류가 없으면 운영 화면의 오류가 "인증 실패" 하나로 뭉개질 수 있다.

**Recommended PRD action**

FR-19 또는 운영 관측성 NFR에 "인증 실패 사유는 조치 가능한 유형으로 분류한다"를 추가한다. 세부 목록은 addendum에 보존해도 충분하다.

## Minor Gaps / Addendum 후보

### Local Agent 보안 hardening 세부

Research는 Windows 작업 노드의 BitLocker, Windows Credential Manager/DPAPI, agent 실행 계정 분리, 원격접속 2FA, Chrome profile 폴더 권한, secret revoke 절차를 제안한다.

PRD는 민감값 redaction과 HTTPS/Agent token을 다루고, addendum은 Gmail token 저장 위치 결정을 열어두지만, Windows 노드 hardening 목록은 보존되어 있지 않다. PRD 본문보다는 architecture/security addendum 후보로 적합하다.

### Platform-level circuit breaker와 canary 복구

Research는 배민/쿠팡 parser 실패율이 높을 때 global circuit breaker를 열고, hotfix 후 canary 고객부터 재개하는 흐름을 제안한다.

PRD는 반복 parser 실패를 경고 상태로 둔다. addendum은 circuit breaker를 언급하지만, global halt/canary resume의 운영 의도는 약하다. 대량 운영을 P6 후속 범위로 둘 경우 addendum 후보로 충분하다.

### KakaoTalk sender 용량 공식과 측정 원칙

Research는 sender 1개의 시간당 전송 가능량을 평균 전송 소요초로 계산하고, 실제 수치는 측정 기반으로 잡아야 한다고 설명한다.

PRD는 Kakao queue lag와 증설 판단을 요구한다. 공식 자체는 PRD 요구사항은 아니지만 capacity planning addendum에 보존하면 구현/운영자가 추정치를 세우기 쉽다.

## No Material Gap 항목

- 고성능 서버 한 대가 최종 정답이 아니라는 전략은 PRD에 반영되어 있다.
- 중앙 서버와 작업 노드/로컬 에이전트 분리는 PRD와 addendum에 반영되어 있다.
- 기존 코드 자산을 전면 재작성하지 않는다는 원칙은 PRD에 반영되어 있다.
- 탭 기반 `크롤링1~9` 모델을 ID 기반 모델로 바꾸는 요구는 PRD에 반영되어 있다.
- 수집/메시지 생성/전송 분리와 fan-out 요구는 PRD에 반영되어 있다.
- KakaoTalk을 위험한 제한 채널로 보는 관점은 PRD에 반영되어 있다.
- Telegram 중앙화와 중복 polling 회피는 PRD와 addendum에 반영되어 있다.
- 배민 인증을 우회하지 않는다는 원칙은 PRD에 반영되어 있다.
- 쿠팡 Gmail token 분리와 redaction은 PRD에 반영되어 있다.
- MVP에서 Kubernetes, 완전 셀프서비스, 결제 자동화, 1000개 이상 운영을 제외하는 경계는 PRD에 반영되어 있다.

## 권장 반영 우선순위

1. **High:** 채널 온보딩/검증/활성화 UX를 FR로 추가한다.
2. **High:** 법무/약관/계정 위임/고객 동의 리스크를 PRD 리스크와 Open Questions에 추가한다.
3. **Medium:** 구독 lifecycle과 결제 실패 grace policy를 후속 범위로 명시 보존한다.
4. **Medium:** 인증 실패 taxonomy를 운영 관측성 요구 또는 addendum에 추가한다.
5. **Low:** 규모별 운영 전략 표, Windows agent hardening 목록, Kakao sender capacity 공식은 addendum에 보존한다.

