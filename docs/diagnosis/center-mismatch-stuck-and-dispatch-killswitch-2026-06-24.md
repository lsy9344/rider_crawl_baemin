# 진단 보고서 — '대상 검증 실패' 고착 · 크롬 반복 인증 · 전송 OFF인데 카카오 큐 적재

- **작성일**: 2026-06-24
- **대상**: target `6b8fd18e`(H&J, 센터 "제이앤에이치플러스 의정부남부", COUPANG)
- **운영 환경**: EC2 54.116.103.149, `rider-db-1`(PostgreSQL), agent `jena-5800h`(Windows, v0.1.0)
- **상태**: **진단만 완료(코드 변경 보류 — 사용자 지시)**. 아래는 라이브 DB 증거 기반 확정 진단과 제안 수정안.

---

## 0. 한 줄 요약

세 증상은 **서로 다른 3개 원인**이다.

1. **'대상 검증 실패' 카드가 안 풀림** — 계정 `auth_state`가 구버전 서버 시절 `CENTER_MISMATCH`로 굳었고, 신버전 서버는 성공 crawl로 자동 해제하지만 **신버전 배포(06-23 11:58) 이후 crawl이 전부 실패**해서 해제 트리거가 한 번도 안 옴.
2. **에이전트 PC에서 크롬이 ~30초/1분 주기로 반복 인증 시도** — crawl이 계속 `CRAWL_TIMEOUT`으로 실패하고, 그 위에 **잡 재시도(30s→60s backoff) + 10분 스케줄 + 수동 재검증**이 겹쳐 실패-재시도 루프가 됨. 근본 원인은 crawl 자체가 timeout 나는 것.
3. **'전송 OFF'인데 실시간 큐에 '카카오 전송'이 쌓임** — 전송 enqueue 경로가 **전역 전송 kill switch(`sending_enabled`)를 검사하지 않는다.** 명백한 서버 버그.

---

## 1. 라이브 DB 증거

### 1-1. 계정 상태 (`platform_accounts`)

| id | platform | auth_state | auto_recovery_attempted_at |
|---|---|---|---|
| `1eb9c671…` | COUPANG | `UNKNOWN` | (none) |
| `3e703327…` | COUPANG | **`CENTER_MISMATCH`** | 2026-06-23 08:05:29 |

- target `6b8fd18e`가 쓰는 계정은 **`3e703327`** → `CENTER_MISMATCH` 고착.
- H&J 라벨 COUPANG 계정이 2개 존재(혼동 주의).

### 1-2. 잡 타임라인 (`jobs`, 발췌)

```
type           status     error_code                 attempts  claimed(KST 아님, UTC)  auth(result_json)
CRAWL_COUPANG  CLAIMED    CRAWL_TIMEOUT              1         06-24 07:29:34          —      ← 지금 도는 잡(크롬창)
CRAWL_COUPANG  FAILED     CRAWL_TIMEOUT             2         06-24 07:26:32          —
CRAWL_COUPANG  FAILED     PROFILE_UNAVAILABLE      2         06-23 11:37:40          —
CRAWL_COUPANG  SUCCEEDED  —                          0         06-23 11:00:54          ACTIVE ← 신버전 서버 배포 전!
CRAWL_COUPANG  SUCCEEDED  —                          0         06-23 10:58:40          ACTIVE
CRAWL_COUPANG  SUCCEEDED  —                          0         06-23 10:40:09          ACTIVE
CRAWL_COUPANG  SUCCEEDED  —                          0         06-23 10:36:20          ACTIVE
CRAWL_COUPANG  FAILED     TARGET_VALIDATION_FAILURE  0         06-23 10:21:35          CENTER_MISMATCH
CRAWL_COUPANG  FAILED     CDP_UNREACHABLE          2         06-23 09:xx             —  (다수)
AUTH_COUPANG_2FA SUCCEEDED —                         0         06-23 08:04:36          —      ← recovery 성공(08:05)
```

핵심: **에이전트는 이미 신버전 코드**(성공 crawl이 `result_json.auth_state=ACTIVE`를 실어 보냄, 06-23 10:36~11:00).

### 1-3. 서버 컨테이너 기동 시각

```
rider-backend-api-1   Up (healthy)   started 2026-06-23 11:58:46 UTC
rider-scheduler-1     started        2026-06-23 11:58:58 UTC
…
```

→ **4건의 ACTIVE 성공(10:36~11:00)은 모두 신버전 서버 배포(11:58) 이전**에 일어났다. 그때 돌던 구버전 서버는 `result_json.auth_state`를 읽지 않아 계정을 ACTIVE로 안 덮었고, 그래서 `CENTER_MISMATCH`가 그대로 남았다.

