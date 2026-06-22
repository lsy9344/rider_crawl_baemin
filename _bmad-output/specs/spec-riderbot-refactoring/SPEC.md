---
id: SPEC-riderbot-refactoring
companions:
  - architecture-contract.md
  - implementation-contract.md
  - data-api-contract.md
  - operations-security-test-contract.md
sources:
  - ../../../docs/refactoring/detailed_work_order.md
  - ../../../docs/refactoring/research.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability only; consult them only if narrative rationale or original wording is needed.

# 배달 실적봇 판매형 전환 리팩토링

## Why

현재 `rider_result_mornitoring`은 운영자 PC에서 Tkinter 탭별로 배민/쿠팡 화면을 읽고 텔레그램/카카오톡으로 전송하는 로컬 자동화 도구다. 판매형 다고객 구독 서비스로 전환하려면 탭 순번 기반 상태, 수집-전송 결합, 평문 secret, 로컬 로그 중심 운영을 제거하고, 클라우드 중앙 서버와 Windows 로컬 에이전트가 역할을 나누는 구조로 바꿔야 한다.

## Capabilities

- id: CAP-1
  intent: 시스템은 기존 배민/쿠팡 parser, 메시지 renderer, 텔레그램 sender, 쿠팡 Gmail 2FA 자산을 보존하면서 탭 순번 대신 tenant/customer/monitoring target ID로 상태를 식별한다.
  success: 기존 활성 배민/쿠팡 target 각 1개가 신규 ID 기반 모델로 마이그레이션되고, dry-run에서 기존 렌더링 결과와 신규 렌더링 결과가 비교 가능하다.

- id: CAP-2
  intent: 시스템은 기존 `run_once` 흐름을 수집, snapshot 저장, 메시지 렌더링, 전송 fan-out, 전송 이력 기록으로 분리한다.
  success: 한 번 수집한 snapshot에서 2개 이상 messenger channel로 메시지를 보내며, 같은 메시지를 재실행해도 DeliveryLog idempotency key로 중복 발송되지 않는다.

- id: CAP-3
  intent: 클라우드 control plane은 고객, 구독, 설정, 작업 큐, 상태, 로그, 관리자 화면, Telegram webhook, secret reference를 관리한다.
  success: Admin에서 고객/target/channel/agent/최근 오류/인증 필요 상태를 볼 수 있고, scheduler가 interval과 jitter를 반영해 CrawlJob을 생성한다.

- id: CAP-4
  intent: Windows Local Agent는 운영자 PC 또는 작업 PC에서 Chrome profile, 배민/쿠팡 수집, 쿠팡 Gmail 2FA, 카카오톡 PC 앱 전송을 실행하고 중앙 서버에 상태를 보고한다.
  success: Agent가 registration code로 등록되고, 30~60초 heartbeat와 HTTPS outbound job polling/claim/complete 루프를 통해 방화벽 뒤에서도 작업을 처리한다.

- id: CAP-5
  intent: 시스템은 배민 휴대폰 인증을 사람 개입형 상태 머신으로 처리하고, 쿠팡 Gmail 2FA는 고객/메일함별 token 분리와 mailbox lock으로 자동복구한다.
  success: 배민 세션 만료는 AUTH_REQUIRED로 표시되고 인증 브라우저 열기 명령으로 복구 가능하며, 쿠팡 인증은 Gmail token refresh 실패와 CAPTCHA를 별도 상태로 분류한다.

- id: CAP-6
  intent: 시스템은 Telegram을 중앙 webhook/sendMessage 구조로 운영하고, KakaoTalk은 sender agent별 FIFO queue와 단일 Windows session 직렬 전송으로 운영한다.
  success: Telegram `/register <code>`가 chat/thread mapping을 만들고, Kakao KAKAO_SEND job은 같은 sender session에서 동시에 두 건 이상 실행되지 않는다.

- id: CAP-7
  intent: 시스템은 고객 생성, 요금제/쿼터 설정, 플랫폼 인증, messenger 검증, 테스트 수집, 테스트 발송, 고객 확인, ACTIVE 전환까지 온보딩 흐름을 제공한다.
  success: 신규 tenant가 setup code를 받고, 플랫폼 계정과 messenger channel을 검증한 뒤 모든 채널 테스트 발송 성공 후 ACTIVE 상태가 된다.

