# 입력 조정 결과: docs/refactoring/detailed_work_order.md

## 요약 판정

`docs/refactoring/detailed_work_order.md`의 핵심 방향은 `prd.md`와 `addendum.md`에 대체로 반영되어 있다. 특히 중앙 서버 + Windows Local Agent, 탭 기반 운영 제거, 수집/렌더링/전송 분리, KakaoTalk 직렬 전송, 배민 인증 우회 금지, Gmail token 분리, secret redaction, 현재 PC를 Agent #1로 쓰고 지표 기반으로 증설한다는 큰 결정은 보존되어 있다.

다만 상세 작업 지시서가 가진 일부 운영 의도와 완료 기준은 PRD의 FR 구조 안에서 약해졌거나 open question으로 밀려 있다. 아래 항목은 후속 PRD 수정 또는 아키텍처/에픽 작성 시 반드시 재검토해야 한다.

## 상위 material gaps

### 1. MVP 범위와 온보딩/구독 상태 흐름이 충돌한다

**Source signal**

- 상세 작업 지시서는 신규 기능에 고객/구독 DB, 고객 온보딩, 인증 상태 관리, 고객 유입/구독/온보딩 자동화를 포함한다.
- 11.1은 `LEAD -> SIGNED_UP -> PAYMENT_ACTIVE -> SETUP_PENDING -> PLATFORM_AUTH_PENDING -> MESSENGER_VERIFY_PENDING -> TEST_RUNNING -> ACTIVE -> DEGRADED -> AUTH_REQUIRED -> SUSPENDED` 상태 흐름을 제시한다.
- 11.2는 setup code 발급, plan/quota 설정, 플랫폼 계정 등록, Telegram/Kakao 채널 등록, Agent 배정, 배민/쿠팡 인증, 테스트 크롤링, 모든 채널 테스트 발송, 고객 확인 후 ACTIVE 전환을 요구한다.
- 11.3은 `PAYMENT_FAILED_GRACE`, `SUSPENDED`, `CANCELLED`에서 작업 실행과 secret/profile 보존 또는 폐기 정책을 나눈다.

**Current PRD/addendum coverage**

- PRD FR-6은 구독 상태에 따른 작업 제어를 요구하지만 `ACTIVE` 중심의 단순 상태로 표현한다.
- PRD FR-22는 test crawl, dry-run render, test send, 인증 확인 같은 운영자 수동 액션을 포함한다.
- PRD §8.2와 Open Questions는 고객 완전 셀프서비스, 결제 자동화, P5 온보딩/인증 고도화를 MVP 밖 또는 미확정으로 둔다.

**Gap**

PRD가 상세 작업 지시서의 "운영자 주도 온보딩 상태 머신"까지 MVP 요구인지, 아니면 P5 후속 범위인지 명확히 결론내리지 않는다. 이 때문에 epics/stories 작성 시 온보딩 상태, setup code, quota, 테스트 발송 승인, 고객 확인, grace/cancel 정책이 누락될 수 있다.

**Recommended reconciliation**

- PRD에서 MVP 범위를 둘 중 하나로 명확히 고정한다.
  - 옵션 A: P0-P4 운영 MVP로 유지하되, 상세 작업 지시서 11장은 "Post-MVP onboarding scope"로 명시하고 최소 MVP에는 `SETUP_PENDING`, `AUTH_REQUIRED`, `ACTIVE`, `SUSPENDED` 같은 운영 상태만 둔다.
  - 옵션 B: 운영자 주도 온보딩을 MVP에 포함하고 FR-6 또는 별도 FR에 setup code, quota, channel verification, test run, customer confirmation, grace/cancel 처리 acceptance criteria를 추가한다.

### 2. 설정/상태 마이그레이션의 안전 제약 일부가 약해졌다

**Source signal**

- P1-03은 설정 저장을 atomic write로 바꾸고 fsync 후 rename하여 강제 종료에도 JSON 손상이 없어야 한다고 지시한다.
- P1-04는 `run_errors.log`와 `kakao_diagnostics.log` 로그 rotation을 요구한다.
- 14.1은 활성 탭만 target 후보로 분류하고, `state/runtime/crawlingN` 폴더를 `targets/<monitoring_target_id>`로 복사하며 원본을 삭제하지 말라고 한다.
- 14.1은 `last_message` hash를 신규 DeliveryLog dedup seed로 가져오라고 한다.

