# 로컬 런타임/메시징 종합 검토

작성일: 2026-06-20  
반영 상태: 코드/문서 적용 완료 (2026-06-20)  
대상 저장소: `rider_result_mornitoring`  
목적: 전체 구조, 로컬 UI 설정, 크롤링/파싱/브라우저 연결, 로컬 메시징/명령 응답을 함께 검토하고 후속 작업의 우선순위를 정한다.

## 적용 내역

2026-06-20에 이 검토 결과를 코드와 문서에 반영했다. UI 설정 보존, 전체 활성 탭 2FA 검증, Windows DPAPI 기본 secret store, CDP/profile/run/Kakao lock 경계, 쿠팡 센터/2FA fail-closed, 쿠팡/배민 parser 보강, Telegram routing snapshot/keyword lock/redaction/local rate limit, 아키텍처 문서와 import guard를 적용했다.

수동 UI/카카오/실 쿠팡 계정 점검은 실제 Windows 앱과 운영 계정이 필요해 이 문서 반영 시점에는 실행하지 않았다. 대신 관련 자동 회귀 테스트를 추가했다.

## 1. 결론

현재 코드는 "로컬 PC에서 1개 운영자가 1개 UI로 9개 설정 탭을 관리한다"는 범위에서는 많은 안전장치를 갖고 있다. 저장 전 검증, 전송 전 확인, 텔레그램/카카오 필수값 검사, 쿠팡 자동 로그인 옵션, parser 테스트가 이미 있다.

하지만 운영 범위가 여러 고객, 여러 Agent, 100개 이상 대상, 장시간 무인 실행으로 커지면 아래 문제가 먼저 터질 가능성이 높다.

- UI 설정 저장이 화면에 렌더된 9개 탭 기준이라, 설정 파일에 10번째 이후 항목이 있으면 저장 시 조용히 사라질 수 있다.
- 쿠팡 센터 검증은 예외가 일부 삼켜지고, heading이 없을 때 성공처럼 지나갈 수 있다.
- 쿠팡 이메일 2FA는 계정 확인 전에 인증 코드를 보낼 수 있고, 화면 계정 매칭도 도메인 수준이라 약하다.
- 카카오톡 전송 lock은 UI 경로에만 있고, 실제 clipboard/hotkey를 쓰는 messenger 경계에는 없다.
- 로컬 실행 lock 위치가 `LOG_DIR`에 따라 달라져 같은 CDP/profile을 두 프로세스가 쓸 수 있다.
- 문서는 서버/Agent/dispatcher 구조를 일부 과거 상태로 설명한다. 실제 Docker Compose에는 queue recovery와 telegram dispatch가 있다.
- 로컬 secret store는 파일 분리 구조는 있으나 기본 구현이 평문 JSON이다.

따라서 후속 작업은 기능 추가보다 "유실 방지, 잘못된 계정/센터 방지, lock 경계 강화, 비밀값 저장 강화, 문서와 코드 일치"를 먼저 해야 한다.

## 2. 검토 범위

| 번호 | 범위 | 검토 초점 |
| --- | --- | --- |
| 1 | `README.md`, `pyproject.toml`, `docs/module-architecture.md`, `docs/project-current-state-and-structure.md`, `tests/test_architecture.py` | 패키지 경계, 의존성 방향, 서버/Agent/로컬 앱 역할 분리, 문서와 코드 불일치 |
| 2 | `src/rider_crawl/ui.py`, `ui_settings.py`, `app.py`, `config.py`, `secret_store.py`, 관련 UI/config/secret tests | UI 입력 검증, 탭별 설정 분리, 비밀값 저장 방식, 설정 마이그레이션, 사용자 실수 방지 |
| 3 | `src/rider_crawl/crawler.py`, `browser_launcher.py`, `parser.py`, `platforms/**`, `auth/**`, 관련 fixture/test | 배민/쿠팡 parser 안정성, HTML 변경 대응, CDP 연결 실패 처리, 로그인/2FA 흐름, timeout/retry |
| 4 | `src/rider_crawl/sender.py`, `messengers/**`, `telegram_commands.py`, `keyword_responder.py`, `message.py`, `redaction.py`, 관련 tests | 텔레그램/카카오톡 전송 중복, lock, topic 처리, keyword 응답, 개인정보 마스킹, 메시지 포맷 |

