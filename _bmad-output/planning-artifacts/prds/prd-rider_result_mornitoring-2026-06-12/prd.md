---
title: "rider_result_mornitoring 리팩토링 PRD"
status: final
created: 2026-06-12
updated: 2026-06-12
---

# PRD: rider_result_mornitoring 리팩토링

## 0. 문서 목적

이 PRD는 현재 정상 동작 중인 `rider_result_mornitoring` Python/tkinter 데스크톱 자동화 앱을 판매 가능한 다고객 운영 구조로 리팩토링하기 위한 제품 요구사항을 정의한다. 대상 독자는 제품 의사결정자, 리팩토링 구현자, 운영자, 이후 아키텍처/에픽/스토리 작성자다.

이 문서는 `docs/refactoring/research.md`, `docs/refactoring/detailed_work_order.md`, `_bmad-output/project-context.md`를 입력으로 삼는다. 본문은 무엇을 만족해야 하는지와 어디까지를 MVP로 볼지를 다루고, 기술 스택, DB/API 세부, 에이전트 루프, 서버 사양 같은 구현 상세는 `addendum.md`에 둔다.

## 1. 비전

`rider_result_mornitoring`은 배민/쿠팡이츠 실적 화면을 로그인된 Chrome 세션에서 읽고, 가공된 실적 메시지를 Telegram 또는 KakaoTalk으로 보내는 운영 자동화 도구다. 현재 앱은 로컬 PC에서 잘 동작하지만, 고객과 모니터링 대상이 늘어나면 `크롤링1~9` 탭, 로컬 설정 파일, 탭별 스레드, Chrome 프로필, CDP 포트, KakaoTalk PC 자동화가 운영 병목이 된다.

이번 리팩토링의 목표는 새 제품을 처음부터 다시 만드는 것이 아니다. 기존 수집, 파싱, 메시지 렌더링, 전송, Gmail 2FA, 테스트 자산을 살리면서, 고객/대상/채널/작업/인증 상태를 ID로 추적할 수 있는 구조로 바꾸는 것이다. 수집, 메시지 생성, 전송을 분리하고, 중앙 서버가 운영 상태를 통제하며, Windows Local Agent가 Chrome과 KakaoTalk처럼 로컬 환경이 필요한 작업을 담당한다.

성공한 리팩토링은 운영자가 “어느 고객의 어느 대상이 언제 마지막으로 성공했는지, 어느 인증이 막혔는지, 어느 전송 채널이 지연되는지, 어떤 에이전트가 어떤 작업을 처리하는지”를 한곳에서 볼 수 있게 만든다. 동시에 기존 고객에게 잘못된 메시지를 보내거나 중복 발송하거나 기존 설정을 잃는 회귀를 만들지 않아야 한다.

## 2. 목표 사용자와 이해관계자

### 2.1 주요 사용자

- **운영자** — 고객, 모니터링 대상, 작업 상태, 인증 필요 상태, 전송 실패, Agent 배정을 관리한다.
- **구독 고객 또는 센터 담당자** — 플랫폼 계정 인증, 메시지 수신 채널 확인, 테스트 메시지 확인에 관여한다.
- **메시지 수신자** — Telegram 그룹/토픽 또는 KakaoTalk 채팅방에서 실적 메시지를 받는다.
- **작업 노드 관리자** — Windows PC 또는 작업 서버에서 Chrome, KakaoTalk PC 앱, Local Agent 상태를 유지한다.
- **개발자/운영 개발자** — 기존 코드 동작을 보존하면서 리팩토링, 테스트, 배포, 장애 대응을 수행한다.

### 2.2 Jobs To Be Done

- 운영자는 고객 수가 늘어나도 탭 번호가 아니라 고객/대상/채널 ID 기준으로 상태를 확인하고 싶다.
- 운영자는 장애가 난 뒤 고객이 알려주기 전에 인증 만료, 수집 실패, 전송 실패, queue 지연을 알고 싶다.
- 운영자는 한 번 수집한 실적 데이터를 여러 수신 채널로 안전하게 fan-out하고 싶다.
- 운영자는 KakaoTalk PC 자동화처럼 위험한 전송 경로를 직렬화하고 검증해서 오발송을 막고 싶다.
- 개발자는 기존 수집/파서/렌더러/전송 기능을 깨지 않으면서 구조를 단계적으로 분리하고 싶다.
- 고객은 본인의 배민/쿠팡 실적 메시지가 기존처럼 계속 도착하길 원하며, 리팩토링 중단이나 중복 발송을 원하지 않는다.

### 2.3 비사용자와 v1 경계

- 일반 배달 플랫폼 판매자용 셀프서비스 SaaS 전체를 v1에서 완성하지 않는다.
- 배민/쿠팡 공식 API 연동 제품을 만들지 않는다.
- KakaoTalk 대량 발송 플랫폼을 만들지 않는다.
- 고객이 직접 모든 설치, 인증, 결제, 장애 복구를 처리하는 완전 셀프서비스 제품은 v1 범위가 아니다. [ASSUMPTION: v1은 운영자 주도 운영 모델을 기본으로 한다.]
- MVP의 Local Agent는 운영자 소유 Windows 작업 노드 중심으로 운영한다. 고객 설치형 Agent는 post-MVP 범위다. [ASSUMPTION: MVP는 운영자 소유 Agent를 기본 배포 모델로 한다.]

### 2.4 핵심 사용자 여정

- **UJ-1. 민지가 기존 활성 탭을 ID 기반 대상 관리로 옮긴다.**
  - **Persona + context:** 민지는 운영자이며, 현재 잘 동작 중인 배민/쿠팡 활성 탭을 잃지 않고 새 구조로 옮겨야 한다.
  - **Entry state:** 기존 `ui_settings.json`, `crawlingN` 상태, Chrome 프로필, 전송 설정이 존재한다.
  - **Path:** 민지는 기존 설정을 백업하고, 탭별 설정에 고객 ID, 플랫폼 계정 ID, 모니터링 대상 ID, legacy alias를 붙인다. 새 구조의 수집/렌더링 dry-run을 실행하고 기존 메시지와 비교한다. 차이가 승인되면 DeliveryRule을 활성화한다.
  - **Climax:** 기존 탭 기준 실행 결과와 새 대상 기준 실행 결과가 같고, 원본 설정은 삭제되지 않는다.
  - **Resolution:** 민지는 이후 탭 번호가 아니라 고객/대상/채널 기준으로 운영 상태를 본다.
  - **Edge case:** 메시지 내용이 달라지면 신규 DeliveryRule을 활성화하지 않고 차이를 검토한다.

