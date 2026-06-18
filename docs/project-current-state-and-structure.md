# Rider Result Monitoring Project Brief

작성일: 2026-06-12  
대상 저장소: `rider_result_mornitoring`

이 문서는 코드를 직접 볼 수 없는 외부 분석자 또는 GPT Pro 모델이 프로젝트의 컨셉, 현재 구현 상태, 운영 구조, 한계, 개선 연구 방향을 이해할 수 있도록 만든 자체 완결형 설명서다. 코드 파일 경로는 내부 근거로 남겨두지만, 이 문서만 읽어도 시스템의 목적과 구조를 파악할 수 있게 설명한다.

민감값은 기록하지 않는다. 예를 들어 텔레그램 토큰, 채팅방 ID, 쿠팡 비밀번호, 인증 이메일 앱 비밀번호 값은 이 문서에 쓰지 않는다.

## 0. 외부 분석자에게 먼저 전달할 핵심 컨셉

이 프로젝트는 배달 대행 또는 배달 운영사가 배민과 쿠팡이츠 관리자 웹페이지에서 실시간 실적을 읽어, 정해진 텔레그램 그룹방 또는 카카오톡 단체방에 자동으로 실적 메시지를 보내는 프로그램이다.

운영자가 원래 하던 일은 다음에 가깝다.

1. 배민 또는 쿠팡이츠 관리자 웹사이트에 로그인한다.
2. 특정 센터/상점의 실시간 실적 화면을 확인한다.
3. 피크타임 목표 대비 완료 건수, 배정/처리 건수, 거절률 같은 값을 읽는다.
4. 이 내용을 그룹방에 사람이 직접 정리해서 보낸다.

현재 프로그램은 이 반복 작업을 자동화한다.

현재 구현의 핵심 방식은 “공식 데이터 API 연동”이 아니라 “로그인된 Chrome 화면을 자동으로 읽는 방식”이다. 즉, 프로그램이 고객 계정의 Chrome 세션에 붙어서 사람이 보는 웹 화면의 HTML/텍스트를 읽고, 필요한 값만 파싱해서 메시지를 만든다.

이 방식은 빠르게 만들 수 있고 실제 웹 화면 기준으로 동작한다는 장점이 있다. 반대로 고객 수가 많아질수록 Chrome 프로필, 로그인 세션, 인증 만료, 포트 충돌, PC 상태 같은 운영 문제가 커진다.

## 0.1 현재 제품이 해결하는 문제

### 사용자의 문제

- 배달 운영자는 피크 시간마다 실적을 자주 확인해야 한다.
- 실적 화면을 보고 그룹방에 수동으로 공유하는 일이 반복된다.
- 여러 센터나 여러 플랫폼을 운영하면 확인해야 할 화면과 채팅방이 늘어난다.
- 로그인 세션이 만료되면 실적 공유가 멈긴다.

### 현재 프로그램의 해결 방식

- 운영자가 한 번 Chrome에 로그인해 둔다.
- 프로그램이 일정 간격으로 해당 Chrome 화면을 읽는다.
- 실적 데이터를 정해진 템플릿으로 메시지화한다.
- 텔레그램 또는 카카오톡으로 자동 전송한다.
- 텔레그램에서는 일부 명령과 키워드 자동응답도 처리한다.

### 장기적으로 만들고 싶은 판매용 제품 이미지

최종 목표가 판매용 시스템이라면, 단순 PC 프로그램이 아니라 다음에 가까운 구조가 필요하다.

- 고객을 쉽게 추가할 수 있는 관리자 화면
- 고객별 배민/쿠팡 계정과 센터 설정
- 고객별 텔레그램/카카오톡 전송 대상 설정
- 배민은 휴대폰 인증을 사람이 완료할 수 있게 안내하고, 완료 여부를 감지
- 쿠팡은 Gmail/Naver 인증 이메일을 IMAP으로 읽어 자동 처리
- 고객별 실행 상태와 오류를 중앙에서 확인
- 설치/업데이트/재인증 과정을 최대한 자동화

## 0.2 용어 정리

| 용어 | 의미 |
| --- | --- |
| 고객 | 이 프로그램을 구매하거나 사용하는 배달 운영 업체 또는 센터 |
| 계정 | 배민 또는 쿠팡이츠 관리자 사이트 로그인 계정 |
| 센터/상점 | 배민 센터 또는 쿠팡이츠 상점/운영 단위 |
| 탭 | 현재 UI에서 `크롤링1`~`크롤링9`로 표현되는 설정 단위 |
| 크롤링 | 로그인된 웹 화면을 읽어 실적 데이터를 추출하는 작업 |
| CDP | Chrome DevTools Protocol. 실행 중인 Chrome에 자동화 도구가 붙는 통로 |
| Chrome 프로필 | 계정별 로그인 세션을 분리하기 위한 Chrome 사용자 데이터 폴더 |
| 인증 이메일 앱 비밀번호 | 쿠팡 이메일 인증번호를 IMAP으로 읽기 위한 메일 계정 앱 비밀번호 |
| 자동복구 | 로그인 만료 후 프로그램이 다시 로그인 또는 인증을 시도하는 기능 |

## 0.3 현재 중요한 전제

- 배민은 휴대폰번호 인증만 존재한다고 본다. 따라서 완전 자동 로그인이 어렵다.
- 쿠팡이츠는 이메일 인증이 존재한다. 그래서 Gmail/Naver IMAP을 사용하면 자동 인증 가능성이 있다.
- 단, 쿠팡 고객마다 인증메일을 받는 메일함이 다를 수 있다. 이 경우 인증 이메일 주소와 앱 비밀번호 ref도 고객별로 분리해야 한다.
- 현재 구현은 중앙 서버 + Windows Local Agent + 관리자 웹 대시보드 구조로 리팩토링 중이다.
- Chrome 화면 읽기와 실제 크롤링은 여전히 로컬 PC/Agent가 담당한다.
- `rider_server`는 Epic 5 이후 PostgreSQL 기반 API, 스케줄러, Admin UI, Agent 등록/큐를 담당한다.
- 현재 코드는 탭 9개까지만 자연스럽게 쓰도록 설계되어 있다.
- 현재 방식 그대로 탭만 100개, 1000개로 늘리는 것은 좋은 개선 방향이 아니다.

## 0.4 GPT Pro에게 기대하는 연구 방향

이 문서를 보는 GPT Pro 모델이 연구해야 할 핵심 질문은 다음과 같다.

1. 현재 로컬 데스크톱 앱을 판매용 다고객 시스템으로 발전시키려면 어떤 아키텍처가 맞는가?
2. 배민 휴대폰 인증처럼 완전 자동화가 어려운 로그인은 어떤 사용자 경험으로 처리해야 하는가?
3. 쿠팡 인증 이메일은 고객별 메일함 ref, 보안 저장소, 앱 비밀번호 갱신 흐름을 어떻게 설계해야 하는가?
4. 고객 100명, 1000명을 운영할 때 Chrome 프로필/브라우저 세션을 어디에서 어떻게 실행해야 하는가?
5. 중앙 서버와 로컬 에이전트의 역할을 어떻게 나눠야 하는가?
6. 고객 세팅을 “관리자가 몇 분 안에 끝낼 수 있는 수준”으로 만들려면 어떤 온보딩 흐름이 필요한가?
7. 비밀번호, 인증 이메일 앱 비밀번호, 텔레그램 token 같은 비밀값은 어떻게 안전하게 저장해야 하는가?
8. 장애 감지, 재시도, 로그인 만료 알림, 로그 수집, 앱 업데이트를 어떻게 설계해야 하는가?
9. 현재 코드에서 재사용할 부분과 새로 설계해야 할 부분은 무엇인가?
10. 소규모 개선, 중규모 개선, 판매용 재설계의 단계를 어떻게 나눌 것인가?