## 3. 범위별 판단

### 3.1 전체 구조와 문서

패키지 방향은 대체로 명확하다. 로컬 크롤러(`rider_crawl`), Agent(`rider_agent`), 서버(`rider_server`)가 나뉘어 있고 일부 아키텍처 테스트가 import 방향을 막는다. 이 구조는 유지해야 한다.

다만 문서가 현재 운영 프로세스와 맞지 않는 부분이 있다. `docs/module-architecture.md`는 collect to dispatch loop가 아직 live path가 아니라고 설명하지만, `deploy/docker-compose.yml`에는 `queue-recovery`, `telegram-dispatch` 프로세스가 있고 `src/rider_server/dispatch/__main__.py`도 존재한다. README도 backend와 scheduler 중심으로만 안내한다. 운영자가 문서만 보고 실행하면 dispatcher/recovery를 빠뜨릴 수 있다.

### 3.2 로컬 UI 앱과 설정 저장

로컬 UI는 탭별 설정을 분리하고 저장 전 검증을 수행한다. 하지만 저장 로직이 "현재 UI에 존재하는 탭"을 기준으로 전체 파일을 다시 쓰는 방식이다. 기본 탭 수가 9개라, 파일에 10번째 이후 설정이 있으면 저장 후 사라질 수 있다. 이 문제는 단순 버그가 아니라 나중에 수십 개 설정이나 마이그레이션을 넣을 때 데이터 유실로 이어진다.

또한 쿠팡 자동 2FA 값 검증은 현재 선택된 탭만 검사한다. 모든 탭을 한 번에 저장하면서 선택 탭만 2FA 필수값을 확인하면, 다른 탭의 잘못된 인증 설정이 저장될 수 있다.

### 3.3 크롤링, 파싱, 브라우저 연결

배민 parser는 기존 fixture와 row 구조에는 맞지만, row shape와 fixed offset에 강하게 기대고 있다. HTML 구조가 바뀌면 일부 candidate가 조용히 skip될 수 있다. 쿠팡 parser도 `목표/완료`와 숫자 블록 위치에 기대는 부분이 강하다.

브라우저 연결은 CDP 준비 확인과 launch/wait가 있지만, 같은 profile/CDP를 두 프로세스가 동시에 준비하는 race를 완전히 막지는 못한다. `prepare_chrome()` 내부의 probe, profile free check, launch, wait가 하나의 resource lock 안에 묶여야 한다.

### 3.4 로컬 메시징과 명령 응답

텔레그램 전송은 중복 방지와 thread/topic 처리를 어느 정도 갖추고 있다. 하지만 routing 설정 교체와 메시지 처리 사이에 lock 없는 공유 상태가 있다. keyword 자동 응답은 crawl command와 같은 lock을 공유해 긴 crawl 중에는 자동 응답도 막힐 수 있다.

카카오톡 전송은 clipboard와 OS hotkey를 쓰는 구조라 같은 PC에서 가장 강한 lock이 필요하다. 지금 lock은 sender/UI 호출 경계에 있어 direct messenger 호출이나 Agent 경로에서는 빠질 수 있다.

## 4. 주요 리스크 요약