- **UJ-2. 성호가 배민 인증 만료를 감지하고 사람 인증 후 작업을 재개한다.**
  - **Persona + context:** 성호는 운영자이며, 배민 휴대폰 인증이 필요한 대상 때문에 자동 수집이 막혔다.
  - **Entry state:** Local Agent가 수집 중 인증 필요 상태를 감지하고 중앙 서버에 보고한다.
  - **Path:** 성호는 Admin UI에서 인증 필요 대상을 확인하고, 해당 브라우저 프로필을 인증 모드로 연다. 담당자가 휴대폰 인증을 완료하면 Agent가 인증 완료 상태를 감지한다. 이후 중단된 작업이 재시도된다.
  - **Climax:** 시스템은 인증을 우회하지 않고, 사람이 완료한 인증 상태만 확인해 작업을 재개한다.
  - **Resolution:** 운영자는 인증으로 막힌 대상과 정상 복귀한 대상을 구분해 볼 수 있다.
  - **Edge case:** 인증이 정해진 시간 안에 완료되지 않으면 대상 상태는 `AUTH_REQUIRED`로 유지되고 전송은 진행하지 않는다.

- **UJ-3. 지윤이 하나의 실적 snapshot을 Telegram과 KakaoTalk으로 안전하게 받는다.**
  - **Persona + context:** 지윤은 고객사 담당자이며, 같은 실적 내용을 Telegram 그룹과 KakaoTalk 방에서 모두 받고 싶다.
  - **Entry state:** 모니터링 대상에는 Telegram 채널과 KakaoTalk 채팅방 DeliveryRule이 연결되어 있다.
  - **Path:** 시스템은 실적을 한 번 수집하고 메시지를 한 번 렌더링한다. Telegram 전송은 중앙 전송 경로로 처리하고, KakaoTalk 전송은 Local Agent의 직렬 queue에 넣는다. 각 전송 결과는 DeliveryLog에 기록된다.
  - **Climax:** 동일 snapshot에서 파생된 메시지가 중복 없이 각 채널로 한 번씩만 전송된다.
  - **Resolution:** 운영자는 채널별 성공/실패와 재시도 상태를 확인한다.
  - **Edge case:** KakaoTalk 채팅방명이 중복되거나 확인에 실패하면 임의 전송하지 않고 실패 상태로 남긴다.

- **UJ-4. 현수가 Agent 상태와 queue 지연을 보고 증설 여부를 판단한다.**
  - **Persona + context:** 현수는 작업 노드 관리자이며, Windows PC 한 대로 처리 가능한 대상 수를 넘기 전에 병목을 알고 싶다.
  - **Entry state:** Local Agent가 heartbeat, 처리 중인 job, Chrome profile, Kakao queue lag, Agent version을 보고한다.
  - **Path:** 현수는 Admin UI에서 마지막 수집 성공 시각, 인증 상태, 전송 실패, queue lag, Agent 상태를 확인한다. 지연이 기준을 넘으면 작업 PC 추가 또는 Kakao sender 분리를 검토한다.
  - **Climax:** 증설 결정이 감이 아니라 실제 지표를 기준으로 이루어진다.
  - **Resolution:** 운영자는 단일 PC 장애가 전체 고객 장애로 번지지 않도록 작업을 분산한다.

## 3. 용어

- **고객(Customer)** — 서비스를 구매하거나 운영 대상이 되는 사업자 단위.
- **테넌트(Tenant)** — 시스템 내부에서 고객 데이터와 운영 범위를 분리하는 단위. [ASSUMPTION: MVP에서는 고객과 테넌트가 1:1에 가깝다.]
- **구독(Subscription)** — 고객의 사용 가능 상태, 요금제, 한도, 중지 여부를 나타내는 운영 상태.
- **플랫폼 계정(Platform Account)** — 배민 또는 쿠팡이츠에 로그인하는 계정 단위.
- **모니터링 대상(Monitoring Target)** — 특정 플랫폼 계정 안에서 실적을 수집할 센터, 매장, 상점, 또는 대상 URL 단위.
- **브라우저 프로필(Browser Profile)** — Chrome 로그인 세션과 CDP 연결을 격리하기 위한 로컬 프로필 단위.
- **수집 작업(Crawl Job)** — 모니터링 대상에서 실적 데이터를 읽는 작업.
- **Snapshot** — 수집 작업이 만든 정규화된 실적 데이터.
- **메시지(Message)** — Snapshot을 사람이 읽을 수 있게 렌더링한 전송 본문.
- **전송 작업(Dispatch Job)** — 메시지를 특정 채널로 보내는 작업.
- **전송 규칙(DeliveryRule)** — 어떤 모니터링 대상의 메시지를 어떤 채널로 보낼지 정의하는 규칙.
- **전송 로그(DeliveryLog)** — 전송 시도, 성공, 실패, 중복 방지 결과를 기록한 로그.
- **Local Agent** — Windows PC 또는 작업 노드에서 Chrome, KakaoTalk, 로컬 secret, job 실행을 담당하는 프로세스.
- **Agent** — 중앙 서버에 등록된 Local Agent 인스턴스.
- **인증 상태(Auth Session)** — 플랫폼 계정 또는 브라우저 프로필의 로그인/인증 필요/만료/복구 상태.
- **legacy alias** — 기존 `크롤링N` 탭명과 상태 폴더를 새 ID 모델과 연결하기 위한 이전 식별자.

## 4. 제품 원칙과 주요 우려

### 4.1 제품 원칙

- **동작 보존 우선:** 현재 잘 동작하는 배민/쿠팡 수집, 파싱, 메시지 생성, Telegram, KakaoTalk, Gmail 2FA 동작을 기준선으로 고정한다.
- **ID 기반 운영:** 탭 번호와 UI 순서를 운영 식별자로 쓰지 않는다.
- **수집과 전송 분리:** 한 번 수집한 Snapshot을 여러 채널로 안전하게 전송할 수 있어야 한다.
- **실패 시 중단 우선:** 다른 계정, 다른 채널, 애매한 KakaoTalk 방으로 보내는 것보다 전송하지 않는 것이 맞다.
- **로컬 제약 인정:** 배민 인증, Chrome 세션, KakaoTalk PC 앱은 완전 클라우드 작업으로 단순화하지 않는다.
- **단계적 전환:** 기존 설정과 상태를 삭제하지 않고, 비교/승인 후 새 경로를 활성화한다.

### 4.2 주요 우려

- 인증 만료나 휴대폰 인증이 조용히 실패해 잘못된 메시지를 보내는 위험.
- Chrome 프로필, CDP 포트, 플랫폼 계정이 섞여 다른 고객 실적을 보내는 위험.
- KakaoTalk PC UI 자동화에서 방 이름 중복, 포커스 실패, 클립보드 문제로 오발송하는 위험.
- Telegram bot token, Gmail OAuth token, 쿠팡 비밀번호, chat ID 같은 민감값이 로그나 예외에 남는 위험.
- 수집과 전송이 결합되어 같은 데이터 fan-out, 재시도, 중복 방지를 제어하기 어려운 위험.
- 탭 기반 `crawlingN` 상태가 고객 추가/삭제와 함께 꼬이는 위험.
- 고성능 서버 구매로 문제를 해결하려다 단일 장애 범위만 키우는 위험.
- 플랫폼 약관, 계정 위임, 고객 동의, 운영자 대행 인증 정책이 불명확한 상태에서 판매형 운영을 시작하는 위험.

## 5. 기능 요구사항

### 5.1 기준선 고정과 회귀 방지

