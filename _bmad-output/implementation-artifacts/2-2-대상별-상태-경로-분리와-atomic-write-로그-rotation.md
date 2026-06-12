---
baseline_commit: 38b6470
---

# Story 2.2: 대상별 상태 경로 분리와 atomic write·로그 rotation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 런타임 상태 경로를 `crawlingN` 순번에서 **`targets/<monitoring_target_id>`** 로 바꾸고, 설정 저장(`ui_settings.json`)을 **atomic write(temp→fsync→rename)** 로, `run_errors.log`·`kakao_diagnostics.log`를 **크기 기준 rotation+보존** 으로 만들고 싶다,
so that 탭 표시 순서를 바꾸거나 앱이 강제 종료돼도 다른 대상의 `last_message`·`run_lock` 상태나 설정 JSON이 섞이거나 손상되지 않고, 로그가 무한히 커지지 않는다.

> **이 스토리의 성격 — Story 2.1이 발급한 ID를 "실제로 사용"하는 첫 스토리.** 2.1은 `UiSettings`에 `monitoring_target_id`(불투명 uuid4)를 **발급·영속화**만 했고, 런타임 경로에는 **연결하지 않았다**(2.1이 명시적으로 2.2 소유로 미룬 부분). 본 스토리는 그 ID를 상태 경로의 주 식별자로 연결한다(P1-02). 더불어 서로 **독립적인** 2개 보강(atomic write P1-03, 로그 rotation P1-04)을 같이 한다. 세 작업은 서로 결합돼 있지 않으니 AC별로 분리해 구현·테스트한다. [Source: 2-1 스토리 범위 경계(19-26), implementation-contract.md P1-02/03/04(37-39)]
>
> **엄격한 범위 경계(스코프 크립 방지).** 본 스토리는 **오직** (1) `state_subdir` 값을 `targets/<monitoring_target_id>`로 바꾸고, (2) 설정 JSON 저장을 atomic하게 하고, (3) 두 로그 파일을 rotation하는 것만 한다. 아래는 **다른 스토리 소유 — 본 스토리에서 절대 손대지 않는다:**
> - 기존 `runtime/state/crawlingN` 폴더를 `targets/<id>`로 **복사**하고 옛 `last_message` 해시를 신규 dedup으로 **seed** 하는 마이그레이션 러너 → **Story 2.7**(Migration Contract). 본 스토리는 경로 **계산식만** 바꾼다(데이터 이전 X). [Source: implementation-contract.md Migration Contract(101-102)]
> - 플랫폼 중립 필드(`center_name`/`display_name`/`target_external_id`/`primary_url`) → **Story 2.3**(P1-05).
> - secret 값 분리·`*_ref`화 → **Story 2.4**(P1-06).
> - 도메인 dataclass/Enum(Tenant/MonitoringTarget 등) → **Story 2.5**.
> - PostgreSQL 테이블·Alembic → **Epic 5**(P4-02).
>
> **기준선 회귀 0.** 현재 HEAD(`38b6470`, Story 2.1 done)에서 전체 스위트는 **598 collected**(참고값 — 복사 금지, 본인이 `.venv/Scripts/python.exe -m pytest --collect-only -q`로 재측정). 본 스토리는 신규/수정 테스트 케이스만큼만 변동이 정상이고, 기존 통과 테스트가 새로 깨지면 실패다(NFR-20). **A2 교훈: dev 노트에 잠정 pass 수치를 박아 stale를 만들지 말 것 — 리뷰 시점 재측정값 1개만 정본으로 기록한다.** [Source: epic-1-retro-2026-06-13.md 액션 A2, memory/dev-env-quirks]
>
> **secret/식별자 비노출(NFR-5, ADD-15).** 신규/수정 테스트·fixture에 실제 토큰/비밀번호/OTP/`chat_id`/전화/이메일 원문을 넣지 않는다. 기존 테스트가 쓰는 명백한 가짜값(`"token"`, `"-100123"`, `"77"`)만 재사용한다. 로그 rotation은 파일을 **크기로 자를 뿐 내용을 바꾸지 않는다** — 로그에 새로 secret을 남기지 않는다(redaction 강화는 Epic 1/NFR-10 별도 책임). [Source: project-context.md(81·89), epic-1-retro 액션 A1]

## Acceptance Criteria

**AC1 — `state_subdir`를 `targets/<monitoring_target_id>`로 변경, 탭 재정렬에도 last_message/run_lock 비혼선 (P1-02, FR-31, NFR-18)**
1. **Given** 현재 `state_subdir`가 `ui.py`의 4개 호출부에서 `f"crawling{index + 1}"`(탭 순번)으로 계산될 때 **When** `monitoring_target_id`가 있는 활성 탭의 `state_subdir`를 **`targets/<monitoring_target_id>`** 로 바꾸면 **Then** 그 탭의 `last_message` 중복 방지 해시(`config.state_dir = runtime_dir/state/<state_subdir>/last_message.<scope>.sha256`)가 **탭 표시 순서가 아니라 안정 ID를 따라** 저장/조회되고, 탭을 재정렬해도 다른 대상의 dedup 기록과 섞이지 않는다. [Source: src/rider_crawl/ui.py(94-98·524·614·808), src/rider_crawl/config.py(128-134), src/rider_crawl/app.py(63-65)]
2. **And** **상태 식별이 더 이상 `crawlingN` 순번에 일차로 의존하지 않는다**(architecture Anti-Pattern 회피). `crawling{n}`은 **`monitoring_target_id`가 아직 비어 있는(=2.1 마이그레이션 전/미저장) 탭에 한해서만** 충돌 없는 **legacy 폴백**으로 쓰이고, 주 식별 스킴이 아니다. 폴백조차 `targets/`(빈 id로 인한 전탭 충돌)로 떨어지지 않는다. [Source: architecture.md Anti-Patterns(365), src/rider_crawl/ui_settings.py(58·204-226)]
3. **And** **`run_lock`은 본 스토리에서 경로/스코프를 바꾸지 않는다.** `run_lock`은 `runtime_dir/state/run_locks/run.<browser_scope>.lock`이며 `state_subdir`가 **아니라** 브라우저 스코프(`_run_scope_key` = CDP endpoint 또는 user_data_dir)로 묶인다. 즉 재정렬 안전성은 이미 브라우저 기준으로 보장돼 있다 — `state_subdir` 변경이 이 스코프를 건드리거나 `run_lock`을 `targets/<id>` 아래로 옮기면 안 된다. 기존 `test_run_once_blocks_parallel_runs_for_same_browser_scope_even_with_different_state_subdirs`(같은 브라우저면 state_subdir 달라도 차단)가 그대로 통과해야 한다. [Source: src/rider_crawl/app.py(68-95), tests/test_app.py(198-239·241-280)]