## 1. 한 줄 요약

현재 프로젝트는 중앙 서버 + Windows Local Agent + 관리자 웹 대시보드로 나뉘는 구조로 리팩토링되어 있다. 기존 Tkinter UI는 `크롤링1`부터 `크롤링9`까지 탭을 만들고, 각 탭이 하나의 배민 또는 쿠팡이츠 계정 세션을 담당한다. 각 탭은 별도 Chrome 프로필과 별도 CDP 포트에 연결해 로그인된 웹 화면을 읽고, 텔레그램 또는 카카오톡으로 실적 메시지를 보낸다. 새 `rider_server`는 PostgreSQL을 정본 저장소로 쓰고, Agent 등록, 작업 큐, 관리자 화면, Telegram webhook, scheduler를 담당한다.

현재 구조는 소수 계정 운영에는 맞지만, 100명 또는 1000명 고객을 판매용으로 운영하려면 로컬 Chrome 세션 관리, Agent 배포/업데이트, 비밀값 보관, 장애 알림, 백업/복구 정책을 더 단단히 운영해야 한다.

중요한 판단: 현재 프로젝트의 가장 큰 문제는 크롤링 코드 자체보다 “운영 모델”이다. 배민/쿠팡 페이지에서 값을 읽고 메시지를 만드는 로직은 재사용 가치가 있지만, 고객을 탭으로 관리하고 각 PC에서 수동으로 세션을 유지하는 구조는 판매용 다고객 제품으로 확장하기 어렵다.

## 1.1 대표 운영 시나리오

외부 분석자는 아래 시나리오를 기준으로 현재 제품을 이해하면 된다.

### 시나리오 A: 배민 1개 센터 운영

1. 운영자가 프로그램을 실행한다.
2. `크롤링1` 탭에서 플랫폼을 배민으로 둔다.
3. 배민 센터명과 센터 ID를 입력한다.
4. Chrome 준비 버튼으로 별도 Chrome 프로필을 연다.
5. 운영자가 배민에 직접 로그인하고 휴대폰 인증을 완료한다.
6. 프로그램이 배민 달성현황 화면을 읽는다.
7. 실적 메시지를 만든다.
8. 텔레그램 또는 카카오톡 방으로 보낸다.
9. 설정한 간격마다 반복한다.

### 시나리오 B: 쿠팡이츠 1개 상점 운영

1. 운영자가 프로그램을 실행한다.
2. `크롤링2` 탭에서 플랫폼을 쿠팡이츠로 둔다.
3. 쿠팡 기대 센터/상점명을 입력한다.
4. Chrome 준비 버튼으로 별도 Chrome 프로필을 연다.
5. 운영자가 쿠팡에 로그인한다.
6. 선택적으로 쿠팡 아이디/비밀번호와 인증 이메일 주소/앱 비밀번호를 등록한다.
7. 프로그램이 쿠팡 peak-dashboard 화면을 읽는다.
8. 로그인 만료 시 인증 이메일 자동 2FA가 켜져 있으면 이메일 인증번호를 읽어 복구를 시도한다.
9. 실적 메시지를 텔레그램 또는 카카오톡으로 보낸다.

### 시나리오 C: 여러 계정 운영

1. 계정마다 UI 탭 하나를 사용한다.
2. 탭마다 CDP 포트를 다르게 둔다.
3. 탭마다 Chrome 프로필 경로를 다르게 둔다.
4. 탭마다 전송 대상 채팅방 또는 토픽을 다르게 둔다.
5. 각 탭에서 `시작`을 눌러 반복 실행한다.

이 시나리오는 2~9개 계정까지는 이해하기 쉽다. 하지만 100개 이상부터는 탭을 직접 관리하는 방식이 운영 도구로 맞지 않는다.

## 1.2 현재 제품에서 절대 오해하면 안 되는 점

- 이 프로그램은 배민/쿠팡의 공식 API를 사용하는 시스템이 아니다.
- 이 프로그램은 웹 화면을 읽는 자동화 도구다.
- 로컬 크롤러/기존 Tkinter UI 자체는 중앙 서버가 아니다.
- 중앙 서버는 고객별 계정/상태를 PostgreSQL로 관리한다.
- 현재 프로그램의 `탭`은 사실상 고객/계정 역할을 대신하는 임시 모델이다.
- 배민 휴대폰 인증은 현재 자동 처리하지 않는다.
- 쿠팡 인증 이메일은 자동화 가능성이 있지만 고객별 메일함 ref 분리가 필요하다.
- 카카오톡 전송은 PC 앱 UI 자동화라서 판매용 대규모 운영에는 위험이 크다.
- 텔레그램 전송은 API 기반이라 카카오톡보다 안정적이다.
- 지금 구조에서 단순히 탭 개수만 9개에서 1000개로 늘리는 것은 좋은 해결책이 아니다.

## 2. 저장소 최상위 구성

현재 루트에는 다음 성격의 파일과 폴더가 있다.

| 경로 | 역할 |
| --- | --- |
| `src/rider_crawl/` | 실제 애플리케이션 소스(기존 Agent/크롤러·UI) |
| `src/rider_server/` | 중앙 서버 코드. 플랫폼 중립 도메인/서비스 계층, FastAPI 라우트, SQLAlchemy 저장소, Alembic migration, scheduler, Admin UI, Telegram webhook을 포함한다 |
| `src/rider_agent/` | Windows Local Agent 런타임. 중앙 서버에 등록하고, outbound HTTPS로 job을 claim/complete하며, `rider_crawl`의 크롤러·카카오 전송 seam을 재사용한다 |
| `tests/` | pytest 기반 단위 테스트와 일부 UI helper 테스트 |
| `docs/` | 설계 문서, 구현 계획, 운영 문서 |
| `runtime/` | 실행 중 생성되는 로컬 상태 파일 |
| `logs/` | 실행 오류 로그 등 로컬 로그 |
| `secrets/` | 로컬 secret/운영 파일 위치. 실제 secret 파일은 Git 제외 |
| `scripts/` | 운영 보조 스크립트 |
| `dist/` | PyInstaller 빌드 산출물. 현재 `rider_crawl_onefile.exe` 존재 |
| `build/` | PyInstaller 임시 빌드 산출물 |
| `config.json` | 텔레그램 키워드 자동응답 설정 |
| `pyproject.toml` | Python 패키지/의존성/pytest 설정 |
| `uv.lock` | 의존성 lock 파일 |
| `rider_crawl_onefile.spec` | PyInstaller onefile 빌드 설정 |
| `rider_crawl_exe_entry.py` | exe 진입점 |
| `.env`, `.env.example` | CLI 실행 또는 환경변수 기반 설정 |

## 3. 실행 진입점

프로그램 진입점은 세 갈래다.

### 3.1 Python 모듈 실행

`src/rider_crawl/__main__.py`는 `rider_crawl.ui.main()`을 호출한다. 개발 환경에서는 보통 아래처럼 실행한다.

```powershell
.venv\Scripts\python.exe -m rider_crawl
```

`--once`를 붙이면 UI 없이 한 번만 실행한다.

```powershell
.venv\Scripts\python.exe -m rider_crawl --once
```

### 3.2 Windows Local Agent 실행