**Description:** 리팩토링은 현재 운영 가능한 상태를 깨지 않는 방식으로 시작해야 한다. 기존 설정, 상태, 메시지 결과, 테스트 절차를 기준선으로 고정하고, 새 구조 전환 전후 결과를 비교할 수 있어야 한다. 실서비스 전송은 검증된 경로에서만 활성화한다. Realizes UJ-1.

#### FR-1: 기존 동작 기준선 저장

운영자는 리팩토링 시작 전에 기존 활성 배민/쿠팡 대상, Telegram/Kakao 전송 테스트, 현재 설정 파일, 현재 상태 폴더, pytest 결과를 기준선으로 저장할 수 있어야 한다.

**Consequences (testable):**
- 기존 `ui_settings.json`과 `crawlingN` 상태 폴더 원본은 마이그레이션 중 삭제되지 않는다.
- 기존 활성 배민 대상 1개와 쿠팡 대상 1개의 수집/렌더링 dry-run 결과가 기준선으로 남는다.
- 기존 Telegram/Kakao 테스트 전송 절차가 문서화되어 리팩토링 후 반복 가능하다.

#### FR-2: 기존 자산 재사용 보장

시스템은 기존 배민 parser/crawler, 쿠팡 parser, 메시지 renderer, Telegram sender, Kakao sender, 쿠팡 Gmail 2FA, 기존 테스트를 재사용 대상으로 취급해야 한다.

**Consequences (testable):**
- 기존 테스트가 리팩토링 중 계속 실행 가능해야 한다.
- 기존 렌더링 결과를 의도 없이 바꾸는 변경은 실패로 취급한다.
- 새 구조가 기존 코드 경계를 감싸더라도 기존 공개 동작은 호환되어야 한다.

#### FR-3: 신규 경로 dry-run 비교

운영자는 새 수집/렌더링/전송 경로를 실제 발송 없이 dry-run으로 실행하고, 기존 메시지와 신규 메시지의 차이를 승인 전 확인할 수 있어야 한다.

**Consequences (testable):**
- dry-run은 실제 Telegram/Kakao 발송을 하지 않는다.
- 메시지 차이가 발생하면 DeliveryRule 활성화가 자동으로 진행되지 않는다.
- 승인된 대상만 신규 DeliveryRule을 통해 실제 전송된다.

### 5.2 ID 기반 운영 모델

**Description:** 고객, 구독, 플랫폼 계정, 모니터링 대상, 브라우저 프로필, 메시지 채널, 전송 규칙, 작업, 인증 상태, Agent는 탭 번호가 아니라 안정적인 ID로 연결되어야 한다. Realizes UJ-1, UJ-4.

#### FR-4: 고객/대상/채널 ID 관리

운영자는 고객, 구독, 플랫폼 계정, 모니터링 대상, 메시지 채널, 전송 규칙을 ID 기반으로 생성, 조회, 수정, 비활성화할 수 있어야 한다.

**Consequences (testable):**
- 모니터링 대상은 플랫폼, 계정, 기대 센터/상점명, URL 또는 식별자, 연결된 브라우저 프로필을 가진다.
- 전송 규칙은 하나의 모니터링 대상에서 하나 이상의 메시지 채널로 연결될 수 있다.
- 삭제 대신 비활성화 상태를 지원해 운영 이력을 보존한다. [ASSUMPTION: MVP는 soft delete 또는 inactive 상태를 기본으로 한다.]

#### FR-5: legacy alias 유지

마이그레이션된 대상은 기존 `크롤링N` 또는 `crawlingN` 식별자를 `legacy alias`로 보존해야 한다.

**Consequences (testable):**
- 기존 탭명은 새 ID의 표시명이나 보조 식별자로만 쓰이며, 내부 주 식별자로 쓰이지 않는다.
- 고객 추가/삭제 또는 표시 순서 변경이 기존 대상 상태와 섞이지 않는다.
- 운영자는 기존 탭 기반 이슈를 새 ID 기반 대상과 추적해 연결할 수 있다.

#### FR-6: 구독 상태에 따른 작업 제어

시스템은 고객 구독 상태가 작업 실행 가능 상태인지 확인하고, 중지된 고객의 신규 수집/전송 작업을 막을 수 있어야 한다. [ASSUMPTION: MVP는 결제 자동화가 아니라 수동으로 관리되는 구독 상태를 사용한다.]

**Consequences (testable):**
- `ACTIVE`가 아닌 고객은 신규 Crawl Job이 예약되지 않는다.
- `SUSPENDED` 고객의 미전송 Dispatch Job은 기본적으로 `HELD` 상태로 전환되며 자동 발송되지 않는다.
- 이미 성공 기록된 Dispatch Job은 구독 상태 변경 후에도 재전송되지 않는다.
- 중지 사유와 마지막 상태 변경 시각이 운영 화면에서 확인된다.

### 5.3 수집-렌더링-전송 분리

**Description:** 기존 한 번 실행 흐름은 수집, 메시지 생성, 전송이 강하게 묶여 있다. 새 구조는 Snapshot을 중심으로 수집과 전송을 분리해 fan-out, 재시도, 중복 방지, dry-run을 가능하게 해야 한다. Realizes UJ-1, UJ-3.

#### FR-7: Crawl Job과 Snapshot 생성

시스템은 모니터링 대상별 Crawl Job을 실행하고, 결과를 Snapshot으로 저장할 수 있어야 한다.

**Consequences (testable):**
- Crawl Job 실패는 Message 생성이나 Dispatch Job 생성으로 이어지지 않는다.
- 필수 실적 데이터가 누락되면 잘못된 기본값으로 메시지를 만들지 않고 실패로 기록한다.
- Snapshot은 어떤 고객, 플랫폼 계정, 모니터링 대상, 실행 시각, Agent에서 만들어졌는지 추적 가능해야 한다.

#### FR-8: Message 렌더링 분리

시스템은 Snapshot에서 Message를 생성하는 단계를 수집과 분리해야 한다.

**Consequences (testable):**
- 동일 Snapshot은 재수집 없이 다시 렌더링될 수 있다.
- 기존 메시지 renderer 결과와 새 렌더링 결과를 비교할 수 있다.
- 플랫폼별 메시지 포맷 변경은 수집 로직을 수정하지 않고 검증 가능해야 한다.

#### FR-9: Dispatch Job fan-out

시스템은 하나의 Message를 연결된 여러 DeliveryRule에 따라 여러 Dispatch Job으로 fan-out할 수 있어야 한다.

**Consequences (testable):**
- 하나의 Snapshot에서 Telegram과 KakaoTalk 전송 작업을 각각 만들 수 있다.
- 특정 채널 전송 실패가 다른 채널 전송 성공을 무효화하지 않는다.
- 전송 채널별 성공, 실패, 재시도, 보류 상태가 따로 기록된다.

#### FR-10: 중복 발송 방지

시스템은 동일 고객, 모니터링 대상, Snapshot, 메시지 채널, 토픽 또는 채팅방 조합에 대해 중복 발송을 막아야 한다.

**Consequences (testable):**
- 같은 Dispatch Job이 재시도되어도 동일 idempotency key의 성공 전송은 다시 보내지 않는다.
- 중복 방지 키는 다른 고객, 다른 대상, 다른 채널의 전송을 잘못 막지 않는다.
- 중복으로 막힌 전송은 DeliveryLog에 별도 결과로 기록된다.

