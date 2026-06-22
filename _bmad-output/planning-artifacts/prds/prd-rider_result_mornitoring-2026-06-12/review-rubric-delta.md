# PRD Rubric Delta Review — Architecture Handoff

## Verdict

**conditional** — 이전 high/medium findings는 대부분 아키텍처 handoff에 충분한 수준으로 정리됐다. PRD는 launch blocker와 architecture readiness gate를 분리했고, 정책/보안/Agent/job/tenant isolation 관련 ADR 주제를 명시했다. 단, 구독 중지 상태에서 이미 생성된 Dispatch Job을 어떻게 처리할지는 아직 architecture handoff 전에 닫거나 ADR 주제로 명시해야 한다.

## Previous High Findings

- **판매/출시 정책의 책임자와 완료 조건** — **addressed for architecture handoff.** §12.2 Launch Blockers가 플랫폼 약관, 계정 위임, 고객 동의, 인증 대행, Gmail 접근, KakaoTalk 자동화 정책 검토의 Owner, Artifact, go/no-go 결정을 명시했다. §13.3 Commercial Launch Readiness도 상용 출시 전 gate로 분리했다. 이는 architecture work를 막는 blocker가 아니라 launch gate로 관리하기에 충분하다.

## Previous Medium Findings

- **MVP 경계가 결정처럼 쓰이다가 다시 열린 질문으로 돌아옴** — **addressed for architecture handoff.** §2.3은 고객 설치형 Agent를 post-MVP로 두고, §5.8 FR-27과 §10은 P0-P4를 MVP 기준으로 유지한다. §12.3 Post-MVP Decisions도 고객 설치형 Agent, 재인증 셀프서비스, 결제 자동화, 100개 이상 확장을 후속 결정으로 분리했다.
- **보안과 운영 액션의 완료 기준이 넓게 열림** — **partially addressed; one medium remains.** §5.9 FR-34는 Admin MFA와 최소 역할(viewer/operator/secret-admin/break-glass admin)을 추가했고, §13.1은 secret storage, token rotation/revocation, queue/job state, tenant isolation, Admin access ADR을 요구한다. 다만 §5.2 FR-6은 이미 생성된 Dispatch Job을 “운영 정책에 따라 중단 또는 보류”한다고만 남아 있어, `SUSPENDED` 전환 시 pending/claimed Dispatch Job의 cancel/hold/complete 허용 규칙이 아직 닫히지 않았다.
- **Open Questions의 phase-blocker와 후속 검토 항목 혼재** — **addressed for architecture handoff.** §12가 Architecture Blockers, Launch Blockers, Post-MVP Decisions로 재구성됐고, §13 Readiness Gates가 Architecture/Implementation/Commercial Launch 단계별 선행 조건을 분리했다.

## Remaining High/Medium Issues

- **medium** `SUSPENDED` 고객의 이미 생성된 Dispatch Job 처리 규칙이 아직 불명확함 (§5.2 FR-6, §13.1) — 아키텍처는 job state model, lease, retry, idempotency를 설계해야 하므로, 구독 상태가 `SUSPENDED`로 바뀐 뒤 pending/leased/claimed Dispatch Job을 cancel, hold, allow-complete 중 무엇으로 처리할지 알아야 한다. *Fix:* §13.1 ADR 목록에 “subscription state transition impact on Crawl/Dispatch Job lifecycle”을 추가하거나, FR-6 consequences에 MVP 기본 정책을 직접 명시한다.