`src/rider_agent/__main__.py`는 중앙 서버와 통신하는 로컬 Agent 진입점이다. Agent는 `rider_crawl`을 재사용하지만 `rider_server`를 import하지 않는다. 명령 형태는 `python -m rider_agent register`, `python -m rider_agent run`, `python -m rider_agent autostart`로 고정한다.

```powershell
.venv\Scripts\python.exe -m rider_agent register --code "<registration-code>"
.venv\Scripts\python.exe -m rider_agent run --server-url "https://server.example"
.venv\Scripts\python.exe -m rider_agent autostart --register --server-url "https://server.example"
```

### 3.3 exe 실행

`rider_crawl_exe_entry.py`가 PyInstaller exe의 진입점이다. `rider_crawl_onefile.spec`가 이 파일을 참조한다. 빌드 결과는 `dist/rider_crawl_onefile.exe`다.

exe는 `console=False`로 빌드된다. 그래서 콘솔 로그가 사용자에게 보이지 않을 수 있다. UI 메시지와 로그 파일에 오류를 남기는 구조가 중요하다.

## 4. 핵심 런타임 흐름

가장 중요한 함수는 `src/rider_crawl/app.py`의 `run_once(config)`다.

실행 흐름은 다음 순서다.

1. `config.log_dir`, `config.state_dir` 디렉터리를 만든다.
2. 실행 중복 방지용 `RunLock`을 잡는다.
3. `platforms.crawl_snapshot(config, platform_name=config.platform_name)`로 플랫폼별 크롤러를 실행한다.
4. `message.render_current_screen_message(...)`로 메시지 텍스트를 만든다.
5. 메시지 내용을 SHA-256으로 해시한다.
6. `send_only_on_change`가 켜져 있고 이전 메시지와 같으면 전송을 건너뛴다.
7. `send_enabled`가 켜져 있으면 텔레그램 또는 카카오톡으로 보낸다.
8. 전송 성공 후 마지막 메시지 해시를 저장한다.

이 구조는 단일 실행 단위가 명확하다는 장점이 있다. 크롤링, 메시지 렌더링, 전송을 한 번의 job으로 묶고 있다.

## 5. 현재 고객/계정 모델

Epic 2 이전에는 `Customer`, `Tenant`, `Account` 같은 도메인 객체가 없었고 UI 탭 하나가 고객 또는 계정 하나처럼 동작했다. Epic 2에서 `src/rider_server/domain/`에 `Tenant`, `Subscription`, `PlatformAccount`, `MonitoringTarget` 등 ID 기반 도메인 모델(frozen dataclass)이 추가됐고, `rider_server.migration.runner`가 기존 활성 탭을 이 모델로 옮기는 마이그레이션 절차를 제공한다. Epic 5 이후에는 이 모델이 FastAPI, SQLAlchemy 저장소, Alembic migration, scheduler, Admin UI 쪽 런타임과 연결되어 중앙 서버 제어판의 기준 모델로 쓰인다. 기존 실행 UI는 여전히 탭 기반으로 동작하므로, 로컬 Agent와 중앙 서버 사이의 책임 분리를 계속 명확히 유지해야 한다.

관련 구조:

| 개념 | 현재 구현 |
| --- | --- |
| 고객 또는 계정 | `UiSettings` 1개 |
| 고객 목록 | `UiSettingsStore.load_all()`이 반환하는 `list[UiSettings]` |
| UI 표현 | `ttk.Notebook` 탭 |
| 탭 이름 | `크롤링1`, `크롤링2`, ... |
| 실행 상태 | `tab_index` 기준 dict |
| 상태 폴더 | `state_subdir=f"crawling{index + 1}"` |

중요한 한계:

- 고객 고유 ID가 없다.
- 탭 순번이 곧 상태 식별자다.
- 고객을 중간에 삽입하거나 순서를 바꾸면 사람이 보기에는 같은 고객이어도 내부 상태 경로가 바뀔 수 있다.
- 운영 로그도 고객명보다 `크롤링N` 기준으로 남는다.

## 6. UI 구조

UI는 `src/rider_crawl/ui.py`에 대부분 들어 있다.

### 6.1 기본 창

`launch_ui()`가 `Tk()`를 만들고 `RiderBotUi`를 생성한다. 창 제목은 `배달 실적봇 (배민·쿠팡이츠)`다.

기본 UI 구성:

1. 제목/설명
2. 설정 영역
3. 시작 전 확인 안내
4. 상태 표시
5. 메시지 미리보기와 로그
6. 하단 버튼

하단 버튼:

- `Chrome 준비하기`
- `설정 저장`
- `1회 실행`
- `시작`
- `중지`

### 6.2 탭 UI

설정 영역은 `ttk.Notebook`이다. `self.vars_by_tab`에 탭별 Tk 변수들을 만들고, 각 탭마다 `_build_settings_fields()`로 같은 폼을 생성한다.

현재 `UiSettingsStore.load_all(max_tabs=9)`가 기본값이므로 탭은 9개다. 설정 파일에 10개 이상이 들어 있어도 현재 기본 로딩에서는 9개까지만 읽는다.

### 6.3 탭별 입력 필드

각 탭은 아래 필드를 가진다.

| 필드 | 설명 |
| --- | --- |
| `performance_url` | 주 URL. 배민은 달성현황 URL, 쿠팡은 peak-dashboard URL |
| `peak_dashboard_url` | 예전 쿠팡 보조 URL. 현재 쿠팡에서는 비워지고 비활성화됨 |
| `platform_name` | `baemin` 또는 `coupang` |
| `baemin_center_name` | 배민 센터명. 쿠팡에서는 기대 센터/상점명으로 재사용 |
| `baemin_center_id` | 배민 센터 ID. 쿠팡에서는 미사용 |
| `browser_mode` | `cdp` 또는 `persistent` |
| `cdp_url` | Chrome 원격 디버깅 주소 |
| `browser_user_data_dir` | Chrome 프로필 경로 |
| `headless` | headless 실행 여부 |
| `messenger_name` | `telegram` 또는 `kakao` |
| `telegram_bot_token` | 텔레그램 봇 토큰 |
| `telegram_chat_id` | 텔레그램 채팅방 ID |
| `telegram_message_thread_id` | 텔레그램 토픽 ID |
| `kakao_chat_name` | 카카오톡 채팅방명 |
| `send_enabled` | 메시지 실제 전송 여부 |
| `send_only_on_change` | 메시지가 바뀔 때만 전송할지 여부 |
| `interval_minutes` | 반복 실행 간격 |
| `page_timeout_seconds` | 페이지 로딩/요소 대기 타임아웃 |
| `run_lock_timeout_seconds` | 실행 락 stale 타임아웃 |
| `coupang_auto_email_2fa_enabled` | 쿠팡 이메일 2FA 자동복구 사용 여부 |
| `coupang_login_id` | 쿠팡 로그인 아이디 |
| `coupang_login_password` | 쿠팡 로그인 비밀번호 |
| `verification_email_address` | 쿠팡 인증 이메일 주소(Gmail/Naver) |
| `verification_email_app_password` | 쿠팡 인증 이메일 앱 비밀번호 |
| `verification_email_subject_keyword` | 인증메일 제목 검색 키워드 |
| `verification_email_sender_keyword` | 인증메일 발신자 검색 키워드 |

### 6.4 시작/중지 모델

`시작`과 `중지`는 전체 탭이 아니라 현재 선택된 탭에만 적용된다.

`start()` 흐름:

1. 현재 탭 index를 얻는다.
2. 이미 실행 중이면 중복 시작을 막는다.
3. 설정을 저장하고 검증한다.
4. `performance_url`이 비어 있으면 비활성 탭으로 보고 시작하지 않는다.
5. 탭 전용 `threading.Event`를 만든다.
6. `BotScheduler`를 만들고 탭 전용 worker thread를 시작한다.
7. 텔레그램 설정이 있으면 텔레그램 명령 poller도 시작한다.

