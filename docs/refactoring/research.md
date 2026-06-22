## 결론부터 말하면

**클라우드로 전부 옮기는 것도, 고성능 서버 한 대에 모두 몰아넣는 것도 최종 정답은 아닙니다.**
이 프로젝트의 병목은 단순 CPU 성능이 아니라 **고객별 로그인 세션, Chrome 프로필, 배민 휴대폰 인증, 쿠팡 Gmail 인증, 카카오톡 PC UI 자동화, 채팅방 전송 안정성**입니다. 현재 구조는 로컬 PC에서 Tkinter 탭 1개가 고객/계정 1개처럼 동작하고, 로그인된 Chrome 화면을 CDP/Playwright로 읽은 뒤 텔레그램 또는 카카오톡으로 보내는 방식입니다. 카카오톡은 PC 앱 UI 자동화이고, 텔레그램은 API 기반이며, 배민은 휴대폰 인증 때문에 완전 자동 로그인을 전제로 두기 어렵다는 점이 핵심 제약입니다. 

제가 권장하는 방향은 이겁니다.

> **클라우드 중앙 서버 + 여러 개의 작업 노드/로컬 에이전트 + 메신저 전송 큐 구조**

즉, 클라우드는 **고객 관리, 구독, 설정, 로그, 상태 대시보드, 텔레그램 webhook, 작업 큐**를 담당하고, 실제 Chrome/카카오톡을 띄우는 부분은 **운영자 소유 Windows 작업 PC/서버 또는 필요 시 고객 PC 에이전트**가 담당하게 해야 합니다.

---

# 1. 현재 구조 진단

현재 프로그램은 “작동하는 자동화 도구”로는 좋은 출발점입니다. 다만 판매용 구독 시스템으로 보려면 가장 먼저 구조를 바꿔야 합니다.

현재 구조의 핵심 문제는 다음입니다.

| 영역    | 현재 구조                             | 확장 시 문제                        |
| ----- | --------------------------------- | ------------------------------ |
| 고객 모델 | `크롤링1~9` 탭이 고객/계정 역할              | 고객 100명부터 관리 불가                |
| 상태 식별 | `crawling1`, `crawling2` 같은 순번 기반 | 탭 순서 변경, 고객 추가/삭제 시 상태 꼬임      |
| 실행 방식 | 탭마다 scheduler thread              | 고객 수 증가 시 thread, retry, 로그 폭증 |
| 브라우저  | 탭마다 Chrome 프로필 + CDP 포트           | 100개 이상부터 포트/프로필 운영 지옥         |
| 메시지   | 크롤링 → 메시지 생성 → 전송이 한 job          | 같은 데이터를 여러 채팅방에 뿌릴 때 비효율       |
| 보안    | JSON/로컬 파일 중심                     | 판매용으로는 토큰·비밀번호 저장 방식 취약        |
| 모니터링  | 각 PC의 UI/로그 확인                    | 고객 장애를 중앙에서 파악하기 어려움           |
| 카카오톡  | PC 앱 UI 자동화                       | 다수 채팅방/다수 고객 운영 시 가장 큰 리스크     |
| 배민 인증 | 사람이 휴대폰 인증                        | 완전 자동 로그인 설계 불가                |
| 쿠팡 인증 | Gmail 2FA 가능                      | 고객별 Gmail token 분리 필수          |

현재 코드에서 재사용할 가치는 충분합니다. 특히 **배민/쿠팡 parser, 메시지 renderer, 플랫폼 registry, 텔레그램 전송, 쿠팡 Gmail 2FA 로직, 테스트 코드**는 살리는 게 맞습니다. 버려야 할 것은 크롤러 자체가 아니라 **탭 기반 고객 관리, 평문 설정 저장, per-tab thread 운영, 카카오톡 직접 전송 결합 구조**입니다.

---

# 2. 클라우드 vs 고성능 서버 vs 현재 PC

## 제 판단

| 선택지                       |       추천도 | 이유                                                 |
| ------------------------- | --------: | -------------------------------------------------- |
| 현재 일반 PC 유지               |    단기만 가능 | 베타/소수 고객에는 가능하지만 장애·업데이트·로그·백업이 약함                 |
| 고성능 서버 한 대                | 중간 단계로 가능 | Chrome/Kakao 작업 노드로는 좋지만 단일 장애점이 됨                 |
| 클라우드로 전부 이전               |       비추천 | 카카오톡 PC 앱, 배민 휴대폰 인증, 다수 Chrome 세션 때문에 비용/운영 리스크 큼 |
| **클라우드 중앙 서버 + 여러 작업 노드** | **최종 추천** | 관리/구독/로그는 클라우드, 브라우저/카톡은 작업 노드로 분리 가능              |

클라우드는 비용 구조상 장기 약정 없이 필요한 만큼 쓰는 데 유리합니다. AWS EC2 On-Demand는 장기 약정 없이 시간/초 단위로 컴퓨팅을 쓰는 모델이고, Google Compute Engine도 VM 리소스 사용량에 따라 과금되며 최소 1분 이후 초 단위 과금 구조를 제공합니다. 다만 이 프로젝트처럼 Chrome 세션과 카카오톡 sender가 사실상 오래 켜져 있어야 하면, **항상 켜진 작업 노드 비용**이 커질 수 있습니다. ([Amazon Web Services, Inc.][1])

