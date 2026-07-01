# KakaoTalk `chatLogs_<id>.edb` 스키마 조사 가이드 (Phase 5 준비)

> 이 문서는 **운영자가 실제 KakaoTalk PC 에서 수행**하는 조사 절차다. 설계
> (`2026-07-01-kakao-inbound-rider-lookup-design.md`) Phase 5 는 *"Latest-20
> requires confirmed `chatLogs_<id>.edb` schema. Implement only after
> [confirmed]"* 이므로, latest-20 리더를 코딩하기 **전에** 이 조사로 스키마를
> 확정해야 한다. 스키마가 확정되기 전에는 latest-one fallback
> (`ChatRoomListReader`, `latest_window_size == 1`)을 degraded health 로 유지한다.

## 목적

현재 인바운드 워처는 `chatListInfo.edb` 의 `chatRoomList`(방당 **마지막 1건**)만
읽는다. 폴링 간격 사이에 같은 방에서 명령이 2건 이상 오면 이전 것을 놓칠 수 있다
(`gap_possible`). Latest-20 은 방별 로그 DB `chatLogs_<id>.edb` 에서 최근 N건을
읽어 이 공백을 줄인다. 그러려면 이 DB 의 **테이블/컬럼/타임스탬프/암호화 여부**를
실제로 확인해야 한다 — 리서치 자료(`docs/kakao_db`)에는 `chatRoomList` 스키마만
있고 `chatLogs_<id>.edb` 스키마는 없다.

## ⚠️ 안전·프라이버시 규칙 (반드시 준수)

- **실제 DB 키, 사용자 해시, MachineGuid/sys_uuid/dev_id/MAC, 계정 이메일, room ID,
  chat 로그, 메시지 본문을 이 저장소(코드/문서/커밋/PR)에 절대 넣지 않는다.**
  조사 산출물은 **스키마(테이블명·컬럼명·타입·의미)뿐**이다.
- 키/해시/경로는 운영자 로컬의 환경변수 또는 로컬 파일에서만 읽는다(아래 스크립트가
  그렇게 되어 있다). 하드코딩 금지.
- 복호화는 항상 **복사본**에 대해 수행한다(KakaoTalk 이 원본을 잠글 수 있고, 원본
  손상 위험을 피한다).
- 조사가 끝나면 복사본(`*_tmp.db`)과 덤프를 삭제한다.

## 사전 준비

- KakaoTalk 로그인 상태의 Windows PC(조사 대상 계정).
- `pip install sqlcipher3`.
- 대상 계정의 DB 복호화 키. `chatLogs_<id>.edb` 는 `chatListInfo.edb` 와 **다른
  per-DB 키**일 수 있다(`keystore.bin` 이 DB별 키 저장소). 먼저 chatListInfo 키로
  열어 보고, 실패하면 `keystore.bin`/메모리 덤프에서 해당 DB 키를 확보한다.

## 절차

### 1. 대상 파일 경로 확인

```
%LOCALAPPDATA%\Kakao\KakaoTalk\users\<USER_HASH>\chat_data\
    ├── chatListInfo.edb        ← 방 목록 (현재 latest-one 소스)
    └── chatLogs_<CHAT_ID>.edb  ← 방별 메시지 로그 (조사 대상)
```

트리거하려는 방의 `<CHAT_ID>` 를 고른다(운영 방의 chatId — `chatRoomList.chatId`
로 확인 가능). 파일이 방마다 하나씩 있다.

### 2. 복호화 키 확보

- 1차: `chatListInfo.edb` 에 쓰던 키로 `chatLogs_<CHAT_ID>.edb` 열기 시도.
- 실패 시: `keystore.bin` 에서 해당 DB 키를 추출하거나(포맷은 별도 조사 필요),
  메모리 덤프에서 `x'<hex>'` 패턴을 전수 시도한다(설계 자료의 키 갱신 절차와 동일한
  방식). **추출한 키 값은 기록/커밋하지 않는다.**