## 7. 설정 저장 구조

### 7.1 UI 설정 파일

기본 UI 설정 경로:

```text
runtime/state/ui_settings.json
```

저장 포맷은 대략 다음 형태다.

```json
{
  "crawlings": [
    {
      "performance_url": "...",
      "platform_name": "baemin",
      "cdp_url": "http://127.0.0.1:9222"
    }
  ]
}
```

`UiSettingsStore.save_all()`은 `crawlings` 배열 전체를 JSON으로 저장한다.

주의할 점 (아래 1~3은 Epic 2 이전 기준이며, 마지막 항목대로 Story 2.2/2.4에서 해소됨):

- (해소됨) 저장이 단순 `write_text()`였다.
- (해소됨) atomic write가 아니었다.
- (해소됨) 전원 종료 또는 프로세스 종료가 파일 쓰기 중 발생하면 일부 파일이 깨질 가능성이 있었다.
- (Epic 2 이전) 쿠팡 비밀번호, 텔레그램 토큰 같은 민감 설정이 같은 JSON에 평문 저장됐다. Epic 2(Story 2.4) secret store seam(`rider_crawl/secret_store.py`) 도입 후에는 설정 JSON에 평문 대신 `*_ref` 불투명 핸들만 남기고 실제 값은 분리된 store 파일(`runtime/state/secrets.local.json`)에 둔다. atomic write(Story 2.2)도 적용돼 위 "단순 `write_text`/비-atomic" 한계는 해소됐다.

### 7.2 현재 런타임 설정 스냅샷

2026-06-12 기준으로 민감값을 제외하고 확인한 현재 `runtime/state/ui_settings.json` 상태:

| 항목 | 값 |
| --- | --- |
| 전체 탭 수 | 9 |
| 활성 탭 수 | 2 |
| 활성 플랫폼 | 배민 1개, 쿠팡 1개 |
| 쿠팡 인증 이메일 자동 2FA 활성 탭 | 0 |
| 텔레그램 전송 활성 탭 | 1 |
| 카카오톡 전송 활성 탭 | 1 |

민감값은 확인하거나 문서화하지 않았다.

### 7.3 `.env` 설정

CLI `--once` 실행은 `AppConfig.from_env()`를 통해 `.env` 또는 환경변수를 읽는다.

주요 환경변수:

- `PERFORMANCE_PLATFORM`
- `PERFORMANCE_URL`
- `BAEMIN_DELIVERY_HISTORY_URL`
- `COUPANG_EATS_URL`
- `BAEMIN_CENTER_NAME`
- `BAEMIN_CENTER_ID`
- `BROWSER_MODE`
- `CDP_URL`
- `BROWSER_USER_DATA_DIR`
- `HEADLESS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_MESSAGE_THREAD_ID`
- `MESSENGER_NAME`
- `SEND_ENABLED`
- `SEND_ONLY_ON_CHANGE`
- `LOG_DIR`
- `STATE_SUBDIR`
- 쿠팡 이메일 자동복구는 현행 UI/Agent 설정의 인증 이메일 주소와 앱 비밀번호 ref를 사용한다.
- Google OAuth 관련 `GMAIL_*` 환경변수는 현행 설정 정본이 아니다.

UI 경로에서는 대부분 UI JSON 값을 쓰고, CLI 경로에서는 환경변수를 쓴다.

## 8. 상태 파일 구조

실행 중 생성되는 주요 상태는 다음과 같다.

### 8.1 마지막 메시지 해시

`app.py`는 메시지 중복 전송 방지를 위해 마지막 메시지 해시를 저장한다.

경로 형태:

```text
runtime/state/<state_subdir>/last_message.<scope_hash>.sha256
```

scope key에는 다음 값들이 들어간다.

- messenger 이름
- platform 이름
- 주 URL
- 보조 URL
- 센터명
- 센터 ID
- 텔레그램이면 bot token, chat id, topic id
- 카카오톡이면 chat name

그래서 같은 탭이라도 전송 대상이나 플랫폼이 바뀌면 다른 해시 파일을 쓴다.

### 8.2 실행 락

같은 브라우저 세션에 중복 실행이 붙지 않게 `RunLock`을 사용한다.

경로 형태:

```text
runtime/state/run_locks/run.<scope_hash>.lock
```

CDP 모드에서는 `cdp_url`이 실행 락 scope에 들어간다. persistent 모드에서는 `browser_user_data_dir`가 scope에 들어간다.

### 8.3 텔레그램 offset

텔레그램 `getUpdates` offset은 봇 토큰 기준으로 저장한다. 이 상태는 탭별 상태와 다르게 고정 앱 상태 루트 아래에 둔다. 이유는 같은 봇 토큰의 update queue는 하나라서, 작업 디렉터리가 바뀌어도 같은 offset을 써야 하기 때문이다.

### 8.4 키워드 자동응답 설정

`config.json`은 exe 옆 또는 현재 작업 디렉터리에 둔다. 키워드 자동응답은 메시지마다 이 파일을 다시 읽는다. 그래서 프로그램을 재시작하지 않고 키워드와 응답 문구를 바꿀 수 있다.

현재 루트 `config.json` 구조:

```json
{
  "keywords": ["사고", "병원"],
  "auto_message": "...",
  "cooldown_seconds": 30
}
```

## 9. 플랫폼 구조

플랫폼 선택은 `src/rider_crawl/platforms/`가 담당한다.

파일 구성:

| 파일 | 역할 |
| --- | --- |
| `platforms/base.py` | `PerformancePlatform` protocol |
| `platforms/__init__.py` | 플랫폼 registry |
| `platforms/baemin.py` | 배민 플랫폼 adapter |
| `platforms/coupang/__init__.py` | 쿠팡 플랫폼 adapter |
| `platforms/coupang/crawler.py` | 쿠팡 페이지 수집/세션 복구 |
| `platforms/coupang/parser.py` | 쿠팡 HTML 파싱 |

`platforms.__init__`에는 기본 플랫폼 `baemin`과 `coupang`이 등록되어 있다.

```text
baemin -> BaeminDeliveryPlatform
coupang -> CoupangEatsPlatform
```

이 구조는 비교적 잘 분리되어 있다. 나중에 다른 배달 플랫폼을 붙일 때 새 adapter를 만들고 registry에 등록하는 방식으로 확장할 수 있다.

## 10. 배민 크롤링 구조

배민은 기존 legacy 구조가 유지된다.

주요 파일:

- `src/rider_crawl/crawler.py`
- `src/rider_crawl/parser.py`
- `src/rider_crawl/platforms/baemin.py`

배민 실행 방식:

1. Chrome이 CDP 포트로 떠 있어야 한다.
2. 사용자가 배민 페이지에 로그인해 둔다.
3. 프로그램이 CDP로 Chrome에 붙는다.
4. 배민 달성현황 페이지로 이동한다.
5. 센터 선택/센터 검증을 수행한다.
6. 페이지 텍스트/HTML을 수집한다.
7. 배민 parser가 실적 값을 뽑는다.
8. `CurrentScreenSnapshot`을 반환한다.

배민 인증에 대한 현재 상태:

- 자동 로그인은 구현되어 있지 않다.
- 휴대폰번호 인증 자동 처리는 없다.
- 사용자가 Chrome에서 직접 로그인해야 한다.
- 배민은 휴대폰번호 인증만 존재한다고 보면, 완전 무인 자동 로그인은 현실적으로 어렵다.
- 가능한 자동화 범위는 “Chrome 프로필 열기, 로그인 페이지 열기, 로그인 완료 감지” 정도다.