**AC2 — 설정 JSON atomic write (P1-03, FR-31)**
4. **Given** `UiSettingsStore.save`/`save_all`이 현재 `self.path.write_text(...)`(비원자적)로 `ui_settings.json`을 쓸 때 **When** 같은 디렉터리의 임시 파일에 직렬화 → `flush()` + `os.fsync()` → `os.replace(temp, path)`로 바꾸면 **Then** 저장 도중 강제 종료(=replace 직전 중단)에도 기존 `ui_settings.json`은 **이전 유효 상태가 그대로 보존**되고 **반쪽짜리로 손상되지 않으며**, 임시 파일은 정리된다(실패 시 temp unlink 후 예외 재발생). [Source: src/rider_crawl/ui_settings.py(191-201), architecture.md File Structure Patterns(287)]
5. **And** atomic 전환은 직렬화 형식을 **바꾸지 않는다**: `ensure_ascii=False, indent=2`, `save_all`의 `{"crawlings":[...]}` 구조, `save`의 평면 객체 구조, `browser_user_data_dir`/`log_dir`의 `str` 직렬화가 전부 동일하다. 기존 라운드트립 테스트(`test_ui_settings_save_and_load_round_trip`, `test_ui_settings_save_all_and_load_all_round_trip`)와 2.1의 persist-on-first-issue 테스트가 회귀 없이 통과한다. [Source: tests/test_ui_settings.py(74-145), src/rider_crawl/ui_settings.py(229-233)]
6. **And** 2.1의 **persist-on-first-issue** 경로(`load`/`load_all`이 새 ID 발급 시 1회 `save`/`save_all`)는 atomic화 덕분에 마이그레이션 write도 자동으로 원자적이 된다. **2.1의 발급 조건·no-file 가드·단일 객체 load 보존 로직은 변경하지 않는다**(atomic은 "어떻게 쓰는가"만 바꾸고 "언제 쓰는가"는 그대로). [Source: src/rider_crawl/ui_settings.py(140-189)]

**AC3 — 로그 rotation + 보존 (P1-04, NFR-10)**
7. **Given** `run_errors.log`(ui.py `_write_run_error_log`)와 `kakao_diagnostics.log`(sender.py `_write_kakao_diagnostics`)가 무한 append로 계속 커질 수 있을 때 **When** append 직전에 **크기 기준 rotation** 을 적용하면(파일이 임계치 이상이면 `name.log`→`name.log.1`로 밀고 `.1..N` 시프트) **Then** 두 로그가 크기 기준으로 rotation되고, 보존 개수(backup_count)를 넘는 오래된 회전본은 삭제되어 **보존 기준이 적용**된다. [Source: src/rider_crawl/ui.py(966-981), src/rider_crawl/sender.py(409-419), epics.md FR-31(74)·NFR-10(95)]
8. **And** rotation은 **로그 내용·기존 인터페이스를 바꾸지 않는다**: `_write_run_error_log`는 여전히 기록한 `Path`(또는 실패 시 `None`)를 반환하고, `_write_kakao_diagnostics`도 동일하게 best-effort(`try/except`로 실패해도 전송/에러 경로를 깨지 않음)를 유지한다. rotation 자체 실패가 에러 로깅/진단 경로를 폭주시키거나 예외로 터뜨리면 안 된다(폴백: rotation 실패해도 append는 시도하거나 조용히 무시). [Source: src/rider_crawl/ui.py(958-981), src/rider_crawl/sender.py(400-419), tests/test_ui_helpers.py(1168-1203)]

## Tasks / Subtasks

- [x] **Task 1 — `state_subdir`를 `targets/<monitoring_target_id>`로 전환 (AC: 1, 2, 3)**
  - [x] `src/rider_crawl/ui.py`에 **단일 파생 헬퍼**를 추가한다: `def _state_subdir_for(settings: UiSettings, index: int) -> str:` — `settings.monitoring_target_id.strip()`가 있으면 `f"targets/{settings.monitoring_target_id}"`, 없으면 legacy 폴백 `f"crawling{index + 1}"`을 반환. **빈 id로 `targets/`(슬래시만)으로 떨어지지 않게** strip 검사 필수. [Source: src/rider_crawl/ui.py(94-98)]
  - [x] 4개 호출부의 `state_subdir=f"crawling{index + 1}"`(또는 `selected_index`/`tab_index`)을 전부 `state_subdir=_state_subdir_for(settings, <그 호출부의 index>)`로 교체한다: (a) `app_configs_from_settings` 96행(스케줄/실행 경로의 중심), (b) `prepare_app_clicked` 524행, (c) 614행, (d) 808행. **`crawl_name=f"크롤링{n}"`은 그대로 둔다**(표시명/메시지 source 라벨 폴백이지 상태 경로가 아님 — `app.run_once`의 `source_label` 참조). [Source: src/rider_crawl/ui.py(96·524·614·808), src/rider_crawl/app.py(37-39)]
  - [x] **run_lock 무변경 확인.** `app.py::_run_lock_path`/`_run_scope_key`/`_message_scope_key`와 `config.py::state_dir`/`runtime_dir`는 건드리지 않는다. `state_subdir`에 슬래시가 들어가도 `config.state_dir = base / self.state_subdir`와 `run_once`의 `config.state_dir.mkdir(parents=True, ...)`가 중첩 폴더(`targets/<id>`)를 만들어 주므로 추가 변경 불필요. `monitoring_target_id`는 uuid4 hex(32자, 파일시스템 안전)라 경로 sanitize 불필요. [Source: src/rider_crawl/config.py(128-134), src/rider_crawl/app.py(32-33·68-70)]
  - [x] **결정 기록(YOLO 기본값) — 발급은 load에만, 경로는 폴백으로:** 2.1은 ID 발급을 `load`/`load_all`에만 두었다(save_all은 발급 안 함). 따라서 "URL 입력→저장→재시작 전 시작" 같은 드문 창에서는 새 활성 탭의 `monitoring_target_id`가 아직 비어 폴백 `crawling{n}`을 쓴다 — 다음 앱 기동의 `load_all`이 안정 ID를 발급·영속화하면 자동 치유되고, 옛 상태 복사는 2.7이 맡는다. **본 스토리는 `save_all`에 ID 발급을 추가하지 않는다**(2.1 발급 계약 보존, 변경 표면 최소화). 운영자가 "저장 시 즉시 발급"을 원하면 dev 전에 알리면 조정한다. [Source: 2-1 스토리 Completion Notes(158-159), src/rider_crawl/ui_settings.py(175-189)]
