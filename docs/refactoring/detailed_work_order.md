**배달 실적봇 판매형 전환  
상세 작업 지시서**

**Rider Result Monitoring / rider_result_mornitoring**

작성일: 2026-06-12 | 버전: v1.0 | 용도: 개발 착수·외주 전달·내부 운영 기준

| **구분**        | **결정**                                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 최종 운영 방향  | 클라우드 중앙 서버 + 운영자 보유 일반 PC를 1차 작업 노드로 사용한다. 고성능 서버는 즉시 구매하지 않고, 지표 기준을 넘을 때 증설한다. |
| 현재 PC 역할    | Windows 로컬 에이전트 1호. Chrome 프로필, 배민/쿠팡 로그인 세션, 카카오톡 PC 앱 전송을 담당한다.                                     |
| 클라우드 역할   | 고객/구독/설정/상태/로그/작업 큐/텔레그램 webhook/관리자 대시보드/secret reference를 담당한다.                                       |
| 가장 먼저 할 일 | 탭 기반 구조를 customer_id/monitoring_target_id 기반으로 바꾸고, 수집과 전송을 분리한다.                                             |
| 절대 하지 말 것 | 탭을 100개로 늘리는 방식, 배민 휴대폰 인증 우회 시도, 카카오톡 동시 UI 전송, Gmail token 공유, 평문 secret 저장.                     |

# **목차**

- 문서 목적과 적용 범위
- 현재 구조 진단과 리팩토링 원칙
- 최종 아키텍처 결정
- 클라우드·일반 PC·서버 구매 운영 지시
- 개발 작업 단계 요약
- 상세 WBS 작업 지시
- 중앙 서버 작업 지시
- 로컬 에이전트 작업 지시
- 플랫폼별 인증 지시: 배민·쿠팡
- 텔레그램·카카오톡 전송 지시
- 고객 유입·구독·온보딩 자동화 지시
- DB 모델·API 명세
- 보안·로그·모니터링 지시
- 마이그레이션·배포·테스트 지시
- 고성능 서버 구매 조건과 권장 사양
- 완료 기준과 금지사항

# **1\. 문서 목적과 적용 범위**

이 문서는 현재 Python Tkinter 기반 로컬 자동화 프로그램을 판매용 다고객 구독 시스템으로 전환하기 위한 상세 작업 지시서다. 개발자는 이 문서를 기준으로 백로그를 생성하고, 단계별 산출물·완료 기준·테스트 기준을 맞춰야 한다.

현재 프로젝트는 중앙 서버형 SaaS가 아니라 운영자 PC에서 실행되는 데스크톱 자동화 도구이며, Tkinter UI의 크롤링1~9 탭이 사실상 고객 또는 계정 하나처럼 동작한다. 배민/쿠팡 공식 API가 아니라 로그인된 Chrome 화면을 CDP/Playwright로 읽고, 텔레그램 또는 카카오톡 PC 앱으로 메시지를 보내는 구조다. 이 전제는 모든 설계와 개발 지시의 기준이다.

## **1.1 지시 대상**

- 기존 저장소: rider_result_mornitoring / Python 기반 rider_crawl 패키지
- 기존 기능: 배민 크롤링, 쿠팡 peak-dashboard 크롤링, 메시지 렌더링, 텔레그램 전송, 카카오톡 PC UI 전송, 쿠팡 Gmail 2FA 일부
- 신규 기능: 중앙 서버, 관리자 대시보드, 고객/구독 DB, 작업 큐, 로컬 에이전트, 고객 온보딩, 인증 상태 관리, 로그/모니터링
- 운영 전제: 현재 고성능 서버 없음. 일반 Windows PC를 작업 노드 1호로 사용한다. 클라우드는 신규 구축 가능하다.

## **1.2 핵심 제약**

| **제약**         | **작업 지시**                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------- |
| 배민 휴대폰 인증 | 완전 자동 로그인을 전제로 설계하지 말 것. 인증 필요 감지, 로그인 화면 자동 오픈, 완료 감지, 재인증 알림을 구현한다. |
| 쿠팡 이메일 인증 | Gmail API 기반 자동복구를 유지하되 Gmail token은 고객별/메일함별로 분리한다.                                        |
| 카카오톡 PC 앱   | UI 자동화이므로 한 Windows 세션에서 직렬 전송만 허용한다. 여러 방을 계속 띄워두는 방식으로 확장하지 않는다.         |
| Chrome 프로필    | 고객/플랫폼/대상별로 분리한다. 같은 User Data Directory를 동시에 여러 브라우저에서 사용하지 않는다.                 |
| 현재 PC 성능     | 1차 MVP에서는 일반 PC를 사용하되, CPU/RAM/카카오 queue lag 기준으로 dedicated 작업 PC 구매를 결정한다.              |

# **2\. 현재 구조 진단과 리팩토링 원칙**

## **2.1 현재 구조에서 재사용할 것**

| **재사용 영역**            | **이유**                              | **필요 조치**                                                      |
| -------------------------- | ------------------------------------- | ------------------------------------------------------------------ |
| 배민 parser/crawler        | 화면에서 실적 데이터를 뽑는 핵심 자산 | 입출력 모델을 표준 Snapshot으로 감싸고 테스트 fixture를 유지한다.  |
| 쿠팡 peak-dashboard parser | 쿠팡 실적 수집 핵심 자산              | 상점/센터명 검증과 parser version 기록을 추가한다.                 |
| 메시지 renderer            | 현재 실적 메시지 포맷 재사용 가능     | template_version, tenant별 템플릿, rendering 결과 저장을 추가한다. |
| 텔레그램 sender            | API 기반이라 중앙화 가능              | getUpdates poller를 폐기하고 webhook 기반으로 전환한다.            |
| 쿠팡 Gmail 2FA             | 자동복구 가치가 큼                    | 고객별 mailbox lock, token 분리, restricted scope 정책을 추가한다. |
| 기존 테스트                | parser/renderer 회귀 방지 자산        | 신규 domain/service 테스트와 통합한다.                             |

## **2.2 버리거나 축소할 것**