## 11. 쿠팡이츠 크롤링 구조

쿠팡 관련 파일:

- `src/rider_crawl/platforms/coupang/crawler.py`
- `src/rider_crawl/platforms/coupang/parser.py`
- `src/rider_crawl/auth/coupang_email_2fa.py`
- `src/rider_crawl/auth/imap_2fa.py`

현재 쿠팡 탭은 UI에서 `performance_url`에 `https://partner.coupangeats.com/page/peak-dashboard`를 고정 입력한다. 예전 문서 일부에는 `rider-performance`와 `peak-dashboard`를 둘 다 읽는다고 되어 있지만, 현재 코드 주석과 모델은 peak-dashboard 중심으로 정리되어 있다. `PerformanceSnapshot.current_screen`은 선택값이며, 현재 일반 경로에서는 `None`일 수 있다.

쿠팡 실행 방식:

1. Chrome이 CDP 포트로 떠 있어야 한다.
2. 쿠팡이츠 파트너 사이트에 로그인된 세션이 있어야 한다.
3. 프로그램이 대상 URL 탭을 찾는다.
4. 로그인 페이지로 이동한 상태라면 로그인 필요 상태로 판단한다.
5. 인증 이메일 자동 2FA가 꺼져 있으면 탭 실행을 중지한다.
6. 인증 이메일 자동 2FA가 켜져 있으면 한 번 복구를 시도한다.
7. 복구 후 대상 페이지를 다시 로드한다.
8. 센터/상점명이 기대값과 맞는지 검증한다.
9. peak-dashboard HTML을 파싱한다.
10. `PerformanceSnapshot`을 반환한다.

## 12. 쿠팡 인증 이메일 IMAP 2FA 구조

쿠팡 자동복구는 `auth/coupang_email_2fa.py`가 담당한다.

복구 흐름:

1. 현재 페이지가 CAPTCHA인지 확인한다.
2. CAPTCHA이면 자동복구하지 않고 실패한다.
3. 아이디/비밀번호 로그인 화면이면 쿠팡 자격증명을 입력한다.
4. 이메일 인증 방식을 클릭한다.
5. 인증번호 발송 버튼을 클릭한다.
6. 발송 직전 시각을 기준으로 Gmail/Naver IMAP 메일함을 polling한다.
7. 요청 시각 이후 도착한 최신 인증메일을 찾는다.
8. 메일 본문에서 인증번호를 추출한다.
9. 쿠팡 인증번호 입력칸에 입력한다.
10. 제출 버튼을 누른다.

IMAP 구현은 `auth/imap_2fa.py`에 있다.

보안/정확성 규칙:

- 메일은 읽음 처리하지 않는다(IMAP `BODY.PEEK` + readonly).
- 요청 시각 이후 도착한 메일만 사용한다.
- 여러 메일이 있으면 가장 최신 메일만 본다.
- 인증번호와 앱 비밀번호 값은 예외 메시지나 로그에 넣지 않는다.
- 앱 비밀번호는 secret store/ref로만 전달하고 DB/job 결과에는 평문을 저장하지 않는다.

여러 고객 메일함 처리:

- 인증 이메일 주소와 앱 비밀번호 ref는 고객/메일함별로 분리한다.
- 여러 쿠팡 계정이 같은 메일함으로 인증메일을 받으면, 동시 인증 요청 때 최신 메일을 잘못 집을 수 있다.
- 따라서 판매용 구조에서는 고객별 메일함 ref와 검색 조건을 명확히 분리해야 한다.

## 13. 브라우저/세션 구조

현재 프로그램은 웹사이트 API를 직접 호출하지 않고, 로그인된 Chrome 화면에 붙어 데이터를 읽는다.

브라우저 연결 방식:

| 방식 | 설명 |
| --- | --- |
| `cdp` | 이미 떠 있는 Chrome의 원격 디버깅 포트에 연결 |
| `persistent` | Playwright가 지정 프로필로 persistent context를 실행 |

현재 UI 기본값은 `cdp`다.

CDP 보안 정책:

- CDP 주소는 로컬 주소만 허용한다.
- `127.0.0.1` 또는 `localhost`가 아니면 설정 저장에서 막는다.
- 이유는 다른 PC의 Chrome 세션을 읽는 위험을 막기 위해서다.

계정 분리 정책:

- 탭마다 다른 CDP 포트가 필요하다.
- 탭마다 다른 Chrome 프로필 경로가 필요하다.
- 활성 탭끼리 CDP 포트가 중복되면 설정 저장이 막힌다.
- 활성 탭끼리 브라우저 프로필 경로가 중복되면 설정 저장이 막힌다.

기본 포트:

| 탭 | 기본 CDP 포트 |
| --- | --- |
| 크롤링1 | 9222 |
| 크롤링2 | 9223 |
| ... | ... |
| 크롤링9 | 9230 |

이 방식은 9개 수준에서는 이해하기 쉽지만, 100개 이상부터는 포트/프로필 관리가 큰 운영 부담이 된다.

## 14. 메시지 렌더링 구조

메시지 생성은 `src/rider_crawl/message.py`가 담당한다.

입력 모델:

- 배민: `CurrentScreenSnapshot`
- 쿠팡: `PerformanceSnapshot`

`render_current_screen_message()`는 snapshot 타입을 보고 배민 메시지 또는 쿠팡 메시지를 렌더링한다.

배민 메시지는 피크/논피크 실적, 거절율 등을 출력한다.

쿠팡 메시지는 peak-dashboard의 업데이트 시각, 피크타임별 목표/완료, 배정/처리 건수, 거절률을 출력한다. `current_screen`이 있으면 수행중인 인원도 출력하지만, 현재 일반 쿠팡 경로에서는 없을 수 있다.

## 15. 메신저 구조

메신저 adapter는 `src/rider_crawl/messengers/`에 있다.

파일 구성:

| 파일 | 역할 |
| --- | --- |
| `messengers/base.py` | Messenger protocol |
| `messengers/__init__.py` | 메신저 registry |
| `messengers/telegram.py` | 텔레그램 adapter |
| `messengers/kakao.py` | 카카오 adapter |
| `sender.py` | 실제 텔레그램 API 호출과 카카오톡 UI 자동화 legacy 코드 |

현재 지원:

- 텔레그램
- 카카오톡 PC 앱

## 16. 텔레그램 구조

텔레그램 전송은 `sender.send_telegram_text()`가 담당한다.

특징:

- Bot API `sendMessage`를 사용한다.
- `telegram_chat_id`가 필요하다.
- 토픽 그룹이면 `telegram_message_thread_id`를 같이 보낸다.
- 전송 실패가 명확히 미전송이면 retry 가능하다.
- 응답을 받지 못해 전송 여부가 애매하면 빠른 재시도를 피한다.

텔레그램 명령 수신은 `telegram_commands.py`가 담당한다.

지원 기능:

- 그룹방에서 `!이름+휴대폰뒤4자리` 형태의 라이더 조회 명령 처리
- 키워드 자동응답
- 같은 봇 토큰을 여러 탭이 공유할 때 poller는 토큰당 하나만 실행
- `chat_id + topic_id` 조합으로 어느 탭에 명령을 보낼지 라우팅

중요한 제약:

- 같은 봇 토큰을 여러 앱 프로세스 또는 여러 PC에서 동시에 쓰면 `getUpdates` 큐가 꼬일 수 있다.
- 현재 코드는 한 앱 내부에서 같은 토큰을 공유하는 것은 방어하지만, 여러 PC/프로세스 간 중앙 조율은 없다.
- 활성 텔레그램 탭끼리 같은 `chat_id + topic_id` 조합은 허용하지 않는다.

## 17. 카카오톡 구조

카카오톡 전송은 Windows PC 앱 UI 자동화 방식이다.

특징:

- Windows에서만 실제 자동 전송을 지원한다.
- macOS에서는 카카오 전송이 비활성화될 수 있다.
- 클립보드와 카카오톡 창 제어에 의존한다.
- 같은 이름의 채팅방이 여러 개면 오발송 위험이 있어 실패 처리한다.
- 카카오 전송은 전역 lock으로 직렬화된다.

판매용 다고객 구조에서는 카카오톡 방식이 가장 운영 리스크가 크다. PC 화면 상태, 카카오톡 로그인 상태, 창 포커스, 채팅방명 중복에 의존하기 때문이다.

## 18. 키워드 자동응답 구조

키워드 자동응답은 `src/rider_crawl/keyword_responder.py`가 담당한다.

동작 방식:

1. 텔레그램 poller가 일반 메시지를 받는다.
2. 라이더 조회 명령이 아니면 키워드 자동응답을 검사한다.
3. `config.json`을 매번 다시 읽는다.
4. 메시지에 키워드가 포함되어 있으면 자동 안내 메시지를 보낸다.
5. 채팅방/토픽 target별 cooldown을 적용한다.

한계:

- `config.json`이 전역 파일이라 고객별 키워드 정책을 분리하기 어렵다.
- exe 모드에서는 `config.json`을 exe 옆에 같이 배포해야 한다.
- 현재 구조에서는 중앙에서 키워드 정책을 일괄 배포하거나 고객별로 관리하는 기능이 없다.

## 19. 스케줄러와 동시성

스케줄러는 `src/rider_crawl/scheduler.py`의 `BotScheduler`다.

동작 방식:

1. `run_job()` 실행
2. 결과가 `False`면 `retry_seconds`만큼 대기. 기본 5초
3. 그 외에는 정규 interval만큼 대기
4. stop event가 set되면 종료

UI에서는 활성 탭마다 scheduler thread 하나를 만든다.

동시성 제어:

| 제어 | 목적 |
| --- | --- |
| 탭별 in-memory lock | 같은 탭의 중복 실행 방지 |
| 파일 기반 `RunLock` | 같은 브라우저 세션에 대한 중복 프로세스 실행 방지 |
| 카카오 전역 lock | PC UI 자동화 충돌 방지 |
| 텔레그램 token별 lock | 같은 bot token의 전송 순서 제어 |
| 텔레그램 token별 poller | `getUpdates` 큐 분산 수신 방지 |

확장 한계:

- 탭 100개면 scheduler thread도 100개다.
- 크롤링 실패가 반복되면 탭마다 5초 retry가 발생할 수 있다.
- 사이트 구조 변경 같은 공통 장애가 생기면 전체 고객이 빠른 재시도를 하며 부하와 로그가 늘 수 있다.
- 중앙 backoff, 장애 차단, 전체 rate limit이 없다.

## 20. 로그와 오류 처리

주요 로그:

| 위치 | 설명 |
| --- | --- |
| `logs/run_errors.log` 또는 탭 설정의 `log_dir/run_errors.log` | UI 실행 오류 상세 |
| `kakao_diagnostics.log` | 카카오톡 자동화 진단 로그 |
| UI 미리보기 Text | 사용자에게 보이는 상태/오류 |

오류 처리 특징:

- `CdpUnavailableError`: Chrome CDP가 떠 있지 않은 환경 오류로 보고 정규 주기까지 기다린다.
- `BrowserActionRequiredError`: 로그인 만료 또는 사용자 조치 필요로 보고 해당 탭 반복 실행을 중지한다.
- `TelegramSendError`, `KakaoSendError`: 전송 실패 성격에 따라 빠른 재시도 여부를 결정한다.
- 일반 예외: 빠른 재시도 대상으로 처리한다.

한계:

- 크기 기준 로그 rotation이 Epic 2(Story 2.2, `rider_crawl/log_rotation.py`)에서 추가돼 `run_errors.log`·`kakao_diagnostics.log`가 무한히 커지지 않는다.
- 중앙 로그 수집이 없다.
- 고객별 상태 페이지가 없다.
- windowed exe에서는 Python logging 출력이 사용자에게 안 보일 수 있다.

## 21. 테스트 구조

테스트는 `tests/`에 pytest로 구성되어 있다.

테스트 범위:

- 설정 로딩/저장
- UI helper 검증
- scheduler
- lock
- app run_once
- message rendering
- Baemin parser/crawler
- Coupang parser/crawler
- 이메일 IMAP 2FA parser/fetcher
- Coupang email 2FA 흐름
- Telegram sender/commands
- Kakao sender 일부
- architecture registry

`pyproject.toml`의 pytest 설정:

- `pythonpath = ["src"]`
- `testpaths = ["tests"]`

현재 문서 작성 작업에서는 런타임 코드 변경이 없으므로 테스트를 실행하지 않았다.

## 22. 의존성

`pyproject.toml` 기준 주요 의존성:

| 의존성 | 용도 |
| --- | --- |
| `crawl4ai==0.8.7` | 배민 쪽 기존 크롤링 보조 |
| `playwright==1.60.0` | Chrome/CDP 연결과 페이지 조작 |
| `python-dotenv` | `.env` 로딩 |
| `pyperclip` | 카카오톡 메시지 붙여넣기 |
| `pyautogui` | 카카오톡 UI 자동화 |
| `pywinauto` | Windows 카카오톡 창 제어 |
| `IMAPClient` | Gmail/Naver IMAP 인증 이메일 읽기 |
| `pytest` | 테스트 |

## 23. 빌드/배포 구조

PyInstaller 설정은 `rider_crawl_onefile.spec`다.

특징:

- onefile exe 이름: `rider_crawl_onefile`
- 진입점: `rider_crawl_exe_entry.py`
- `console=False`
- `playwright`, `pywinauto`, `psutil` 관련 hidden import 포함
- `playwright` 데이터/바이너리를 `collect_all('playwright')`로 포함

배포 주의:

- `config.json`은 exe 안에 넣지 않고 exe 옆에 둬야 한다.
- 인증 이메일 앱 비밀번호나 secret store 파일은 Git에 올리지 않는다.
- 고객별 인증 이메일 ref/secret은 배포/백업/보호 정책이 필요하다.
- `dist/` 산출물은 현재 존재하지만, 일반적으로는 재생성 산출물로 관리하는 편이 안전하다.

## 24. 현재 문서/코드 불일치 또는 주의점

확인된 주의점:

1. 일부 README 내용은 쿠팡이 `rider-performance`와 `peak-dashboard`를 둘 다 읽는다고 설명하지만, 현재 코드 주석과 실행 경로는 peak-dashboard 한 페이지 중심으로 바뀌어 있다.
2. `peak_dashboard_url` 필드는 남아 있지만, UI에서는 쿠팡 선택 시 비우고 비활성화하는 흐름이다.
3. `coupang_eats_url`이라는 필드명이 남아 있지만 실제로는 플랫폼별 주 URL처럼 쓰인다.
4. `baemin_center_name` 필드는 쿠팡에서 기대 센터/상점명으로 재사용된다. 기능상 동작은 하지만 이름만 보면 혼란스럽다.
5. UI 설정은 최대 9탭으로 설계되어 있고, 문서도 9탭 운영을 기준으로 쓰인 부분이 있다.