- [x] **Task 2 — 설정 JSON atomic write (AC: 4, 5, 6)**
  - [x] `src/rider_crawl/ui_settings.py`에 모듈 헬퍼 `def _atomic_write_text(path: Path, text: str) -> None:`를 추가한다. 동작: `path.parent`에 임시 파일 생성(예: `tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False)` 또는 `path.with_name(path.name + ".tmp")` — **반드시 같은 디렉터리**라야 `os.replace`가 같은 볼륨에서 원자적) → `text` write → `flush()` + `os.fsync(fileno)` → close → `os.replace(tmp, path)`. 예외 발생 시 `tmp`를 unlink하고 예외를 재발생한다. [Source: architecture.md(287)]
  - [x] `save`/`save_all`의 `self.path.write_text(...)`를 `_atomic_write_text(self.path, ...)`로 교체한다. `json.dumps(..., ensure_ascii=False, indent=2)`와 `{"crawlings":[...]}`/평면 구조는 **그대로**(직렬화 코드는 옮기지 말고 결과 문자열만 atomic 헬퍼에 넘긴다). `self.path.parent.mkdir(parents=True, exist_ok=True)`는 유지(헬퍼 안/밖 어느 쪽이든 한 번). [Source: src/rider_crawl/ui_settings.py(191-201)]
  - [x] **크로스플랫폼 주의:** `os.replace`는 Windows·POSIX 모두에서 같은 볼륨 원자적 교체를 보장한다. **디렉터리 fsync는 하지 않는다**(Windows 미지원). `encoding="utf-8"`. 텍스트 모드 write 시 줄바꿈 변환이 일어나지 않게 한다(`json.dumps` 출력에 `\n`만 있으므로 기본 utf-8 text write로 충분, 단 일관성 위해 명시적 encoding 사용). [Source: project-context.md(36-37·68)]
  - [x] **2.1 로직 불변:** `load`/`load_all`의 발급 조건(`performance_url.strip()` 활성 판정, `_issue_missing_ids`, no-file 가드, 다중 탭 단일 `load()` 보존)은 손대지 않는다. atomic화는 그 write 경로를 통과만 시킨다. [Source: src/rider_crawl/ui_settings.py(140-189)]
- [x] **Task 3 — 로그 rotation 유틸 + 두 writer 연결 (AC: 7, 8)**
  - [x] 신규 모듈 `src/rider_crawl/log_rotation.py`에 공용 헬퍼를 추가한다: `def rotate_if_needed(path: Path, *, max_bytes: int, backup_count: int) -> None:` — `path`가 존재하고 `path.stat().st_size >= max_bytes`면 RotatingFileHandler식으로 회전: `path.{backup_count}` 삭제, `path.{k}`→`path.{k+1}`(k=backup_count-1..1), `path`→`path.{1}`. 모든 파일 연산은 best-effort(존재하지 않으면 skip). 모듈 상수 `DEFAULT_MAX_BYTES = 1_000_000`(1MB), `DEFAULT_BACKUP_COUNT = 5` 정의. **logging.handlers.RotatingFileHandler를 쓰지 않는 이유**: 두 writer는 logging 모듈 기반이 아니라 커스텀 타임스탬프 포맷 + **경로 반환** 계약을 가진 수동 append라, 핸들러로 바꾸면 포맷·반환 계약·기존 테스트가 깨진다. append 직전 크기 검사만 더하는 최소 변경이 안전. [Source: src/rider_crawl/ui.py(966-981), src/rider_crawl/sender.py(409-419)]
  - [x] `ui.py::_write_run_error_log`: `path = target_dir / "run_errors.log"` 계산 직후, `path.open("a")` **이전**에 `rotate_if_needed(path, max_bytes=..., backup_count=...)`를 호출한다(이미 감싼 `try/except`(966-981) 안에 두어 rotation 실패가 `None` 반환으로 흡수되게). 반환 계약(기록 경로 또는 `None`) 유지. [Source: src/rider_crawl/ui.py(966-981)]
  - [x] `sender.py::_write_kakao_diagnostics`: `path = config.log_dir / "kakao_diagnostics.log"` 직후, append 이전에 동일 호출. 이미 `try/except`(409-419)로 감싸 best-effort 유지. [Source: src/rider_crawl/sender.py(409-419)]
  - [x] **내용 불변·redaction 경계:** rotation은 파일을 크기로 자르고 회전본을 관리할 뿐, 기록되는 traceback/진단 텍스트를 바꾸지 않는다. 로그에 새 secret을 추가하지 않는다(redaction 강화는 본 스토리 범위 밖, NFR-10/Epic 1). [Source: project-context.md(89), epics.md NFR-10(95)]