**Current PRD/addendum coverage**

- PRD FR-1, FR-3, FR-5, §11은 기존 설정/상태 원본 보존, legacy alias, dry-run, 메시지 비교, DeliveryRule 승인 후 활성화를 요구한다.
- PRD §6.4는 `runtime/`, `logs/`, `ui_settings.json`, `crawlingN` 관련 상태 원본 보존을 말한다.
- Addendum은 baseline, dry-run, unit/integration/E2E verification을 보존한다.

**Gap**

원본 보존과 dry-run은 반영됐지만, 운영 중 설정 파일 손상 방지와 로그 무한 증가 방지, `last_message` hash를 dedup seed로 승계하는 제약이 PRD-level acceptance로 남아 있지 않다. 이는 단순 구현 디테일이 아니라 기존 고객에게 중복 발송하거나 상태를 잃는 회귀를 막는 migration constraint다.

**Recommended reconciliation**

- PRD §6.4 또는 FR-1/FR-10/FR-27에 다음 acceptance를 추가한다.
  - 설정 저장은 강제 종료에도 손상되지 않도록 atomic write 또는 동등한 안전장치를 사용한다.
  - 기존 `last_message` 또는 동등한 중복 방지 상태는 신규 DeliveryLog/idempotency seed로 승계된다.
  - 운영 로그는 token redaction뿐 아니라 rotation/retention 기준을 가진다.
  - 마이그레이션 후보는 활성 탭 기준으로 분류하고 비활성 탭은 보존하되 자동 활성화하지 않는다.

### 3. Local Agent의 실제 Windows 운영 조건이 acceptance로 충분히 고정되지 않았다

**Source signal**

- 8.1은 KakaoTalk PC 앱 제어가 interactive desktop을 필요로 하므로 Windows Service Session 0 방식만으로 실행하면 안 된다고 지시한다.
- 6.4 P3-07은 Windows 시작 프로그램 또는 작업 스케줄러 등록을 요구하고, PC 재부팅 후 사용자 로그인 시 agent 자동 실행을 완료 기준으로 둔다.
- 8.1은 agent local layout, profile path, log path, secret 저장 금지/DPAPI 임시 허용을 제시한다.
- 8.2는 startup/main loop 형태를 명시한다.

**Current PRD/addendum coverage**

- PRD FR-12~FR-16은 Agent 등록, heartbeat, job polling/claim/complete, Browser Profile 격리, outbound-only 통신을 요구한다.
- PRD FR-28은 현재 Windows PC를 Agent #1로 사용한다고 한다.
- Addendum은 KakaoTalk 자동화가 interactive Windows desktop session을 필요로 하며 Session 0 service-only workload로 보면 안 된다고 보존한다.

**Gap**

Addendum에는 중요한 제약이 남아 있지만 PRD acceptance에는 "사용자 로그인 후 자동 실행", "Session 0 단독 서비스 금지", "재부팅 후 운영 복구"가 없다. Agent가 서버와 통신할 수 있어도 실제 KakaoTalk 전송이 불가능한 실행 방식으로 구현될 위험이 있다.

**Recommended reconciliation**

- PRD FR-12 또는 FR-28에 Agent runtime acceptance를 추가한다.
  - Local Agent는 KakaoTalk 작업이 필요한 노드에서 interactive user session으로 실행 가능해야 한다.
  - PC 재부팅 후 사용자 로그인 시 Agent가 자동 시작되고 heartbeat가 복구되어야 한다.
  - KakaoTalk 기능이 비활성인 순수 crawler agent와 Kakao sender agent의 실행 조건을 구분한다.

### 4. 클라우드 운영 보안/복구 요구가 제품 요구로는 약하다

**Source signal**