## 25. 현재 구조의 장점

현재 구조에도 장점은 분명하다.

- `run_once()` 경계가 명확하다.
- 플랫폼 registry와 메신저 registry가 있어 확장점은 있다.
- 배민/쿠팡 파서 테스트가 있다.
- 텔레그램 token별 poller 중복 방지 처리가 되어 있다.
- 탭별 CDP 포트/Chrome 프로필 중복 검증이 있다.
- 쿠팡 인증 이메일 IMAP 2FA 자동복구의 기본 구조가 이미 있다.
- 인증번호와 앱 비밀번호 값이 로그에 직접 남지 않도록 주의한 코드가 있다.
- 중복 전송 방지를 위해 메시지 해시를 저장한다.

## 26. 현재 구조의 핵심 한계

### 26.1 탭이 고객 모델을 대신한다

10명 정도는 이해하기 쉽지만, 100명부터는 탭 UI가 운영 도구로 부적합하다. 검색, 필터, 상태 일괄 확인, 고객별 상세 화면이 필요하다.

### 26.2 고객 ID가 없다

판매용 시스템에서는 고객을 `customer_id`로 식별해야 한다. 지금은 `크롤링1`, `crawling1`처럼 순번이 식별자다.

### 26.3 로컬 Chrome 세션에 강하게 의존한다

고객마다 Chrome 프로필과 CDP 포트가 필요하다. 1000명을 하나의 PC에서 운영하는 구조는 현실적이지 않다.

### 26.4 배민 자동 로그인은 현실적으로 제한된다

배민은 휴대폰번호 인증만 존재한다는 전제에서는 완전 자동 로그인이 어렵다. 자동화 가능한 부분과 사람이 해야 하는 인증 단계를 분리해야 한다.

### 26.5 쿠팡 인증 이메일은 고객별 메일함 분리가 필수다

쿠팡은 이메일 인증이므로 Gmail/Naver IMAP 자동화가 가능하다. 하지만 고객마다 쓰는 메일함이 다르면 인증 이메일 주소와 앱 비밀번호 ref도 반드시 분리해야 한다.

### 26.6 비밀값 저장 방식이 판매용으로 약하다

비밀번호와 토큰을 평문 JSON 또는 로컬 파일에 두는 방식은 내부 테스트/소규모 운영에는 가능하지만, 판매용으로는 보안 정책이 부족하다.

### 26.7 중앙 상태 관리가 없다

현재는 각 PC의 UI와 로그 파일을 봐야 한다. 고객이 많아지면 다음 정보가 중앙에서 보여야 한다.

- 마지막 성공 수집 시각
- 마지막 전송 시각
- 로그인 만료 여부
- 인증 이메일 앱 비밀번호/IMAP 접근 오류 여부
- Chrome 실행 상태
- 최근 오류
- 고객별 플랫폼/센터/전송 대상
- 앱 버전

## 27. 판매용 시스템으로 바꿀 때 권장 구조

현재 코드를 전부 버릴 필요는 없다. 크롤러, parser, message renderer, 이메일 IMAP 2FA 일부는 재사용할 수 있다. 다만 운영 구조는 바꿔야 한다.

권장 구조:

```text
중앙 관리자 서버
  - 고객 관리
  - 라이선스/구독 상태
  - 고객별 설정 저장
  - 상태 모니터링
  - 로그/오류 수집
  - 앱 업데이트 관리
  - 텔레그램 bot/webhook 관리

로컬 에이전트
  - 고객 PC 또는 운영 PC에 설치
  - Chrome 프로필 생성/실행
  - CDP 포트 자동 배정
  - 배민/쿠팡 로그인 세션 유지
  - 크롤링 실행
  - 쿠팡 인증 이메일 IMAP 2FA 처리
  - 중앙 서버에 상태 보고

관리자 웹 대시보드
  - 고객 목록
  - 고객 추가 마법사
  - 플랫폼별 설정
  - 재인증 요청
  - 실행 상태/오류 확인
  - 원격 설정 배포
```

## 28. 고객 세팅 자동화 구상

### 28.1 공통 세팅

1. 관리자가 중앙 웹에서 고객을 생성한다.
2. 시스템이 `customer_id`와 설치 등록 코드를 만든다.
3. 고객 PC에서 로컬 에이전트를 설치한다.
4. 에이전트에 등록 코드를 입력한다.
5. 에이전트가 중앙 서버에서 고객 설정을 받아온다.
6. 에이전트가 고객별 Chrome 프로필 폴더를 만든다.
7. 에이전트가 고객별 CDP 포트를 자동 배정한다.
8. 에이전트가 대상 플랫폼 URL을 연다.
9. 로그인 완료 여부를 감지한다.
10. 테스트 크롤링을 실행하고 중앙 서버에 결과를 보고한다.

### 28.2 배민 고객 세팅

배민은 휴대폰번호 인증 때문에 다음 방식이 현실적이다.

1. 에이전트가 배민 로그인 페이지 또는 달성현황 페이지를 연다.
2. 고객 또는 운영자가 휴대폰 인증을 직접 완료한다.
3. 에이전트가 로그인 완료를 감지한다.
4. 센터명/센터 ID를 확인한다.
5. 테스트 메시지를 만든다.
6. 전송 대상에 테스트 발송한다.

배민은 “완전 자동 로그인”이 아니라 “로그인 준비와 성공 확인 자동화”로 설계해야 한다.

### 28.3 쿠팡 고객 세팅

쿠팡은 이메일 인증이 있으므로 자동화 범위를 더 넓힐 수 있다.

1. 관리자가 쿠팡 로그인 ID를 등록한다.
2. 비밀번호는 OS 보안 저장소 또는 암호화 secret store에 저장한다.
3. 고객 인증 이메일 계정에서 IMAP 사용과 앱 비밀번호를 준비한다.
4. 인증 이메일 주소와 앱 비밀번호는 고객별 secret ref로 저장한다.
5. 에이전트가 쿠팡 로그인 페이지를 연다.
6. 필요하면 아이디/비밀번호를 입력한다.
7. 이메일 인증번호를 IMAP으로 읽는다.
8. 인증번호를 입력한다.
9. peak-dashboard에 진입한다.
10. 기대 센터/상점명과 화면 센터/상점명을 대조한다.
11. 정상 수집 여부를 중앙 서버에 보고한다.

## 29. 단계별 개선 로드맵

### 완료된 리팩토링

- `UiSettings` 대상 ID와 target 기반 상태 경로가 추가됐다.
- 설정 파일 atomic write와 로그 rotation이 추가됐다.
- 쿠팡 설명은 `peak-dashboard` 주 URL과 IMAP 기반 2FA 정책으로 정리됐다.
- Windows Local Agent 패키지와 `register`/`run`/`autostart` 진입점이 추가됐다.
- 중앙 서버, PostgreSQL 스키마, scheduler, Admin UI, Telegram webhook, Agent queue가 추가됐다.

### 1단계: 현재 앱의 구조 정리

- `baemin_center_name`을 플랫폼 중립 이름으로 정리
- `coupang_eats_url`을 `performance_url` 또는 `primary_url`로 정리

### 2단계: 탭 UI 제거 또는 보조화

- `ttk.Notebook` 탭 대신 고객 목록 UI로 변경
- 검색/필터/상태 표시 추가
- 고객 상세 설정 화면 추가
- `시작/중지`를 고객별, 선택 고객 일괄, 전체 일괄로 분리
- 고객별 최근 오류/최근 성공 시각 표시

### 3단계: 로컬 에이전트화

