# done_Kakao Inbound Health Alerting Hardening

Date: 2026-07-02
Status: 보강 설계 문서

## 목적

Kakao inbound 기능이 활성화되어 있어도 로컬 DB 리더, SQLCipher 패키지, 로컬 설정,
interactive session, 서버 watchlist 중 하나가 불량이면 실시간 명령이 큐에 들어가지 않는다.
현재 코드는 일부 상태를 heartbeat와 Agent fleet 표에 표시하지만, 운영자가 웹앱에서 즉시
인지할 수 있는 상단 경고나 명확한 안내는 부족하다.

이 문서는 이번 `db_unavailable` 장애를 기준으로 프로젝트 구조와 상태 전파 경로를 정리하고,
Kakao inbound 설정/런타임 불량을 웹앱에서 선명하게 드러내기 위한 보강 범위, 데이터 계약,
테스트 기준, 운영 절차를 정의한다.

## 배경

최근 KakaoTalk 방에 `!!함욱현4750, !!신성국4941, !!아무개4444`처럼 명령을 보냈지만
실시간 큐에 올라오지 않았다. 직접 원인은 Agent PC의 `.venv`에 설치된 `sqlcipher3`가
깨진 상태였기 때문이다.

관측된 증상:

- Agent 로그에 `AGENT_KAKAO_INBOUND_DISABLED`, reason `db_unavailable`.
- 예외는 `module 'sqlcipher3' has no attribute 'connect'`.
- `sqlcipher3` dist-info에는 `RECORD`/`METADATA`가 없었고, 패키지 본문에도 top-level
  `__init__.py`/`dbapi2.py`가 없었다.
- `sqlcipher3._sqlite3.connect`는 존재했지만 기존 런타임은 top-level
  `sqlcipher3.connect`를 기대했다.
- 패키지를 재설치하고 DB-API fallback을 추가한 뒤 Agent가 다시 event를 accept하고
  lookup job을 완료했다.

이 장애는 단순 의존성 문제만이 아니다. 운영 관점에서는 "inbound가 켜져 있는데 실제로
읽지 못하는 상태"를 웹앱이 충분히 크게 알려주지 못한 것도 문제다.

## 프로젝트 구조 요약

이 저장소는 세 런타임 경계로 나뉜다.

| 경계 | 위치 | 책임 |
| --- | --- | --- |
| 크롤링/공용 재사용 | `src/rider_crawl` | Baemin/Coupang 화면 읽기, Kakao DB reader, Kakao sender, 공용 parser/renderer |
| Windows Agent | `src/rider_agent` | 서버 job polling, heartbeat, browser/Kakao worker 실행, 로컬 Kakao DB scan |
| 중앙 서버/WebApp | `src/rider_server` | FastAPI API, job queue, admin dashboard, channel/target/tenant 정책, audit |

Kakao inbound 관련 핵심 파일:

| 파일 | 현재 책임 |
| --- | --- |
| `src/rider_crawl/kakao_db.py` | KakaoTalk SQLCipher DB를 복사/열고 방 목록 및 최신 메시지를 읽는다. `sqlcipher3` 의존성은 이 경계 안에 있어야 한다. |
| `src/rider_crawl/rider_lookup.py` | `!!이름1234` 명령 parser와 라이더 매칭/렌더링 공용 로직. 현재 한 메시지에서 첫 번째 valid token만 처리한다. |
| `src/rider_agent/reuse.py` | Agent가 `rider_crawl` 기능을 가져오는 재사용 경계. Agent가 `sqlcipher3`를 직접 import하지 않도록 막는다. |
| `src/rider_agent/kakao_inbound.py` | watcher, event client, local settings loader, server watchlist client, health state/reason 정의. |
| `src/rider_agent/__main__.py` | CLI `run`에서 local settings, secure store, server watchlist client를 조립해 watcher를 `run_agent`에 주입한다. |
| `src/rider_agent/job_loop.py` | inbound polling thread 실행, watcher health를 `kakao_status.inbound`로 heartbeat provider에 병합한다. |
| `src/rider_agent/heartbeat.py` | `/v1/agents/heartbeat` payload를 만든다. token/secret은 body에 넣지 않는다. |
| `src/rider_server/api/agents.py` | heartbeat 수신과 `/v1/agents/kakao-inbound-config` watchlist 제공. |
| `src/rider_server/services/agent_registry.py` | heartbeat `capacity_json` 저장 전 allowlist/sanitizer 적용. `kakao_status.inbound`는 허용하되 민감 key는 제거한다. |
| `src/rider_server/admin/dashboard_repository_postgres.py` | Agent heartbeat와 audit log를 dashboard facts로 읽는다. |
| `src/rider_server/admin/dashboard_service.py` | `AgentHealthFacts`를 `AgentRow`로 변환하고 `kakao_inbound_state/reason`을 화면 모델에 매핑한다. |
| `src/rider_server/admin/routes.py` | `/admin`, `/admin/agents`, `/admin/kakao-inbound` HTML fragment 제공. |
| `src/rider_server/admin/templates/dashboard.html` | 상단 상태 배너와 dashboard 전체 레이아웃. 현재 Kakao inbound health는 배너 집계에 포함되지 않는다. |
| `src/rider_server/admin/templates/_agents.html` | Agent fleet 표. 현재 `inbound <state>`와 `<reason>`을 표시한다. |
| `src/rider_server/admin/templates/_kakao_inbound.html` | 서버가 받은 Kakao inbound event decision 목록. DB 리더가 죽어 event가 오지 않으면 이 표는 비어 있다. |

보호 대상 주의:

- `src/rider_agent/worker_composition.py`는 Coupang 2FA 보호 파일 목록에 포함되어 있다.
- 이 보강의 1차 구현은 `worker_composition.py`를 건드리지 않는 방향을 우선한다.
- Coupang login/email 2FA protected runtime/test set은 이 작업 범위 밖이다.

## 현재 데이터 흐름

### 1. 설정/watchlist 흐름

```text
Admin/WebApp 설정
  -> server messenger_channels / delivery_rules
  -> GET /v1/agents/kakao-inbound-config
  -> Agent KakaoWatchlistClient
  -> RefreshingKakaoInboundWatcher
  -> local settings + secure store secret + session probe와 병합
```

effective enabled 조건:

```text
local enabled
&& db_key/user_hash/db_path 존재
&& interactive Windows session
&& server watchlist enabled
&& server watchlist rooms non-empty
```

하나라도 실패하면 watcher는 동작하지 않는다.

### 2. 정상 명령 처리 흐름

```text
KakaoTalk local DB
  -> rider_crawl.kakao_db reader
  -> rider_agent.kakao_inbound.KakaoInboundWatcher.scan_once()
  -> parse_rider_lookup_command()
  -> POST /v1/kakao/inbound-events
  -> server decide_inbound_event()
  -> RIDER_LOOKUP job enqueue
  -> Agent worker lookup
  -> KAKAO_SEND reply job
  -> KakaoSenderWorker
```

이 흐름에서 raw message text, parsed name, phone suffix, room name, DB path, DB key,
user hash 원문은 status/heartbeat/log에 넣으면 안 된다.

### 3. health/관측 흐름

```text
KakaoInboundWatcher.health()
  -> job_loop._merge_kakao_status_provider()
  -> heartbeat kakao_status.inbound
  -> POST /v1/agents/heartbeat
  -> agent_registry.heartbeat_capacity()
  -> agents.capacity_json
  -> DashboardService.agent_row()
  -> _agents.html Agent fleet 표
```

현재 이 경로는 watcher 객체가 존재할 때만 안정적으로 동작한다. watcher 조립 이전 단계에서
local config가 없거나 `enabled=false`이면 `_build_kakao_inbound_watcher()`가 `None`을 반환하고,
`kakao_status.inbound` 자체가 누락될 수 있다.

### 4. inbound decision 관측 흐름

```text
POST /v1/kakao/inbound-events
  -> kakao_inbound_event_service
  -> kakao_inbound_wiring audit log
  -> DashboardService.kakao_inbound_rows()
  -> /admin/kakao-inbound
```