### 3. SQLCipher 로 열기 (복사본)

프로젝트 코드(`rider_crawl/kakao_db.py`)와 **동일한 open idiom** 을 쓴다:

```python
conn.execute("PRAGMA cipher_compatibility = 4")
conn.execute("PRAGMA key = \"x'\" + KEY + \"'\"")   # KEY 는 절대 로그로 출력하지 않는다
```

### 4. 스키마 덤프

```sql
SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name;
-- 각 테이블에 대해:
PRAGMA table_info('<table>');
```

기록: 테이블 목록과 각 컬럼(name/type/notnull/pk). **값은 조회하지 않는다.**

### 5. 메시지 테이블 식별 + latest-N 쿼리 검증

메시지 로그 테이블을 찾는다(예상 후보 컬럼 — 실제 이름은 조사로 확정):

| 필요 개념 | 예상 컬럼(확정 필요) | 매핑 대상(`KakaoMessageRef`) |
|---|---|---|
| 메시지 고유 ID(단조 증가) | `logId` / `id` | `log_id` |
| 방 ID | `chatId` (파일당 단일일 수도) | `chat_id` |
| 본문 | `message` / `text` / `v`(JSON?) | `text` |
| 시각 | `createdAt` / `sendAt`(epoch 초?) | `timestamp` |
| 종류(일반/삭제/시스템) | `type` | (필터용) |

latest-N 후보 쿼리(컬럼명 확정 후):

```sql
SELECT <logId>, <message>, <createdAt>, <type>
FROM <message_table>
WHERE <message> LIKE '%!!%'          -- CANDIDATE_LIKE 와 동일한 prefilter
ORDER BY <logId> DESC                 -- 또는 createdAt DESC (단조성 확인 후 택1)
LIMIT 20;
```

### 6. 확인 항목 체크리스트

- [ ] 메시지 테이블/컬럼 이름 확정.
- [ ] `logId`(또는 정렬 키)가 **단조 증가**하는가(하이워터마크로 안전한가).
- [ ] `createdAt` 단위(초/밀리초)와 타임존.
- [ ] 본문이 평문인지, JSON/추가 암호화인지(추가 복호화 필요 여부).
- [ ] 삭제/시스템/피드 메시지를 거르는 `type` 값.
- [ ] 파일이 방당 1개면 `chat_id` 를 파일명 `<CHAT_ID>` 에서 얻어야 하는지.
- [ ] 이전 하이워터마크가 최근 20건 밖으로 밀리면 `gap_possible=true` 로 보고 가능한지.

## 조사 결과 기록 템플릿 (값 없이 스키마만)

```
message_table = "____"
columns:
  log_id   -> "____" (type ____, 단조 ____)
  chat_id  -> "____" | 파일명에서 파생
  text     -> "____" (평문? ____ / JSON? ____)
  timestamp-> "____" (단위 ____, tz ____)
  type     -> "____" (제외값: ____)
latest_20_query verified: yes/no
notes: ____
```

## 결과를 코드로 반영하는 법

스키마가 확정되면 `rider_crawl/kakao_db.py` 에 **새 리더**를 추가한다. 기존
`KakaoDbReader` Protocol 을 그대로 구현해 워처는 무변경으로 교체 가능해야 한다:

```python
class KakaoDbReader(Protocol):
    latest_window_size: int
    def list_rooms(self) -> list[KakaoRoomRef]: ...
    def latest_messages(self, room: KakaoRoomRef, limit: int) -> list[KakaoMessageRef]: ...
```

- 신규 `ChatLogsReader`: `latest_window_size = 20`, `latest_messages()` 는 위 검증
  쿼리로 방별 최근 N건을 `KakaoMessageRef(chat_id, room_name, log_id, timestamp,
  text)` 로 반환(order: 오래된→최신 또는 최신→오래된 중 워처 dedupe 와 맞는 방향으로;
  현재 워처는 `(chat_id or room_name, log_id)` 로 dedupe).