따라서 클라우드는 다음 용도로 쓰는 게 효율적입니다.

**클라우드에 올릴 것**

| 컴포넌트                               | 이유                   |
| ---------------------------------- | -------------------- |
| 관리자 웹 대시보드                         | 어디서든 고객/상태/장애 확인     |
| 고객/구독 DB                           | 판매용 제품의 중심           |
| 작업 큐                               | 고객 수 증가 시 스케줄 제어     |
| 로그/상태 수집 API                       | 장애 대응 자동화            |
| 텔레그램 webhook                       | API 기반이라 클라우드와 궁합 좋음 |
| Secret Manager 또는 암호화 secret store | 토큰/비밀번호 관리           |

**클라우드에 바로 올리면 애매한 것**

| 컴포넌트                 | 이유                      |
| -------------------- | ----------------------- |
| 카카오톡 PC 앱 자동화        | Windows GUI/포커스/클립보드 의존 |
| 배민 로그인 유지용 Chrome    | 휴대폰 인증·세션 만료 대응 필요      |
| 고객별 수십~수백 Chrome 프로필 | 메모리/디스크/인증 리스크 큼        |

고성능 서버 한 대는 “작업 노드 1호”로는 좋습니다. 예를 들어 운영자 사무실이나 IDC에 **Windows 작업 서버 1대**를 두고, 거기서 Kakao Sender와 일부 Chrome 세션을 운영하는 방식입니다. 하지만 고객이 늘면 한 대에 계속 몰아넣지 말고, 처음부터 **작업 노드 여러 대로 쪼갤 수 있는 구조**를 만들어야 합니다.

---

# 3. 권장 목표 아키텍처

## 최종 구조

```text
[고객/운영자]
   ↓
[관리자 웹/온보딩 페이지]
   ↓
[중앙 서버]
 - 고객/구독 관리
 - 플랫폼 계정 설정
 - 채팅방 설정
 - 작업 스케줄러
 - 작업 큐
 - 로그/상태 DB
 - 알림/재인증 요청
 - 텔레그램 webhook
 - secret reference 관리
   ↓
[작업 노드 / 로컬 에이전트들]
 - Chrome 프로필 실행
 - 배민/쿠팡 화면 수집
 - 쿠팡 Gmail 2FA 처리
 - 카카오톡 PC 앱 전송
 - 상태 heartbeat 보고
   ↓
[텔레그램 / 카카오톡 채팅방]
```

핵심은 **수집과 전송을 분리**하는 것입니다.

현재는 `run_once()`가 크롤링, 메시지 렌더링, 전송까지 한 번에 처리하는 구조입니다. 이 방식은 소수 탭에서는 단순하지만, 같은 실적 데이터를 여러 채팅방에 뿌릴 때 매번 크롤링하거나 전송 대상에 따라 실행 단위가 꼬일 수 있습니다. 현재 문서상 실행 흐름도 크롤링 → 메시지 생성 → 중복 해시 확인 → 텔레그램/카카오 전송이 하나의 job으로 묶여 있습니다. 

리팩토링 후에는 이렇게 나눠야 합니다.

```text
1. CrawlJob
   배민/쿠팡에서 실적 snapshot 수집

2. SnapshotStore
   수집 결과 저장

3. MessageRenderJob
   고객별 템플릿으로 메시지 생성

4. DispatchJob
   텔레그램/카카오톡 여러 채팅방으로 fan-out

5. DeliveryLog
   전송 성공/실패/중복방지 기록
```

이렇게 해야 고객 한 명이 카카오톡 방 5개, 텔레그램 토픽 3개에 뿌리더라도 **크롤링은 한 번만 하고 메시지만 여러 곳으로 분배**할 수 있습니다.

---

# 4. 규모별 현실적인 운영 방식

여기서 “고객 수”보다 더 중요한 단위는 **모니터링 대상 수**입니다.

> 모니터링 대상 = 고객 + 플랫폼 계정 + 센터/상점 + 수집 URL 조합

고객 1명이 배민 2개 센터, 쿠팡 3개 상점을 쓰면 고객 1명이지만 모니터링 대상은 5개입니다.

## 10개 대상 이하

현재 앱을 조금 정리해서도 운영 가능합니다.

권장 작업:

* `customer_id` 추가
* 탭 순번 기반 상태 제거
* 설정 저장 atomic write 적용
* 로그 rotation 추가
* 카카오톡 전송 큐 추가
* Chrome 프로필/포트 자동 배정
* 실패 상태를 UI에 명확히 표시

이 단계에서는 아직 클라우드 전체 이전보다 **현재 PC + 백업 + 원격접속 + 자동재시작**이 더 빠릅니다.

## 30~100개 대상

이때부터는 탭 UI를 버려야 합니다.

권장 구조:

```text
클라우드 중앙 서버 1개
+
Windows 작업 노드 1~3대
+
PostgreSQL / Redis
+
관리자 대시보드
```

이 구간에서는 고성능 서버 한 대를 작업 노드로 쓰는 것이 현실적입니다. 다만 서버 한 대에 모든 고객을 묶지 말고, 중앙 서버가 다음처럼 작업을 배정해야 합니다.

```text
target_001 → worker_a
target_002 → worker_a
target_030 → worker_b
target_071 → worker_c
```

장애가 나면 어느 고객이 영향을 받는지 중앙에서 바로 보여야 합니다.

## 100~500개 대상

이때부터는 “서버 사양”보다 **샤딩, 큐, 모니터링, 인증 UX**가 중요합니다.

필수 구조:

* 작업 큐 기반 스케줄러
* agent heartbeat
* worker별 수용량 제한
* 고객별 retry/backoff
* 플랫폼별 circuit breaker
* Chrome 프로필 health check
* 메시지 전송 queue lag 모니터링
* 배민 재인증 요청 자동 알림
* 쿠팡 Gmail token 재승인 플로우
* 카카오 sender pool

## 1000개 대상 이상

단일 서버는 부적합합니다.

권장 구조:

```text
중앙 서버
  - API / Admin / Subscription
  - PostgreSQL
  - Redis or RabbitMQ
  - Object Storage
  - Secret Store
  - Observability

작업 노드 풀
  - baemin-worker-001~N
  - coupang-worker-001~N
  - kakao-sender-001~N
  - telegram-dispatcher
```

1000개 대상이 5분마다 한 번씩 수집된다면 단순 계산으로 분당 200개 수집 작업입니다. 작업 하나가 평균 20초 걸린다면 동시 실행량은 대략 67개입니다.

```text
동시 실행량 ≈ 대상 수 ÷ 수집 주기(분) × 평균 작업시간(초) ÷ 60
1000 ÷ 5 × 20 ÷ 60 = 약 66.7
```

여기서 중요한 점은 **Chrome을 항상 1000개 띄워두는 구조와, 필요한 시점에 profile을 열어 수집하는 구조의 비용이 완전히 다르다**는 것입니다. 리팩토링 목표는 “항상 열린 탭 1000개”가 아니라 **세션은 유지하되 브라우저 실행은 작업 노드가 관리하는 구조**입니다.

---

# 5. 플랫폼별 인증 전략

## 배민

배민은 완전 자동 로그인을 목표로 잡으면 안 됩니다. 현재 전제처럼 휴대폰 인증이 필요하다면, 현실적인 UX는 다음입니다.

```text
1. 중앙 대시보드에서 배민 계정 등록
2. 작업 노드가 고객별 Chrome 프로필 생성
3. 배민 로그인 페이지 자동 오픈
4. 운영자 또는 고객이 휴대폰 인증 직접 완료
5. 에이전트가 로그인 완료 감지
6. 센터명/센터 ID 검증
7. 테스트 크롤링
8. 테스트 메시지 발송
9. 활성화
```

재인증이 필요할 때는 자동 재시도를 무한히 하지 말고 상태를 바꿔야 합니다.

```text
ACTIVE
 → AUTH_REQUIRED
 → USER_ACTION_PENDING
 → AUTH_VERIFIED
 → ACTIVE
```

운영 화면에는 이렇게 보여야 합니다.

```text
고객명: A센터
플랫폼: 배민
상태: 재인증 필요
마지막 성공: 2026-06-12 11:58
조치: Chrome 열기 / 고객에게 인증 요청 보내기 / 완료 감지 재시도
```

배민 쪽에서 자동화할 수 있는 것은 “로그인 자체”가 아니라 **로그인 페이지 열기, 인증 필요 감지, 로그인 완료 감지, 재인증 알림, 성공 후 자동 재개**입니다.

## 쿠팡이츠

쿠팡은 Gmail 인증 자동화 가능성이 있으므로 배민보다 자동화 범위를 넓힐 수 있습니다. 다만 고객마다 Gmail이 다르면 token도 반드시 분리해야 합니다. 현재 문서에서도 쿠팡 Gmail token은 고객별/메일함별 분리가 필요하다고 정리되어 있습니다. 

권장 흐름:

```text
1. 쿠팡 계정 등록
2. 쿠팡 비밀번호는 secret store에 저장
3. 고객 Gmail OAuth 1회 승인
4. token은 customer_id 또는 mailbox_id 기준으로 분리 저장
5. 로그인 만료 감지
6. 아이디/비밀번호 입력
7. 이메일 인증 요청
8. Gmail API로 인증메일 조회
9. 인증번호 입력
10. peak-dashboard 진입 확인
11. 수집 재개
```

주의할 점은 Gmail API scope입니다. Google 문서상 `gmail.readonly`는 Gmail 메시지와 설정을 볼 수 있는 restricted scope이고, restricted scope 데이터를 서버에 저장하거나 전송하면 보안 평가가 필요할 수 있습니다. 따라서 판매용으로 갈수록 Gmail token과 메일 데이터 취급 정책을 매우 보수적으로 설계해야 합니다. ([Google for Developers][2])

