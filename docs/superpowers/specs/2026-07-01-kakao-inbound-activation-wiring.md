# Kakao Inbound Watcher 활성화 배선 설계 (Hybrid)

> 목적: `KakaoInboundWatcher` 를 Agent 런타임에서 실제로 조립·주입해, 통제된 방의
> `!!강민기1234` 가 감지→서버 POST→lookup→답장까지 흐르게 한다. job_loop 스레드
> 주입점은 구현됨(`run_agent(kakao_inbound_watcher=...)`, 커밋 78d4124). 남은 것은
> **watchlist/secret 소스 + watcher 조립 + `__main__` 주입**이다.

## 결정 (확정): Hybrid config source

- **서버/WebApp = non-secret Kakao inbound watchlist 의 source of truth.**
- **Agent local = local prerequisite + secret 의 source of truth.**
- **Effective enabled = local enabled(kill switch) && local prerequisites OK
  && server watchlist non-empty.** 셋 중 하나라도 실패하면 watcher 미기동
  (disabled health + 이유).

### 서버가 관리(non-secret)
- Kakao messenger channel 의 `room_name`, optional `chat_id`.
- channel `ACTIVE`/`INACTIVE`, `command_trigger_enabled`.
- `delivery_rules` 를 통한 target mapping.
- tenant/subscription/send gate, dedupe, rate limit, in-flight policy.

### Agent 로컬이 관리(secret + prerequisite)
- Kakao DB path 또는 DB root.
- SQLCipher `db_key`.
- `user_hash`.
- per-room chatLogs 키가 필요하면 `chatlogs_key:<chat_id>`.
- local enabled/kill switch.
- local scan/read defaults: `use_chat_logs`, `limit` 등 PC 종속 런타임 값.

## 서버 watchlist 경로: 별도 authenticated Agent config endpoint (선호)

- `GET /v1/agents/kakao-inbound-config` (Bearer agent token — 기존 agent endpoint 와
  동일 인증).
- 서버가 Agent identity → tenant scope → 해당 tenant 의 kakao messenger_channels 중
  `ACTIVE && command_trigger_enabled` 인 방 목록을 **non-secret 만** 반환.
- 응답 payload:
  ```json
  {
    "kakao_inbound": {
      "enabled": true,
      "config_version": 3,
      "rooms": [
        {"room_name": "고객방명", "chat_id": "optional-known-chat-id",
         "use_chat_logs": true, "limit": 20}
      ]
    }
  }
  ```
- Agent runtime 은 `RefreshingKakaoInboundWatcher` 로 watchlist 를 주기적으로 다시 가져와
  `config_version`/room fingerprint 변경 시 내부 watcher 를 재조립한다.
- `config_version` 으로 변경 감지(Agent 는 폴링 캐시; 버전 동일이면 재적용 생략).
- **범위를 줄여야 하면** heartbeat 응답 확장(`commands`/`config_version` 옆
  `kakao_inbound` 필드)을 fallback 경로로 쓴다. 별도 endpoint 를 1차 선호.

## Agent watchlist 적용

- Agent 는 **서버 watchlist 에 있는 방만 스캔**한다
  (`KakaoInboundConfig.rooms = 서버 rooms`).
- **로컬 rooms 설정은 bootstrap/fallback/canary 로만** 허용한다(서버 도달 전/일시 실패
  시 임시 스캔 범위). **장기 source of truth 가 아니다** — 문서·코드 주석·테스트에
  명시한다. 서버 watchlist 를 받으면 로컬 rooms 를 대체한다.

## Effective enabled 게이트 (불변식)

```
effective_enabled =
    local_enabled(kill switch)
    && local_prereq_ok(db_key & user_hash & db_path resolve)
    && session_interactive           # KakaoTalk 실행 세션 필요(kakao_sender 와 동일)
    && server_watchlist.enabled
    && server_watchlist.rooms non-empty
    # if the server explicitly returns rooms: [], local fallback rooms are not used
```
- 어느 하나라도 실패 → watcher 미기동, disabled/degraded health + 고정 사유.

## Secret / 비노출 불변식 (강화)

1. DB key, user_hash, DB path, chatLogs key 는 **절대 서버로 보내거나 서버에서
   내려받지 않는다.** Agent local only.
2. 서버가 내려주는 값은 **non-secret watchlist 까지만**이다.
3. inbound event 수신 후 서버는 **다시** channel/tenant/target/send gate 를 검증한다
   (`decide_inbound_event`). Agent watchlist 는 최종 권한 판단이 아니라 **스캔 범위
   제한**일 뿐이다.
4. 서버로 전송하는 값은 **sanitized event 와 digest 만** 허용한다. raw message text,
   plaintext secret, DB path, user_hash 원문, command name/phone suffix 를
   heartbeat/log/job event 에 넣지 않는다.

## 경계 / protected

- `rider_agent` 는 `rider_server`, `sqlcipher3`, `pywinauto` 를 직접 import 하지
  않는다. reader 는 `rider_crawl`/reuse seam 경유로 주입한다.
- protected Coupang login/email 2FA 파일은 변경하지 않는다. protected 파일을 건드려야
  하면 CLAUDE.md 절차 + protected test set 을 먼저 따른다.
- Agent 통합은 job_loop 주입점(non-protected)을 쓰고 `worker_composition.py`
  (protected)는 변경하지 않는다.

## 구현 슬라이스 (test-first)

- **A. 서버 endpoint** — `GET /v1/agents/kakao-inbound-config` (Agent auth,
  tenant→kakao channels watchlist, non-secret 만). 계약 테스트.
- **B. Agent watchlist client** — fetch/cache/`config_version`, 실패 시 로컬 fallback.
  적용 테스트.
- **C. local secret/prerequisite 로더 + effective-enabled 게이트** — fail-closed
  테스트(키/경로 없음 → disabled).
- **D. build_kakao_inbound_watcher + reader_factory + `__main__` 주입** — raw 비노출
  테스트.

각 슬라이스는 독립 커밋. protected 파일 무변경.

## 테스트 (필수)

- **서버 watchlist 계약**: tenant scope 준수, `ACTIVE && command_trigger_enabled` 만
  포함, non-secret 만(키/경로/user_hash 부재), 인증 실패 401/403.
- **Agent watchlist 적용**: 서버 rooms 만 스캔; 로컬 rooms 는 fallback 으로만.
- **local secret/prerequisite fail-closed**: db_key/user_hash/path 없으면 disabled,
  crash 아님.
- **raw 비노출**: event/log/heartbeat/job event 에 raw text·secret·DB path·user_hash
  원문·command name·phone suffix 없음.

## 등록 흐름 (운영자)

- 서버/WebApp: 대상 kakao messenger channel 을 ACTIVE + `command_trigger_enabled`
  로 설정(room_name/optional chat_id, delivery_rules target).
- Agent PC(로컬만): db_key/user_hash/DB path 를 secure store/config 에 등록(CLI
  `rider_agent kakao-inbound-register` 권장 또는 수동). 실제 값은 저장소·로그에 남기지
  않는다.

## 미해결 / 운영자 몫

- 실제 db_key/user_hash/경로/방 설정은 서버(채널) + Agent PC(secret) 양쪽에서 운영자만.
- headed E2E: 서버 채널 설정 + Agent secret 등록 후, 기존 중복 Agent 정리 → 이 브랜치
  코드/venv 단일 Agent 재시작 → 통제 방에서 `!!강민기1234` → 배민 lookup → scoped
  답장 확인. PR 은 draft 유지.