#### FR-11: 재시도와 실패 상태 관리

시스템은 수집, 렌더링, 전송 실패를 상태로 기록하고, 재시도 가능 실패와 사람 개입이 필요한 실패를 구분해야 한다.

**Consequences (testable):**
- 인증 필요 상태는 무한 재시도하지 않고 `AUTH_REQUIRED` 계열 상태로 남는다.
- 일시적 네트워크/서버 오류는 제한된 재시도와 backoff 정책을 따른다.
- 반복 실패한 parser 또는 플랫폼 작업은 운영자가 확인할 수 있는 경고 상태가 된다.

### 5.4 Local Agent와 작업 노드

**Description:** Local Agent는 Windows 환경에서만 안전하게 처리할 수 있는 Chrome 세션, CDP 연결, KakaoTalk PC 앱 전송, 로컬 secret 접근을 담당한다. 중앙 서버는 Agent에 직접 inbound 접속하지 않고 Agent가 outbound로 job을 가져가고 결과를 보고한다. Realizes UJ-2, UJ-3, UJ-4.

#### FR-12: Agent 등록과 heartbeat

Local Agent는 중앙 서버에 등록되고, 자신의 상태, 버전, 처리 가능 job type, 마지막 heartbeat, 현재 작업 상태를 보고해야 한다.

**Consequences (testable):**
- Agent가 일정 시간 heartbeat를 보내지 않으면 운영 화면에서 offline 또는 degraded로 표시된다.
- Agent 버전이 중앙 서버 기대 버전과 다르면 운영자가 식별할 수 있다.
- Agent별 처리 가능 작업 유형이 표시되어야 한다.

#### FR-13: Agent job polling/claim/complete

Local Agent는 중앙 서버에서 작업을 polling하고, claim한 작업만 실행하며, 완료/실패/보류 결과를 보고해야 한다.

**Consequences (testable):**
- 두 Agent가 같은 job을 동시에 성공 처리하지 않는다.
- Agent가 중간에 죽어도 job은 timeout 후 재할당 또는 실패 상태가 된다.
- job 결과에는 실행 Agent, 시작/종료 시각, 실패 사유가 포함된다.

#### FR-14: Browser Profile 격리

시스템은 플랫폼 계정과 모니터링 대상에 맞는 Browser Profile과 CDP 연결을 격리해서 사용해야 한다.

**Consequences (testable):**
- 서로 다른 고객 또는 계정이 같은 Browser Profile을 잘못 공유하지 않는다.
- CDP 포트나 프로필 중복이 감지되면 작업을 시작하지 않는다.
- 기대 센터/상점명 검증에 실패하면 메시지를 만들거나 보내지 않는다.

#### FR-15: KakaoTalk 직렬 전송

KakaoTalk 전송은 Local Agent의 직렬 queue를 통해 처리되어야 하며, 정확한 채팅방 검증 전에는 메시지를 보내지 않아야 한다.

**Consequences (testable):**
- 한 Agent의 KakaoTalk 전송은 동시에 여러 방에 병렬 입력하지 않는다.
- 채팅방명 중복, 창 확인 실패, 포커스 실패, 전송 결과 확인 실패는 실패로 기록되고 임의 전송하지 않는다.
- KakaoTalk 전송 queue lag가 운영 화면에 표시된다.

#### FR-16: outbound-only Agent 통신

Agent는 중앙 서버로 outbound HTTPS polling/reporting을 수행해야 하며, 중앙 서버가 운영자 PC로 직접 inbound 접속하는 모델을 요구하지 않아야 한다.

**Consequences (testable):**
- Agent는 방화벽 inbound 포트 개방 없이 job 수신과 결과 보고가 가능하다.
- 서버는 Agent의 마지막 통신 시각과 통신 실패를 상태로 표시한다.
- Agent 인증 토큰이 없거나 만료되면 job을 받을 수 없다.

### 5.5 플랫폼 인증과 계정 안전

**Description:** 배민과 쿠팡이츠 인증은 자동화의 가장 큰 운영 위험이다. 시스템은 인증을 우회하려고 하지 않고, 인증 필요 상태를 정확히 감지하고, 사람이 처리할 수 있는 흐름을 제공해야 한다. Realizes UJ-2.

#### FR-17: 배민 인증 필요 감지

시스템은 배민 수집 중 휴대폰 인증 또는 로그인 만료가 필요한 상태를 감지하고 작업을 인증 필요 상태로 전환해야 한다.

**Consequences (testable):**
- 인증 필요 상태에서는 실적 메시지를 생성하거나 전송하지 않는다.
- 운영자는 어떤 고객/대상/브라우저 프로필이 인증을 요구하는지 확인할 수 있다.
- 인증 완료 후 수집은 명시적 재시도 또는 자동 재개 정책에 따라 재개된다.

#### FR-18: 사람 개입형 배민 재인증

운영자 또는 담당자는 해당 브라우저 프로필을 열어 배민 인증을 완료할 수 있어야 한다. [ASSUMPTION: MVP에서는 인증을 운영자가 대행하거나 안내하는 모델을 우선한다.]

**Consequences (testable):**
- 시스템은 휴대폰 인증을 우회하거나 자동으로 통과하려고 시도하지 않는다.
- 인증 완료 여부가 확인되기 전에는 해당 대상의 작업이 정상으로 표시되지 않는다.
- 인증 실패 또는 timeout은 운영 상태에 남는다.

#### FR-19: 쿠팡 Gmail 2FA 분리

시스템은 쿠팡 Gmail 2FA 흐름을 유지하되 고객/메일함/token 단위로 분리하고, 동시에 같은 메일함을 읽는 충돌을 막아야 한다.

**Consequences (testable):**
- Gmail token은 고객 또는 계정 단위로 분리되어야 한다.
- 같은 mailbox에 대한 인증번호 읽기는 lock 또는 동등한 충돌 방지 정책을 따른다.
- 인증번호, OAuth token, 쿠팡 비밀번호는 로그와 예외 메시지에 남지 않는다.

#### FR-20: 플랫폼 대상 검증

시스템은 수집한 화면이 기대한 고객/센터/상점/대상과 일치하는지 검증해야 한다.

**Consequences (testable):**
- 쿠팡 탭의 기대 센터/상점명이 비어 있거나 기본값이면 작업을 위험 상태로 보고한다.
- 기대 대상과 다른 화면이면 메시지 전송이 중단된다.
- 검증 실패는 운영자가 조치할 수 있는 오류로 표시된다.

### 5.6 중앙 서버와 Admin 운영 화면

**Description:** 중앙 서버와 Admin UI는 고객, 대상, Agent, 작업, 인증, 전송 상태를 운영자가 한곳에서 볼 수 있게 한다. MVP는 완전한 고객 셀프서비스보다 운영자 중심 제어와 관측성을 우선한다. Realizes UJ-1, UJ-2, UJ-4.

#### FR-21: 운영 대시보드