쿠팡 인증에서 꼭 넣어야 하는 제어는 다음입니다.

| 문제                      | 대응                                     |
| ----------------------- | -------------------------------------- |
| 같은 Gmail로 여러 쿠팡 인증메일 수신 | mailbox 단위 lock                        |
| 최신 메일을 잘못 집음            | 요청 시각 이후 + subject/from/query + 계정별 조건 |
| CAPTCHA 발생              | 자동화 중단, 사람 개입 상태 전환                    |
| token 만료/폐기             | Gmail 재승인 요청                           |
| 비밀번호 오류                 | 고객 설정 오류로 분리                           |
| 인증메일 지연                 | 제한된 polling 후 backoff                  |

---

# 6. 텔레그램과 카카오톡 전략

## 텔레그램은 중앙화

텔레그램은 API 기반이므로 중앙 서버에 붙이는 게 맞습니다. Telegram Bot API는 update 수신 방식으로 `getUpdates` long polling과 webhook을 제공하며, 둘은 동시에 쓰는 구조가 아닙니다. 또한 webhook은 Telegram이 지정 HTTPS URL로 update를 POST하는 구조입니다. 대규모 운영에서는 각 PC가 `getUpdates`를 따로 호출하는 것보다 중앙 webhook으로 모으는 편이 관리하기 쉽습니다. ([Telegram][3])

텔레그램 온보딩은 수동 chat_id 입력을 없애야 합니다.

```text
1. 고객이 텔레그램 그룹에 봇 추가
2. 고객이 /register ABC123 입력
3. 중앙 서버가 chat_id, topic_id 자동 저장
4. 테스트 메시지 발송
5. 고객이 확인 버튼 클릭
```

Telegram Bot API에는 forum topic/thread 대상 식별자인 `message_thread_id`가 있으므로, 현재처럼 토픽별 전송도 중앙 라우팅으로 관리할 수 있습니다. ([Telegram][3])

## 카카오톡은 별도 Sender Pool

카카오톡은 이 프로젝트에서 가장 위험한 부분입니다. 현재 방식은 Windows PC 앱 UI 자동화이고, 클립보드/창 포커스/채팅방명에 의존합니다. 제공 문서에서도 카카오톡 전송은 Windows PC 앱 UI 자동화이며, 같은 이름의 채팅방이 있으면 오발송 위험이 있어 실패 처리하고, 전역 lock으로 직렬화된다고 되어 있습니다. 

따라서 카카오톡은 이렇게 설계해야 합니다.

```text
Kakao Sender Agent
 - Windows 로그인 세션 필요
 - KakaoTalk PC 앱 로그인 필요
 - 하나의 sender agent는 전송을 직렬 처리
 - 채팅방 검색/선택/붙여넣기/전송
 - 성공/실패 스크린샷 또는 진단 로그 저장
 - 중앙 서버에 결과 보고
```

중요한 설계 원칙은 **카카오톡 채팅방을 전부 띄워놓는 방식으로 확장하지 않는 것**입니다. 수많은 채팅방을 모두 열어두면 포커스 충돌, 메모리 증가, 오발송, UI 꼬임이 커집니다. 대신 “채팅방 registry + 전송 큐 + 필요 시 방 선택” 구조로 가야 합니다.

카카오톡 온보딩은 이렇게 해야 합니다.

```text
1. 고객이 서비스용 카카오 계정을 단톡방에 초대
2. 방 이름을 고유하게 맞춤
   예: [라이더봇] A센터_주간실적
3. 고객이 등록코드 입력
   예: 등록 ABC123
4. 운영자/agent가 해당 방을 검색
5. 테스트 메시지 발송
6. 고객이 확인
7. room mapping 활성화
```

카카오톡 요금제/상품 전략도 나눠야 합니다.

| 상품                  | 권장             |
| ------------------- | -------------- |
| Telegram 전송         | 기본 요금제         |
| KakaoTalk 전송        | 프리미엄 또는 제한 요금제 |
| 고객별 전용 Kakao sender | 고가 요금제         |
| 대량 Kakao 방 전송       | SLA 제한 필요      |

카카오톡은 텔레그램처럼 API로 안정적으로 fan-out하는 구조가 아니므로, “카톡도 무제한 채팅방 지원”이라고 팔면 운영 리스크가 커집니다.

---

# 7. 리팩토링 컨셉

## 핵심 컨셉

현재 프로그램을 다음 5개 계층으로 나눠야 합니다.

```text
1. Domain
   Customer, Subscription, PlatformAccount, MonitoringTarget,
   BrowserProfile, MessengerChannel, DeliveryRule

2. Application
   CrawlService, MessageRenderService, DispatchService,
   AuthRecoveryService, OnboardingService

3. Infrastructure
   PlaywrightBrowserSessionProvider, GmailClient,
   TelegramSender, KakaoUiSender, SecretStore, StateStore

4. Worker
   CrawlWorker, AuthWorker, DispatchWorker, KakaoSenderWorker

5. Interface
   Admin Web, Local Agent UI, API, Webhook
```