- [x] **Task 4 — 테스트 추가/보강 (AC: 1~8)** — 기존 패턴(`tmp_path`, 순수 파일 I/O, 외부 미호출, monkeypatch) 사용:
  - [x] **(AC1) state_subdir 파생 — `tests/test_ui_helpers.py`:** `monitoring_target_id`가 채워진 활성 settings로 `app_configs_from_settings`(또는 `_state_subdir_for`) → `state_subdir == f"targets/{id}"` 단언. id 비어 있으면 폴백 `crawling{n}` 단언(빈 `targets/` 아님). 기존 `test_app_configs_from_settings_names_tabs_and_skips_blank_urls`(coerce_settings 경로라 id 빈값 → `crawling1` 유지)가 **그대로 통과**하는지 확인(폴백 하위호환). [Source: tests/test_ui_helpers.py(1468-1514)]
  - [x] **(AC1 재정렬 안전) — `tests/test_app.py`:** 같은 `monitoring_target_id`/`state_subdir=targets/<id>`를 가진 config는 탭 순서와 무관하게 같은 `last_message` 경로를 쓰고, 다른 id는 분리됨을 단언(예: `_last_message_hash_path` 또는 `config.state_dir` 경로 비교). **run_lock 회귀:** 기존 `test_run_once_blocks_parallel_runs_for_same_browser_scope_even_with_different_state_subdirs`가 무수정 통과(같은 브라우저면 state_subdir이 `targets/a`/`targets/b`로 달라도 차단)함을 확인. [Source: tests/test_app.py(198-239), src/rider_crawl/app.py(63-70)]
  - [x] **(AC2 atomic) — `tests/test_ui_settings.py`:** (a) 기존 라운드트립·persist-on-issue 테스트가 그대로 통과. (b) **강제 종료 시뮬레이션:** 기존 유효 `ui_settings.json`을 만든 뒤 `os.replace`(또는 `os.fsync`)를 예외 던지도록 monkeypatch → `save_all` 호출이 예외를 내도, **원본 파일 내용이 변하지 않고**(이전 JSON 그대로 load 가능) 디렉터리에 `.tmp` 잔여물이 남지 않음을 단언. (c) 저장 후 텍스트가 여전히 `ensure_ascii=False`(한글 비escape)·`"crawlings"` 키 포함을 단언. [Source: tests/test_ui_settings.py(74-145)]
  - [x] **(AC3 rotation) — `tests/test_log_rotation.py`(신규) + 두 writer:** (a) `rotate_if_needed` 단위 테스트: 임계 미만 → 회전 없음; 임계 이상 → `name.log.1` 생성·새 base 비움; `backup_count` 초과 → 가장 오래된 회전본 삭제(보존). (b) `_write_run_error_log`/`_write_kakao_diagnostics`를 작은 `max_bytes`로 호출해 실제 회전 발생·반환 경로 정상·best-effort(`None`/예외 없음) 확인. tmp_path만 사용, 실제 UI/네트워크/PC앱 미호출. [Source: src/rider_crawl/ui.py(966-981), src/rider_crawl/sender.py(409-419)]
  - [x] secret 비노출: 신규 테스트 값은 placeholder/가짜값만(`"token"`, `"-100123"`, `"77"`, `"실적봇_A"`). 로그 rotation 테스트 본문은 임의 더미 텍스트로 채운다(실제 토큰 형태 금지). [Source: project-context.md(81), epic-1-retro 액션 A1]