운영자는 고객, 모니터링 대상, 마지막 수집 성공, 마지막 전송 성공, 인증 상태, Agent 상태, queue 상태, 오류 상태를 한 화면 또는 연결된 화면에서 확인할 수 있어야 한다.

**Consequences (testable):**
- 대상별 마지막 성공 시각과 마지막 실패 사유가 표시된다.
- Agent별 heartbeat, 버전, 현재 job, 처리 가능 job type이 표시된다.
- KakaoTalk queue lag와 Telegram 전송 오류가 구분되어 표시된다.

#### FR-22: 수동 운영 액션

운영자는 Admin UI에서 대상 활성/비활성, Agent 배정, test crawl, dry-run render, test send, job retry, 인증 필요 상태 확인을 수행할 수 있어야 한다.

**Consequences (testable):**
- test send는 운영자가 지정한 테스트 채널로만 전송된다.
- retry는 중복 발송 방지 정책을 우회하지 않는다.
- 위험한 수동 액션은 실행자와 실행 시각이 기록된다. [ASSUMPTION: MVP는 상세 RBAC보다 운영자 감사 로그를 우선한다.]

#### FR-23: 상태 심각도 표시

시스템은 수집/전송/Agent/인증 상태를 정상, 주의, 위험, 중지 같은 운영자가 이해 가능한 심각도로 표시해야 한다.

**Consequences (testable):**
- 마지막 수집 성공 시간이 스케줄 주기의 2배를 넘으면 warning 후보로 표시된다.
- 마지막 수집 성공 시간이 스케줄 주기의 4배를 넘으면 critical 후보로 표시된다.
- 인증 필요, 기대 대상 검증 실패, KakaoTalk 오발송 위험은 자동 전송보다 중지를 우선한다.

### 5.7 메시지 채널과 전송 정책

**Description:** Telegram과 KakaoTalk은 성격이 다르다. Telegram은 중앙화하기 쉽지만, KakaoTalk은 PC 앱 UI 자동화라 전송량, 동시성, 채팅방 검증, 실패 대응을 더 엄격하게 관리해야 한다. Realizes UJ-3.

#### FR-24: Telegram 중앙 전송

시스템은 Telegram 채널 등록, topic ID 관리, test message, sendMessage 실행, 전송 결과 기록을 중앙 서버 중심으로 처리할 수 있어야 한다.

**Consequences (testable):**
- 동일 bot token을 여러 프로세스에서 동시에 polling하는 구조를 만들지 않는다.
- chat ID와 topic ID 조합은 전송 대상 scope에 포함된다.
- Telegram 전송 실패는 DeliveryLog에 채널별로 기록된다.

#### FR-25: KakaoTalk 제한 운영

시스템은 KakaoTalk 전송을 무제한 기본 기능이나 강한 SLA 채널로 취급하지 않고, queue 지연, 계정 제한 가능성, UI 변경, 오발송 위험을 운영 정책에 반영해야 한다. [ASSUMPTION: MVP에서는 KakaoTalk을 제한/best-effort 기능으로 제공하고, 무제한/대량 전송은 별도 상품, 공식 채널/API 대안, 또는 별도 sender pool 판단으로 남긴다.]

**Consequences (testable):**
- KakaoTalk 전송량과 queue lag가 운영 화면에 표시된다.
- KakaoTalk 전송 실패는 자동으로 다른 방에 보내는 방식으로 복구하지 않는다.
- queue lag가 기준을 넘으면 운영자가 증설 또는 제한 조치를 판단할 수 있다.
- 공식 Kakao 채널/API 대안은 후속 검토 항목으로 남는다.

#### FR-26: 채널별 전송 이력

시스템은 각 DeliveryRule과 Dispatch Job의 전송 이력을 채널별로 추적해야 한다.

**Consequences (testable):**
- 운영자는 같은 Snapshot에서 파생된 Telegram/Kakao 전송 성공 여부를 따로 확인할 수 있다.
- 실패한 채널만 재시도할 수 있다.
- 전송 이력은 중복 발송 방지 판단에 사용된다.

### 5.8 마이그레이션과 배포 운영

**Description:** MVP는 구조 분리와 운영 가능성 확보가 목적이다. 고성능 서버 구매나 완전한 클라우드 이전보다, 현재 Windows PC를 Local Agent #1로 쓰면서 중앙 제어 구조를 먼저 세운다.

#### FR-27: 단계별 전환

시스템은 P0 기준선, P1 ID 모델, P2 수집/전송 분리, P3 Local Agent, P4 중앙 서버 순서로 전환할 수 있어야 한다. [ASSUMPTION: 이 PRD의 MVP 완료 범위는 P0-P4이며, P5 온보딩/인증 고도화와 P6 대량 운영 안정화는 후속 범위로 둔다.]

**Consequences (testable):**
- P2 이후에도 기존 UI 1회 실행 결과가 기존과 동일해야 한다.
- P3 이후 Local Agent는 최소 하나의 대표 대상에 대해 job polling, claim, complete를 수행한다.
- P4 이후 운영자는 중앙 서버/Admin에서 대상과 Agent 상태를 볼 수 있다.

#### FR-28: 현재 PC를 Agent #1로 사용

MVP는 현재 일반 Windows PC를 첫 Local Agent로 사용하고, 고성능 작업 PC/서버 구매는 지표 기반 증설 판단 이후로 미룬다.

**Consequences (testable):**
- Local Agent는 기존 Chrome/KakaoTalk 실행 환경을 활용할 수 있다.
- 증설 판단에는 대상 수, 평균 수집 시간, Kakao queue lag, PC 안정성, 운영 시간이 반영된다.
- 단일 고성능 서버에 모든 작업을 몰아넣는 구조를 MVP 기본안으로 삼지 않는다.

### 5.9 온보딩, 스케줄링, 운영 안전 보강

**Description:** MVP는 완전 셀프서비스가 아니어도 운영자 주도 온보딩, 채널 검증, scheduler 안전장치, 마이그레이션 안전장치, Admin 보호를 요구해야 한다. 이 항목들은 후속 자동화 범위를 줄이더라도 운영 사고를 막기 위한 기본 제품 요구다.

#### FR-29: 채널 등록/검증/활성화

운영자는 Telegram과 KakaoTalk 채널을 등록코드, 테스트 메시지, 고객 또는 운영자 확인 절차를 통해 활성화할 수 있어야 한다.

**Consequences (testable):**
- Telegram 채널은 chat ID와 topic ID가 확인된 뒤 전송 대상이 된다.
- KakaoTalk 채팅방은 고유 방명 또는 동등한 식별 정책을 통과해야 전송 대상이 된다.
- 테스트 메시지 확인 전 DeliveryRule은 실제 운영 전송에 쓰이지 않는다.

#### FR-30: 운영자 주도 고객/구독 상태 흐름

시스템은 완전 결제 자동화 없이도 고객 setup, 인증 대기, 채널 검증 대기, 테스트 실행, 활성, 성능 저하, 인증 필요, 중지 상태를 구분할 수 있어야 한다.