- 7.1은 RDS retention 7일 이상, PITR, 수동 snapshot, S3 lifecycle, infra config 백업, 복구 리허설 절차를 요구한다.
- 7.1은 Admin에 관리자 2FA 또는 VPN/IP allowlist를 적용하라고 한다.
- 7.1은 CloudWatch logs/alarms, disk/CPU, API error rate, agent offline count, queue lag metric 수집을 요구한다.
- 13.1은 token 변경/폐기, Agent token revoke, profile path ref 저장, BitLocker 권장을 포함한다.

**Current PRD/addendum coverage**

- PRD §6.2는 HTTPS, Agent token 만료/유출 차단, redaction, Gmail token 저장 보류를 다룬다.
- PRD §6.3은 운영 관측성 지표를 다룬다.
- Addendum은 AWS, HTTPS, Docker, PostgreSQL, managed secret storage, outbound-only agent communication을 architecture input으로 보존한다.

**Gap**

PRD는 운영 보안과 관측성을 말하지만, Admin 접근 보호, backup/restore, token revoke, secret rotation 같은 운영 필수 조건은 acceptance로 약하다. 판매형 전환 PRD라면 "운영자가 상태를 본다"뿐 아니라 "운영 데이터와 관리자 접근을 복구/보호한다"는 제품 수준 요구가 필요하다.

**Recommended reconciliation**

- PRD NFR에 다음을 추가한다.
  - Admin 접근은 최소한 2FA, VPN, IP allowlist, 또는 동등한 접근 제한 중 하나를 가져야 한다.
  - DB와 diagnostic artifact는 backup/retention/restore rehearsal 정책을 가진다.
  - Agent token과 Telegram/Gmail/쿠팡 관련 secret은 revoke/rotate 가능한 상태로 관리된다.
  - 운영 알림은 `agent_offline`, `queue_lag`, `api_error_rate`, `auth_required`를 최소 포함한다.

### 5. Scheduler/queue 운영 의도가 FR 구조에서 일부 사라졌다

**Source signal**

- 7.3은 `next_run_at` 기준 due target 조회, deterministic jitter, inactive/suspended subscription 차단, platform global circuit breaker, agent capacity와 target affinity 기반 배정, error_code별 backoff, 5초 무한 재시도 금지를 요구한다.
- 14.3은 100개 fake target scheduling에서 job storm 없이 jitter와 queue 동작을 확인하라고 한다.

**Current PRD/addendum coverage**

- PRD FR-11은 제한된 재시도와 backoff, 반복 parser/platform 실패 경고를 요구한다.
- PRD FR-13은 job claim/timeout/reassign을 요구한다.
- PRD FR-21/23은 queue 상태와 warning/critical 표시를 요구한다.
- PRD SM-6은 100개 fake target scheduling smoke를 포함한다.
- Addendum은 jittered scheduling, retry/backoff, circuit breaker behavior, queue lag monitoring을 보존한다.

**Gap**

Addendum에는 남아 있지만 PRD acceptance에는 platform global circuit breaker, target affinity, agent capacity 기반 배정, deterministic jitter가 명시적이지 않다. 특히 "작업 폭주 방지"와 "특정 target이 맞는 agent/profile에서 돌도록 보장"은 판매형 운영 안정성 요구에 가깝다.

**Recommended reconciliation**

- PRD FR-13 또는 FR-27에 scheduler acceptance를 추가한다.
  - schedule jitter는 같은 시각에 모든 target이 몰리지 않게 검증 가능해야 한다.
  - platform-wide 장애 시 신규 CrawlJob 생성을 제한하는 circuit breaker 또는 동등한 보호 장치가 있어야 한다.
  - job assignment는 agent capacity와 target/profile affinity를 고려해야 한다.

## 부분 보존 또는 addendum으로 충분한 항목

### DB/API 세부 모델

상세 작업 지시서 12장의 필수 DB 테이블과 Agent API endpoint는 PRD 본문보다 아키텍처/implementation artifact에 더 적합하다. PRD는 ID 기반 운영 모델과 Agent job lifecycle을 요구하고, addendum은 table/entity 방향과 API 흐름을 구현 입력으로 보존한다. 따라서 PRD 본문 누락으로 보지는 않는다. 단, epics 작성 시 12.1~12.3을 직접 참조해야 한다.