| **대상**                      | **왜 문제인가**                                      | **대체 구조**                                        |
| ----------------------------- | ---------------------------------------------------- | ---------------------------------------------------- |
| 크롤링1~9 탭 모델             | 고객 ID가 없고 순번이 상태 식별자가 된다.            | Tenant/Customer/MonitoringTarget 모델                |
| 탭별 scheduler thread         | 고객 증가 시 thread와 retry 폭증                     | 중앙 scheduler + job queue + agent worker            |
| 크롤링과 전송이 묶인 run_once | 같은 데이터를 여러 채팅방에 뿌릴 때 중복 크롤링 발생 | CrawlJob → Snapshot → Message → DispatchJob 분리     |
| 평문 JSON secret              | 토큰/비밀번호 노출 위험                              | Secret Manager 또는 Windows Credential Manager/DPAPI |
| 카카오 즉시 전송              | UI 포커스와 클립보드 충돌                            | KakaoSendJob queue + sender lock                     |

## **2.3 절대 원칙**

- 고객은 반드시 customer_id 또는 tenant_id로 식별한다. crawling1, crawling2 같은 순번을 운영 식별자로 쓰지 않는다.
- 수집과 전송은 반드시 분리한다. Chrome 크롤링은 한 번만 수행하고, 메시지는 여러 채널로 fan-out한다.
- 배민 인증은 자동 우회가 아니라 사용자 조치 상태 머신으로 처리한다.
- 카카오톡 전송은 동일 Windows 세션에서 동시에 실행하지 않는다.
- Gmail OAuth token, 텔레그램 bot token, 쿠팡 비밀번호, 인증번호는 로그에 절대 남기지 않는다.
- 운영자 PC가 꺼져도 중앙 서버에는 고객 상태가 남아야 하며, 다음 실행 시 복구 가능해야 한다.

# **3\. 최종 아키텍처 결정**

최종 구조는 "클라우드 중앙 서버 + 운영자 PC 로컬 에이전트 + 필요 시 작업 PC 추가"다. 클라우드가 모든 Chrome과 카카오톡을 직접 띄우는 구조는 채택하지 않는다. 카카오톡 PC 앱 UI 자동화와 배민 휴대폰 인증 때문에 실제 브라우저/카카오톡 실행은 운영자 소유 Windows 작업 노드에서 처리하는 편이 안정적이다.