## 새 도메인 모델

최소한 아래 모델은 필요합니다.

| 모델                 | 설명                                   |
| ------------------ | ------------------------------------ |
| `Tenant`           | 구독 고객사                               |
| `Subscription`     | 요금제, 만료일, 허용 대상 수                    |
| `PlatformAccount`  | 배민/쿠팡 계정 단위                          |
| `MonitoringTarget` | 실제 수집 단위. 플랫폼 + 센터/상점 + URL          |
| `BrowserProfile`   | Chrome profile 경로, worker 배정, health |
| `MessengerChannel` | Telegram chat/topic 또는 Kakao room    |
| `DeliveryRule`     | 어떤 snapshot을 어느 채팅방에 보낼지             |
| `AuthSession`      | 로그인 상태, 재인증 필요 여부                    |
| `JobRun`           | 수집/전송 실행 기록                          |
| `DeliveryLog`      | 메시지별 성공/실패/중복방지                      |
| `Agent`            | 작업 노드/로컬 에이전트                        |
| `SecretRef`        | 실제 secret 값이 아니라 참조값                 |

## 현재 `run_once()` 분해

현재:

```text
run_once(config)
 → crawl_snapshot
 → render_message
 → hash compare
 → send telegram/kakao
```

리팩토링 후:

```text
CrawlWorker
 → CrawlJob 실행
 → Snapshot 저장

RenderWorker
 → Snapshot 기반 메시지 생성
 → Message 저장

DispatchWorker
 → DeliveryRule 조회
 → Messenger별 queue 등록

TelegramDispatcher
 → API 전송

KakaoSenderAgent
 → Windows UI 자동화 전송
```

이렇게 해야 “수집 실패”, “렌더링 오류”, “전송 실패”, “카카오톡 UI 오류”를 각각 분리해서 볼 수 있습니다.

---

# 8. 작업 큐와 트래픽 처리

이 시스템의 트래픽은 일반 웹서비스의 트래픽과 다릅니다. 고객이 웹에 많이 접속해서 생기는 트래픽보다, **정해진 시간마다 배민/쿠팡을 읽고 채팅방에 뿌리는 batch성 트래픽**이 핵심입니다.

필수 설계는 다음입니다.

## 1) 스케줄 jitter

모든 고객을 정각에 실행하면 안 됩니다.

나쁜 예:

```text
12:00:00 고객 1000명 동시 수집
12:05:00 고객 1000명 동시 수집
```

좋은 예:

```text
12:00:00~12:04:59 사이에 고객별로 분산 실행
```

## 2) 작업 큐

추천 기술은 MVP 기준으로 다음이면 충분합니다.

```text
FastAPI
PostgreSQL
Redis
RQ / Celery / Dramatiq 중 하나
Playwright worker
```

처음부터 Kubernetes로 가지 않아도 됩니다. 먼저 Docker Compose 또는 단순 VM 기반으로 시작하고, 100개 이상부터 worker pool을 늘리면 됩니다.

## 3) idempotency

같은 메시지를 두 번 보내면 신뢰가 깨집니다.

전송 키를 이렇게 잡아야 합니다.

```text
message_dedup_key =
  monitoring_target_id
  + messenger_channel_id
  + snapshot_collected_at
  + template_version
  + message_hash
```

## 4) circuit breaker

배민/쿠팡 화면 구조가 바뀌면 모든 고객이 실패할 수 있습니다. 이때 5초마다 무한 재시도하면 사이트에도 부담이고 운영 로그도 폭발합니다.

예:

```text
배민 parser 실패율 30% 초과
 → BaeminGlobalCircuitOpen
 → 신규 수집 일시중지
 → 내부 관리자에게 알림
 → parser hotfix 후 canary 고객부터 재개
```

## 5) fan-out

수집과 전송은 분리해야 합니다.

```text
배민 A센터 수집 1회
 → Telegram 1번방
 → Telegram 2번 토픽
 → Kakao A센터방
 → Kakao 관리자방
```

이 구조가 되어야 채팅방이 늘어도 Chrome 부하가 같이 늘지 않습니다.

---

# 9. 보안 설계

판매용이면 평문 JSON에 비밀번호, Telegram token, Gmail token 경로를 두는 구조는 바꿔야 합니다. Google Cloud Secret Manager 같은 secret 관리 서비스는 API key, username, password, certificate 등 민감 데이터를 저장·관리하고, secret versioning, IAM 기반 접근제어, 암호화를 제공합니다. ([Google Cloud][4])

권장 원칙:

| 항목                 | 권장 저장 방식                              |
| ------------------ | ------------------------------------- |
| 쿠팡 비밀번호            | Secret Manager 또는 OS Credential Store |
| Telegram bot token | Secret Manager                        |
| Gmail OAuth token  | 고객/메일함별 분리, 암호화 저장                    |
| Chrome profile     | worker 로컬 암호화 디스크                     |
| 설정값                | DB 저장                                 |
| secret 값           | DB에 직접 저장하지 않고 `secret_ref`만 저장       |
| 로그                 | token, OTP, 비밀번호 redaction 필수         |