**Consequences (testable):**
- `ACTIVE`, `AUTH_REQUIRED`, `DEGRADED`, `SUSPENDED`는 MVP에서 구분되어야 한다.
- 결제 실패 또는 수동 중지 상태에서는 신규 수집이 중단되지만 기존 설정과 secret/profile 참조는 `SUSPENDED` 상태 동안 보존된다.
- `SUSPENDED`에서 `ACTIVE`로 복구될 때 `HELD` Dispatch Job은 운영자 확인 후 폐기 또는 재개 중 하나로 처리되어야 한다.
- 취소 또는 장기 중지 상태의 secret/profile 폐기 정책은 후속 결정으로 남기되, 상태 모델에서 표현 가능해야 한다.
- MVP는 결제 PG 자동 연동 없이 운영자가 구독 상태를 수동으로 변경하는 모델을 기본값으로 한다.

#### FR-31: 마이그레이션 안전 제약

시스템은 기존 설정과 중복 방지 상태를 새 구조로 옮길 때 데이터 손상, 중복 발송, 비활성 대상 자동 활성화를 막아야 한다.

**Consequences (testable):**
- 설정 저장은 강제 종료에도 손상되지 않도록 atomic write 또는 동등한 안전장치를 사용한다.
- 기존 `last_message` 또는 동등한 중복 방지 상태는 신규 DeliveryLog/idempotency seed로 승계된다.
- 마이그레이션 후보는 활성 탭 기준으로 분류하고, 비활성 탭은 보존하되 자동 활성화하지 않는다.
- 운영 로그는 redaction뿐 아니라 rotation 또는 보존 기준을 가져야 한다.

#### FR-32: Local Agent 실제 실행 조건

Local Agent는 서버와 통신하는 것뿐 아니라 실제 Windows 운영 조건에서 수집과 KakaoTalk 전송을 수행할 수 있어야 한다.

**Consequences (testable):**
- KakaoTalk 작업이 필요한 Agent는 interactive user session에서 실행 가능해야 하며 Session 0 service-only 방식에 의존하지 않는다.
- PC 재부팅 후 사용자 로그인 시 Agent가 자동 시작되고 heartbeat가 복구되어야 한다.
- 순수 crawler Agent와 Kakao sender Agent의 실행 조건과 처리 가능 job type은 구분되어야 한다.

#### FR-33: Scheduler와 queue 안전장치

시스템은 대상 수가 늘어나도 job 폭주, 잘못된 Agent 배정, 플랫폼 전체 장애 확산을 막을 수 있어야 한다.

**Consequences (testable):**
- schedule jitter는 같은 시각에 모든 target이 몰리지 않도록 검증 가능해야 한다.
- platform-wide 장애 또는 parser 실패율 급증 시 신규 Crawl Job 생성을 제한하는 circuit breaker 또는 동등한 보호 장치가 있어야 한다.
- job assignment는 Agent capacity와 target/profile affinity를 고려해야 한다.
- error code별 backoff 정책은 5초 무한 재시도 같은 폭주 패턴을 만들지 않아야 한다.

#### FR-34: Admin 보안과 복구성

중앙 서버와 Admin UI는 운영자가 상태를 보는 기능뿐 아니라 관리자 접근, token 폐기, 백업/복구를 안전하게 다룰 수 있어야 한다.

**Consequences (testable):**
- Admin 접근은 모든 관리자 계정 MFA를 기본으로 하며, VPN 또는 IP allowlist 같은 추가 제한을 함께 둘 수 있어야 한다.
- MVP는 최소 역할을 구분해야 한다: viewer, operator, secret/admin, break-glass admin.
- Agent token과 주요 외부 service token은 revoke 또는 rotate 가능한 방식으로 관리되어야 한다.
- 운영 DB와 진단 산출물은 backup, retention, restore rehearsal 정책을 가져야 한다.
- 최소 운영 알림은 `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`를 포함해야 한다.

## 6. 교차 비기능 요구사항

### 6.1 신뢰성과 안전성

- 시스템은 잘못된 고객, 잘못된 대상, 잘못된 채팅방으로 메시지를 보내는 것보다 작업을 실패시키는 쪽을 선택해야 한다.
- 필수 실적 데이터가 누락되면 메시지를 만들지 않아야 한다.
- 재시도는 idempotency key와 DeliveryLog를 사용해 중복 발송을 막아야 한다.
- 인증 필요 상태는 무한 재시도하지 않고 사람 개입 상태로 전환해야 한다.

### 6.2 보안과 개인정보

- Telegram token, Gmail OAuth token, 쿠팡 비밀번호, 인증번호, chat ID, topic ID, 고객 식별 정보는 로그와 예외 메시지에서 redaction되어야 한다.
- Agent와 중앙 서버 통신은 인증된 HTTPS 경로를 사용해야 한다.
- Agent token이 유출되거나 만료되면 해당 Agent는 job을 받을 수 없어야 한다.
- Gmail token 저장 위치는 보안 검토 전까지 명시적으로 결정하지 않는다. [ASSUMPTION: MVP는 Agent 로컬 보관을 우선 검토하고 중앙 저장은 별도 보안 결정으로 둔다.]
- 운영자 Admin 접근은 최소 보호 장치를 가져야 하며, token revoke/rotation과 backup/restore 절차는 아키텍처에서 구체화되어야 한다.
- 아키텍처는 secret, credential, 고객 식별자, 메시지 본문, 운영 로그, 브라우저 프로필, backup, 진단 산출물의 data inventory를 작성해야 한다.
- secret 저장 위치는 중앙 DB, managed secret store, Windows DPAPI/Credential Manager, 환경변수, 비저장 중 하나로 분류되어야 한다.
- DB, backup, Windows profile/secret 저장소는 적용 가능한 범위에서 encryption at rest를 가져야 한다.
- screenshot, HTML dump, exception trace, queue payload, 실패 메시지 본문 같은 진단 산출물은 retention과 scrubbing 정책을 가져야 한다.

### 6.3 운영 관측성

- 운영자는 고객/대상/Agent/채널/job 단위 상태를 확인할 수 있어야 한다.
- warning/critical 상태의 기준은 스케줄 주기와 마지막 성공 시각을 기준으로 계산 가능해야 한다.
- KakaoTalk queue lag, Agent heartbeat, job 실패율, 인증 필요 상태는 운영 화면에 노출되어야 한다.
- 장애 원인은 “수집 실패”, “인증 필요”, “렌더링 실패”, “Telegram 실패”, “KakaoTalk 실패”, “중복 차단”, “대상 검증 실패”처럼 조치 가능한 분류로 남아야 한다.
- 인증 실패 사유는 token 만료, 비밀번호 오류, 인증메일 지연, CAPTCHA, mailbox 충돌, 최신 메일 오인식처럼 조치 가능한 유형으로 분류 가능해야 한다.
- MVP 운영 runbook은 `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`, `profile_mismatch`, `kakao_ambiguous_room`, `duplicate_blocked`를 최소 포함해야 한다.

### 6.4 호환성과 마이그레이션