### 구체 기술 스택

FastAPI, PostgreSQL, Alembic, Docker Compose, AWS EC2/RDS/S3/Secrets Manager/CloudWatch, Redis/SQS 교체 가능성은 addendum에 architecture input으로 보존되어 있다. PRD가 이를 제품 요구로 강제하지 않은 것은 적절하다. 단, 사용자가 이미 AWS 서울 리전과 RDS를 확정한 의사결정으로 보고 있다면 architecture 문서에서 결정을 다시 열지 않아야 한다.

### KakaoTalk 세부 오류 코드와 클립보드 처리

PRD는 KakaoTalk을 제한/best-effort 채널로 다루고, 직렬 queue, 정확한 채팅방 검증, queue lag, 실패 시 임의 전송 금지를 반영한다. 상세 오류 코드(`ROOM_NOT_FOUND`, `ROOM_AMBIGUOUS`, `KAKAO_NOT_LOGGED_IN`, `UI_TIMEOUT`, `CLIPBOARD_ERROR`)와 클립보드 백업/복구는 구현 acceptance 또는 story 세부 조건으로 옮기는 것이 적절하다. 다만 Kakao room naming convention과 고객 확인 후 활성화는 온보딩 범위 결정에 따라 PRD로 올라와야 한다.

### 완료 기준 대부분

상세 작업 지시서 16.1의 MVP 완료 기준은 PRD의 FR/SM/In Scope에 대부분 반영되어 있다. 누락 위험이 큰 부분은 "기존 활성 탭 2개 기능을 신규 구조에서 dry-run 및 실제 테스트 발송까지 검증" 중 실제 테스트 발송의 위치다. PRD는 test send를 운영 액션으로 다루지만, baseline migration 완료 기준으로 "실제 테스트 발송까지"를 명시하지는 않는다.

## 요구사항 매핑

| Source area | PRD/addendum coverage | Status |
| --- | --- | --- |
| 중앙 서버 + Windows Local Agent | PRD §1, FR-12~16, FR-28; addendum Technical Direction | Covered |
| 현재 PC를 Agent #1로 사용, 고성능 서버 구매 보류 | PRD FR-28, §11, 비목표; addendum Operational Thresholds | Covered |
| 탭 기반 구조 제거, customer/target ID | PRD FR-4~5, 용어, 원칙 | Covered |
| 수집/렌더링/전송 분리, fan-out | PRD FR-7~10; addendum data flow | Covered |
| 배민 인증 우회 금지, 사람 개입 | PRD FR-17~18, 비목표 | Covered |
| 쿠팡 Gmail token/mailbox 분리 | PRD FR-19, §6.2; addendum Open Items | Covered |
| Telegram 중앙 webhook/register/send | PRD FR-24; addendum Technical Direction | Covered |
| Kakao 직렬 queue, 오발송 방지 | PRD FR-15, FR-25; addendum Technical Direction | Covered |
| 고객 온보딩 상태 머신 | PRD Open Questions/Out of Scope 일부 | Material gap / scope conflict |
| 구독 grace/cancel 정책 | PRD FR-6 단순 상태 | Material gap |
| atomic settings write/log rotation | PRD §6.4 일부만 | Material gap |
| `last_message` dedup seed migration | PRD FR-10/§11 일부만 | Material gap |
| Agent interactive desktop / autostart | Addendum only, PRD weak | Material gap |
| Admin 2FA/VPN/IP allowlist, backup/restore | Addendum partial, PRD weak | Material gap |
| Scheduler circuit breaker/capacity/affinity | Addendum partial, PRD weak | Material gap |

## 결론

상세 작업 지시서의 큰 제품 방향은 PRD와 addendum에 잘 보존되어 있다. 핵심 보완점은 PRD가 "무엇을 MVP에 넣고 무엇을 후속으로 뺄지"를 온보딩/구독 상태에서 더 선명하게 결정하고, 마이그레이션 안전성, Agent 실제 Windows 운영 조건, 운영 보안/복구, scheduler 폭주 방지 조건을 acceptance criteria로 끌어올리는 것이다.