로컬 Windows 작업 노드에서는 다음을 권장합니다.

* BitLocker 활성화
* Windows Credential Manager 또는 DPAPI 사용
* agent 실행 계정 분리
* 원격접속 계정 2FA
* Chrome profile 폴더 권한 제한
* 로그에 인증번호/토큰 절대 기록 금지
* 작업 노드 분실/폐기 시 secret revoke 절차

---

# 10. 고객 유입부터 자동화까지의 운영 플로우

구독제로 직접 운영하려면 “고객이 돈을 냈다”에서 끝나는 게 아니라, **활성화 완료까지의 상태 머신**이 필요합니다.

## 고객 상태

```text
LEAD
 → SIGNED_UP
 → PAYMENT_ACTIVE
 → SETUP_PENDING
 → PLATFORM_AUTH_PENDING
 → MESSENGER_VERIFY_PENDING
 → TEST_RUNNING
 → ACTIVE
 → DEGRADED
 → AUTH_REQUIRED
 → SUSPENDED
```

## 온보딩 플로우

```text
1. 고객 신청/결제
2. tenant 자동 생성
3. 요금제에 따라 target quota 설정
4. 고객 전용 setup link 발급
5. 플랫폼 선택
   - 배민
   - 쿠팡
   - 둘 다
6. 센터/상점 정보 입력
7. 전송 채널 선택
   - Telegram
   - KakaoTalk
8. 플랫폼 인증
   - 배민: 사람이 휴대폰 인증
   - 쿠팡: Gmail OAuth + 필요 시 자동 2FA
9. 테스트 수집
10. 테스트 메시지 전송
11. 고객 확인
12. 운영 활성화
```

## Telegram 자동 등록

```text
1. 고객이 봇을 그룹에 추가
2. /register ABC123 입력
3. 서버가 chat_id/topic_id 저장
4. 테스트 메시지 발송
5. 고객 확인
```

## KakaoTalk 등록

```text
1. 고객이 서비스용 카카오 계정을 단톡방에 초대
2. 방 이름 고유화
3. 등록코드 입력
4. Kakao Sender Agent가 방 검색
5. 테스트 메시지 전송
6. 고객 확인
```

## 결제/구독 자동화

구독 상태에 따라 job 실행을 제어해야 합니다.

```text
payment_active = true
 → 정상 실행

payment_failed + grace_period
 → 경고 표시, 알림 발송

payment_failed + grace_period_expired
 → 신규 수집 중지
 → 기존 설정 보존
 → 결제 복구 시 재개
```

---

# 11. 운영 대시보드에 반드시 있어야 하는 것

관리자 화면에는 고객 목록만 있으면 부족합니다. 최소한 아래 상태가 보여야 합니다.

| 항목                | 설명                                |
| ----------------- | --------------------------------- |
| 마지막 수집 성공 시각      | 고객별 장애 판단                         |
| 마지막 전송 성공 시각      | 메시지 정상 여부                         |
| 현재 상태             | ACTIVE, AUTH_REQUIRED, DEGRADED 등 |
| 플랫폼               | 배민/쿠팡                             |
| worker 배정         | 어느 PC/서버에서 돌고 있는지                 |
| Chrome profile 상태 | 실행 중/종료/오류                        |
| 로그인 상태            | 정상/재인증 필요/CAPTCHA                 |
| Gmail token 상태    | 정상/재승인 필요                         |
| Kakao queue lag   | 카톡 전송 밀림 여부                       |
| 최근 오류             | 사람이 바로 조치 가능해야 함                  |
| 앱/agent 버전        | 업데이트 관리                           |
| 메시지 중복방지 상태       | 마지막 hash, 마지막 snapshot            |

알림 기준 예시는 다음처럼 잡으면 됩니다.

```text
마지막 성공 > 수집주기 × 2
 → warning

마지막 성공 > 수집주기 × 4
 → critical

AUTH_REQUIRED 발생
 → 즉시 운영자/고객에게 인증 요청

Kakao queue lag > 120초
 → sender 증설 또는 전송 간격 분산

같은 platform parser 오류 다수 발생
 → global circuit breaker
```

---

# 12. 카카오톡 다수 채팅방 운영 가이드

카카오톡은 “채팅방을 많이 띄워놓는다”가 아니라 **전송 큐를 운영한다**는 개념으로 바꿔야 합니다.

## 구조

```text
Central Dispatch Queue
   ↓
Kakao Sender Queue
   ↓
Kakao Sender Agent 1
   - Kakao account A
   - Windows session A
   - 직렬 전송

Kakao Sender Agent 2
   - Kakao account B
   - Windows session B
   - 직렬 전송
```

## 왜 직렬화가 필요한가

카카오톡 PC UI 자동화는 보통 다음 자원을 공유합니다.

* 창 포커스
* 클립보드
* 키보드 입력
* 마우스/윈도우 핸들
* 검색창
* 현재 선택된 채팅방