- 기존 `runtime/`, `logs/`, `runtime/state/ui_settings.json`, `crawlingN` 관련 상태는 마이그레이션 중 원본을 보존해야 한다.
- 기존 CLI/env 경로와 UI 저장 경로의 설정 정책은 의도 없이 섞지 않는다.
- 기존 테스트와 수동 회귀 시나리오는 리팩토링 각 단계에서 계속 실행 가능해야 한다.
- 기존 중복 방지 상태는 신규 idempotency/DeliveryLog 판단으로 승계되어야 한다.
- 마이그레이션은 discovered, mapped, dry-run passed, approved, active, paused, rolled back 같은 상태를 표현할 수 있어야 한다.
- 운영자는 global dispatch kill switch와 tenant/channel 단위 pause를 사용할 수 있어야 한다.
- old path와 new path가 동시에 실제 전송하지 않도록 cutover 규칙을 가져야 한다.
- rollback은 신규 DeliveryRule을 비활성화하고 기존 런타임 경로를 복구하되, 신규 로그는 중복 방지 기록으로 보존해야 한다.

### 6.5 성능과 확장

- MVP는 최소 100개 가짜 target scheduling smoke를 통해 job scheduling, queue, 상태 추적이 동작함을 보여야 한다.
- 실제 수용량은 Chrome 메모리, 평균 수집 시간, Kakao 평균 전송 시간, 로그인 만료 빈도, Agent 안정성 측정값으로 결정한다.
- KakaoTalk 전송은 queue lag 기준을 통해 증설 또는 제한 정책을 판단한다.
- 운영 규모는 고객 수보다 모니터링 대상 수 기준으로 판단한다.
- negative safety test는 wrong tenant, wrong profile, wrong Kakao room, stale Agent token, restored DB, double Agent claim, crash-after-send를 포함해야 한다.

## 7. 명시적 비목표

- 배민 휴대폰 인증을 자동 우회하거나 완전 자동 로그인으로 처리하지 않는다.
- 배민/쿠팡 공식 API 연동 제품으로 바꾸지 않는다.
- 현재 잘 동작하는 parser, renderer, Gmail 2FA, Telegram/Kakao 전송 코드를 이유 없이 전면 재작성하지 않는다.
- 기존 설정 파일과 상태 폴더를 마이그레이션 과정에서 삭제하지 않는다.
- KakaoTalk 무제한 대량 발송을 기본 상품으로 약속하지 않는다.
- Kubernetes, 복잡한 마이크로서비스, 고성능 서버 구매를 MVP 선행조건으로 삼지 않는다.
- 고객 완전 셀프 온보딩, 결제 자동화, 요금제 자동 과금은 MVP 필수 완료 범위가 아니다.

## 8. MVP 범위

### 8.1 In Scope

- P0 기준선 고정과 회귀 방지 장치.
- 고객/구독/플랫폼 계정/모니터링 대상/채널/Agent/job/log/auth 상태의 ID 기반 모델.
- 운영자 주도 온보딩 상태 관리와 채널 등록/검증/활성화.
- 기존 탭 설정의 legacy alias 보존과 새 ID 모델 연결.
- 수집, Snapshot, 메시지 렌더링, Dispatch Job, DeliveryLog 분리.
- Telegram 중앙 전송 관리와 채널별 전송 로그.
- KakaoTalk Local Agent 직렬 queue와 정확한 채팅방 검증.
- Local Agent 등록, heartbeat, polling, claim, complete, 실패 보고.
- 배민 인증 필요 감지와 사람 개입형 재인증 흐름.
- 쿠팡 Gmail 2FA token/mailbox 분리와 민감값 redaction.
- 운영자 중심 Admin UI의 최소 관측/제어 기능.
- 현재 Windows PC를 Agent #1로 사용하는 배포 경로.
- dry-run, unit/integration/E2E smoke, 100 fake target scheduling smoke 검증.

### 8.2 Out of Scope for MVP

- 결제 PG 연동과 자동 과금.
- 고객 완전 셀프서비스 회원가입/온보딩. 운영자 주도 온보딩 상태 관리는 MVP에 포함한다.
- 고객이 직접 설치하는 Local Agent 배포 UX. 고객 설치형 모델은 지원, 보안, 설치 실패 비용이 크므로 post-MVP decision으로 둔다.
- KakaoTalk 대량 전송 상품화와 sender pool 자동 증설.
- 여러 리전 또는 대규모 멀티테넌트 인프라.
- Kubernetes 기반 배포.
- 1000개 이상 대상 운영 자동화.
- 고성능 서버 즉시 구매.

## 9. 성공 지표

**Primary**

- **SM-1: 기존 동작 보존율** — 기준선 배민/쿠팡 대상의 dry-run 수집/렌더링 결과가 승인 없이 달라지지 않는다. Target: 기준선 대표 대상 100% 보존. Validates FR-1, FR-2, FR-3, FR-27.
- **SM-2: 중복/오발송 방지** — 동일 idempotency scope의 중복 전송이 실제 발송으로 이어지지 않고, 대상/채팅방 검증 실패 시 전송하지 않는다. Target: 검증 시나리오 100% 차단. Validates FR-10, FR-14, FR-15, FR-20, FR-25.
- **SM-3: 운영 가시성 확보** — 운영자는 고객/대상/Agent/인증/전송 상태와 마지막 성공/실패를 Admin UI에서 확인할 수 있다. Target: MVP 필수 상태 항목 100% 표시. Validates FR-21, FR-22, FR-23.

**Secondary**

- **SM-4: 작업 분리 검증** — 수집 실패, 렌더링 실패, Telegram 실패, Kakao 실패가 서로 다른 상태와 로그로 기록된다. Target: integration test에서 각 실패 유형 확인. Validates FR-7, FR-8, FR-9, FR-11, FR-26.
- **SM-5: Agent 운영 가능성** — Agent #1이 중앙 서버에서 job을 polling/claim/complete하고 heartbeat를 보고한다. Target: 대표 배민/쿠팡 dry-run과 Kakao test queue 처리 성공. Validates FR-12, FR-13, FR-16, FR-28.
- **SM-6: 부하 smoke 통과** — 100개 fake target scheduling smoke에서 job 생성, 상태 전환, queue 기록이 실패 없이 완료된다. Validates FR-11, FR-21, FR-27.
- **SM-7: 운영 안전장치 통과** — 채널 검증 전 활성화 차단, atomic settings write, `last_message` seed 승계, Agent autostart heartbeat 복구, scheduler jitter/circuit breaker 시나리오가 검증된다. Validates FR-29, FR-31, FR-32, FR-33, FR-34.

**Counter-metrics**

- **SM-C1: 자동화율만 높이지 않기** — 인증 우회나 애매한 KakaoTalk 전송으로 자동 성공률을 높이면 안 된다. Counterbalances SM-2, SM-5.
- **SM-C2: 전송 성공 수만 최적화하지 않기** — 중복 발송이나 잘못된 대상 발송을 성공으로 취급하면 안 된다. Counterbalances SM-2, SM-4.
- **SM-C3: 서버 성능만 최적화하지 않기** — 고성능 서버 구매로 로컬 인증/메신저 제약을 숨기면 안 된다. Counterbalances SM-5, SM-6.

## 10. 리스크와 대응