| ID | 심각도 | 영역 | 요약 | 우선 처리 이유 |
| --- | --- | --- | --- | --- |
| R-01 | P0 | UI 설정 | 10번째 이후 설정이 저장 시 유실될 수 있음 | 사용자 데이터 손실 |
| R-02 | P0 | 쿠팡 검증 | 센터 mismatch 예외가 일부 삼켜지고 heading 없음이 통과 가능 | 잘못된 계정/센터 전송 |
| R-03 | P0 | 쿠팡 2FA | 계정 확인 전 인증 코드 발송 가능, 매칭 약함 | 잘못된 메일 계정에 코드 발송 |
| R-04 | P0 | 카카오 | 실제 OS 자동화 경계에 lock이 없음 | 중복/오발송 위험 |
| R-05 | P0 | 실행 lock | `LOG_DIR`별 runtime lock 분리 | 같은 CDP/profile 동시 사용 |
| R-06 | P1 | 문서 | dispatcher/recovery 등 운영 프로세스 설명 누락 | 배포 누락과 장애 대응 실패 |
| R-07 | P1 | 비밀값 | 로컬 secret store 기본이 평문 JSON | 토큰/비밀번호 노출 |
| R-08 | P1 | UI 검증 | 선택 탭만 쿠팡 2FA 검증 | 저장된 비정상 탭 누락 |
| R-09 | P1 | 텔레그램 | routing 교체와 처리 사이 race | 다른 채팅방/토픽 처리 위험 |
| R-10 | P1 | keyword | keyword 응답이 crawl lock에 묶임 | 자동 응답 지연 |
| R-11 | P1 | 로그 마스킹 | rider command가 그대로 로그에 남을 수 있음 | 개인정보 노출 |
| R-12 | P1 | 브라우저 | prepare check to launch race | CDP/profile 충돌 |
| R-13 | P1 | 쿠팡 parser | label과 숫자 구조에 강하게 의존 | HTML 변경 취약 |
| R-14 | P1 | secret 분류 | `verification_email_address` 저장 정책 불일치 | 평문 저장/마이그레이션 혼선 |
| R-15 | P2 | 아키텍처 테스트 | `rider_crawl -> rider_server` import guard 없음 | 경계 회귀 감지 누락 |
| R-16 | P2 | 텔레그램 전송 | durable queue/rate limiter 없음 | burst 시 재시도/유실 관리 약함 |
| R-17 | P2 | 메시지 포맷 | 주말 피크 시간 상수 검증 필요 | 업무 규칙 오류 가능 |
| R-18 | P2 | 배민 parser | fixed row shape 의존 | HTML 변경 취약 |
| R-19 | P2 | 문서 | dependency count 문서 불일치 | 유지보수 혼선 |

## 5. 상세 검토사항

### R-01. UI 설정 저장이 보이는 9개 탭만 기준으로 동작한다

심각도: P0  
관련 파일: `src/rider_crawl/ui_settings.py:226`, `src/rider_crawl/ui_settings.py:239`, `src/rider_crawl/ui.py:530`

`UiSettingsStore.load_all(max_tabs=9)`는 최대 9개 항목만 UI에 올린다. 이후 UI의 `save_all()`은 화면에 있는 9개 설정만 다시 저장한다. 기존 JSON에 10개 이상 설정이 있으면 10번째 이후 항목은 저장 버튼 한 번으로 사라질 수 있다.

영향:

- 기존 설정 파일을 수동으로 확장한 사용자에게 데이터 유실이 발생한다.
- 추후 서버/Agent 설정이나 100개 대상 import 기능을 붙일 때 같은 패턴이 더 큰 유실로 이어진다.
- 사용자는 저장 성공으로 보지만 일부 대상이 사라져 원인을 찾기 어렵다.

권장 조치:

- UI가 렌더하지 않은 설정 항목은 저장 시 그대로 보존한다.
- 삭제는 명시적인 삭제 버튼이나 archive flag로만 수행한다.
- UI 탭 수는 저장 파일의 전체 항목 수와 분리한다.
- 관련 테스트는 "10개 설정 파일을 열고 1번째만 수정 후 저장하면 10번째가 유지된다"로 작성한다.

### R-02. 쿠팡 센터 검증이 fail-open될 수 있다

심각도: P0  
관련 파일: `src/rider_crawl/platforms/coupang/crawler.py:52`, `src/rider_crawl/platforms/coupang/crawler.py:149`, `tests/test_coupang_crawler.py:249`

`rider-performance` 화면에서 센터 mismatch가 발생해도 RuntimeError로 처리되어 `current_screen=None` 경로로 삼켜질 수 있다. peak dashboard에서는 heading이 없으면 검증을 건너뛸 수 있고, 테스트도 heading 없음 허용을 고정한다.

영향:

- 잘못 로그인된 계정이나 다른 센터 화면을 읽어도 메시지가 생성될 수 있다.
- 쿠팡 HTML 변경으로 heading이 사라진 상황을 정상처럼 처리할 수 있다.
- 수동 확인 없이 자동 발송이 켜져 있으면 잘못된 실적이 나갈 수 있다.

권장 조치:

- 센터 mismatch 예외를 별도 타입으로 만들고 screen detection 실패와 분리한다.
- 기대 센터명이 설정되어 있으면 최소 한 번은 화면에서 명시적으로 확인해야 한다.
- heading이 없는 경우에는 "검증 불가"로 fail-closed한다.

### R-03. 쿠팡 이메일 2FA가 계정 확인 전에 코드를 보낼 수 있다

심각도: P0  
관련 파일: `src/rider_crawl/auth/coupang_email_2fa.py:79`, `src/rider_crawl/auth/coupang_email_2fa.py:83`, `src/rider_crawl/auth/coupang_email_2fa.py:137`

자동 2FA 흐름은 화면 계정을 강하게 확인하기 전에 인증 코드를 보낼 수 있다. `_account_matches_screen()`도 도메인 수준 매칭에 가까워 같은 도메인의 다른 메일 계정을 구분하기 어렵다.

영향:

- 운영자가 탭 설정을 잘못 복사했을 때 다른 계정으로 인증 코드가 갈 수 있다.
- 잘못된 계정에 대한 로그인 시도가 계속될 수 있다.
- 보안 사고 분석 때 어떤 계정으로 인증이 갔는지 추적하기 어렵다.

권장 조치:

- "인증 코드 보내기" 전에 화면에 표시된 수신자와 설정된 수신자를 확인한다.
- 마스킹된 로컬 파트까지 비교한다. 도메인만 맞는 경우는 거부한다.
- 화면에 수신자 표시가 없거나 ambiguous하면 자동 발송을 멈추고 수동 확인을 요구한다.

### R-04. 카카오톡 전송 lock이 실제 OS 자동화 경계에 없다

심각도: P0  
관련 파일: `src/rider_crawl/sender.py:349`, `src/rider_crawl/messengers/kakao.py:23`, `src/rider_crawl/ui.py:635`

카카오톡 전송은 clipboard, window focus, hotkey, Enter를 사용한다. 이런 작업은 process-wide로 하나씩만 수행되어야 한다. 현재 lock은 UI가 sender에 넘기는 callback에 가까워, `send_kakao_text()`를 직접 호출하는 경로는 보호받지 못한다.

영향:

- 두 전송이 동시에 clipboard를 덮어쓸 수 있다.
- 다른 채팅방에 메시지가 들어갈 수 있다.
- UI 경로와 Agent/테스트 경로의 동작이 달라진다.

권장 조치:

- `send_kakao_text()` 내부에서 process-wide lock을 잡는다.
- 가능하면 Windows named mutex나 lock file로 cross-process lock까지 적용한다.
- selection, paste, send, verify 전체를 하나의 lock 범위로 묶는다.

### R-05. 로컬 run lock 위치가 `LOG_DIR`에 따라 분리된다

심각도: P0  
관련 파일: `src/rider_crawl/config.py:101`, `src/rider_crawl/app.py:68`

runtime directory가 `log_dir.parent / "runtime"`에서 만들어진다. 같은 CDP 주소와 같은 브라우저 profile을 쓰더라도 `LOG_DIR`가 다르면 lock 파일 위치가 달라질 수 있다.

영향:

- 같은 PC에서 두 프로세스가 같은 Chrome profile/CDP를 잡을 수 있다.
- 하나는 정상으로 보이고 다른 하나는 CDP 연결 실패, session 꼬임, 카카오 focus 꼬임이 날 수 있다.

권장 조치:

- CDP 주소와 profile path 기준의 resource lock을 고정된 app state 위치에 둔다.
- 최소한 normalized CDP/profile key를 lock 이름에 포함한다.
- `LOG_DIR`는 로그 경로일 뿐 실행 lock root가 되지 않도록 분리한다.

### R-06. 아키텍처 문서가 현재 운영 프로세스를 반영하지 않는다

심각도: P1  
관련 파일: `docs/module-architecture.md:271`, `README.md:104`, `deploy/docker-compose.yml:108`, `src/rider_server/dispatch/__main__.py:91`

문서는 collect to dispatch loop가 live path가 아니라고 설명하지만, 배포 파일에는 `queue-recovery`, `telegram-dispatch` 서비스가 있다. README의 실행 설명도 backend와 scheduler 중심이다.

영향:

- 운영자가 dispatcher나 recovery worker를 빠뜨릴 수 있다.
- 장애 대응 때 어떤 프로세스가 어떤 책임을 갖는지 혼동한다.
- 아키텍처 테스트가 통과해도 문서가 틀린 상태가 된다.

권장 조치:

- README와 architecture docs에 backend, scheduler, queue-recovery, telegram-dispatch, Agent, local UI 역할을 나눠 적는다.
- Docker Compose service와 문서의 프로세스 목록을 맞추는 테스트를 추가한다.

### R-07. 로컬 secret store 기본 구현이 평문 JSON이다

심각도: P1  
관련 파일: `src/rider_crawl/secret_store.py:56`, `src/rider_crawl/secret_store.py:91`, `src/rider_crawl/secret_store.py:99`

`LocalFileSecretStore`는 비밀값을 설정 파일과 분리하지만, 저장 방식은 JSON 파일이다. 파일 권한이나 Windows Credential Manager/DPAPI 같은 OS 보호를 기본으로 쓰지 않는다.

영향:

- 토큰, 로그인 비밀번호, 앱 비밀번호가 사용자 폴더 파일로 남는다.
- 백업, 원격 지원, 로그 수집 중에 파일이 같이 복사될 수 있다.

권장 조치:

- Windows에서는 DPAPI 또는 Credential Manager 기반 구현을 기본으로 둔다.
- 파일 fallback은 명시적으로 opt-in하게 하고, ACL을 사용자 전용으로 제한한다.
- 기존 평문 JSON은 첫 실행 시 안전 저장소로 마이그레이션하고 원본 제거 여부를 확인한다.

### R-08. 쿠팡 2FA 검증이 현재 선택 탭만 검사한다

심각도: P1  
관련 파일: `src/rider_crawl/ui.py:517`, `src/rider_crawl/ui.py:530`

UI는 모든 탭을 저장하지만, 쿠팡 자동 2FA 필수값 검증은 선택된 탭에만 적용된다.

영향:

- 다른 탭의 app password, email address 누락이 저장 시 걸리지 않는다.
- 나중에 해당 탭을 실행할 때만 실패한다.

권장 조치:

- 저장 대상 전체 탭을 순회해 active 쿠팡 자동 2FA 설정을 검증한다.
- 비활성 탭은 검증 대상에서 제외하되, 활성 판단 기준을 명확히 둔다.

### R-09. 텔레그램 routing 설정 교체에 race가 있다

심각도: P1  
관련 파일: `src/rider_crawl/telegram_commands.py:185`, `src/rider_crawl/telegram_commands.py:218`

`update_routing()`은 config와 dict를 교체하고, `handle_text()`는 같은 상태를 읽는다. 상태 교체와 읽기가 lock으로 보호되지 않으면 경계 시점에서 일부 값만 바뀐 상태를 볼 수 있다.

영향:

- 특정 chat/topic 명령이 잘못된 대상 설정으로 처리될 수 있다.
- 드문 문제라 재현이 어렵고 운영 로그만으로 찾기 어렵다.

권장 조치:

- routing 상태를 immutable snapshot 객체로 만든다.
- snapshot 교체와 읽기를 lock으로 보호한다.
- `handle_text()`는 시작 시 snapshot 하나만 잡고 끝까지 사용한다.

### R-10. keyword 자동 응답이 crawl lock과 같은 lock을 쓴다

심각도: P1  
관련 파일: `src/rider_crawl/telegram_commands.py:294`, `src/rider_crawl/ui.py:923`

keyword 자동 응답이 crawl command와 같은 lock 경로에 묶인다. 긴 crawl이 진행 중이면 간단한 keyword 응답도 지연된다.

영향:

- 사용자는 자동 응답이 동작하지 않는다고 느낄 수 있다.
- polling loop가 불필요하게 오래 붙잡힐 수 있다.

권장 조치:

- crawl lock과 keyword response send lock을 분리한다.
- keyword 응답은 작은 queue나 짧은 timeout으로 처리한다.
- 동일 키워드 반복 응답은 cooldown으로 제어한다.

### R-11. 라이더 명령 로그에 개인정보가 남을 수 있다

심각도: P1  
관련 파일: `src/rider_crawl/telegram_commands.py:235`, `src/rider_crawl/redaction.py:130`