따라서 한 Windows session에서 동시에 여러 카카오 전송을 하면 오발송 위험이 큽니다. 현재 코드도 카카오 전송을 전역 lock으로 직렬화하는 구조입니다. 

## 카카오톡 확장 공식

대략 이렇게 봐야 합니다.

```text
1개 sender의 시간당 전송 가능량
≈ 3600초 ÷ 평균 전송 소요초
```

예를 들어 방 검색, 붙여넣기, 전송, 확인까지 평균 5초라면 이론상 시간당 720건입니다. 하지만 실제로는 UI 실패, 재시도, PC 성능, 알림창, 중복 방명 때문에 훨씬 낮게 잡아야 합니다. 그래서 카카오톡 대량 전송은 반드시 **측정 기반**으로 sender 수를 늘려야 합니다.

## 카카오톡 운영 규칙

* 같은 이름의 방 허용 금지
* 방 이름에 고객/센터 식별자 포함
* 등록코드 기반 검증
* 테스트 메시지 확인 후 활성화
* 전송 전후 로그 저장
* 실패 시 자동 재시도 횟수 제한
* 반복 실패 시 사람 조치 상태로 전환
* 고객별 전용 sender 계정 옵션 제공

---

# 13. 추천 기술 스택

## 1차 판매용 MVP

```text
Backend:
 - Python FastAPI

DB:
 - PostgreSQL

Queue / Lock:
 - Redis

Worker:
 - Python worker
 - Playwright
 - 현재 parser/message renderer 재사용

Admin:
 - 간단한 React/Next.js 또는 서버렌더링 템플릿

Agent:
 - Python Windows tray app 또는 console agent
 - Kakao sender는 Windows interactive session에서 실행

Logging:
 - structured JSON log
 - Sentry 또는 OpenTelemetry
 - 파일 로그 rotation
```

## 나중에 확장

```text
- Kubernetes 또는 Nomad
- managed PostgreSQL
- managed Redis
- object storage for screenshots/sanitized HTML
- Terraform
- Prometheus/Grafana
- canary deployment
- worker auto-scaling
```

초기부터 Kubernetes로 가면 개발 속도가 느려질 수 있습니다. 먼저 **모듈러 모놀리스 + 큐 + agent**로 가고, 고객이 늘면 worker만 분리하는 게 좋습니다.

---

# 14. 단계별 구현 로드맵

## 0단계: 지금 당장 해야 할 안정화

목표는 “고객이 늘어나기 전에 내부 모델을 고치는 것”입니다.

작업:

* `customer_id` 추가
* `customer_name` 추가
* `monitoring_target_id` 추가
* `state_subdir`를 `crawlingN`이 아니라 ID 기준으로 변경
* 설정 파일 atomic write
* 로그 rotation
* secret redaction
* 카카오톡 전송 큐 도입
* 텔레그램/카카오 전송 결과 로그 분리
* Chrome profile manager 분리
* 현재 탭 UI는 유지하더라도 내부는 고객 ID 기반으로 변경

## 1단계: 수집과 전송 분리

목표는 fan-out 기반 구조입니다.

작업:

* `CrawlJob` 생성
* `Snapshot` 저장
* `Message` 저장
* `DeliveryRule` 생성
* `TelegramDispatchJob` 생성
* `KakaoDispatchJob` 생성
* 전송 dedup key 도입
* 같은 snapshot을 여러 채팅방에 전송 가능하게 변경

이 단계가 끝나야 “각 채팅방에 뿌려주는 구독 서비스”가 됩니다.

## 2단계: 탭 UI 제거

목표는 운영자가 고객 100명을 볼 수 있는 화면입니다.

작업:

* 탭 UI → 고객 목록 UI
* 검색/필터
* 고객 상세 설정
* 상태 배지
* 최근 오류 표시
* 고객별 시작/중지
* 일괄 시작/중지
* 인증 필요 고객 필터
* Kakao queue 상태 표시

## 3단계: 중앙 서버 도입

목표는 판매용 운영 기반입니다.

작업:

* FastAPI 서버
* PostgreSQL schema
* Redis queue
* admin login
* tenant/subscription 모델
* agent 등록 코드
* agent heartbeat
* job assignment
* status report API
* secret reference 관리
* Telegram webhook 전환

## 4단계: 로컬/작업 노드 agent화

목표는 Chrome과 카카오톡을 중앙 서버에서 직접 품지 않고, 작업 노드가 담당하게 하는 것입니다.

작업:

* agent installer
* agent registration
* worker capacity 설정
* Chrome profile 자동 생성
* CDP 포트 자동 배정
* browser health check
* Kakao sender health check
* auto restart
* remote config pull
* version report

## 5단계: 인증 자동화/재인증 UX

작업:

* 배민 `AUTH_REQUIRED` 감지
* 고객/운영자에게 인증 요청
* 인증 완료 자동 감지
* 쿠팡 Gmail OAuth onboarding
* Gmail token 고객별 분리
* mailbox lock
* CAPTCHA 감지
* Gmail 재승인 플로우
* 인증 실패 사유 분류

