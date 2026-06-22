# PRD Quality Review — rider_result_mornitoring 리팩토링

## Overall verdict

이 PRD는 기존 동작 보존, ID 기반 운영, 수집-렌더링-전송 분리, Local Agent 제약을 하나의 명확한 리팩토링 방향으로 묶고 있어 구현 계획과 아키텍처 작업의 입력으로는 충분히 쓸 수 있다. 다만 판매 가능한 다고객 운영 제품으로 넘어가기 위한 약관/동의/인증 대행/KakaoTalk 정책 책임과 MVP 경계가 아직 열린 질문으로 남아 있어, 출시 또는 유료 운영 승인 문서로 쓰기에는 보강이 필요하다.

## Decision-readiness — adequate

의사결정에 필요한 제품 방향은 잘 드러난다. §1 비전은 “새 제품을 처음부터 다시 만드는 것이 아니다”라고 선을 긋고, 기존 자산 보존과 중앙 서버 + Windows Local Agent 구조를 선택한 이유를 설명한다. §4.1 제품 원칙도 “실패 시 중단 우선”, “로컬 제약 인정”, “단계적 전환”처럼 실제 trade-off를 의사결정 언어로 제시한다.

하지만 판매형 운영으로 넘어갈 때 막히는 결정은 아직 닫히지 않았다. 특히 §4.2와 §10이 플랫폼 약관, 고객 동의, 인증 대행, KakaoTalk 자동화 정책 위험을 인정하지만, §12 Open Questions에서 책임자와 완료 조건을 다시 묻고 있다. 리팩토링 착수에는 충분하지만, 외부 고객에게 제공할 운영 모델을 승인하기에는 일부 결정이 보류되어 있다.

### Findings

- **high** 판매/출시 정책의 책임자와 완료 조건이 결정되지 않았다 (§4.2, §10, §12 Q11) — PRD는 “플랫폼 약관, 계정 위임, 고객 동의, 인증 대행, KakaoTalk 자동화 정책”을 주요 위험으로 정확히 잡지만, §12 Q11에서 “책임자와 완료 조건은 무엇인가?”로 남겨 둔다. 판매 가능한 다고객 운영 구조라는 문서 목적상 이 항목은 출시 승인 조건을 좌우한다. *Fix:* 정책 검토 owner, 완료 산출물, go/no-go 기준, 미완료 시 MVP에서 차단할 기능을 명시한다.
- **medium** MVP 경계가 결정처럼 쓰이다가 다시 열린 질문으로 돌아온다 (§5.8 FR-27, §8.1, §12 Q1) — FR-27은 “MVP 완료 범위는 P0-P4”라고 가정하고 §8.1 In Scope도 그 범위를 따른다. 그런데 §12 Q1은 “P0-P4로 고정할 것인가”를 다시 묻는다. downstream 에픽 작성자는 P5 온보딩/인증 UX를 빼도 되는지 판단하기 어렵다. *Fix:* P0-P4를 확정 결정으로 바꾸거나, Q1을 phase-blocker로 표시하고 P5가 들어올 경우 영향을 받는 FR/SM을 표시한다.

## Substance over theater — strong

내용은 템플릿 채우기보다 실제 운영 문제에서 출발한다. §2.2 Jobs To Be Done은 탭 번호 운영, 인증 만료, fan-out, KakaoTalk 오발송, 회귀 위험을 직접 겨냥하고, §5의 FR들은 이 문제들을 각각 ID 모델, Snapshot, DeliveryRule, Local Agent, Auth Session, DeliveryLog로 연결한다.

NFR도 일반론에 머물지 않는다. §6.5는 “100개 가짜 target scheduling smoke”, Chrome 메모리, 평균 수집 시간, Kakao 평균 전송 시간, 로그인 만료 빈도처럼 이 제품에 맞는 기준을 둔다. §6.2도 token, 인증번호, chat ID, topic ID 등 실제 민감값을 특정한다.

## Strategic coherence — strong

PRD의 thesis는 일관적이다. 기존 desktop 자동화의 동작을 보존하면서 운영 식별자와 작업 경계를 재정의하고, 로컬 제약은 Local Agent에 남긴다는 방향이 §1, §4, §5.1, §5.3, §5.4 전반에 반복된다. 기능 목록이 독립적으로 흩어지지 않고 “기존 동작 보존 → ID 모델 → 수집/전송 분리 → Agent → Admin 관측” 순서로 이어진다.

성공 지표도 이 thesis를 검증한다. §9 SM-1은 기존 동작 보존, SM-2는 중복/오발송 방지, SM-3은 운영 가시성을 검증하고, Counter-metrics는 자동화율이나 전송 성공 수를 잘못 최적화하지 못하게 막는다.

## Done-ness clarity — adequate

대부분의 FR은 testable consequences를 갖고 있어 story 작성의 출발점으로 쓸 수 있다. 예를 들어 §5.3 FR-10은 idempotency scope와 DeliveryLog 결과를 요구하고, §5.4 FR-13은 동시 claim, timeout 재할당, 결과 필드를 다룬다. §5.6 FR-23도 warning/critical을 스케줄 주기의 2배/4배로 잡아 검증 가능하게 만든다.