- **기존 동작 회귀** — 기준선 저장, dry-run 비교, 기존 테스트 유지, 활성화 전 승인으로 대응한다.
- **고객/대상 혼선** — ID 기반 모델, legacy alias, 기대 센터/상점명 검증, Browser Profile 격리로 대응한다.
- **인증 실패 반복** — 인증 필요 상태와 사람 개입 흐름을 명시하고, 무한 재시도를 금지한다.
- **KakaoTalk 오발송** — 직렬 queue, 정확한 채팅방 검증, 실패 시 중단 원칙으로 대응한다.
- **민감값 노출** — redaction test, secret 저장 정책, 예외 메시지 검토로 대응한다.
- **MVP 범위 과대화** — P0-P4를 MVP로 두고 결제/셀프 온보딩/대량 운영은 후속으로 분리한다.
- **단일 작업 노드 장애** — Agent 상태 관측, queue lag, 증설 기준을 마련하고 단일 고성능 서버 몰아넣기를 피한다.
- **약관/계정 위임 리스크** — 플랫폼 약관, 고객 동의, 인증 대행 정책, KakaoTalk 자동화 정책을 출시 전 검토 항목으로 둔다.

## 11. 출시와 마이그레이션 요구

- 마이그레이션 전 기존 설정과 상태를 백업한다.
- 기존 활성 배민/쿠팡 대상의 기준선 수집/렌더링 결과를 저장한다.
- 새 ID 모델을 기존 탭 설정에 붙이되, 기존 탭명은 legacy alias로 남긴다.
- 새 수집/렌더링/전송 경로는 dry-run으로 먼저 검증한다.
- Telegram/KakaoTalk 채널은 등록/검증/테스트 확인 후 활성화한다.
- DeliveryRule은 운영자가 차이를 확인한 뒤 활성화한다.
- Agent #1은 현재 Windows PC에서 시작한다.
- 중앙 서버와 Admin UI는 Agent 상태와 target 상태를 먼저 보여주는 범위로 시작한다.
- 고성능 작업 PC/서버 구매는 실제 queue lag, target 수, PC 안정성, 운영 시간 지표를 근거로 결정한다.

## 12. Open Questions

### 12.1 Architecture Blockers

1. Gmail OAuth token은 MVP에서 Agent 로컬 DPAPI/Credential Manager에 둘 것인가, 중앙 secret store로 옮길 것인가?
2. Agent authentication, job claim protocol, queue/job state model, idempotency, tenant isolation, Admin access model은 어떤 ADR로 확정할 것인가?
3. warning/critical 기준과 Kakao queue lag 기준은 문서의 초기값을 그대로 사용할 것인가, 운영 실측 후 조정할 것인가?

### 12.2 Launch Blockers

1. 플랫폼 약관, 계정 위임, 고객 동의, 인증 대행, Gmail 접근, KakaoTalk 자동화 정책 검토를 완료해야 한다. Owner: 제품/사업 책임자. Artifact: 정책 검토 기록, 고객 동의 문안, 금지 작업 목록, go/no-go 결정.
2. KakaoTalk 전송은 기본 제공, 제한 제공, 프리미엄 제공 중 어느 정책으로 갈 것인가? MVP 기본값은 제한/best-effort이며, 상용 출시 전 고객 안내 문구와 quota/장애 대응 정책이 필요하다.
3. KakaoTalk PC 앱 자동화 대신 공식 Kakao 채널/API 대안을 검토해야 하는가? 공식 대안이 없거나 쓰지 않는 경우 강한 SLA를 약속하지 않는다.

### 12.3 Post-MVP Decisions

1. 고객 설치형 Local Agent를 지원할 것인가?
2. 배민 재인증을 고객 셀프서비스로 전환할 것인가?
3. 결제/요금제 자동 연동을 언제 도입할 것인가?
4. 100개 이상 모니터링 대상 운영을 위한 worker pool, sender pool, sharding을 어느 시점에 도입할 것인가?

## 13. Readiness Gates

### 13.1 Architecture Readiness

아키텍처 문서 작성 전에는 다음 ADR 주제를 반드시 다룬다.

- Agent authentication과 job claim/complete protocol.
- Secret storage, token rotation, token revocation, diagnostic artifact handling.
- Queue/job state model, lease, retry, idempotency, crash-after-send behavior.
- Tenant isolation model across DB, API, queue, logs, Admin, Agent assignment.
- Migration cutover, rollback, kill switch, and simultaneous-send prevention.
- Admin access control, MFA, roles, audit log shape.
- KakaoTalk product policy and sender runtime constraints.

### 13.2 Implementation Readiness

구현 스토리 작성 전에는 다음 evidence가 있어야 한다.

- Baseline capture plan and canary migration plan for at least one Baemin and one Coupang target.
- Negative safety test plan for cross-tenant denial, stale/revoked Agent token, duplicate dispatch, restored non-sending environment, Kakao ambiguous room, focus loss, and crash-after-send.
- Local Agent hardening checklist for Agent #1, including disk encryption or documented exception, Windows account isolation, profile folder permissions, screen lock policy, remote access MFA, and lost/decommissioned node handling.
- Admin/Agent audit log fields: actor, source, before/after value, target IDs, reason, timestamp, and result.

### 13.3 Commercial Launch Readiness

상용 출시 전에는 다음 gate가 닫혀야 한다.

- Legal/policy review completed for platform terms, customer consent, account delegation, assisted authentication, Gmail access, and KakaoTalk automation.
- Customer-facing terms explain what the service accesses, what operators may do, how credentials/OTP are handled, how messaging works, and how authorization can be revoked.
- KakaoTalk is described as limited/best-effort unless an official high-reliability channel is adopted.
- Backup/restore rehearsal proves restored environments start in non-sending mode until explicitly activated.
- Runbooks exist for the minimum incident set in §6.3.

## 14. Assumptions Index

- §2.3 — v1은 운영자 주도 운영 모델을 기본으로 한다.
- §2.3 — MVP는 운영자 소유 Agent를 기본 배포 모델로 한다.
- §3 — MVP에서는 고객과 테넌트가 1:1에 가깝다.
- §5.2 FR-4 — MVP는 soft delete 또는 inactive 상태를 기본으로 한다.
- §5.2 FR-6 — MVP는 결제 자동화가 아니라 수동으로 관리되는 구독 상태를 사용한다.
- §5.5 FR-18 — MVP에서는 배민 인증을 운영자가 대행하거나 안내하는 모델을 우선한다.
- §5.6 FR-22 — MVP는 상세 RBAC보다 운영자 감사 로그를 우선한다.
- §5.7 FR-25 — MVP에서는 KakaoTalk을 제한/best-effort 기능으로 제공하고, 무제한/대량 전송은 별도 상품, 공식 채널/API 대안, 또는 별도 sender pool 판단으로 남긴다.
- §5.8 FR-27 — 이 PRD의 MVP 완료 범위는 P0-P4이며, P5 온보딩/인증 고도화와 P6 대량 운영 안정화는 후속 범위로 둔다.
- §6.2 — MVP는 Gmail token의 Agent 로컬 보관을 우선 검토하고 중앙 저장은 별도 보안 결정으로 둔다.