텔레그램 명령 로그가 원문 text를 포함할 수 있다. `!이름1234` 같은 라이더 조회 명령은 이름이나 전화번호 일부를 포함할 수 있는데, redaction 규칙은 이 형태를 충분히 마스킹하지 않는다.

영향:

- 운영 로그에 이름, 전화번호 일부, 주문 식별자가 남을 수 있다.
- 고객사 지원 중 로그 공유가 어려워진다.

권장 조치:

- command log에는 원문 대신 command type, chat id hash, update id 정도만 남긴다.
- rider lookup command 전용 redaction 규칙을 추가한다.

### R-12. 브라우저 prepare 단계에 check to launch race가 있다

심각도: P1  
관련 파일: `src/rider_crawl/browser_launcher.py:126`

CDP endpoint probe, profile 사용 가능 확인, Chrome launch, wait가 하나의 lock 안에 있지 않다. 두 프로세스가 동시에 실행되면 둘 다 "비어 있음"으로 판단할 수 있다.

영향:

- 같은 user data dir을 두 Chrome이 동시에 쓰려다 실패한다.
- 하나는 launch되고 다른 하나는 연결에 실패하는 식의 간헐 문제가 생긴다.

권장 조치:

- normalized CDP/profile key 기준 lock을 `prepare_chrome()` 전체에 적용한다.
- lock timeout과 사용자 안내 메시지를 별도로 둔다.

### R-13. 쿠팡 parser가 label과 숫자 구조에 강하게 의존한다

심각도: P1  
관련 파일: `src/rider_crawl/platforms/coupang/parser.py:53`, `src/rider_crawl/platforms/coupang/parser.py:233`

쿠팡 parser는 `목표/완료`와 숫자 블록의 exact shape에 기대는 부분이 있다. HTML에서 label 문구, 공백, 단위, 숫자 separator가 바뀌면 실패할 수 있다.

영향:

- 실제 화면은 정상인데 parser가 빈 결과를 내거나 일부만 읽을 수 있다.
- 자동 발송에서는 "0건"이나 누락된 내용이 그대로 나갈 위험이 있다.

권장 조치:

- label alias와 whitespace normalization을 넓힌다.
- 숫자 parsing에서 comma, unit, slash 주변 공백을 허용한다.
- fixture에 변형 HTML을 추가한다.

### R-14. `verification_email_address`의 secret 저장 정책이 불일치한다

심각도: P1  
관련 파일: `src/rider_crawl/secret_store.py:27`, `src/rider_crawl/ui_settings.py:22`, `src/rider_crawl/ui_settings.py:382`

`secret_store.py`는 `verification_email_address`를 secret으로 분류하지만, UI 설정의 `_SECRET_FIELDS`에는 빠져 있다. `_to_jsonable()`은 `_SECRET_FIELDS`에 있는 값만 secret ref로 뺀다.

영향:

- 이메일 주소가 설정 JSON에 평문으로 남을 수 있다.
- 어떤 값이 secret인지 파일마다 다르게 이해하게 된다.

권장 조치:

- 이메일 주소를 secret으로 볼지, 개인정보 설정값으로 볼지 정책을 하나로 정한다.
- secret으로 본다면 `_SECRET_FIELDS`에 포함하고 마이그레이션 테스트를 추가한다.
- secret이 아니라면 `secret_store.py`의 field classification을 수정한다.

### R-15. `rider_crawl -> rider_server` import guard가 없다

심각도: P2  
관련 파일: `docs/module-architecture.md:65`, `tests/agent/test_agent_package.py:233`

문서는 로컬 크롤러와 서버 패키지의 경계를 설명하지만, import guard는 `rider_crawl -> rider_agent`, `rider_agent -> rider_server` 중심이다. `rider_crawl`이 `rider_server`를 직접 import하는 회귀를 막는 테스트가 필요하다.

권장 조치:

- `tests/test_architecture.py` 또는 agent package boundary 테스트에 `rider_crawl`이 `rider_server`를 import하지 않는 AST guard를 추가한다.

### R-16. 텔레그램 로컬 전송에 durable queue/rate limiter가 없다

심각도: P2  
관련 파일: `src/rider_crawl/messengers/__init__.py:28`, `src/rider_crawl/sender.py:102`

