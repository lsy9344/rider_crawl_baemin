---
project_name: 'rider_result_mornitoring'
user_name: 'Noah Lee'
date: '2026-06-12'
sections_completed: ['discovery', 'technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'code_quality_rules', 'workflow_rules', 'critical_rules']
existing_patterns_found: 9
status: 'complete'
rule_count: 56
optimized_for_llm: true
---

# AI 에이전트를 위한 프로젝트 컨텍스트

_이 파일은 AI 에이전트가 이 프로젝트에서 코드를 구현할 때 반드시 따라야 할 중요한 규칙과 패턴을 담습니다. 일반적인 내용보다 에이전트가 놓치기 쉬운 프로젝트 고유 규칙에 집중합니다._

---

## 기술 스택과 버전

- Python `>=3.10` 기준 프로젝트다. 새 코드는 `src/rider_crawl/` 패키지 안에 두고, 테스트는 `tests/`의 pytest 구조를 따른다.
- `crawl4ai==0.8.7`은 고정 버전이다. 파서/크롤러 동작이 바뀔 수 있으므로 임의로 업그레이드하지 않는다.
- `playwright==1.60.0`을 사용해 로그인된 Chrome에 CDP로 연결한다. 기본 운영 방식은 자동 로그인이나 새 세션 생성이 아니라, 사용자가 로그인해 둔 Chrome 화면을 읽는 방식이다.
- UI는 표준 `tkinter` 기반 데스크톱 앱이다. 웹 프론트엔드나 서버 앱 구조를 가정하지 않는다.
- 텔레그램은 표준 라이브러리 `urllib`로 Bot API를 호출한다. 카카오톡은 Windows PC 앱 UI 자동화(`pyautogui`, `pywinauto`, `pyperclip`) 경로다.
- 쿠팡 Gmail 2FA는 `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`를 사용하며, Gmail scope는 읽기 전용 흐름을 기준으로 한다.
- 배포 파일은 PyInstaller onefile exe(`rider_crawl_onefile.spec`)다. `build/`는 재생성 산출물이고, `dist/`의 exe는 배포 산출물로 취급한다.

## 중요한 구현 규칙

### 언어별 규칙

- 데이터 전달 객체는 기존처럼 `dataclass`를 우선 사용한다. 이미 `AppConfig`, `RunResult`, `CurrentScreenSnapshot`, `PerformanceSnapshot`, `UiSettings`가 이 패턴을 따른다.
- `AppConfig`는 실행 시점 설정 스냅샷이다. UI 설정(`UiSettings`)과 환경변수 로딩(`AppConfig.from_env`)의 역할을 섞지 않는다.
- 새 설정을 추가할 때는 UI 저장 경로와 CLI/env 경로가 같은 정책을 써야 하는지 먼저 정한다. 특히 쿠팡 2FA는 현재 UI 탭별 저장값이 우선이며, `UiSettings.to_app_config()`는 `.env`를 읽지 않는다.
- 외부 서비스, 브라우저, 메신저 호출은 함수 주입이나 adapter 경계를 유지한다. `run_once()` 테스트처럼 fake 함수로 대체할 수 있어야 한다.
- 파서 오류는 조용히 기본값으로 덮지 않는다. 필수 데이터가 없으면 `MissingPerformanceDataError`처럼 명확한 예외를 내고 잘못된 실적 메시지를 보내지 않는다.
- 경로는 `Path`를 사용하고, 런타임 상태는 `runtime/`, 로그는 `logs/`, 비밀 파일은 `secrets/google/` 정책을 따른다.
- 주석은 운영 정책이나 헷갈리는 상태 분리처럼 코드만으로 알기 어려운 곳에만 짧게 단다. 단순 동작 설명 주석은 늘리지 않는다.

### 프레임워크/런타임 구조 규칙

- `app.run_once(config)`는 한 번의 수집, 메시지 생성, 중복 확인, 전송을 묶는 핵심 실행 경계다. 새 기능은 가능하면 이 경계를 우회하지 말고 주입 가능한 crawler/sender 또는 하위 adapter로 연결한다.
- 플랫폼 확장은 `rider_crawl.platforms` registry를 사용한다. 새 플랫폼은 `PerformancePlatform` 계약을 구현하고 `CrawlSnapshotResult` 호환 모델을 반환해야 한다.
- 메신저 확장은 `rider_crawl.messengers` registry를 사용한다. 새 전송 방식은 `Messenger.send_text(config, message)` 경계를 지킨다.
- 배민 legacy 모듈인 `crawler.py`, `parser.py`, `sender.py`는 기존 import와 테스트 호환을 위해 유지한다. 쿠팡 전용 로직은 `platforms/coupang/` 아래에 둔다.
- 기본 브라우저 방식은 `cdp`다. CDP 모드에서는 실행 락 scope가 `cdp_url` 기준이고, `persistent` 모드에서는 `browser_user_data_dir` 기준이다.
- UI는 최대 9개 `크롤링N` 탭 모델을 전제로 한다. 탭별 설정은 `UiSettingsStore.load_all(max_tabs=9)`와 `runtime/state/ui_settings.json` 구조를 깨지 않게 다룬다.
- 텔레그램 수신 폴러는 봇 토큰별 단일 큐라는 제약이 있다. 같은 봇 토큰을 여러 프로세스에서 동시에 쓰는 설계를 추가하지 않는다.
- 카카오톡 전송은 PC 앱 UI 자동화라 전역 lock과 정확한 채팅방명 검증을 유지해야 한다. 임의 창이나 이름이 애매한 방으로 보내면 안 된다.

### 테스트 규칙

- 테스트는 pytest를 사용하며 `pyproject.toml`의 `pythonpath = ["src"]`, `testpaths = ["tests"]` 설정을 따른다.
- 새 기능은 관련 모듈 옆의 기존 테스트 파일 패턴을 따른다. 예: 설정은 `test_config.py`/`test_ui_settings.py`, 실행 흐름은 `test_app.py`, 플랫폼 registry는 `test_architecture.py`, 쿠팡 파서는 `test_coupang_parser.py`.
- 외부 브라우저, 텔레그램, 카카오톡, Gmail API를 단위 테스트에서 직접 호출하지 않는다. fake 함수, monkeypatch, fake page, tmp_path를 사용해 재현 가능하게 만든다.
- `run_once()` 관련 테스트는 `crawl_snapshot`과 `send_message`를 주입해 크롤링/전송 부작용을 끊는다.
- 상태 파일 테스트는 실제 `runtime/`이나 `logs/`를 쓰지 말고 `tmp_path` 안에서 검증한다.
- 파서 변경은 정상 케이스와 필수 데이터 누락 케이스를 함께 테스트한다. 누락 시 잘못된 기본 메시지로 진행하지 않고 명확한 예외가 나야 한다.
- 설정 마이그레이션이나 기본값 변경은 기존 저장 JSON 호환 테스트를 추가한다. 특히 legacy 카카오 설정, 탭 9개 로딩, 쿠팡 플랫폼 추론을 깨지 않아야 한다.
- 동시성/락 변경은 같은 브라우저 scope 차단과 다른 scope 병렬 허용을 모두 테스트한다.

### 코드 품질과 스타일 규칙

- 제품 코드는 `src/rider_crawl/`만 기준으로 본다. `.agents/`, `.claude/`, `.codex/`, `_bmad/`는 BMAD/에이전트 도구 코드이므로 제품 기능 구현 대상으로 섞지 않는다.
- 새 플랫폼별 구현은 플랫폼 폴더 아래에 둔다. 쿠팡 전용 selector, 로그인 복구, parser 로직을 배민 legacy `crawler.py`/`parser.py`에 섞지 않는다.
- 함수와 변수는 기존 Python snake_case 스타일을 따른다. 클래스와 dataclass는 PascalCase를 쓴다.
- 공개 경계 이름은 기존 호환을 우선한다. 예를 들어 `coupang_eats_url`이 현재는 플랫폼별 주 URL처럼 쓰여도, 넓은 변경 없이 이름만 바꾸지 않는다.
- 설정 저장 파일은 `ensure_ascii=False, indent=2` JSON 스타일을 유지한다.
- README, `docs/`, `.env.example`가 실제 동작과 충돌하면 실제 코드와 README를 우선 정본으로 보고 문서도 함께 고친다.
- `build/`, `.venv/`, `.pytest_cache/`, `runtime/`, `logs/`, `secrets/google/*.json` 같은 로컬/비밀/생성 파일을 기능 변경에 포함하지 않는다.
- PyInstaller 변경은 `rider_crawl_onefile.spec`와 `rider_crawl_exe_entry.py`의 역할을 이해한 뒤 최소 변경으로 한다.

### 개발 워크플로 규칙

- 일반 검증은 `.venv\Scripts\python.exe -m pytest` 또는 환경에 맞는 Python으로 pytest를 실행한다. pytest 설정은 `pyproject.toml`에 있다.
- 개발 실행은 `python -m rider_crawl`, 1회 CLI 실행은 `python -m rider_crawl --once` 흐름을 기준으로 한다.
- Windows 배포 exe는 `rider_crawl_onefile.spec`로 PyInstaller를 실행해 만든다. 빌드 산출물인 `build/`는 재생성 대상으로 보고 직접 수정하지 않는다.
- Chrome은 원격 디버깅 포트와 탭별 프로필을 분리해야 한다. 여러 계정/탭을 다룰 때 같은 CDP 포트나 같은 프로필을 공유하게 만들지 않는다.
- UI 설정은 `runtime/state/ui_settings.json`에 저장되는 로컬 상태다. 저장 포맷을 바꾸면 기존 사용자의 JSON 마이그레이션을 고려한다.
- `config.json`은 키워드 자동응답 설정이며 실행 파일 옆 또는 작업 디렉터리에서 읽는다. exe에 번들되는 내부 리소스로 가정하지 않는다.
- 민감값은 `.env`, `runtime/state/ui_settings.json`, `secrets/google/` 같은 로컬 파일에만 둔다. 코드, 테스트 fixture, 문서 예시에 실제 토큰/비밀번호/chat_id를 넣지 않는다.
- Git 상태가 더러울 수 있으므로 관련 없는 변경을 되돌리지 않는다. 특히 BMAD 설치 파일이나 문서 생성물은 제품 코드 변경과 분리해서 본다.

### 절대 놓치면 안 되는 규칙

- 이 프로젝트는 공식 배민/쿠팡 API 연동이 아니다. 로그인된 Chrome 화면을 CDP/Playwright로 읽는 도구이므로, 사이트 구조 변경과 로그인 만료를 정상 운영 위험으로 다룬다.
- 배민은 휴대폰 인증 때문에 완전 자동 로그인을 전제로 설계하지 않는다. 사용자 조치 필요 상태를 감지하고 잘못된 메시지를 보내지 않는 것이 우선이다.
- 쿠팡은 기대 센터/상점명 검증이 필수다. 쿠팡 탭에서 `baemin_center_name`은 실제로 기대 센터/상점명으로 재사용되며, 비어 있거나 배민 기본값이면 다른 계정 실적 오발송 위험이 있다.
- 쿠팡 Gmail 2FA는 인증번호, OAuth token, 쿠팡 비밀번호를 로그나 예외 메시지에 남기면 안 된다. 자동복구 실패 시 반복 인증 요청을 계속 보내지 말고 탭을 중지하는 기존 정책을 유지한다.
- 텔레그램 `getUpdates`는 봇 토큰 하나가 큐 하나를 공유한다. 같은 봇 토큰을 여러 앱 프로세스에서 동시에 polling하는 구조를 만들지 않는다.
- 활성 텔레그램 탭끼리 같은 `chat_id + topic_id` 조합을 공유하면 안 된다. 오발송/명령 라우팅 혼선 방지가 우선이다.
- `send_only_on_change`의 마지막 메시지 해시는 플랫폼, URL, 센터, 전송 대상 scope에 묶인다. scope key를 줄이면 다른 탭/계정의 중복 판단이 섞일 수 있다.
- CDP 포트와 Chrome 프로필은 계정 격리 장치다. 탭 추가나 자동 실행 기능을 만들 때 포트/프로필 중복 검증을 약화하지 않는다.
- 카카오톡 전송은 UI 자동화라 실패 시 임의 전송보다 중단이 맞다. 같은 이름의 창이 여러 개거나 창 목록 확인이 실패하면 보내지 않는다.
- `runtime_dir`와 `app_state_root()`는 일부러 다르다. run lock/last hash는 로그 경로 기준 런타임에, 텔레그램 offset/lock은 토큰별 고정 상태 루트에 둔다.

---

## 사용 가이드

**AI 에이전트용**

- 코드를 구현하기 전에 이 파일을 먼저 읽는다.
- 위 규칙을 프로젝트 고유 제약으로 취급하고, 일반 관례보다 우선한다.
- 확신이 없으면 더 제한적인 선택을 한다.
- 새 패턴이나 중요한 예외가 생기면 이 파일을 함께 갱신한다.

**사람용**

- 이 파일은 에이전트가 놓치기 쉬운 규칙만 남기고 짧게 유지한다.
- 기술 스택, 실행 구조, 보안 정책이 바뀌면 갱신한다.
- 오래되었거나 더 이상 특별하지 않은 규칙은 주기적으로 제거한다.

마지막 업데이트: 2026-06-12