### 1-4. 배포된 서버 코드 확인

`rider-backend-api-1` 컨테이너 내부 `_platform_account_auth_update`에 `auth_state = result_json.get("auth_state")` 분기 **존재 확인**. 즉 신버전 서버에는 자동 해제 로직이 살아 있다. 11:58 이후 성공 crawl이 한 건만 와도 자동으로 풀린다 — 그런데 **그 이후 성공이 0건**이다.

### 1-5. 전송 상태

- `RIDER_SENDING_ENABLED` 환경변수 **미설정** → `Settings.sending_enabled` 기본값 `False`(fail-closed). 그래서 대시보드가 정확히 "전송 OFF" 표시.
- `delivery_rules`: target `6b8fd18e` → channel `4daa8e03`(KAKAO "이수열"), **`enabled=t`**.
- `messenger_channels`: `4daa8e03` = KAKAO, **`state=ACTIVE`**.
- KAKAO_SEND 잡 집계: **`FAILED` 22건, `SUCCEEDED` 1건** — 전송 OFF인데도 잡이 만들어졌다는 직접 증거.

---

## 2. 원인 분석 (코드 경로)

### 증상 ① — '대상 검증 실패' 카드 안 풀림

- 대시보드 배지는 `platform_accounts.auth_state` 기준이고 현재 값은 `CENTER_MISMATCH`라 **배지 자체는 정확**하다.
- 해제 경로: 성공 crawl → `_snapshot_payload`(`crawl_worker.py:723`)가 `auth_state=AUTH_STATE_ACTIVE` 포함 → 서버 `_platform_account_auth_update`(`postgres_queue.py:173`)가 읽어 계정 ACTIVE로 UPDATE.
- **막힌 이유**: 신버전 서버 배포 후 성공 crawl이 0건. 모든 crawl이 `CRAWL_TIMEOUT`/`PROFILE_UNAVAILABLE`로 실패하고, 실패 result_json엔 `auth_state`가 없어(또는 AUTH_REQUIRED라) ACTIVE로 안 덮인다. → 카드가 영영 안 풀림.
- **결론**: 코드 버그 아님. **crawl이 성공해야 자동 해제됨**(증상 ②가 해결되면 자동으로 풀림). 즉시 풀려면 1회 수동 UPDATE.

> 참고: 이전 메모 `coupang-success-auth-state-stale-deploy`는 "agent 미배포가 원인"이라 했으나, 이번 증거상 **agent는 이미 신버전**이다(성공 crawl이 ACTIVE를 실어 보냄). 이번 고착의 직접 원인은 **server 배포 타이밍 + 이후 crawl 전패**다. 메모를 갱신함.

### 증상 ② — 크롬 ~30s/60s 반복 인증

- `CRAWL_TIMEOUT`/`CDP_UNREACHABLE`/`PROFILE_UNAVAILABLE` → `retry.py:14-17`에서 `FailureCategory.CRAWL_FAILURE`(재시도 가능)로 매핑.
- backoff: `delivery_failure_policy.py` base=30s, factor=2 → attempt1=30s, attempt2=60s. **사용자가 본 "30초/1분 주기"와 정확히 일치.**
- `DEFAULT_MAX_JOB_ATTEMPTS=3`(`retry.py:11`) → 잡당 최대 3회 시도 후 소진.
- 잡 소진 후엔 **10분 스케줄러**(`monitoring_targets.interval_minutes=10`)가 새 crawl을 또 만든다. + 사용자가 누른 **수동 재검증**(`test_crawl`)도 한 건 추가.
- auth gate(`policy.py:153-159`): `CENTER_MISMATCH`는 `_AUTH_OK_STATES`에 포함 → "로그인은 됐고 센터검증만 실패"로 보고 **scheduled crawl을 계속 허용**(설계 의도). 그래서 인증게이트가 루프를 막지 않는다.
- **근본 원인**: agent에서 쿠팡 crawl이 매번 timeout. 로그인 세션/페이지 로드/CDP 문제로 추정되나 **agent 로그 미확인** → 추가 조사 필요(아래 권장 조치).

### 증상 ③ — 전송 OFF인데 KAKAO_SEND 적재 (★ 서버 버그)