이 경로는 서버가 event를 받은 뒤의 판단 결과만 보여준다. DB 리더가 죽었거나 watchlist가
비어 있어 event가 전혀 오지 않는 상태는 이 카드에 직접 남지 않는다.

## 현재 health 상태와 의미

`src/rider_agent/kakao_inbound.py`의 안전한 고정 state/reason:

| state | reason | 의미 | 운영 심각도 제안 |
| --- | --- | --- | --- |
| `active` | `ok` | 정상 scan 가능 | 정상 |
| `degraded` | `latest_window_size_1` | latest-N이 아니라 latest-one fallback. 동작은 하나 메시지 누락 가능성이 높다. | 주의 |
| `warning` | `configured_room_not_found` | 설정된 방을 로컬 Kakao DB에서 찾지 못함 | 주의 |
| `disabled` | `feature_disabled` | 로컬 kill switch off. 의도된 off이면 정상, 서버 watchlist가 켜져 있으면 주의 | 조건부 |
| `disabled` | `sqlcipher_missing` | SQLCipher DB reader 의존성 없음 | 위험 |
| `disabled` | `db_unavailable` | DB open/list/read 실패. 이번 장애의 직접 reason | 위험 |
| `disabled` | `db_key_missing` | DB key/schema/key 관련 오류 | 위험 |
| `disabled` | `non_interactive_session` | KakaoTalk 접근 가능한 interactive session이 아님 | 위험 |
| `disabled` | `prerequisites_missing` | db_key/user_hash/db_path 등 로컬 필수값 누락 | 위험 |
| `disabled` | `empty_watchlist` | 서버 watchlist에 scan할 방이 없음 | 주의 |

중요한 구분:

- `degraded/latest_window_size_1`은 완전 장애가 아니라 신뢰도 저하다.
- `feature_disabled`는 의도된 off일 수 있으므로, 서버가 해당 Agent/tenant에 inbound를 기대하는지와 함께 판단해야 한다.
- `db_unavailable`, `sqlcipher_missing`, `prerequisites_missing`, `non_interactive_session`은 활성 운영 중이면 즉시 조치 대상이다.

## 발견된 격차

### Gap 1. 웹앱 상단 상태 배너가 Kakao inbound 불량을 계산하지 않는다

`dashboard.html`의 status banner는 target severity, auth required, stuck job, Agent offline,
Kakao send queue lag, Telegram error를 집계한다. `AgentRow.kakao_inbound_state/reason`은
Agent fleet 표에만 있고 배너 집계에 들어가지 않는다.

결과적으로 운영자는 화면을 열어도 상단에 "정상" 또는 다른 요약만 보고 지나갈 수 있다.

### Gap 2. watcher 조립 전 실패는 heartbeat에서 사라질 수 있다

`__main__._build_kakao_inbound_watcher()`는 local settings가 없거나 disabled이면 `None`을
반환한다. `job_loop._merge_kakao_status_provider()`는 watcher가 `None`이면 inbound health를
병합하지 않는다.

결과적으로 "설정 파일 없음", "bad JSON", "local enabled false" 같은 조립 전 상태는
웹앱에서 구분하기 어렵다.

### Gap 3. `/admin/kakao-inbound`는 event decision 카드라서 reader 장애를 설명하지 않는다

DB reader가 죽으면 event가 서버로 오지 않는다. 이때 `/admin/kakao-inbound`는 "최근 event
없음"만 보여줄 수 있고, "왜 event가 없는지"는 알려주지 않는다.

### Gap 4. 패키징 계약이 운영 설치 절차에 충분히 고정되지 않았다

`sqlcipher3`는 Kakao inbound DB reader의 선택 의존성이다. 현재 `pyproject.toml`에는
`kakao` extra가 있지만, 운영 설치/재설치/패키징 절차가 이 extra를 반드시 쓰는지 별도
검증해야 한다.