- id: CAP-8
  intent: 시스템은 판매용 운영에 필요한 secret 관리, 로그 redaction, 모니터링 지표, 백업, 배포, 회귀 테스트를 계약 조건으로 강제한다.
  success: token/password/OTP가 DB/로그/설정 파일/diagnostic artifact에 평문으로 남지 않고, unit/integration/E2E dry-run/messenger/auth/load smoke 기준이 CI 또는 운영 절차에 연결된다.

- id: CAP-9
  intent: 시스템은 현재 일반 PC를 Agent #1로 시작하되, 측정 지표에 따라 전용 작업 PC와 sender pool을 늘릴 수 있게 설계한다.
  success: CPU/RAM, active target 수, Kakao queue lag, agent heartbeat, target success lag 기준으로 증설 판단과 target sharding이 가능하다.

## Constraints

- 1차 구조는 "클라우드 중앙 서버 + 운영자 보유 Windows Local Agent #1"이다. 고성능 작업 서버는 즉시 구매하지 않는다.
- AWS 서울 리전, Dockerized API/Admin/Scheduler, PostgreSQL, secret manager, HTTPS, token-auth Agent API를 기본 방향으로 한다.
- 탭을 9개에서 100개로 늘리는 방식, per-tab scheduler thread 확장, 탭 순번 기반 상태 식별은 금지한다.
- Cloud server가 운영자 PC의 Chrome CDP port에 직접 접속하면 안 된다. Agent가 outbound HTTPS로 poll/report해야 한다.
- 배민 휴대폰 인증을 무인 자동화하거나 우회하는 기능은 만들지 않는다.
- KakaoTalk PC 앱 전송은 같은 Windows session에서 병렬 실행하지 않는다.
- Gmail OAuth token은 고객/메일함별로 분리하고, 같은 mailbox로 동시 인증을 요청하지 않는다.
- Telegram bot token, 쿠팡 비밀번호, Gmail token, Agent token, OTP, authorization code는 DB/로그/설정 파일/스크린샷에 평문으로 남기지 않는다.
- Chrome User Data Directory는 target별로 분리하고, 같은 profile을 두 Chrome process에서 동시에 열지 않는다.
- Scheduler는 jitter, retry backoff, subscription gating, platform circuit breaker, agent capacity를 반영해야 한다.
- KakaoTalk은 무제한 채팅방 상품으로 판매하지 않고, 고유 방명, 테스트 전송, sender queue, 실패 분류를 전제로 한다.

## Non-goals

- 배민/쿠팡 공식 API 전환은 이번 계약 범위가 아니다.
- 클라우드가 Chrome과 KakaoTalk PC 앱을 직접 실행하는 구조는 이번 목표가 아니다.
- Kubernetes, multi-region, full autoscaling은 MVP 목표가 아니다.
- 지금 즉시 고성능 서버를 구매하는 일은 목표가 아니다.
- 배민 휴대폰 인증 완전 자동화, 카카오톡 UI 병렬 전송, Gmail token 공유는 구현하지 않는다.

## Success signal

운영자는 Admin 웹에서 고객/target/agent/channel/auth/error 상태를 보고, 중앙 서버가 만든 CrawlJob을 현재 Windows Agent가 실행해 snapshot을 업로드하며, 같은 snapshot이 Telegram과 KakaoTalk 테스트 채널로 중복 없이 fan-out되는 것을 확인한다. 기존 활성 탭 2개는 신규 구조에서 dry-run과 실제 테스트 발송까지 검증된다.

## Assumptions

- `project-context.md` persistent fact는 루트에 없어 읽지 못했고, 산출물은 `docs/refactoring/detailed_work_order.md`와 `docs/refactoring/research.md`만 기준으로 만들었다.
- Queue backend의 1차 구현은 PostgreSQL job table 또는 Redis 중 하나를 선택할 수 있지만, `QueueBackend` 인터페이스로 SQS/managed Redis 교체가 가능해야 한다.
- Admin UI 기술은 원문이 고정하지 않았으므로 React/Next.js 또는 서버 렌더링 템플릿 중 구현 단계에서 선택한다.

## Open Questions

- 첫 MVP의 queue backend를 PostgreSQL job table로 시작할지 Redis로 시작할지 결정해야 한다.
- Admin UI의 첫 구현 기술을 React/Next.js로 할지 서버 렌더링 템플릿으로 할지 결정해야 한다.
- 실제 도메인, 결제 provider, 고객 동의/약관 문구, 요금제 quota 숫자는 별도 제품 결정이 필요하다.