- [x] **Task 5 — 회귀·범위·누출 검증 및 마무리 (AC: 1~8)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`. WSL 시스템 `python3` 사용 금지(pytest 미설치). 기준선 **598**(참고값 — 본인이 재측정) 대비 기존 통과가 새로 깨지지 않고 신규 케이스만큼만 증가가 정상. [Source: memory/dev-env-quirks]
  - [x] 범위 점검: `git diff -w --stat`에 `ui.py` + `ui_settings.py` + `sender.py` + 신규 `log_rotation.py` + 관련 테스트만 보이고, `config.py`/`app.py`/`lock.py`/`models.py`는 **무변경**임을 확인(run_lock/state_dir 스코프 보존). CRLF/LF 노이즈·무관 파일은 되돌리지 않는다. 모든 상태/로그 테스트는 `tmp_path` 안에서만(실 `runtime/`·`logs/`·`ui_settings.json` 미변형). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep: 신규/수정 코드·테스트에 실제 봇 토큰(`[0-9]{6,}:[A-Za-z0-9_-]{30,}`)·`chat_id=<digits>`·한국 휴대폰 평문이 없는지 확인. [Source: epic-1-retro 액션 A1]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2). [Source: epic-1-retro 액션 A2]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **3개 독립 보강**만 한다: (P1-02) `state_subdir` 값 변경, (P1-03) 설정 JSON atomic write, (P1-04) 두 로그 파일 rotation. 변경 표면: `ui.py`(4 호출부+헬퍼), `ui_settings.py`(save/save_all+atomic 헬퍼), `sender.py`(rotation 호출 1줄), 신규 `log_rotation.py`, + 테스트.
- **건드리지 않는다:** 옛 `crawlingN` 폴더 복사·`last_message` seed(2.7 러너), `run_lock` 스코프/`_run_scope_key`(브라우저 기준 유지), `config.py::state_dir`/`runtime_dir`(경로 정책), `to_app_config`/`AppConfig` 필드 형태(`state_subdir` 필드는 이미 존재 — 값만 바뀜), 2.1의 ID 발급 조건. 플랫폼 중립 필드(2.3)·secret_ref(2.4)·도메인 모델(2.5). [Source: implementation-contract.md(37-39·101-102)]

### 코드 앵커 (변경 대상 정밀 위치)

- `src/rider_crawl/ui.py`
  - `app_configs_from_settings()` 94-98: `state_subdir=f"crawling{index + 1}"` → 헬퍼. 이 함수가 스케줄/실행의 중심 경로다.
  - `prepare_app_clicked()` 524: 단일 탭 `to_app_config(state_subdir=...)`.
  - 614, 808: 나머지 `to_app_config(state_subdir=...)` 호출부.
  - `_write_run_error_log()` 966-981: `path = target_dir / "run_errors.log"` 직후 rotation 삽입(기존 try/except 안).
  - `active_crawling_settings()` 61-62: "활성" 판정(`performance_url.strip()`) — 변경 없이 참조만.
- `src/rider_crawl/ui_settings.py`
  - `save()` 191-196, `save_all()` 198-201: `write_text` → `_atomic_write_text`. 직렬화 형식 불변.
  - `load()` 140-156, `load_all()` 158-189: 2.1 발급/persist 로직 — **변경 금지**(write 방식만 atomic화로 자동 적용).
  - `_to_jsonable()` 229-233: 직렬화 정책(asdict + Path→str) — 불변.
- `src/rider_crawl/sender.py`
  - `_write_kakao_diagnostics()` 409-419: `path = config.log_dir / "kakao_diagnostics.log"` 직후 rotation 삽입(기존 try/except 안).
- `src/rider_crawl/config.py` 128-134 / `src/rider_crawl/app.py` 63-70: `state_dir`(state_subdir 포함)·`_last_message_hash_path`·`_run_lock_path` — **읽기만**, 변경 금지.

### state_subdir = `targets/<id>` 설계 (AC1 — 핵심, 반드시 읽을 것)

- **데이터 흐름:** `ui.py` 호출부가 `settings`(2.1이 `load_all`에서 활성 탭에 `monitoring_target_id` 발급·영속화)를 받아 `state_subdir` 문자열을 만들어 `to_app_config`에 넘긴다 → `AppConfig.state_subdir` → `config.state_dir = runtime_dir/state/<state_subdir>` → `last_message.<scope>.sha256`이 그 아래 저장. 따라서 호출부에서 `crawling{n}` 대신 `targets/<id>`만 넣으면 dedup 경로가 안정 ID를 따른다.
- **왜 헬퍼 1개로 중앙화하나:** 호출부가 4곳이라 인라인 4회 복제하면 폴백/strip 규칙이 갈라진다. `_state_subdir_for(settings, index)` 하나로 모아 "id 있으면 targets/<id>, 없으면 crawling{n}"을 단일 정의한다(wheel 재발명·드리프트 방지).
- **폴백이 필요한 이유(빈 id 충돌 함정):** 2.1은 ID를 **활성 탭에만**, 그리고 **load 시점에만** 발급한다. coerce_settings로 막 만든 settings나 테스트 픽스처는 `monitoring_target_id == ""`다. 이때 `f"targets/{id}"`를 그냥 쓰면 모든 탭이 `targets/`(같은 경로)로 충돌한다 → 반드시 `strip()` 검사 후 빈 값이면 `crawling{n}`. 폴백은 **legacy 하위호환·충돌 회피용**이지 주 식별 스킴이 아니다(anti-pattern은 "state_subdir를 다시 crawlingN 순번으로 식별"하는 설계를 금지 — 폴백은 미발급 탭 한정 임시값이고 다음 load에서 안정 ID로 치유). [Source: architecture.md(365), src/rider_crawl/ui_settings.py(175-189)]
- **run_lock은 의도적으로 다른 축이다(혼동 금지):** AC 문구가 "last_message나 run_lock이 섞이지 않고"라고 둘 다 말하지만, 코드상 `run_lock`은 `state_subdir`가 아니라 **브라우저 스코프**(`_run_scope_key`: CDP면 endpoint, persistent면 user_data_dir)로 묶인다. 탭을 재정렬해도 각 탭의 CDP/프로필은 따라가므로 run_lock 재정렬 안전성은 **이미** 성립한다. 본 스토리는 run_lock을 건드리지 않는 것으로 이 AC를 만족한다. `run_lock`을 `targets/<id>` 아래로 옮기면 "같은 브라우저 동시 실행 차단"(test_app.py 198-280)을 깨므로 **금지**. [Source: src/rider_crawl/app.py(68-95), tests/test_app.py(198-280), project-context.md(46)]
- **마이그레이션 데이터 이전은 2.7:** 경로식만 바꾸면 업그레이드 직후 활성 탭의 새 `targets/<id>` 폴더에는 옛 `last_message` 해시가 없다 → `send_only_on_change` 탭이 1회 더 보낼 수 있다(허용된 동작). 옛 `crawlingN` 폴더 복사 + dedup seed는 **Story 2.7**(Migration Contract "Copy old crawlingN folders ... do not delete originals", "Seed DeliveryLog dedup from old last_message hash")가 맡는다. 본 스토리에서 복사/seed를 구현하지 않는다. [Source: implementation-contract.md Migration Contract(101-102), epics.md NFR-18(109)]

### atomic write 설계 (AC2)

- **목표:** `os.replace`는 같은 볼륨에서 원자적 — 교체 중간 상태가 관찰되지 않는다. 그래서 temp(같은 디렉터리)→fsync→replace 순서가 핵심이다(architecture.md 287의 "temp→fsync→rename"). temp가 **다른** 디렉터리에 있으면 `os.replace`가 cross-device로 비원자적이 될 수 있으니 반드시 `path.parent`에 만든다.
- **fsync 위치:** temp 파일 핸들에 `flush()` 후 `os.fsync(fileno)`로 디스크 반영을 강제하고 close → replace. 디렉터리 fsync는 Windows에서 불가하니 생략(크로스플랫폼 우선). 
- **실패 처리:** replace 이전 어느 단계에서 예외가 나면 temp를 unlink하고 예외를 재발생해 호출자(save_settings)가 기존 `messagebox`/상태 흐름으로 처리하게 둔다. 기존 파일은 손대지 않았으므로 직전 유효 상태가 남는다.
- **형식 보존이 회귀의 1순위:** 기존 18+개 `test_ui_settings.py`가 `ensure_ascii=False`·`{"crawlings":[...]}`·Path→str을 잠갔다. 직렬화 코드를 헬퍼로 옮기지 말고 **완성된 문자열만** atomic 헬퍼에 넘겨 형식을 100% 보존한다. [Source: tests/test_ui_settings.py, src/rider_crawl/ui_settings.py(229-233)]

### 로그 rotation 설계 (AC3)

- **두 writer 모두 logging 모듈을 안 쓴다.** 커스텀 포맷(타임스탬프 헤더 + 구분선)에 **경로를 반환**해 UI 에러 메시지에 파일 위치를 붙인다(`run_errors.log`) / 진단 메시지에 첨부한다(`kakao_diagnostics.log`). `RotatingFileHandler`로 바꾸면 (a) 포맷이 바뀌고 (b) 반환 경로 계약이 깨지고 (c) `test_browser_action_required_stops_tab_and_writes_error_log` 등 기존 테스트가 깨진다. → **append 직전 크기 검사 + 회전**이라는 최소 침습 방식 채택.
- **크기 vs 날짜:** AC는 "크기 또는 날짜 기준"을 허용 — 크기 기준이 더 단순하고 결정적이라 채택. 임계치/보존 개수는 `log_rotation.py` 모듈 상수(`DEFAULT_MAX_BYTES=1MB`, `DEFAULT_BACKUP_COUNT=5`)로 노출해 추후 조정 가능하게.
- **best-effort 보존:** 두 writer는 이미 `try/except`로 감싸 실패해도 전송/에러 흐름을 막지 않는다(`_write_run_error_log`는 `None` 반환). rotation 호출을 그 안에 두어 rotation 실패가 흐름을 깨지 않게 한다. rotation이 부분 실패(예: `.1` 삭제 실패)해도 append는 진행되도록 헬퍼 내부를 best-effort로 둔다.
- **redaction은 별도 책임:** rotation은 내용을 안 바꾼다. 진단/traceback 마스킹은 Epic 1 redaction·NFR-10 소관이며 본 스토리에서 손대지 않는다(단, 새 secret을 로그에 추가하지 말 것). [Source: epics.md NFR-10(95), project-context.md(89)]

### 보존해야 할 공개 동작 (깨면 regression)

- (a) **JSON 호환** — `ensure_ascii=False, indent=2`, `{"crawlings":[...]}`, 9탭 로딩, legacy 카카오/쿠팡 추론(2.1·1.5가 잠금). atomic 전환이 이를 흔들면 안 된다.
- (b) **run_lock 의미** — 같은 브라우저 스코프 동시 실행 차단 / 다른 스코프 병렬 허용(`test_app.py` 162-280). state_subdir 변경이 건드리면 안 된다.
- (c) **2.1 ID 안정성** — 재로드 시 동일 ID(persist-on-first-issue). atomic write가 그 영속화를 통과만 시키고 조건은 안 바꾼다.
- (d) **에러/진단 로그 반환 계약** — `_write_run_error_log`→`Path|None`, UI 메시지에 경로 부착. rotation이 이를 깨면 안 된다. [Source: project-context.md(47·54·59·68), src/rider_crawl/app.py(68-95)]

### 이전 스토리 인텔리전스 (Epic 1 → 2.1 → 2.2 이월 교훈)

- **A1(secret 게이트):** Epic 1에서 secret near-miss 반복. 신규 테스트 값은 명백한 가짜값만(`"token"`/`"-100123"`/`"77"`). 로그 rotation 테스트는 더미 텍스트로 채우고 실제 토큰 형태를 만들지 않는다. [Source: epic-1-retro 액션 A1]
- **A2(테스트 수치 stale):** dev 노트에 잠정 pass 수치 박지 말 것 — **리뷰 시점 재측정값 1개만** 정본. [Source: epic-1-retro 액션 A2, 2-1 Senior Review M1(177)]
- **2.1 교훈(범위 규율):** 2.1은 ID를 발급만 하고 경로 연결은 의도적으로 2.2로 미뤘다. 본 스토리는 그 연결만 + 직교 보강 2개. 2.7 마이그레이션 러너 영역(폴더 복사/seed)을 끌어오지 않는다. [Source: 2-1 스토리(19-26·158-159)]
- **dev-env:** pytest는 반드시 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님). git tree는 CRLF/LF 노이즈 — 범위 확인은 `git diff -w`. [Source: memory/dev-env-quirks]
- **테스트 컨벤션:** `tests/`는 미러 구조 + `tmp_path` + 순수 파일 I/O(외부 브라우저/네트워크/PC앱 미호출). 신규 `test_log_rotation.py`도 동일. [Source: project-context.md(53-57), architecture.md(280-282)]

### Project Structure Notes

- 변경/신규: `src/rider_crawl/ui.py`·`ui_settings.py`·`sender.py`(제품 코드), 신규 `src/rider_crawl/log_rotation.py`, 테스트(`test_ui_helpers.py`·`test_app.py`·`test_ui_settings.py`·`test_sender.py`/신규 `test_log_rotation.py`). `.agents/`·`.claude/`·`_bmad/`는 대상 아님. [Source: project-context.md(64)]
- 신규 `log_rotation.py`는 `redaction.py`처럼 작은 단일 책임 유틸 모듈 패턴을 따른다(테스트 옆 파일 `test_log_rotation.py`). [Source: src/rider_crawl/redaction.py, architecture.md(280-282)]
- 상태 식별을 `crawlingN` 순번에 다시 묶지 않는다(architecture Anti-Pattern 365). `targets/<monitoring_target_id>`가 주 식별, `crawling{n}`은 미발급 탭 폴백뿐. [Source: architecture.md(365)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.2(380-399)] — user story·3개 Given/When/Then AC 원문(state_subdir→targets/<id> 재정렬 비혼선, atomic write 강제종료 무손상, 로그 rotation+보존).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2(353-355)] — Epic 2 목표(탭 번호 대신 안정 ID, 원본 손실·중복·자동활성화 없는 마이그레이션, atomic write·로그 rotation 포함).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-31(74)·NFR-10(95)·NFR-18(109)·NFR-20(111)] — 마이그레이션 안전(atomic write·로그 rotation/보존·원본 보존), 진단 산출물 retention, 단계별 회귀.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#P1-02·P1-03·P1-04(37-39)] — state_subdir→targets/<id>(재정렬 시 last_message/run_lock 비혼선), atomic write(temp→fsync→rename, 강제종료 무손상), 로그 rotation(크기/날짜).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#Migration-Contract(101-102)] — 옛 crawlingN 폴더 복사(원본 미삭제)·last_message dedup seed는 **2.7** 소유(본 스토리 비범위).
- [Source: _bmad-output/planning-artifacts/architecture.md(287)] — 설정 JSON `ensure_ascii=False, indent=2` + atomic write(temp→fsync→rename) 정본.
- [Source: _bmad-output/planning-artifacts/architecture.md(361-366)] — Anti-Patterns(❌ state_subdir를 crawlingN 순번으로 재식별).
- [Source: src/rider_crawl/ui.py(61-62·94-98·524·614·808·958-981)] — 활성 판정, 4개 state_subdir 호출부, run_errors.log writer.
- [Source: src/rider_crawl/ui_settings.py(140-201·229-233)] — load/load_all 발급·persist(불변), save/save_all(atomic 대상), 직렬화 정책.
- [Source: src/rider_crawl/sender.py(400-419)] — kakao_diagnostics.log writer(best-effort, rotation 대상).
- [Source: src/rider_crawl/config.py(108-134)·app.py(63-95)] — runtime_dir/state_dir(state_subdir 포함), _last_message_hash_path/_run_lock_path/_run_scope_key(run_lock=브라우저 스코프, 변경 금지).
- [Source: tests/test_app.py(162-280)] — run_lock 동시성 회귀 잠금(같은 브라우저 차단·다른 스코프 허용·state_subdir 달라도 같은 브라우저면 차단).
- [Source: tests/test_ui_helpers.py(1468-1514)] — app_configs_from_settings state_subdir 기존 단언(폴백 하위호환 회귀).
- [Source: tests/test_ui_settings.py(74-145)] — save/save_all 라운드트립 회귀(형식 보존 기준).
- [Source: _bmad-output/implementation-artifacts/2-1-uisettings에-고객-대상-id-부여와-legacy-alias-보존.md(19-26·154-169)] — 2.1이 명시한 2.2 경계, ID 발급=load 한정·persist-on-first-issue 설계.
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-06-13.md] — 액션 A1(secret 게이트)·A2(테스트 수치 단일 정본).
- [Source: memory/dev-env-quirks] — pytest는 `.venv/Scripts/python.exe`, 범위 확인 `git diff -w`.
- 요구사항 추적: FR-31(atomic write·로그 rotation·원본 보존), FR-4(ID 기반 모델), NFR-10(진단 retention), NFR-18(원본 보존), NFR-19/NFR-20(JSON 호환·각 단계 회귀), P1-02/03/04.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 전체 스위트(리뷰 시점 재측정): `.venv/Scripts/python.exe -m pytest -q` → **618 passed** (기준선 598 + 신규 20). HALT/3연속 실패/추가 의존성 없음. (신규 20 = dev-story 15 + QA-automation 갭 보강 5(G1~G4). 최초 dev 기록 613/15는 QA 갭 5건 반영 전 수치라 리뷰 재측정값으로 정정 — A2.)

### Completion Notes List

- **AC1 (P1-02) — `state_subdir` → `targets/<monitoring_target_id>`:** `ui.py`에 단일 파생 헬퍼 `_state_subdir_for(settings, index)`를 추가하고 4개 호출부(`app_configs_from_settings` 96, `prepare_app_clicked` 524, `_run_once_background` 614, `_start_telegram_listener` 808)를 전부 교체했다. `monitoring_target_id.strip()`가 있으면 `targets/<id>`, 없으면 legacy 폴백 `crawling{index+1}`(빈 id로 `targets/` 슬래시-only 충돌 방지). `crawl_name=f"크롤링{n}"`는 표시명이라 그대로 유지. `config.py`/`app.py`의 `state_dir`/`run_lock`/scope는 무변경 — run_lock은 브라우저 스코프축이라 재정렬 안전성이 이미 성립.
- **AC2 (P1-03) — 설정 JSON atomic write:** `ui_settings.py`에 모듈 헬퍼 `_atomic_write_text(path, text)` 추가(같은 디렉터리 `NamedTemporaryFile` → `flush()`+`os.fsync()` → `os.replace`; 실패 시 temp unlink 후 예외 재발생). `save`/`save_all`의 `write_text`만 이 헬퍼로 교체하고 직렬화 코드(`ensure_ascii=False, indent=2`, `{"crawlings":[...]}`, 평면 구조, Path→str)는 완성 문자열만 넘겨 100% 보존. 2.1의 발급 조건·persist-on-first-issue·no-file 가드(load/load_all)는 손대지 않음 — write 방식만 atomic화로 자동 통과.
- **AC3 (P1-04) — 로그 rotation+보존:** 신규 단일 책임 모듈 `log_rotation.py`(`rotate_if_needed`, `DEFAULT_MAX_BYTES=1MB`, `DEFAULT_BACKUP_COUNT=5`)를 추가하고 `ui.py::_write_run_error_log`·`sender.py::_write_kakao_diagnostics`에서 `path` 계산 직후·append 직전에 호출(둘 다 기존 `try/except` 안). RotatingFileHandler 대신 "append 직전 크기 검사+회전" 최소 침습 방식 — 커스텀 포맷·Path 반환 계약·기존 테스트 보존. 모든 파일 연산 best-effort라 rotation 실패가 에러/진단 흐름을 깨지 않음. 내용은 바꾸지 않음(로그에 새 secret 미추가).
- **테스트(20 신규 = dev-story 15 + QA-automation 갭 5):** `test_ui_helpers.py`(+4 state_subdir 파생/폴백/타겟 경로), `test_app.py`(+2 재정렬 시 last_message 경로가 안정 ID를 따름 + run_lock은 브라우저 스코프라 state_subdir 변경에 불변), `test_ui_settings.py`(+4 atomic 강제종료 무손상(os.replace·os.fsync 두 실패 지점)·.tmp 무잔여, 단일 `save()` 경로 포함, 형식 보존), `test_log_rotation.py`(신규 +10: 임계 미만/누락 skip, base→.1·새 base 비움, 시프트+보존 개수, 비활성 파라미터, 두 writer 통합, rotation 예외 시 best-effort ×2). 기존 회귀 잠금(`test_app_configs_from_settings_names_tabs_and_skips_blank_urls` 폴백, run_lock 동시성, round-trip, persist-on-issue) 모두 무수정 통과.
- **범위/누출:** `git diff -w --stat`에 `ui.py`+`ui_settings.py`+`sender.py`+신규 `log_rotation.py`+테스트만. `config.py`/`app.py`/`lock.py`/`models.py` 무변경 확인. 누출 grep 결과 실제 봇 토큰·`chat_id=`·전화 평문 없음(`"0123456789"`는 byte-size 더미 필러로 전화 정규식 우연 매칭 — secret 아님). 모든 상태/로그 테스트는 `tmp_path` 안에서만 수행.

### File List

- `src/rider_crawl/ui.py` (수정 — `_state_subdir_for` 헬퍼 추가, 4개 state_subdir 호출부 교체, `_write_run_error_log` rotation 연결, `rotate_if_needed` import)
- `src/rider_crawl/ui_settings.py` (수정 — `_atomic_write_text` 헬퍼 추가, `save`/`save_all` atomic화, `os`/`tempfile` import)
- `src/rider_crawl/sender.py` (수정 — `_write_kakao_diagnostics` rotation 연결, `rotate_if_needed` import)
- `src/rider_crawl/log_rotation.py` (신규 — 크기 기준 rotation 유틸 + 모듈 상수)
- `tests/test_ui_helpers.py` (수정 — state_subdir 파생 테스트 4개 추가)
- `tests/test_app.py` (수정 — 재정렬 안전 테스트 2개 추가: last_message 경로가 안정 ID를 따름 + run_lock은 브라우저 스코프라 state_subdir 불변)
- `tests/test_ui_settings.py` (수정 — atomic write 테스트 4개 추가: os.replace·os.fsync 실패 무손상, 단일 `save()` 경로, 형식 보존, `pytest` import)
- `tests/test_log_rotation.py` (신규 — rotation 단위/통합 테스트 10개)

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome:** Approve (Status → done)

**범위 검증(소스).** `git diff -w --stat` 결과 변경/신규는 `ui.py`·`ui_settings.py`·`sender.py`·신규 `log_rotation.py` + 테스트 4종뿐이며 File List와 정확히 일치한다. `config.py`/`app.py`/`lock.py`/`models.py`는 무변경(run_lock/state_dir 스코프 보존 확인). git 실제 변경 ↔ 스토리 File List 사이 소스 불일치 0건.

**AC 검증(전부 IMPLEMENTED).**
- **AC1 (P1-02):** `_state_subdir_for(settings, index)` 단일 헬퍼 + 4개 호출부 교체 확인. id 있으면 `targets/<id>`, 빈 id(공백 포함)는 `strip()` 후 `crawling{n}` 폴백 — `targets/`(슬래시-only) 전탭 충돌 없음. `test_last_message_path_follows_target_id_not_tab_order`로 재정렬 비혼선 입증, `test_run_lock_path_is_browser_scoped_independent_of_state_subdir` + 기존 동시성 회귀로 run_lock 무변경(브라우저 스코프축) 입증.
- **AC2 (P1-03):** `_atomic_write_text`가 같은 디렉터리 temp→`flush()`+`os.fsync()`→`os.replace`, 실패 시 temp unlink 후 재발생(`BaseException` 광역 정리). `os.replace`·`os.fsync` 두 실패 지점 모두에 대해 원본 무손상·`.tmp` 무잔여 테스트 통과. 직렬화 형식(`ensure_ascii=False, indent=2`, `{"crawlings":[...]}`/평면) 보존, 2.1 발급/persist 로직 불변.
- **AC3 (P1-04):** `rotate_if_needed`(RotatingFileHandler식 크기 회전, 전 연산 best-effort)를 두 writer의 append 직전·기존 `try/except` 안에 연결. Path 반환 계약·best-effort(rotation 예외 흡수) 테스트 통과.

**Task 감사.** Task 1~5 모두 [x]이고 코드/테스트 근거로 실제 완료 확인(빈 마킹 0건).

**Findings.**
- ~~**[Med] M1 — 테스트 수치 stale(A2 위반).**~~ Dev Agent Record(Debug Log/Completion Notes/File List/Change Log)가 dev-story 시점 수치 **613 passed/신규 15**를 기록했으나, 이후 QA-automation 갭 보강 5건(G1~G4, `test-summary-2.2.md`)이 스토리에 미반영. 리뷰 시점 재측정 = **618 passed/신규 20**. → **자동 수정 완료**: 4개 위치 수치를 재측정값으로 정정(A2 "리뷰 시점 재측정값 1개만 정본" 준수). 소스/AC에는 영향 없는 문서 정합성 문제.
- **[Low] 관찰(무수정).** `_atomic_write_text`는 텍스트 모드(`mode="w"`)라 Windows에서 `\n`→`\r\n` 변환 가능성이 있으나, 직전 `Path.write_text(...)`도 동일 텍스트 모드였으므로 디스크 바이트 동작 동일 — **회귀 아님**. JSON 라운드트립·형식 테스트 통과로 확인.

**CRITICAL 0건** → Status: done. 코드 품질·보안(신규 secret 미추가, 누출 grep 0)·테스트 품질(실제 단언, `tmp_path`/`monkeypatch` 격리) 양호.

## Change Log

- 2026-06-13: Story 2.2 구현 완료 — `state_subdir`를 `targets/<monitoring_target_id>`로 전환(legacy `crawling{n}` 폴백 보존), `ui_settings.json` 저장을 atomic write(temp→fsync→rename)로, `run_errors.log`·`kakao_diagnostics.log`를 크기 기준 rotation+보존으로 보강. 신규 모듈 `log_rotation.py` 추가. 전체 스위트 618 passed(기준선 598 + 신규 20), 회귀 0. Status → review.
- 2026-06-13: Senior Developer Review(AI) — 적대적 리뷰 수행. AC1~3 전부 IMPLEMENTED, Task 1~5 실완료, 소스 File List 일치 확인. M1(테스트 수치 stale, A2 위반: 613/15 → 618/20) 자동 수정 — Debug Log·Completion Notes·File List·Change Log 4개 위치 정정. CRITICAL 0건 → Status: review → done.