또한 Windows wheel이 top-level `sqlcipher3.connect`가 아니라 `sqlcipher3._sqlite3.connect`
를 제공하는 경우가 있으므로, runtime smoke test가 필요하다.

### Gap 5. 한 메시지 다중 명령은 별도 기능이다

현재 parser는 한 Kakao message에서 첫 번째 valid token만 처리한다. 따라서
`!!A1111, !!B2222, !!C3333`은 DB reader가 정상이어도 기본적으로 한 건만 처리된다.
이 문서의 주제는 health alerting이므로 다중 명령 처리는 별도 설계/계획으로 분리한다.

## 보강 목표

### 필수 목표

1. Kakao inbound가 운영상 기대되는 Agent에서 불량이면 웹앱 상단 상태 배너에 반드시 드러난다.
2. watcher가 만들어진 뒤 발생하는 scan/read 장애뿐 아니라 watcher 조립 전 실패도 heartbeat로
   전달된다.
3. `/admin/kakao-inbound` 카드가 비어 있을 때도, Agent inbound health가 불량이면 운영자가
   같은 화면에서 원인을 찾을 수 있다.
4. 모든 상태 값은 고정 reason code만 사용한다. raw room name, raw message, parsed name,
   phone suffix, DB path, DB key, user hash 원문은 금지한다.
5. 설치/패키징은 Kakao inbound 의존성 유무를 재현 가능하게 검증한다.

### 비목표

- KakaoTalk 방이나 Telegram으로 out-of-band 알림을 자동 발송하지 않는다. 1차 보강은 Admin
  WebApp 화면 경고다.
- raw Kakao room/message를 dashboard에 노출하지 않는다.
- Coupang login/email 2FA protected flow를 변경하지 않는다.
- 한 메시지 다중 `!!` 명령 처리는 이 보강에 포함하지 않는다.
- 새 DB 테이블을 1차 보강에서 만들지 않는다. 필요하면 후속 alert history 설계로 분리한다.

## 권장 설계

### 접근안 비교

| 접근 | 내용 | 장점 | 단점 | 판단 |
| --- | --- | --- | --- | --- |
| A. Dashboard banner 보강 | heartbeat `kakao_status.inbound`를 읽어 상단 배너와 Agent fleet에 더 강하게 표시 | 빠르고 DB migration 없음, 현재 구조와 잘 맞음 | 히스토리/ack 없음 | 1차 권장 |
| B. Alert record/audit 추가 | health 전이를 audit/alert로 저장하고 `/admin/alerts` 또는 카드에 표시 | 장애 이력 추적 가능 | schema/API/UI 범위 증가 | 2차 후보 |
| C. Out-of-band 운영자 알림 | Kakao/Telegram/email로 운영자에게 자동 전송 | 화면을 안 봐도 알 수 있음 | 오발송/중복/수신자 관리/비밀값 문제 | 후속 별도 설계 |

1차는 A를 구현한다. 단, A가 제대로 동작하려면 Agent가 조립 전 실패도 heartbeat로 보내야 한다.

### Agent health source 보강

현재 `run_agent(kakao_inbound_watcher=...)`는 watcher가 있을 때만 inbound health를 병합한다.
다음 계약을 추가한다.

```text
kakao_inbound_health_source:
  - health() -> dict[str, str | int | bool | None]
  - scan thread는 돌지 않아도 됨
  - heartbeat에만 사용 가능
```

구현 방향:

1. `src/rider_agent/kakao_inbound.py`
   - 안전한 helper를 추가한다.
   - 예: `static_kakao_inbound_health(state, reason, **metrics)` 또는 작은 dataclass.
   - 반환 값은 `_safe_kakao_inbound_health()` allowlist 안의 key만 사용한다.

2. `src/rider_agent/__main__.py`
   - `_build_kakao_inbound_watcher()`가 단순 `object | None`만 반환하는 대신, 내부적으로
     조립 결과 reason을 보존할 수 있게 한다.
   - local config missing/bad JSON/disabled, identity missing, setup exception도
     `{"state": "disabled", "reason": "<fixed_reason>"}`로 heartbeat에 실을 수 있어야 한다.
   - 단, local `enabled=false`가 의도된 off인지 운영 불량인지는 서버가 완전히 알 수 없으므로
     reason은 `feature_disabled`로 고정한다.