## 6단계: 대량 운영 안정화

작업:

* 스케줄 jitter
* exponential backoff
* platform circuit breaker
* parser canary
* worker sharding
* queue lag autoscaling
* 장애 알림
* version rollout
* customer impact report
* SLA 리포트

---

# 15. 단기 장비/서버 선택안

## 지금 고객이 10~30개 대상이라면

새 클라우드 이전보다 다음이 낫습니다.

```text
현재 PC 개선
+
백업 작업 PC 1대
+
원격접속/자동재시작
+
로그 rotation
+
고객 ID 기반 리팩토링
```

## 30~100개 대상이 곧 보인다면

고성능 작업 서버 1대를 추가하세요.

권장 방향:

```text
Windows 작업 서버
 - CPU: 중상급 다코어
 - RAM: 최소 64GB, 가능하면 128GB
 - NVMe SSD
 - 유선 인터넷
 - UPS
 - 자동 로그인/자동 재시작 정책
 - 원격접속 보안
```

GPU는 거의 필요 없습니다. 병목은 GPU가 아니라 Chrome 메모리, 브라우저 안정성, 카카오톡 UI 자동화, 로그인 세션입니다.

동시에 클라우드에는 작은 중앙 서버를 둡니다.

```text
Cloud Control Plane
 - 2~4 vCPU
 - 4~8GB RAM
 - PostgreSQL
 - Redis
 - Admin API
 - Telegram webhook
```

## 100개 이상이면

고성능 서버 한 대가 아니라 작업 노드 풀로 가야 합니다.

```text
worker-001: 배민/쿠팡 Chrome 30~50 targets
worker-002: 배민/쿠팡 Chrome 30~50 targets
kakao-001: Kakao sender account A
kakao-002: Kakao sender account B
```

정확한 수용량은 실측해야 합니다. 특히 Chrome 1개 profile의 장시간 메모리 사용량, 평균 수집 시간, 카카오톡 평균 전송 시간, 로그인 만료 빈도를 측정해야 합니다.

---

# 16. 가장 큰 리스크와 대응책

| 리스크               |   심각도 | 대응                                                |
| ----------------- | ----: | ------------------------------------------------- |
| 배민 휴대폰 인증         |    높음 | 완전 자동화 포기, 재인증 UX 설계                              |
| 카카오톡 UI 자동화       | 매우 높음 | 별도 sender pool, 직렬 queue, 고유 방명, 테스트 인증           |
| 배민/쿠팡 화면 변경       | 매우 높음 | parser canary, circuit breaker, sanitized HTML 저장 |
| 고객별 Chrome 세션 폭증  |    높음 | browser session manager, worker sharding          |
| 쿠팡 Gmail token 혼선 |    높음 | mailbox별 token/lock/query 분리                      |
| 평문 secret 저장      |    높음 | secret store, OS credential store, 로그 redaction   |
| 단일 서버 장애          |    높음 | 중앙 서버/작업 노드 분리, backup node                       |
| 중복 메시지 발송         | 중간~높음 | idempotency key, DeliveryLog                      |
| 결제 실패 고객 계속 실행    |    중간 | subscription 상태와 job scheduler 연동                 |
| 약관/계정 위임 이슈       |    높음 | 출시 전 약관·동의서·보안정책 검토                               |

---

# 17. 제가 제안하는 최종 방향

가장 효율적인 방향은 다음입니다.

```text
단기:
현재 프로그램을 customer_id 기반으로 정리하고,
수집/전송을 분리하고,
카카오톡 전송 큐를 만든다.

중기:
클라우드 중앙 서버를 만들고,
현재 PC 또는 고성능 서버를 worker agent로 붙인다.

장기:
작업 노드를 여러 대로 늘리고,
Telegram은 중앙 webhook으로,
Kakao는 Windows sender pool로,
배민은 재인증 UX로,
쿠팡은 Gmail OAuth 자동복구로 운영한다.
```

한 문장으로 요약하면:

> **클라우드는 “두뇌”, 작업 서버/PC는 “손발”, 카카오톡은 “별도 전송 공장”, 배민 인증은 “사람 개입형 UX”, 쿠팡 인증은 “Gmail token 분리형 자동복구”로 설계해야 합니다.**

지금 바로 고성능 서버부터 사기보다, 먼저 **고객 ID 모델, 작업 큐, 수집/전송 분리, agent 구조**를 잡는 것이 더 중요합니다. 이 구조가 잡히면 그다음에는 현재 PC든, 고성능 서버든, 클라우드 VM이든 같은 worker로 붙일 수 있습니다.

[1]: https://aws.amazon.com/ec2/pricing/on-demand/ "EC2 On-Demand Instance Pricing"
[2]: https://developers.google.com/workspace/gmail/api/auth/scopes "Choose Gmail API scopes  |  Google for Developers"
[3]: https://core.telegram.org/bots/api "Telegram Bot API"
[4]: https://cloud.google.com/secret-manager/docs/overview "Secret Manager overview  |  Google Cloud Documentation"