남은 약점은 일부 운영/보안 요구가 “정책에 따라”, “동등한”, “최소 보호” 같은 표현으로 끝나서 MVP 완료 기준이 구현자에게 넘어간다는 점이다. 이 PRD가 리팩토링 기준선 역할을 하려면 해당 표현들은 최소 정책값 또는 수용 기준으로 바뀌어야 한다.

### Findings

- **medium** 보안과 운영 액션의 완료 기준이 일부 넓게 열려 있다 (§5.2 FR-6, §5.9 FR-34, §6.2) — FR-6은 이미 생성된 Dispatch Job을 “운영 정책에 따라 중단 또는 보류”한다고 하고, FR-34는 Admin 접근을 “2FA, VPN, IP allowlist, 또는 동등한 접근 제한 중 하나 이상”으로 둔다. §6.2도 token revoke/rotation과 backup/restore를 아키텍처에서 구체화한다고 넘긴다. 이 상태로는 어떤 정책 조합이면 MVP pass인지 애매하다. *Fix:* MVP 기본값을 정한다. 예: suspended 고객의 pending Dispatch Job 기본 처리, Admin 접근 최소 조합, token rotation/revoke acceptance, restore rehearsal 최소 주기를 명시한다.
- **low** 일부 UI 요구는 필수 정보는 말하지만 사용 가능한 화면 조건은 덜 분명하다 (§5.6 FR-21, §5.6 FR-22) — “한 화면 또는 연결된 화면”에서 상태를 본다고 되어 있으나, 운영자가 장애를 triage하기 위해 필요한 기본 필터, 정렬, drill-down 기준은 없다. *Fix:* MVP Admin에서 최소로 제공할 목록/상세/필터 기준을 FR-21 consequences에 추가한다.

## Scope honesty — adequate

범위 제외는 비교적 솔직하다. §2.3과 §7은 공식 API 전환, 완전 셀프서비스, KakaoTalk 대량 발송, Kubernetes, 고성능 서버 구매를 명시적으로 제외한다. §8.2도 고객 설치형 Local Agent 배포 UX와 1000개 이상 대상 운영을 MVP 밖으로 둔다.

Assumption과 Open Questions도 숨기지 않는다. 다만 열린 질문의 일부가 “나중에 더 좋게 만들 것인가”가 아니라 MVP 경계와 판매 리스크를 정하는 질문이므로, 중요도 표시가 더 필요하다.

### Findings

- **medium** Open Questions의 phase-blocker와 후속 검토 항목이 섞여 있다 (§12) — Q1, Q5, Q6, Q7, Q11은 MVP 구현 또는 출시 가능성에 직접 영향을 주지만 Q3, Q8, Q10처럼 후속 정책 조정에 가까운 항목과 같은 목록에 있다. *Fix:* §12를 “MVP blocker”, “launch blocker”, “post-MVP decision”으로 나누고 각 항목의 owner 또는 revisit condition을 둔다.

## Downstream usability — strong

아키텍처, 에픽, 스토리 작성자가 추출하기 좋은 구조다. §3 Glossary는 Customer, Tenant, Platform Account, Monitoring Target, Browser Profile, Snapshot, DeliveryRule, DeliveryLog, Local Agent 같은 도메인 명사를 정리하고, FR-1부터 FR-34까지 ID가 연속된다. UJ-1부터 UJ-4는 named protagonist와 entry/path/climax/resolution을 갖고 있어 UX와 운영 workflow 추출에 쓸 수 있다.

성공 지표도 FR에 연결되어 있다. §9의 SM-1부터 SM-7은 Validates FR 목록을 포함하고, 각 주요 섹션의 Description은 Realizes UJ를 달아 요구사항 추적이 쉽다. addendum.md는 FastAPI, PostgreSQL, Redis, AWS 같은 기술 후보를 PRD 본문 밖으로 분리해 아키텍처 입력으로 보존하므로 PRD 본문이 구현 선택으로 오염되지 않는다.

## Shape fit — strong

제품 성격에 맞는 형태다. 이 문서는 소비자용 UX PRD가 아니라 정상 동작 중인 Python/tkinter 자동화 앱을 다고객 운영 구조로 바꾸는 brownfield/internal-operations 성격의 PRD다. 그래서 capability spec 중심의 FR 구조가 맞고, UJ는 운영자/고객 담당자/작업 노드 관리자 관점에서 필요한 만큼만 들어가 있다.

기술 상세도 적절히 분리되어 있다. PRD 본문은 “무엇을 만족해야 하는지”를 다루고, addendum.md는 service boundary, queue, deployment posture, Windows Agent hardening 같은 아키텍처 재료를 보존한다. 이는 현재 문서 목적과 downstream handoff에 잘 맞는다.

## Mechanical notes

- FR ID는 FR-1부터 FR-34까지 연속이며 중복을 발견하지 못했다.
- UJ ID는 UJ-1부터 UJ-4까지 연속이며 각 UJ에 named protagonist가 있다.
- SM ID는 SM-1부터 SM-7, counter-metric은 SM-C1부터 SM-C3까지 일관된다.
- inline `[ASSUMPTION]` 항목은 §13 Assumptions Index에 모두 roundtrip되어 있다.
- `[NOTE FOR PM]`은 §8.2에 1개 있으며 별도 index는 없지만 현재 수량에서는 추적 위험이 낮다.
- Glossary의 주요 도메인 명사는 본문에서 대체로 일관되게 쓰인다.