3. `src/rider_agent/job_loop.py`
   - scan 대상 watcher와 heartbeat용 health source를 분리한다.
   - watcher가 `None`이어도 health source가 있으면 `kakao_status.inbound`를 병합한다.
   - inbound polling thread는 실제 watcher가 있을 때만 시작한다.

보호 파일 회피:

- `src/rider_agent/worker_composition.py`를 수정하지 않고, `run_agent`의 기존
  `kakao_status_provider` 병합 경로 또는 새 `kakao_inbound_health_source` 인자를 사용한다.
- `worker_composition.py` 변경이 꼭 필요해지면 AGENTS.md의 protected 절차를 따른다.

### Server 저장 계약 유지

`agent_registry.heartbeat_capacity()`는 이미 `kakao_status` allowlist에 `inbound`를 허용하고,
민감 key(`message`, `room`, `path`, `secret`, `password`, `token` 등)를 제거한다.

보강 후에도 저장 payload는 아래 형태를 넘지 않는다.

```json
{
  "kakao_status": {
    "enabled": true,
    "state": "idle",
    "interactive_session_available": true,
    "inbound": {
      "state": "disabled",
      "reason": "db_unavailable",
      "latest_window_size": 20,
      "configured_missing_count": 0
    }
  }
}
```

금지 예:

```json
{
  "inbound": {
    "room_name": "운영방",
    "message": "!!홍길동1234",
    "db_path": "C:/Users/...",
    "db_key": "..."
  }
}
```

### Admin dashboard 보강

`DashboardService.agent_row()`는 이미 inbound state/reason을 `AgentRow`로 매핑한다.
1차 보강은 template 계산만으로 시작할 수 있다.

`dashboard.html` 상단 집계에 다음 개념을 추가한다.

```text
kakao_inbound_bad =
  online Agent 중
  kakao_inbound_state가 존재하고
  kakao_inbound_state != "active"
  그리고 reason != "feature_disabled" 또는 운영상 기대 enabled
```

권장 severity:

| 조건 | 배너 |
| --- | --- |
| online Agent의 inbound reason이 `db_unavailable`, `sqlcipher_missing`, `db_key_missing`, `prerequisites_missing`, `non_interactive_session` | critical |
| online Agent의 inbound reason이 `configured_room_not_found`, `empty_watchlist`, `latest_window_size_1` | warning |
| inbound state/reason 없음 | warning 후보. 단, Kakao inbound를 기대하는지 알 수 없으면 Agent fleet에서만 표시 |
| offline Agent | 기존 offline 집계 유지 |
| `feature_disabled` | 기본 neutral. 서버 watchlist가 enabled인데 local disabled임을 알 수 있는 후속 데이터가 있으면 warning |

상단 detail 예:

```text
Kakao inbound 1대 확인 필요
Kakao inbound 1대 장애
```

Agent fleet 표에서는 기존 표시를 유지하되, 위험 reason은 `sev-critical`, 주의 reason은
`sev-warning`으로 구분한다.

### `/admin/kakao-inbound` 안내 보강

이 카드는 event decision 전용임을 유지한다. 대신 빈 상태 메시지를 다음처럼 바꾼다.

```text
최근 Kakao inbound event가 없습니다. Agent fleet의 Kakao inbound 상태가 disabled/warning이면
DB 리더, SQLCipher, watchlist, interactive session을 먼저 확인하세요.
```

이 문구에는 room/message/name/phone이 들어가지 않는다.

### 설치/패키징 보강

Kakao inbound Agent 설치는 다음 중 하나를 만족해야 한다.

1. venv 설치:

```powershell
uv sync --extra kakao
```

또는 이미 설치된 venv에서:

```powershell
uv pip install --python .\.venv\Scripts\python.exe "sqlcipher3>=0.6.2"
```

2. 패키지 검증:

```powershell
.\.venv\Scripts\python.exe - <<'PY'
import importlib

mod = importlib.import_module("sqlcipher3")
connect = getattr(mod, "connect", None)
if connect is None:
    sub = importlib.import_module("sqlcipher3._sqlite3")
    connect = getattr(sub, "connect", None)
assert callable(connect), "sqlcipher3 DB-API connect is unavailable"
conn = connect(":memory:")
try:
    assert conn.execute("select 1").fetchone()[0] == 1
finally:
    conn.close()
print("sqlcipher3 ok")
PY
```

3. PyInstaller/onefile을 Agent inbound 배포에 사용한다면 다음을 검토한다.

- `sqlcipher3`와 `sqlcipher3._sqlite3` hidden import 필요 여부.
- `_sqlite3.cp310-win_amd64.pyd` 같은 native extension 포함 여부.
- packaged exe에서 위 smoke test와 동일한 import/connect 검증.

현재 `rider_crawl_onefile.spec`는 legacy crawler onefile 중심이며 `sqlcipher3` hidden import가 없다.
이 spec을 Kakao inbound Agent 배포물로 재사용한다면 별도 보강이 필요하다.

## 테스트 전략

### Agent tests

대상:

- `tests/agent/test_kakao_inbound.py`
- `tests/agent/test_job_loop.py`
- `tests/agent/test_heartbeat.py`

추가해야 할 케이스:

1. watcher가 존재하고 `health()`가 `disabled/db_unavailable`이면 heartbeat payload에
   `kakao_status.inbound.reason == "db_unavailable"`이 들어간다.
2. watcher가 `None`이어도 health source가 있으면 inbound health가 heartbeat에 들어간다.
3. watcher가 `None`이고 health source도 없으면 기존 기본 `DEFAULT_KAKAO_STATUS` 무회귀.
4. local config bad JSON 또는 missing file은 crash 없이 `feature_disabled` 또는
   명시적 fixed reason으로 표면화된다.
5. health payload에 `room_name`, `message`, `db_path`, `db_key`, `user_hash`, parsed name,
   phone suffix가 들어가지 않는다.

### Server API/storage tests

대상:

- `tests/server/test_agents_api.py`
- `tests/server/test_admin_dashboard.py`

추가해야 할 케이스:

1. heartbeat의 nested `kakao_status.inbound`가 저장되며 민감 key는 제거된다.
   기존 `test_heartbeat_preserves_nested_kakao_inbound_status`를 확장한다.
2. Admin dashboard full page가 `db_unavailable` inbound 상태를 상단 warning/critical banner에
   포함한다.
3. `feature_disabled`는 기본적으로 critical로 올리지 않는다.
4. `_agents.html`은 reason별 severity class를 다르게 렌더한다.
5. `/admin/kakao-inbound` 빈 상태 문구가 event decision 카드의 한계를 안내한다.

### Package/runtime tests

대상:

- `tests/test_kakao_db.py`
- packaging smoke script 또는 운영 runbook

필수 검증:

1. top-level `sqlcipher3.connect`가 없어도 `sqlcipher3._sqlite3.connect`를 fallback으로 사용한다.
2. `sqlcipher3`가 아예 없으면 `sqlcipher_missing`으로 fail closed 된다.
3. 설치 후 실제 venv에서 `connect(":memory:").execute("select 1")`가 성공한다.
4. `uv lock`에 `sqlcipher3`가 `kakao` extra로 고정되어 있다.

## 운영 Runbook

Kakao inbound가 동작하지 않을 때 운영자는 다음 순서로 확인한다.

1. Admin WebApp 상단 배너 확인
   - `Kakao inbound 장애` 또는 `Kakao inbound 확인 필요`가 보이면 Agent fleet로 이동한다.

2. Agent fleet의 Kakao 상태 확인
   - `inbound disabled db_unavailable`: SQLCipher 패키지/DB 파일 접근/DB lock 확인.
   - `inbound disabled sqlcipher_missing`: venv 설치 또는 배포 패키지 누락.
   - `inbound disabled non_interactive_session`: 예약 작업이 interactive user session에서 실행 중인지 확인.
   - `inbound warning configured_room_not_found`: 서버 watchlist 방 설정과 실제 KakaoTalk 방 존재 여부 확인.
   - `inbound degraded latest_window_size_1`: latest-one fallback 상태이므로 짧은 burst 유실 가능성을 안내.