현재는 per-call retry 중심이다. burst가 커지거나 Telegram API 제한을 만나면 중앙 queue와 backpressure가 없다.

권장 조치:

- 로컬 UI 범위에서는 최소 token 단위 rate limiter를 둔다.
- 서버/Agent 경로에서는 dispatch queue를 우선 경로로 삼고, 로컬 direct send는 수동/소규모 용도로 문서화한다.

### R-17. 주말 피크 시간 상수는 업무 규칙 검증이 필요하다

심각도: P2  
관련 파일: `src/rider_crawl/message.py:18`, `tests/test_coupang_message.py:108`

주말 피크 시간 범위가 `10:55~01:59` 등으로 표현되어 있다. 의도된 정책일 수 있지만, 날짜를 넘는 범위라 운영자가 이해하기 어렵다.

권장 조치:

- 실제 업무 규칙과 맞는지 확인한다.
- 날짜를 넘는 구간이면 메시지 문구에 "다음날" 의미를 명확히 한다.
- 테스트 이름에 업무 규칙을 설명한다.

### R-18. 배민 parser가 fixed row shape에 의존한다

심각도: P2  
관련 파일: `src/rider_crawl/parser.py:365`, `src/rider_crawl/parser.py:381`, `src/rider_crawl/parser.py:422`

배민 parser는 센터 id row와 인접 cell 구조에 기대는 부분이 있다. parsing 실패 candidate를 일부 skip할 수 있어, 구조가 조금 바뀌면 누락 원인을 알기 어렵다.

권장 조치:

- header based column map을 우선 사용한다.
- candidate parse 실패는 debug metric이나 warning으로 남긴다.
- 변형 fixture를 추가한다.

### R-19. 의존성 개수 문서가 실제 `pyproject.toml`과 다르다

심각도: P2  
관련 파일: `docs/module-architecture.md:228`, `pyproject.toml:6`, `tests/agent/test_agent_package.py:219`

문서는 pinned dependency 수를 9개로 설명하지만 실제 `pyproject.toml`의 runtime dependency 수는 다르다.

권장 조치:

- 문서에서 숫자를 제거하고 "runtime dependencies are intentionally small"처럼 유지보수 부담이 적은 표현으로 바꾼다.
- 숫자를 유지하려면 `pyproject.toml`에서 계산하는 테스트로 문서 값을 검증한다.

## 6. 유지해야 할 좋은 설계

- 로컬 UI는 저장 전 필수 입력 검증과 시작 전 확인을 이미 갖고 있다.
- 전송 기본값이 꺼져 있어 처음 설정 중 실발송 위험을 줄인다.
- 플랫폼별 parser가 테스트 fixture를 갖고 있어 변경 감지가 가능하다.
- `SecretStore` 추상화가 있어 OS 보안 저장소로 교체할 수 있는 시작점이 있다.
- 서버, Agent, 로컬 크롤러 패키지가 분리되어 있어 역할 경계를 강화하기 쉽다.
- 텔레그램 topic/thread 처리와 unique target guard가 있어 다중 채팅방 운영의 기본 골격은 있다.

## 7. 권장 우선순위

### P0: 데이터 유실과 오발송 방지

1. UI 설정 10번째 이후 보존
2. 쿠팡 센터 검증 fail-closed
3. 쿠팡 2FA 계정 확인 강화
4. 카카오 OS 자동화 lock을 messenger 내부로 이동
5. CDP/profile resource lock root 분리

### P1: 운영 안정성과 보안 기본값 강화

1. 로컬 secret store를 DPAPI/Credential Manager 기본으로 변경
2. 모든 저장 대상 탭의 2FA 값 검증
3. 텔레그램 routing snapshot과 lock
4. keyword 응답 lock 분리
5. 개인정보 redaction 확장
6. 브라우저 prepare lock 범위 확대
7. 쿠팡 parser 변형 fixture 추가
8. 아키텍처 문서와 Docker Compose 프로세스 일치

### P2: 유지보수성과 회귀 감지

1. `rider_crawl -> rider_server` import guard 추가
2. 텔레그램 local send rate limiter 검토
3. 주말 피크 시간 업무 규칙 확인
4. 배민 parser header 기반 fallback 보강
5. dependency count 문서 정리
