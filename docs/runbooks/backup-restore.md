# Runbook: DB 백업·복원 리허설과 복구 non-sending (Story 5.8 / AC3, NFR-9·25)

> 범위: 운영 DB(PostgreSQL)와 진단 산출물의 **백업/보존/복원 리허설 정책**과, 복구·신규
> 환경의 **non-sending(기본 전송 차단) → 명시적 활성화** 흐름. 모니터링 지표 runbook 7종은
> Story 5.9 소유다(여기서는 다루지 않는다).

## 1. 백업·보존 정책

- **PITR(Point-In-Time Recovery)**: 운영 PostgreSQL 은 WAL 아카이빙 기반 PITR 을 켠다.
- **보존 기간**: 자동 백업 **최소 7일** 보존(일 단위 스냅샷 + WAL 연속 보관). 규제/사고 분석
  요구가 늘면 보존 기간을 연장한다.
- **수동 스냅샷**: 위험 작업(마이그레이션·대량 데이터 변경·cutover) **직전** 에 수동 스냅샷을
  1건 추가로 만든다(라벨에 변경 사유·리비전 기록).
- **진단 산출물**: audit_logs 등 감사 데이터는 DB 백업에 포함된다. 외부로 내보내는 진단 덤프는
  redaction 통과본만 보관한다(평문 secret 0 — `redact`/`redact_mapping`).

## 2. 마이그레이션 게이트: "backup 확인 후 실행"

스키마 마이그레이션(`alembic upgrade`)은 **반드시 백업 확인 후** 실행한다(additive 라도).

1. 최신 자동 백업 시각과 상태를 확인한다(7일 이내 성공 백업 존재).
2. 마이그레이션 **직전 수동 스냅샷**을 1건 만든다(라벨: `pre-<revision>` 예 `pre-0005`).
3. 오프라인 SQL 을 먼저 검토한다: `alembic upgrade head --sql` 로 적용될 DDL 을 사람이 읽고
   확인한다(additive 컬럼만인지, 테이블 수 14 유지인지).
4. 스테이징/리허설 DB 에 `upgrade head` → 검증 → `downgrade` round-trip 을 먼저 통과시킨다.
5. 운영에 `upgrade head` 적용. 실패 시 즉시 `downgrade <prev>` 또는 스냅샷 복원으로 롤백한다.
6. 적용 후 head 리비전이 기대값(현재 `0005_audit_fields_and_agent_token_revoke`)인지 확인한다.

> 게이트 위반(백업 미확인 상태의 운영 마이그레이션)은 금지한다 — 모호하면 실행하지 않는다.

## 3. 복원 리허설(restore rehearsal) 절차

복원은 "필요할 때 처음 해보는" 작업이 되면 안 된다. **주기적으로 리허설** 한다(분기 1회 권장).

1. 최신 백업/스냅샷을 **격리된 복구 환경**(운영과 분리된 별도 인스턴스)에 복원한다.
2. 복원 환경의 무결성을 확인한다: 테이블 수 14, head 리비전 일치, 핵심 행 수 sanity check.
3. 복원 환경은 **non-sending 모드로 기동**된다(아래 4절) — 실고객 전송이 절대 나가지 않는다.
4. 읽기 전용 Admin 대시보드로 데이터가 정상 복원됐는지 육안 확인한다.
5. 리허설 결과(복원 소요시간 RTO, 데이터 손실 범위 RPO)를 기록하고 리허설 환경을 폐기한다.

## 4. 복구·신규 환경의 non-sending 시작 → 명시적 활성화 (NFR-9·25)

복구/신규 환경은 **명시적으로 활성화하기 전까지 실전송을 하지 않는다**(fail-closed —
모호하면 보내지 않는다). 신규 차단 경로를 만들지 않고 기존 dispatch `send_enabled`/kill switch 와
`effective_send_enabled(send_enabled, sending_enabled)` 로 **compose** 한다.

- 환경 전역 플래그 `sending_enabled`(`Settings.sending_enabled` / 환경변수 `RIDER_SENDING_ENABLED`)
  는 **기본 OFF** 다. 복구·신규 환경은 이 값이 꺼진 채 기동된다.
- 실전송은 `send_enabled`(채널/대상별 게이트)와 `sending_enabled`(환경 전역)가 **둘 다 True** 일
  때만 일어난다. 둘 중 하나라도 꺼져 있으면 전송 0.

### 활성화 절차(복구 검증 완료 후)

1. 데이터 무결성·인증 상태·대상/구독 상태를 Admin 대시보드로 검증한다.
2. dry-run/test-send(단일 테스트 채널)로 렌더·전송 경로를 실고객 fan-out 없이 확인한다.
3. **secret-admin 또는 break-glass** 권한자가 `RIDER_SENDING_ENABLED=1`(또는 운영 토글)로
   `sending_enabled` 를 켠다. 이 활성화는 audit 에 남는다(누가·언제·왜).
4. 활성화 직후 첫 전송 윈도에서 중복/오발송이 없는지 모니터링한다(이상 시 즉시 OFF 로 되돌린다).

> 의심스러우면 OFF 를 유지한다. 전역 pause/kill switch 의 전체 매트릭스는 Story 5.10 소유이며,
> 본 runbook 은 "복구 시 기본 차단 + 명시적 활성화"만 정의한다.