3. Agent PC에서 설치 확인

```powershell
.\.venv\Scripts\python.exe -c "import sqlcipher3; print(sqlcipher3, hasattr(sqlcipher3, 'connect'))"
.\.venv\Scripts\python.exe -m pytest tests\test_kakao_db.py tests\agent\test_kakao_inbound.py -q
```

4. Agent 재시작

```powershell
schtasks /run /tn RiderBotAgent
```

5. 회복 확인
   - Agent 로그에 `AGENT_KAKAO_INBOUND_VERDICT` accepted true가 찍히는지 확인.
   - 서버 실시간 큐에 `RIDER_LOOKUP` job이 생성되는지 확인.
   - lookup complete 후 reply `KAKAO_SEND` 흐름이 정상인지 확인.

## 구현 순서 제안

1. Agent heartbeat health source 보강
   - watcher 조립 전 disabled reason도 heartbeat에 들어가게 만든다.
   - protected `worker_composition.py`는 건드리지 않는다.

2. Server/Admin banner 보강
   - `dashboard.html` 상단 집계에 inbound critical/warning count를 추가한다.
   - `_agents.html` reason 표시 class를 세분화한다.

3. `/admin/kakao-inbound` 빈 상태 안내 문구 보강
   - event decision 카드와 health 카드의 차이를 운영자가 알 수 있게 한다.

4. Packaging/runbook 보강
   - `docs/operations` 또는 Agent PC setup 문서에 `uv sync --extra kakao`와 smoke test를 추가한다.
   - onefile/installer가 Agent inbound 배포에 쓰이면 `sqlcipher3` native extension 포함 검증을 추가한다.

5. Optional 후속
   - alert history/ack 모델.
   - 운영자 out-of-band 알림.
   - 한 메시지 다중 `!!` token 처리.

## 완료 기준

1. Kakao inbound가 `db_unavailable`이면 다음 heartbeat 이후 Admin WebApp 상단 배너가 warning
   또는 critical로 바뀐다.
2. Agent fleet 표에서 `inbound disabled db_unavailable`이 민감값 없이 보인다.
3. local config가 없거나 disabled인 경우에도 의도된 `feature_disabled` 또는 고정 reason이
   heartbeat에 나타난다.
4. `/admin/kakao-inbound`가 비어 있어도 운영자는 Agent fleet를 확인해야 한다는 안내를 본다.
5. `tests/agent/test_kakao_inbound.py`, `tests/agent/test_job_loop.py`,
   `tests/server/test_agents_api.py`, `tests/server/test_admin_dashboard.py`,
   `tests/test_kakao_db.py`의 관련 테스트가 통과한다.
6. 운영 venv 또는 배포 패키지에서 `sqlcipher3` import/connect smoke test가 통과한다.

## 남은 결정 사항

1. `feature_disabled`를 언제 warning으로 볼 것인가?
   - 서버 watchlist가 enabled인데 특정 Agent local switch만 꺼진 상태를 서버가 알 수 있어야 한다.
   - 1차에서는 neutral로 두고, dashboard에는 Agent fleet 세부 표시만 유지하는 것이 안전하다.

2. dashboard 상단 배너의 critical/warning 문구를 어느 정도로 구체화할 것인가?
   - 권장: 상단은 count 중심, 세부 reason은 Agent fleet에서 표시.

3. alert history를 바로 만들 것인가?
   - 권장: 1차에서는 만들지 않는다. heartbeat 최신 상태와 banner만으로 이번 장애 유형은 충분히 잡힌다.

4. 운영자 out-of-band 알림을 붙일 것인가?
   - 권장: 별도 설계. 수신자, 중복 억제, quiet hour, ack, 개인정보 비노출 계약이 필요하다.