- 전송 잡 enqueue는 성공 crawl ingest 시 `snapshot_repository_postgres.py::_enqueue_dispatch_records`(L267~)에서 일어난다.
- 이 함수가 검사하는 게이트는 **`_send_window_allows_dispatch`(시작/종료 시간 윈도, L290) 하나뿐**이다.
- enqueue 조건(L298~360): `delivery_rules.enabled=True` AND 채널 `state=ACTIVE` AND (KAKAO면) → `JOB_TYPE_KAKAO_SEND` insert.
- **`sending_enabled`(전역 kill switch)도 `effective_send_enabled`도 여기서 호출되지 않는다.** 그래서 운영자가 전송을 꺼도(또는 신규 환경 기본 OFF여도) crawl이 성공할 때마다 KAKAO_SEND가 enqueue된다.
- 이건 **이미 알려진 갭**이다 — `actions_routes.py:351` 주석이 "실 send 호출부에 동일 `effective_send_enabled` 게이트를 compose해야 한다"고 명시. 실 send 호출부(`dispatch_service.py:66`)와 auth-test 경로엔 게이트가 있지만 **crawl→dispatch enqueue 경로엔 빠졌다.**

---

## 3. 제안 수정안 (구현 보류 — 승인 시 적용)

### 수정 A (권장·우선) — 전송 enqueue에 kill switch 게이트 추가

- 위치: `snapshot_repository_postgres.py::_enqueue_dispatch_records`.
- 방식: 윈도 검사처럼 **enqueue 직전에 `effective_send_enabled(send_enabled=True, sending_enabled=<Settings.sending_enabled>)`를 검사**해 False면 KAKAO_SEND insert를 건너뛴다. `sending_enabled`를 repository/서비스에 주입(생성자 또는 호출부)해야 함 — 현재 이 클래스는 Settings를 안 보므로 배선 추가 필요.
- 효과: 전송 OFF면 잡이 아예 안 쌓인다. delivery_log 예약행도 만들지 말지(미발송 dedup 회수와의 상호작용) 검토 필요.
- 테스트: send OFF 상태 ingest → KAKAO_SEND 0건, send ON → 정상 enqueue.

### 수정 B — crawl timeout 근본 원인 조사 (코드 아닌 운영/조사)

- agent PC에서 쿠팡 crawl 로그 확인: 로그인 세션 유효한지, 대시보드 페이지 로드 timeout인지, CDP 연결 문제인지.
- `coupang-auth-open-slow-dashboard-wait` 메모(로그인 전인데 대시보드 텍스트를 60s×2 헛대기)와 동일 패턴 가능성 → 점검.
- 필요시 루프 완화: 실패 누적 시 스케줄 일시 중단(circuit breaker는 동일 플랫폼 15분 30% 기준이나 단일 대상이라 min_samples=5에 막힐 수 있음 — 확인).

### 수정 C — 고착된 CENTER_MISMATCH 1회 수동 해제(선택)

- crawl이 성공하기 시작하면 자동으로 풀리므로 **B가 해결되면 C는 불필요**할 수 있다.
- 즉시 카드를 풀려면:
  ```sql
  UPDATE platform_accounts SET auth_state='ACTIVE'
   WHERE id='3e703327-84ea-42ce-bf4a-2282848f6bfa' AND auth_state='CENTER_MISMATCH';
  ```
  단, 실제 센터 불일치가 사실이 아닐 때만(오발송 위험 회피). crawl이 여전히 실패하면 카드는 다른 사유로 다시 STOPPED가 될 수 있다.

---

## 4. 우선순위 권고

1. **수정 A(전송 kill switch)** — 명백한 버그, 안전하게 픽스 가능. 오발송 방지 직결.
2. **조사 B(crawl timeout)** — ②의 근본이자 ①의 해제 트리거. agent 로그 확보가 선행.
3. **수정 C** — B 진행 중 즉시 카드만 풀고 싶을 때 1회성.

---

## 부록 — 재현/조사 명령

```bash
# EC2 접속
ssh -i deploy/terraform/.secrets/rider-server-keypair.pem ubuntu@54.116.103.149
# DB
sudo docker exec -i rider-db-1 psql -U rider -d rider
# 계정 상태
SELECT id, platform, auth_state, auto_recovery_attempted_at FROM platform_accounts;
# 최근 잡
SELECT type, status, error_code, attempts, claimed_at, completed_at, result_json->>'auth_state'
  FROM jobs ORDER BY COALESCE(completed_at, claimed_at, run_after) DESC LIMIT 20;
# KAKAO_SEND 적재 확인
SELECT type, status, count(*) FROM jobs WHERE type='KAKAO_SEND' GROUP BY 1,2;
# 서버 컨테이너 기동시각
sudo docker ps --format '{{.Names}}\t{{.CreatedAt}}'
```