- 중앙 서버 job 계약과 Agent 배포/업데이트 절차를 운영 문서로 고정
- 로컬 JSON과 중앙 서버 설정의 cutover 절차 정리
- Agent 장애/재인증/카카오 interactive session 운영 runbook 보강

### 4단계: 중앙 서버 추가

- readiness/healthcheck와 scheduler 마지막 성공 tick 지표 강화
- job lease 회수와 실패 metric/alarm 운영화
- 플랫폼 계정 secret ref/rotation 정책 강화
- 백업/복구와 배포 rollback 절차 문서화

### 5단계: 대량 운영 안정화

- 사이트 장애 시 전체 backoff
- 고객별 retry 정책
- 인증 이메일 앱 비밀번호/IMAP 접근 오류 감지와 재설정 알림
- 배민 휴대폰 인증 필요 알림
- Chrome 프로필 health check
- 크롤링 결과 품질 검증
- 전송 성공/실패 추적

## 30. 결론

현재 프로젝트는 “소수 계정을 운영자가 직접 관리하는 로컬 자동화 도구”로는 잘 맞는다. 그러나 “100명 또는 1000명 고객에게 판매하는 시스템”으로 보려면 고객 모델, 중앙 설정, 중앙 모니터링, 보안 저장소, 로컬 에이전트, 설치/등록 자동화가 필요하다.

현실적인 방향은 다음과 같다.

1. 기존 크롤러와 메시지 렌더링 로직은 유지한다.
2. 탭 기반 고객 관리를 customer_id 기반 모델로 바꾼다.
3. 배민은 휴대폰 인증 때문에 완전 자동 로그인 대상에서 제외하고, 로그인 완료 감지와 재인증 알림 중심으로 설계한다.
4. 쿠팡은 IMAP 기반 자동복구를 살리되, 인증 이메일 ref를 고객별/메일함별로 분리한다.
5. 판매용 최종 구조는 중앙 서버 + 로컬 에이전트 + 관리자 웹 대시보드로 가는 것이 맞다.

## 31. GPT Pro 연구용 요청문

아래 문장은 이 문서를 GPT Pro 같은 외부 연구 모델에 전달할 때 그대로 붙여 넣을 수 있는 요청문이다.

```text
아래 프로젝트 설명을 읽고, 현재 로컬 데스크톱 자동화 프로그램을 판매용 다고객 시스템으로 발전시키기 위한 개선 방향을 연구해 주세요.

중요한 제약:
- 배민은 휴대폰번호 인증만 존재하므로 완전 자동 로그인을 전제로 두면 안 됩니다.
- 쿠팡이츠는 이메일 인증이 있으므로 Gmail/Naver IMAP 기반 자동 인증을 고려할 수 있습니다.
- 쿠팡 고객마다 인증메일을 받는 메일함이 다를 수 있으므로 인증 이메일 주소와 앱 비밀번호 ref는 고객별/메일함별로 분리해야 합니다.
- 현재 구현은 중앙 서버 + Windows Local Agent + 관리자 웹 대시보드로 나뉘며, Chrome 화면 읽기는 고객/운영자 PC의 Agent가 담당합니다.
- 현재 구조는 탭 하나가 고객/계정 하나처럼 동작합니다.
- 현재 탭은 최대 9개이고, 100명/1000명으로 늘리기에는 UI와 운영 방식이 맞지 않습니다.
- 현재 크롤링 방식은 공식 API가 아니라 로그인된 Chrome 화면을 CDP/Playwright로 읽는 방식입니다.
- 카카오톡 전송은 PC 앱 UI 자동화라 대규모 운영 안정성이 낮고, 텔레그램은 API 기반이라 상대적으로 안정적입니다.

연구해 주세요:
1. 현재 구조에서 재사용할 부분과 버려야 할 부분
2. 10명, 100명, 1000명 규모별 현실적인 아키텍처
3. 중앙 서버와 로컬 에이전트의 역할 분리
4. 고객 온보딩 자동화 흐름
5. 배민 휴대폰 인증을 포함한 재인증 UX
6. 쿠팡 인증 이메일 IMAP 2FA 자동화와 고객별 메일함 ref 관리 방식
7. 비밀번호, 인증 이메일 앱 비밀번호, 텔레그램 token 보안 저장 방식
8. 장애 감지, 로그 수집, 모니터링, 알림 구조
9. 현재 코드베이스를 단계적으로 개선하는 로드맵
10. 판매용 제품으로 만들 때 가장 먼저 해결해야 할 리스크

답변은 다음 형식으로 주세요:
- 현재 구조에 대한 진단
- 목표 아키텍처 제안
- 플랫폼별 인증 전략
- 고객 세팅 자동화 설계
- 보안/운영/모니터링 설계
- 단계별 구현 로드맵
- 가장 큰 리스크와 대응책
```

## 32. 개선안 평가 기준

GPT Pro가 제안하는 개선안은 아래 기준으로 평가하면 된다.

| 평가 항목 | 좋은 개선안 | 나쁜 개선안 |
| --- | --- | --- |
| 배민 인증 이해 | 휴대폰 인증은 사람이 개입해야 함을 인정하고 UX를 설계 | 배민도 무조건 자동 로그인 가능하다고 가정 |
| 쿠팡 인증 이해 | 인증 이메일 ref를 고객별로 분리하고 앱 비밀번호 갱신 흐름 포함 | 모든 고객이 같은 메일함 secret을 공유한다고 가정 |
| 고객 모델 | `customer_id` 중심으로 설정/상태/로그를 분리 | `크롤링1`, `크롤링2` 같은 순번 모델 유지 |
| 확장성 | 중앙 서버와 로컬 에이전트 역할을 분리 | 한 PC에서 Chrome 1000개 실행 전제 |
| 보안 | OS 보안 저장소, 암호화, secret store 고려 | 비밀번호와 token을 평문 JSON에 계속 저장 |
| 운영성 | 중앙 모니터링, 알림, 로그 수집, 업데이트 포함 | 고객 PC별 수동 확인 전제 |
| UI/UX | 고객 추가 마법사, 재인증 안내, 상태 대시보드 제안 | 탭을 무한히 늘리는 방식 |
| 장애 대응 | backoff, rate limit, 전체 장애 차단 포함 | 모든 실패를 5초마다 무한 재시도 |

## 33. 최종 판단

이 프로젝트는 이미 작동하는 로컬 자동화 도구의 기반을 갖고 있다. 특히 플랫폼별 parser, 메시지 생성, 텔레그램 전송, 쿠팡 인증 이메일 IMAP 2FA는 판매용 시스템에서도 일부 재사용할 수 있다.

하지만 판매용 제품의 핵심은 크롤러보다 운영 시스템이다. 고객 추가, 인증 유지, 상태 모니터링, 보안 저장, 장애 대응, 업데이트 배포가 제품 품질을 좌우한다.

따라서 다음 개선 방향이 가장 합리적이다.

1. 단기: 현재 앱의 고객 식별자, 설정 구조, 문서 불일치, 보안 저장 문제를 정리한다.
2. 중기: 탭 UI를 고객 목록/상세 화면으로 바꾸고, 로컬 에이전트 구조로 분리한다.
3. 장기: 중앙 서버와 관리자 대시보드를 만들고, 로컬 에이전트가 고객별 Chrome 세션과 크롤링을 담당하게 한다.

배민은 “자동 로그인”이 아니라 “인증 필요 감지와 사용자 안내”가 핵심이다. 쿠팡은 “IMAP 기반 이메일 인증과 고객별 메일함 ref 관리”가 핵심이다. 이 차이를 정확히 반영한 개선안만 현실성이 있다.