\[관리자/운영자\]  
↓ 웹 대시보드  
\[Cloud Control Plane\]  
\- 고객/구독/설정 DB  
\- 작업 스케줄러 및 큐  
\- 상태/로그/알림  
\- Telegram webhook 및 API 전송  
\- Secret reference 관리  
↓ outbound polling / HTTPS  
\[Windows Local Agent #1: 현재 일반 PC\]  
\- Chrome profile 실행/관리  
\- 배민/쿠팡 화면 수집  
\- 쿠팡 Gmail 2FA 처리  
\- KakaoTalk PC 앱 전송  
\- heartbeat/status/log 보고  
↓  
\[Telegram 그룹/토픽\] \[KakaoTalk 단체방\]

## **3.1 1차 목표 아키텍처**

| **구성요소**   | **1차 구현 방식**                                             | **장기 전환**                                          |
| -------------- | ------------------------------------------------------------- | ------------------------------------------------------ |
| API/Admin 서버 | AWS EC2 Linux + Docker Compose                                | ECS/Fargate 또는 Kubernetes로 이전 가능하게 컨테이너화 |
| DB             | Amazon RDS PostgreSQL 권장                                    | Multi-AZ, read replica, PITR 확장                      |
| 작업 큐        | 초기: PostgreSQL job table 또는 Redis. 인터페이스 추상화 필수 | SQS FIFO/Standard 또는 managed Redis로 교체            |
| Secret         | AWS Secrets Manager + Agent 로컬 DPAPI                        | 전면 Secret Manager + IAM least privilege              |
| 로그/모니터링  | CloudWatch Logs/Alarms + 앱 이벤트 DB                         | OpenTelemetry/Sentry/Grafana 추가                      |
| 작업 노드      | 현재 일반 Windows PC 1대                                      | Windows 작업 PC/서버 여러 대로 agent pool 구성         |

## **3.2 책임 분리**

| **작업**              | **클라우드**             | **로컬 에이전트**                      |
| --------------------- | ------------------------ | -------------------------------------- |
| 고객/구독 관리        | 담당                     | 수신만 함                              |
| 스케줄 계산           | 담당                     | job 수신                               |
| 배민/쿠팡 Chrome 실행 | 직접 실행하지 않음       | 담당                                   |
| 배민 휴대폰 인증      | 인증 필요 상태/알림 관리 | 로그인 페이지 열기/완료 감지           |
| 쿠팡 Gmail 2FA        | 정책/상태 관리           | 실제 Gmail token 사용 및 인증번호 처리 |
| 텔레그램 전송         | 담당                     | 필요 시 fallback만                     |
| 카카오톡 전송         | job 생성/상태 관리       | 담당                                   |
| 로그/스크린샷 저장    | 수집/조회/알림           | sanitized event 업로드                 |

# **4\. 클라우드·일반 PC·서버 구매 운영 지시**

## **4.1 지금 당장 구매하지 말 것**

현재는 고성능 서버가 없으므로 1차 구현은 일반 Windows PC를 Local Agent #1로 사용한다. 단, 이 PC는 운영 중 카카오톡 UI 자동화와 Chrome 세션을 안정적으로 유지해야 하므로, 가능하면 일반 업무용 PC와 분리하여 사용한다. 초기에는 고성능 서버 구매보다 구조 분리가 우선이다.

## **4.2 클라우드는 즉시 구축**

- AWS 서울 리전(ap-northeast-2)을 기본값으로 한다. 다른 리전을 쓰더라도 IaC 변수로 분리한다.
- 도메인을 확보하고 api.&lt;domain&gt;, admin.&lt;domain&gt;으로 HTTPS를 적용한다.
- API/Admin/Scheduler는 Docker 이미지로 빌드되게 한다.
- DB는 RDS PostgreSQL을 기본 권장한다. 내부 베타 비용 절감이 최우선이면 EC2 내부 Postgres를 임시로 허용하되, 유료 고객 운영 전 RDS로 이전한다.
- Secret은 서버 코드나 DB에 평문 저장하지 말고 AWS Secrets Manager 또는 로컬 DPAPI 참조값으로 관리한다.
- Agent는 서버가 PC로 inbound 접속하지 않는다. Agent가 outbound HTTPS로 poll/heartbeat/report한다.

## **4.3 고성능 작업 PC/서버 구매 조건**

| **구매 트리거** | **측정 기준**                                              | **조치**                                     |
| --------------- | ---------------------------------------------------------- | -------------------------------------------- |
| Chrome 부하     | 피크시간 3일 연속 CPU 70% 이상 또는 RAM 80% 이상           | 전용 작업 PC 1대 구매 또는 agent 분리        |
| 대상 수 증가    | 활성 monitoring target 20~30개 초과                        | 현재 PC를 전용화하고 64GB RAM급 작업 PC 검토 |
| 카카오 지연     | Kakao queue lag 120초 초과가 반복                          | Kakao sender PC 또는 sender 계정 추가        |
| 장애 리스크     | 현재 PC가 업무용으로도 쓰여 포커스/재부팅/절전 문제가 발생 | 전용 Windows 작업 PC 구매                    |
| 운영 시간       | 매일 피크타임 무중단 운영 필요                             | UPS/유선/원격관리 가능한 전용 장비로 이전    |

## **4.4 서버 구매 시 권장 사양**

| **규모**         | **권장 장비**                                                        | **비고**                                     |
| ---------------- | -------------------------------------------------------------------- | -------------------------------------------- |
| 30개 대상 이하   | 현재 PC 또는 RAM 32GB급 Windows PC                                   | 카카오 전송이 적으면 가능                    |
| 30~100개 대상    | 8코어 이상 CPU, RAM 64GB, NVMe 1TB, Windows 11 Pro, 유선 인터넷, UPS | 전용 작업 노드 1대                           |
| 100개 대상 이상  | 12~16코어, RAM 128GB, NVMe 2TB 또는 작업 PC 2~3대                    | 한 대 고성능보다 여러 agent로 샤딩 권장      |
| 카카오 대량 전송 | 별도 Windows PC 또는 별도 Windows 세션/계정                          | 동일 세션 동시 전송 금지. sender pool로 확장 |

# **5\. 개발 작업 단계 요약**

| **단계**                 | **목표**                             | **핵심 산출물**                                                  |
| ------------------------ | ------------------------------------ | ---------------------------------------------------------------- |
| P0. 기준선 고정          | 현재 동작 보존 및 테스트 기준 수립   | 테스트 리포트, 설정 백업, 민감정보 redaction                     |
| P1. 도메인/설정 리팩토링 | 탭 순번 구조 제거 준비               | customer_id, monitoring_target_id, atomic settings, log rotation |
| P2. 수집/전송 분리       | fan-out 구조 구현                    | CrawlJob, Snapshot, Message, DispatchJob, DeliveryLog            |
| P3. 로컬 에이전트        | 현재 PC를 Worker #1로 전환           | Agent 등록, heartbeat, job polling, BrowserProfileManager        |
| P4. 중앙 서버            | 클라우드 control plane 구축          | FastAPI, DB, Admin, Scheduler, API, Telegram webhook             |
| P5. 온보딩/인증          | 고객 등록부터 테스트 발송까지 자동화 | 배민 인증 UX, 쿠팡 Gmail OAuth, Telegram/Kakao 등록              |
| P6. 운영/모니터링        | 대량 운영 안정화                     | 알림, circuit breaker, queue lag, backup, 배포 자동화            |

# **6\. 상세 WBS 작업 지시**

## **6.1 P0 - 기준선 고정 및 안전장치**

| **ID** | **작업 지시**                                                                                                 | **완료 기준**                                                    |
| ------ | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| P0-01  | 현재 main branch 또는 배포 branch를 태그한다. 예: baseline-local-ui-20260612.                                 | 태그와 백업 zip이 존재한다.                                      |
| P0-02  | runtime/state/ui_settings.json, config.json, .env.example 구조를 문서화한다. 실제 민감값은 문서화하지 않는다. | 민감값이 제거된 설정 샘플이 docs에 저장된다.                     |
| P0-03  | pytest 전체를 실행하고 실패 테스트를 분류한다.                                                                | 테스트 리포트가 docs/qa/에 저장된다.                             |
| P0-04  | 로그에 token/password/OTP가 찍히지 않는지 점검하고 redaction utility를 추가한다.                              | redaction unit test 통과                                         |
| P0-05  | 현재 2개 활성 탭을 기준으로 수동 회귀 시나리오를 만든다.                                                      | 배민 1회 실행, 쿠팡 1회 실행, 텔레그램/카카오 테스트 절차 문서화 |

## **6.2 P1 - 도메인/설정 리팩토링**

| **ID** | **작업 지시**                                                                                                                       | **완료 기준**                                           |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| P1-01  | UiSettings에 customer_id, customer_name, platform_account_id, monitoring_target_id를 추가한다. 기존 탭은 legacy_alias로만 유지한다. | 기존 ui_settings.json 자동 migration 성공               |
| P1-02  | state_subdir를 crawlingN이 아닌 targets/&lt;monitoring_target_id&gt; 기준으로 변경한다.                                             | 탭 순서를 바꿔도 last_message/run_lock이 꼬이지 않는다. |
| P1-03  | 설정 저장을 atomic write로 변경한다. 임시 파일에 쓰고 fsync 후 rename한다.                                                          | 강제 종료 테스트 후 JSON 손상 없음                      |
| P1-04  | 로그 rotation을 추가한다. run_errors.log와 kakao_diagnostics.log가 무한 증가하지 않아야 한다.                                       | 용량/날짜 기준 회전 확인                                |
| P1-05  | 플랫폼 중립 필드명 도입: center_name/display_name/target_external_id/primary_url.                                                   | 배민/쿠팡 모두 같은 Target 모델로 표현                  |
| P1-06  | secret 값과 일반 설정을 분리한다. UI JSON에는 secret_ref만 남기는 구조를 준비한다.                                                  | 평문 token/password가 신규 설정 파일에 저장되지 않음    |

## **6.3 P2 - run_once 분해 및 fan-out 구조**

| **ID** | **작업 지시**                                                                                                     | **완료 기준**                                   |
| ------ | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| P2-01  | run_once를 CrawlService, MessageRenderService, DispatchService로 분해한다.                                        | 기존 UI 1회 실행 결과가 기존과 동일             |
| P2-02  | Snapshot 모델을 정의한다. platform, target_id, collected_at, normalized_data, parser_version, quality_state 포함. | 배민/쿠팡 snapshot fixture 테스트 통과          |
| P2-03  | Message 모델을 정의한다. snapshot_id, template_version, text, text_hash 포함.                                     | 동일 snapshot에서 동일 hash 생성                |
| P2-04  | DeliveryRule을 정의한다. target_id → messenger_channel_id 매핑을 여러 개 허용한다.                                | 한 번 수집 후 2개 이상 채널 fan-out 테스트 통과 |
| P2-05  | DeliveryLog와 idempotency key를 구현한다.                                                                         | 같은 메시지를 재실행해도 중복 발송되지 않음     |
| P2-06  | 전송 실패를 수집 실패와 분리한다.                                                                                 | 크롤링 성공/카카오 실패가 각각 별도 상태로 보임 |

## **6.4 P3 - Local Agent 구현**

| **ID** | **작업 지시**                                                                                  | **완료 기준**                                   |
| ------ | ---------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| P3-01  | rider_agent 패키지를 만든다. 기존 crawler/parser/renderer를 import하여 사용한다.               | python -m rider_agent 실행 가능                 |
| P3-02  | Agent 등록 코드 입력 기능을 구현한다. 등록 후 agent_id와 token을 안전 저장한다.                | 등록 코드 1회 사용 후 서버에 agent 표시         |
| P3-03  | Heartbeat를 30~60초 간격으로 서버에 보고한다.                                                  | Admin에서 online/offline 상태 확인              |
| P3-04  | Job polling/claim/complete 루프를 구현한다. inbound 포트 오픈 없이 HTTPS outbound만 사용한다.  | 방화벽 뒤 PC에서도 작업 수신 가능               |
| P3-05  | BrowserProfileManager를 구현한다. profile 생성, CDP port 배정, Chrome 실행, health check 담당. | 동일 profile/port 중복 사용 방지                |
| P3-06  | KakaoSenderWorker를 별도 큐 worker로 분리한다.                                                 | 동일 Windows 세션에서 카카오 전송이 직렬 처리됨 |
| P3-07  | Windows 시작 프로그램 또는 작업 스케줄러 등록을 구현한다.                                      | PC 재부팅 후 사용자 로그인 시 agent 자동 실행   |

## **6.5 P4 - 중앙 서버 구현**

| **ID** | **작업 지시**                                                                                   | **완료 기준**                             |
| ------ | ----------------------------------------------------------------------------------------------- | ----------------------------------------- |
| P4-01  | FastAPI 기반 backend를 만든다. /health, /version, /metrics 기본 제공.                           | Docker container로 실행 가능              |
| P4-02  | PostgreSQL schema와 Alembic migration을 작성한다.                                               | 빈 DB에서 migration 후 모든 테이블 생성   |
| P4-03  | Admin UI를 만든다. 고객 목록, target 목록, agent 상태, 최근 오류, 인증 필요 필터를 포함한다.    | 운영자가 현재 상태를 웹에서 확인 가능     |
| P4-04  | Scheduler를 구현한다. interval + jitter로 CrawlJob을 생성한다.                                  | 동일 시각에 모든 고객이 몰리지 않음       |
| P4-05  | Job queue abstraction을 만든다. 초기 구현은 DB queue여도 되지만 SQS/Redis로 교체 가능해야 한다. | QueueBackend 인터페이스 테스트 통과       |
| P4-06  | Telegram webhook endpoint를 만든다. secret header 검증을 구현한다.                              | getUpdates를 사용하지 않고 /register 동작 |
| P4-07  | Audit log를 구현한다. 누가 고객/secret/channel 설정을 변경했는지 기록한다.                      | Admin 작업 이력 조회 가능                 |

# **7\. 중앙 서버 작업 지시**

## **7.1 Cloud MVP 구성**

| **항목**       | **지시**                                                                                                                                                         |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compute        | AWS EC2 Ubuntu 24.04 LTS 또는 현재 안정 LTS에 Docker Compose 설치. api/admin/scheduler 컨테이너 실행. 추후 ECS/Fargate 이전 가능하게 Dockerfile과 환경변수 분리. |
| Database       | Amazon RDS PostgreSQL. 최소 retention 7일 이상. 운영 전 point-in-time recovery와 수동 snapshot 정책 설정.                                                        |
| Object Storage | S3 bucket: sanitized screenshot, sanitized HTML fixture, export 파일 저장. 원본 민감 HTML 저장 금지.                                                             |
| Secret         | AWS Secrets Manager: Telegram bot token, 서버 JWT signing key, DB password, 외부 API key 저장. 고객 Gmail token은 MVP에서 로컬 agent 저장 우선.                  |
| Network        | HTTPS only. Admin은 관리자 계정 2FA 또는 VPN/IP allowlist 적용. Agent API는 token 기반 인증.                                                                     |
| Monitoring     | CloudWatch Logs, alarms, disk/CPU, API error rate, agent offline count, queue lag metric 수집.                                                                   |
| Backup         | RDS automated backup + S3 lifecycle + infra config 백업. 복구 리허설 절차 작성.                                                                                  |

## **7.2 서버 프로세스 분리**

backend-api:  
\- Admin API  
\- Agent API  
\- Telegram webhook  
\- Auth/session/status API  
<br/>scheduler:  
\- MonitoringTarget interval 계산  
\- jitter 적용  
\- CrawlJob 생성  
\- subscription 상태에 따른 job 생성 차단  
<br/>telegram-dispatcher:  
\- Message -> Telegram sendMessage  
\- retry/backoff  
\- DeliveryLog 기록  
<br/>admin-ui:  
\- 고객/대상/채널/agent 상태 화면  
\- 인증 필요 고객 필터  
\- 테스트 발송/수동 재실행 버튼

## **7.3 Scheduler 지시**

- monitoring_targets.next_run_at 기준으로 due target을 조회한다.
- 정각 몰림을 막기 위해 target 생성 시 0~interval 범위의 deterministic jitter를 부여한다.
- subscription이 inactive/suspended이면 신규 CrawlJob을 생성하지 않는다.
- 플랫폼 global circuit breaker가 열려 있으면 해당 플랫폼 신규 CrawlJob을 만들지 않는다.
- agent capacity와 target affinity를 고려하여 job을 배정한다.
- 실패 시 즉시 5초 무한 재시도 금지. error_code별 backoff 정책을 적용한다.

# **8\. 로컬 에이전트 작업 지시**

## **8.1 설치/실행 방식**

MVP에서는 현재 일반 Windows PC에 Local Agent #1을 설치한다. 카카오톡 PC 앱 제어는 interactive desktop이 필요하므로 Windows 서비스 Session 0 방식으로만 실행하면 안 된다. Agent는 사용자 로그인 후 실행되는 tray app 또는 console app으로 시작하고, 작업 스케줄러로 자동 시작한다.

C:\\RiderBot\\  
agent\\  
rider_agent.exe  
version.json  
data\\  
agent_config.json # secret 값 없이 ref/agent token만  
profiles\\  
&lt;tenant_id&gt;\\&lt;target_id&gt;\\ # Chrome User Data Dir  
logs\\  
agent.log  
kakao_sender.log  
browser_manager.log  
secrets\\  
\# 가능하면 파일 저장 금지. 임시 베타 시에만 DPAPI 암호화 파일 허용.

## **8.2 Agent loop**

startup:  
load_local_agent_identity()  
validate_agent_token()  
start_heartbeat_thread()  
start_kakao_sender_worker_if_enabled()  
<br/>main_loop:  
while running:  
config = pull_remote_config()  
job = claim_next_job(capabilities, capacity)  
if job is None:  
sleep(short_poll_interval)  
continue  
emit_job_started(job)  
result = execute_job(job)  
upload_sanitized_artifacts_if_any(result)  
complete_job(job, result)

## **8.3 BrowserProfileManager 요구사항**

| **기능**     | **상세 요구사항**                                                                                                 |
| ------------ | ----------------------------------------------------------------------------------------------------------------- |
| 프로필 생성  | target_id별 독립 User Data Directory 생성. 기본 Chrome 프로필 재사용 금지.                                        |
| 포트 배정    | 사용 가능한 127.0.0.1:&lt;port&gt;를 자동 배정. 중앙 서버에 profile_id/cdp_port 상태 보고.                        |
| Chrome 실행  | 필요 시 --remote-debugging-port, --user-data-dir 인자로 실행. 수집 후 계속 유지/종료 정책은 target 설정으로 제어. |
| Health check | CDP endpoint 응답, 로그인 URL 상태, page count, memory 추정치를 보고.                                             |
| 중복 방지    | 같은 user_data_dir를 동시에 두 Chrome에서 열지 않는다. lock file + process check 적용.                            |
| 복구         | CDP unavailable이면 Chrome 재실행. 로그인 필요이면 AUTH_REQUIRED로 전환하고 무한 재시도 금지.                     |

## **8.4 Agent job types**

| **Job Type**       | **실행 위치** | **설명**                                                       |
| ------------------ | ------------- | -------------------------------------------------------------- |
| CRAWL_BAEMIN       | Agent         | 배민 Chrome profile로 달성현황 수집 후 Snapshot 업로드         |
| CRAWL_COUPANG      | Agent         | 쿠팡 peak-dashboard 수집. 로그인 만료 시 Gmail 2FA 복구 시도   |
| AUTH_CHECK         | Agent         | 로그인 상태만 확인하고 AUTH_REQUIRED/ACTIVE 보고               |
| OPEN_AUTH_BROWSER  | Agent         | 운영자가 인증할 수 있도록 해당 profile Chrome을 열고 상태 보고 |
| KAKAO_SEND         | Agent         | 중앙 서버가 만든 message text를 카카오톡 방에 직렬 전송        |
| CAPTURE_DIAGNOSTIC | Agent         | 오류 상황에서 sanitized screenshot/log를 업로드                |

# **9\. 플랫폼별 인증 작업 지시**

## **9.1 배민 인증**

배민은 휴대폰 인증이 필요하므로 자동 로그인 구현을 목표로 잡지 않는다. 목표는 인증 필요 상태를 정확히 감지하고, 운영자가 인증을 완료한 뒤 자동으로 수집을 재개하는 UX다.

BAEMIN_AUTH_STATE:  
UNKNOWN  
ACTIVE  
AUTH_REQUIRED  
USER_ACTION_PENDING  
AUTH_VERIFIED  
CENTER_MISMATCH  
BLOCKED_OR_CAPTCHA

| **작업**         | **지시**                                                                                                        |
| ---------------- | --------------------------------------------------------------------------------------------------------------- |
| 로그인 필요 감지 | 로그인 페이지, 휴대폰 인증 화면, 세션 만료 화면을 구분하는 detector를 작성한다.                                 |
| 인증 시작        | Admin에서 "인증 브라우저 열기" 클릭 시 Agent가 해당 Chrome profile을 연다.                                      |
| 사람 개입        | 운영자 또는 고객이 휴대폰 인증을 완료한다. 인증번호를 프로그램이 우회하거나 자동 취득하는 기능은 만들지 않는다. |
| 완료 감지        | Agent가 target URL 진입, 센터명/센터ID 일치, 핵심 요소 표시를 확인하면 ACTIVE로 전환한다.                       |
| 재인증 알림      | AUTH_REQUIRED 발생 즉시 Admin 배지, 텔레그램/내부 알림, 고객별 액션 로그를 생성한다.                            |

## **9.2 쿠팡 Gmail 2FA**

쿠팡은 Gmail 인증 자동복구를 유지하되, 고객별 Gmail token 분리를 필수로 한다. 같은 Gmail mailbox를 여러 쿠팡 계정이 공유할 수 있으므로 mailbox-level lock이 필요하다.

| **작업**         | **지시**                                                                                                                                       |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| OAuth onboarding | 고객/운영자가 Gmail OAuth를 1회 승인한다. token은 mailbox_id 기준으로 저장한다.                                                                |
| Token 저장       | MVP에서는 agent 로컬 DPAPI/Windows Credential Manager 저장을 우선한다. 중앙 서버에는 token 값이 아니라 secret_ref/local_mailbox_id만 저장한다. |
| 메일 검색        | 인증 요청 시각 이후 도착한 메일만 조회한다. from/subject/query/customer 조건을 조합한다.                                                       |
| Mailbox lock     | 같은 mailbox_id로 동시에 두 쿠팡 인증을 요청하지 않는다. lock timeout과 release를 구현한다.                                                    |
| CAPTCHA 처리     | CAPTCHA나 비정상 로그인 화면은 자동복구 중지 후 USER_ACTION_REQUIRED로 전환한다.                                                               |
| 재승인           | Gmail token refresh 실패/권한 철회 시 GMAIL_REAUTH_REQUIRED 상태로 전환한다.                                                                   |

# **10\. 텔레그램·카카오톡 전송 지시**

## **10.1 텔레그램**

- 텔레그램은 중앙 서버에서 직접 전송한다. Agent별 getUpdates poller를 제거한다.
- Bot token은 AWS Secrets Manager에 저장하고 DB에는 secret_ref만 저장한다.
- Webhook endpoint는 Telegram secret_token header를 검증한다.
- 등록 방식은 /register &lt;code&gt; 명령으로 구현한다. 이때 chat_id와 message_thread_id를 자동 저장한다.
- topic 그룹이면 message_thread_id를 DeliveryRule에 저장한다.
- sendMessage 실패 시 Telegram 오류 코드에 따라 retryable/non-retryable을 분리한다.

## **10.2 카카오톡**

카카오톡은 판매용 운영에서 가장 큰 리스크다. API 기반이 아니라 Windows PC 앱 UI 자동화이므로 반드시 sender queue와 직렬화를 적용한다. "수많은 채팅방을 계속 띄워놓는 방식"은 금지한다. Agent가 필요할 때 채팅방을 검색하고 전송한 뒤 결과를 보고한다.

| **기능**      | **지시**                                                                                                          |
| ------------- | ----------------------------------------------------------------------------------------------------------------- |
| Room registry | kakao_chat_name은 고유해야 한다. 같은 이름 방이 2개 이상이면 활성화 금지.                                         |
| 등록 절차     | 고객이 서비스용 카카오 계정을 단톡방에 초대하고 방 이름을 \[라이더봇\] 고객명\_센터명 형태로 맞춘다.              |
| 테스트 전송   | 활성화 전 테스트 메시지 전송과 고객 확인을 필수로 한다.                                                           |
| 전송 queue    | KAKAO_SEND job은 sender agent별 FIFO로 처리한다. 동일 세션 동시 실행 금지.                                        |
| 클립보드      | 전송 전 현재 클립보드 백업, 전송 후 복구를 시도한다. 실패해도 secret이 남지 않게 한다.                            |
| 오류 처리     | ROOM_NOT_FOUND, ROOM_AMBIGUOUS, KAKAO_NOT_LOGGED_IN, UI_TIMEOUT, CLIPBOARD_ERROR 등으로 분류한다.                 |
| 진단          | 오류 시 sanitized screenshot 또는 창 제목/검색 결과 count만 업로드한다. 메시지 전문/민감값은 로그에서 마스킹한다. |

# **11\. 고객 유입·구독·온보딩 자동화 지시**

## **11.1 고객 상태 머신**

LEAD  
\-> SIGNED_UP  
\-> PAYMENT_ACTIVE  
\-> SETUP_PENDING  
\-> PLATFORM_AUTH_PENDING  
\-> MESSENGER_VERIFY_PENDING  
\-> TEST_RUNNING  
\-> ACTIVE  
\-> DEGRADED  
\-> AUTH_REQUIRED  
\-> SUSPENDED

## **11.2 온보딩 단계**

- 관리자가 Admin에서 고객을 생성한다. tenant_id와 setup_code를 자동 발급한다.
- 요금제를 선택하고 허용 monitoring_target 수와 messenger channel 수를 설정한다.
- 플랫폼 계정을 등록한다. 배민/쿠팡을 분리하고 동일 고객이 여러 target을 가질 수 있게 한다.
- 전송 채널을 등록한다. Telegram은 /register 코드로 자동 매핑, Kakao는 방 이름 고유성 검증 후 테스트 전송한다.
- Agent에 target을 배정한다. 초기에는 현재 일반 PC의 Agent #1에 배정한다.
- 배민은 인증 브라우저를 열고 사람이 휴대폰 인증을 완료한다.
- 쿠팡은 Gmail OAuth와 이메일 2FA 자동복구 테스트를 수행한다.
- 테스트 크롤링을 실행하고 snapshot 품질을 검증한다.
- 테스트 메시지를 모든 채널에 발송한다.
- 고객 확인 후 ACTIVE로 전환한다.

## **11.3 구독 상태와 작업 실행 제어**

| **상태**             | **시스템 동작**                                                  |
| -------------------- | ---------------------------------------------------------------- |
| PAYMENT_ACTIVE       | 정상 스케줄링 및 전송                                            |
| PAYMENT_FAILED_GRACE | 수집/전송은 유지하되 Admin에 경고 표시, 고객 알림 가능           |
| SUSPENDED            | 신규 CrawlJob/DispatchJob 생성 중지. 설정과 프로필은 보존        |
| CANCELLED            | 정책에 따라 일정 기간 후 secret revoke 및 profile archive/delete |

# **12\. DB 모델·API 명세**

## **12.1 필수 DB 테이블**

| **테이블**         | **핵심 필드**                                                                        | **설명**                     |
| ------------------ | ------------------------------------------------------------------------------------ | ---------------------------- |
| tenants            | id, name, status, created_at                                                         | 구독 고객사                  |
| subscriptions      | tenant_id, plan, status, current_period_end, quotas                                  | 요금제와 사용 한도           |
| platform_accounts  | id, tenant_id, platform, label, username_ref, password_ref, auth_state               | 배민/쿠팡 로그인 계정        |
| monitoring_targets | id, tenant_id, platform_account_id, name, external_id, url, interval_minutes, status | 실제 수집 단위               |
| browser_profiles   | id, agent_id, target_id, profile_path_ref, cdp_port, state                           | Chrome profile 관리          |
| messenger_channels | id, tenant_id, messenger, telegram_chat_id, thread_id, kakao_room_name, state        | 전송 채널                    |
| delivery_rules     | id, target_id, channel_id, template_id, enabled, send_only_on_change                 | 수집 결과를 어느 방에 보낼지 |
| snapshots          | id, target_id, collected_at, normalized_json, parser_version, quality_state          | 수집 결과                    |
| messages           | id, snapshot_id, template_version, text_hash, text_redacted_preview                  | 렌더링 결과                  |
| delivery_logs      | id, message_id, channel_id, status, dedup_key, error_code, sent_at                   | 전송 이력/중복 방지          |
| agents             | id, name, machine_id, version, os, status, last_heartbeat_at, capacity_json          | 작업 노드                    |
| jobs               | id, type, target_id, agent_id, status, run_after, attempts, error_code               | 작업 큐                      |
| auth_sessions      | id, account_id, state, reason, requested_at, resolved_at                             | 인증 상태                    |
| audit_logs         | actor_id, action, target_type, target_id, diff_redacted, created_at                  | 관리자 변경 이력             |

## **12.2 Agent API**

POST /v1/agents/register  
request: { registration_code, machine_fingerprint, hostname, os, agent_version }  
response: { agent_id, agent_token, tenant_scope, config_version }  
<br/>POST /v1/agents/heartbeat  
request: { agent_id, metrics, capabilities, active_jobs, kakao_status, browser_profiles }  
response: { server_time, config_version, commands }  
<br/>POST /v1/jobs/claim  
request: { agent_id, capabilities, max_jobs }  
response: { jobs: \[ ... \] }  
<br/>POST /v1/jobs/{job_id}/events  
request: { event_type, severity, message_redacted, artifact_refs }  
<br/>POST /v1/jobs/{job_id}/complete  
request: { status, result_json, error_code, error_message_redacted, metrics }

## **12.3 Admin API/UI**

- 고객 생성/수정/중지/재개
- 플랫폼 계정 생성/인증 상태 조회/인증 브라우저 열기 command 발행
- MonitoringTarget 생성/스케줄 설정/수동 1회 실행
- MessengerChannel 등록/검증/테스트 메시지
- DeliveryRule 설정
- Agent 배정/상태/버전/용량 조회
- 최근 오류, 마지막 성공 시각, queue lag, auth_required 필터
- Audit log 조회

# **13\. 보안·로그·모니터링 지시**

## **13.1 Secret 관리**

| **Secret**         | **저장 위치**                                    | **지시**                                                                                    |
| ------------------ | ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| Telegram bot token | AWS Secrets Manager                              | DB에는 secret_ref만 저장. token 변경/폐기 절차 제공.                                        |
| 쿠팡 비밀번호      | 가능하면 AWS Secrets Manager 또는 Agent DPAPI    | Admin 화면에서 평문 재표시 금지. 변경 시 덮어쓰기만 허용.                                   |
| Gmail OAuth token  | MVP: Agent 로컬 DPAPI/Windows Credential Manager | 중앙 서버 저장은 보안 평가/정책 검토 후 진행. 고객/메일함별 분리 필수.                      |
| Agent token        | Agent 로컬 secure store                          | 분실/탈취 시 서버에서 revoke 가능해야 함.                                                   |
| Chrome profile     | Agent 로컬 디스크                                | BitLocker 권장. profile 경로는 중앙에 평문 파일시스템 경로 대신 profile_id/ref 위주로 저장. |

## **13.2 로그 redaction 규칙**

- 비밀번호, token, refresh token, authorization code, OTP, 휴대폰번호 전체, 이메일 전체는 로그에 기록하지 않는다.
- 고객명/센터명은 운영 로그에 필요하면 저장하되 외부 전송용 diagnostic artifact에는 마스킹 옵션을 둔다.
- HTML 원본 저장은 기본 금지. parser 장애 분석이 필요할 때만 sanitized HTML을 저장한다.
- 카카오톡 전송 오류 스크린샷에는 채팅방 개인정보가 포함될 수 있으므로 업로드 전 마스킹 또는 운영자 승인 절차를 둔다.

## **13.3 모니터링 지표**

| **지표**                     | **경고 기준**      | **조치**                            |
| ---------------------------- | ------------------ | ----------------------------------- |
| agent_last_heartbeat         | 2분 이상 미수신    | Agent offline 경고                  |
| target_last_success_at       | interval x 2 초과  | warning. interval x 4 초과 critical |
| auth_required_count          | 1건 이상           | 운영자 알림 및 인증 필요 목록 표시  |
| kakao_queue_lag_seconds      | 120초 초과 반복    | sender 증설 또는 전송 간격 분산     |
| crawl_error_rate_by_platform | 최근 15분 30% 초과 | platform circuit breaker 검토       |
| telegram_send_error_rate     | 최근 10분 급증     | Bot token/chat 권한 확인            |
| gmail_reauth_required_count  | 1건 이상           | Gmail 재승인 요청                   |

# **14\. 마이그레이션·배포·테스트 지시**

## **14.1 설정 마이그레이션**

- 기존 runtime/state/ui_settings.json을 백업한다.
- crawlings 배열을 읽고 활성 탭만 대상 후보로 분류한다.
- 각 활성 탭에 tenant_id, platform_account_id, monitoring_target_id를 발급한다.
- 기존 state/runtime/crawlingN 폴더는 targets/&lt;monitoring_target_id&gt;로 복사한다. 원본은 삭제하지 않는다.
- last_message hash는 신규 DeliveryLog dedup seed로 가져온다.
- 마이그레이션 후 1회 dry-run을 수행한다. 이때 실제 전송은 꺼둔다.
- 기존 렌더링 메시지와 신규 렌더링 메시지를 비교한다.
- 운영자가 승인하면 신규 DeliveryRule을 활성화한다.

## **14.2 배포 파이프라인**

- GitHub Actions 또는 동일 CI에서 lint/test/build를 수행한다.
- Backend/Admin Docker image를 빌드하고 tag를 부여한다.
- DB migration은 배포 전 백업 확인 후 실행한다.
- Agent는 version manifest 방식으로 업데이트한다. 실행 중 job이 없을 때만 업데이트하고 rollback binary를 보존한다.
- 운영 배포 전 staging tenant에서 배민/쿠팡 fake 또는 fixture 기반 smoke test를 실행한다.

## **14.3 테스트 기준**

| **테스트**     | **범위**                                                   | **완료 기준**                              |
| -------------- | ---------------------------------------------------------- | ------------------------------------------ |
| Unit           | domain, parser, renderer, dedup, redaction                 | CI에서 전부 통과                           |
| Integration    | Agent API, job lifecycle, Telegram sender mock, Gmail mock | 실패/재시도/중복방지 확인                  |
| E2E dry-run    | 기존 활성 배민/쿠팡 target 1개씩                           | 수집/렌더링/저장까지 성공, 실제 전송 없음  |
| Messenger test | Telegram test chat, Kakao test room                        | 테스트 메시지 1회 성공 및 DeliveryLog 기록 |
| Auth test      | 배민 AUTH_REQUIRED 감지, 쿠팡 Gmail 2FA fixture            | 상태 전환 정확성 확인                      |
| Load smoke     | 가짜 target 100개 스케줄링                                 | job storm 없이 jitter와 queue 동작 확인    |

# **15\. 고성능 서버 구매·증설 최종 지시**

최종 판단은 "지금 즉시 서버 구매하지 말고, cloud control plane과 agent 구조를 먼저 만든 뒤 지표가 넘으면 작업 PC를 증설"이다. 크롬과 카카오톡은 CPU만으로 해결되는 문제가 아니며, 인증 세션과 Windows GUI 안정성이 중요하다. 따라서 한 대 초고성능 서버보다 여러 안정적인 Windows 작업 노드로 분리할 수 있게 설계한다.

| **시점**                                   | **결정**                                                              |
| ------------------------------------------ | --------------------------------------------------------------------- |
| 개발/P0~P4                                 | 현재 일반 PC 사용. Cloud control plane 구축. 서버 구매 보류.          |
| 유료 고객 5~10개 또는 Kakao 유료 전송 시작 | 현재 PC를 전용 작업 PC처럼 사용. 절전/업무 사용/자동 업데이트를 통제. |
| 대상 20~30개 초과 또는 queue lag 발생      | 64GB RAM급 전용 Windows 작업 PC 구매. Agent #2로 등록.                |
| 대상 100개 이상                            | 작업 PC 여러 대로 target sharding. Kakao sender는 별도 pool로 분리.   |

# **16\. 완료 기준과 금지사항**

## **16.1 MVP 완료 기준**

- Admin 웹에서 고객, target, agent, channel, 최근 오류, 인증 필요 상태를 볼 수 있다.
- 현재 일반 PC Agent가 중앙 서버에 등록되고 heartbeat를 보고한다.
- 중앙 서버가 배민/쿠팡 CrawlJob을 생성하고 Agent가 실행 후 Snapshot을 업로드한다.
- 한 Snapshot에서 여러 DeliveryRule로 메시지를 fan-out할 수 있다.
- Telegram은 중앙 webhook/register/sendMessage로 동작한다.
- Kakao는 Agent queue에서 직렬 전송하고 DeliveryLog를 남긴다.
- 배민 인증 만료 시 AUTH_REQUIRED로 표시되고 운영자가 인증 브라우저를 열 수 있다.
- 쿠팡 Gmail token은 고객/메일함별로 분리되고, Gmail 재승인 필요 상태를 표시한다.
- Secret이 DB/로그/설정 파일에 평문으로 저장되지 않는다.
- 기존 활성 탭 2개 기능을 신규 구조에서 dry-run 및 실제 테스트 발송까지 검증한다.

## **16.2 금지사항**

- 탭을 9개에서 100개로 늘리는 방식으로 해결하지 않는다.
- 배민 휴대폰 인증을 무인 자동화/우회하는 기능을 개발하지 않는다.
- 같은 Windows 세션에서 카카오톡 전송을 병렬 실행하지 않는다.
- Gmail token을 여러 고객이 공유하게 만들지 않는다.
- token/password/OTP를 로그, 에러 메시지, screenshot, DB text field에 남기지 않는다.
- Cloud server가 운영자 PC의 Chrome CDP 포트에 직접 접속하도록 만들지 않는다. Agent outbound 방식만 사용한다.
- 사이트 구조 변경으로 parser 오류가 대량 발생할 때 무한 빠른 재시도하지 않는다.

# **17\. 참고 근거**

아래 자료를 기준으로 지시서를 작성했다. 개발 문서에는 실제 URL을 남기되, 민감값은 포함하지 않는다.

| **자료**                            | **활용 내용**                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------------- |
| Project Current State and Structure | 현재 로컬 Tkinter 앱, 탭 구조, CDP/Playwright, 배민/쿠팡, Gmail 2FA, 카카오 UI 자동화 제약 확인 |
| AWS EC2 On-Demand Pricing           | 초기 cloud compute를 장기 약정 없이 시작할 수 있다는 판단 근거                                  |
| AWS Fargate Pricing / Amazon ECS    | 장기적으로 컨테이너 운영을 서버 관리 부담 없이 확장할 수 있다는 판단 근거                       |
| Amazon RDS for PostgreSQL           | PostgreSQL 설치/업그레이드/백업/복제 등 관리형 DB 채택 근거                                     |
| AWS Secrets Manager                 | OAuth token/API key/DB credential 등을 secret으로 관리하고 rotate할 수 있다는 근거              |
| Amazon SQS                          | 작업 큐를 통해 component를 분리하고 확장할 수 있다는 근거                                       |
| Amazon CloudWatch                   | 로그, metric, alarm, dashboard 기반 운영 모니터링 근거                                          |
| Telegram Bot API                    | getUpdates와 webhook의 관계, HTTPS webhook, secret_token, topic/thread 전송 설계 근거           |
| Google Gmail API scopes             | gmail.readonly가 restricted scope이며 서버 저장/전송 시 보안 평가가 필요할 수 있다는 근거       |
| Playwright Python BrowserType docs  | connect_over_cdp, persistent context, user_data_dir 분리 제약 근거                              |