- `list_rooms()` 는 계속 `chatListInfo.edb`(`ChatRoomListReader`)로 방을 찾고,
  `latest_messages()` 만 `chatLogs_<id>.edb` 로 승격하는 조합도 가능(방 발견과 메시지
  수집을 분리).
- **Fallback 유지**: `chatLogs` 열기/스키마 실패 시 `ChatRoomListReader`
  (`latest_window_size == 1`)로 자동 강등하고 degraded health 로 표면화한다(설계의
  "Keep fallback available with degraded health").
- SQLCipher 미설치/키 실패는 **crash 가 아니라** disabled/degraded health
  (`kakao_inbound.py` 의 기존 health 상태 재사용).

## 운영자 로컬 검증 스크립트 예시 (저장소 커밋 금지)

아래를 운영자 로컬에만 두고 실행한다. **키/해시/경로는 환경변수로만** 주입하며 값은
출력하지 않는다. 산출물은 스키마 텍스트뿐이다.

```python
# investigate_chatlogs.py  (로컬 전용, 커밋하지 말 것)
import os, shutil, sqlcipher3

key = os.environ["KAKAO_DB_KEY"]          # 로컬 환경변수, 절대 print 하지 않는다
src = os.environ["KAKAO_CHATLOGS_PATH"]    # ...\chatLogs_<CHAT_ID>.edb
tmp = src + ".invtmp"
shutil.copy2(src, tmp)
try:
    conn = sqlcipher3.connect(tmp)
    conn.execute("PRAGMA cipher_compatibility = 4")
    conn.execute("PRAGMA key = \"x'\" + key + \"'\"")
    for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        print("TABLE", name)
        for cid, col, ctype, notnull, dflt, pk in conn.execute(f"PRAGMA table_info('{name}')"):
            print("   COL", col, ctype, "notnull" if notnull else "", "pk" if pk else "")
    conn.close()
finally:
    os.remove(tmp)
# 스키마 확인 후 값 조회가 필요하면, 본문/이름/전화는 절대 로그로 남기지 말 것.
```

조사 결과(위 기록 템플릿을 채운 스키마)를 공유해 주시면, 그에 맞춰
`ChatLogsReader` + latest-20 승격 + fallback/health 강등을 구현한다.

## Confirmed Result - 2026-07-01

No secret values, chat IDs, room names, account identifiers, or message bodies
were recorded. The schema was checked from copied local DB files only.

```
message_table = "chatLogs"
columns:
  log_id    -> "logId" (UNSIGNED BIG INT, primary key, monotonic)
  chat_id   -> derived from file name chatLogs_<chat_id>.edb
  text      -> "message" (TEXT, plaintext after SQLCipher open)
  timestamp -> "sendAt" (INTEGER epoch seconds)
  type      -> "type" (INTEGER)
  deleted   -> "deleted" (INTEGER, filter with COALESCE(deleted, 0) = 0)
latest_20_query verified: yes
notes:
  - SQLCipher open uses PRAGMA cipher_compatibility = 4 and raw hex key.
  - chatListInfo.edb and chatLogs_<id>.edb can use different keys.
  - latest-N reader returns oldest-to-newest after selecting newest rows so the
    watcher can process new messages in high-water order.
```

Implemented follow-up:

- `ChatLogsReader` reads `chatLogs_<chat_id>.edb` latest candidates with
  `latest_window_size = 20`.
- Room discovery still uses `chatListInfo.edb` / `chatRoomList`.
- If chatLogs open/schema/key lookup fails, the reader falls back to
  `ChatRoomListReader` and reports `latest_window_size = 1` for degraded health.
- The watcher primes latest-N startup to the newest visible message and reports
  `gap_possible` instead of flooding old visible messages when the previous
  high-water mark falls outside the latest-N window.
