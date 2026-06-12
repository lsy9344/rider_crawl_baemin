---
baseline_commit: 506c30b9edd63e4a9f1c2e3ab999bf5f38e26149
---

# Story 1.1: 기준선 branch/tag와 설정 백업 생성

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 리팩토링을 시작하기 전에 현재 동작하는 코드와 설정을 branch/tag와 백업으로 고정하고,
so that 이후 어떤 단계에서도 깨끗하게 되돌릴 수 있는 기준점이 남는다.

> 본 스토리는 Epic 1(기준선 안전망, P0)의 첫 스토리이며, spec 단계 **P0-01·P0-02**를 구현한다. 코드 기능 변경이 아니라 **운영 기준선을 고정하는 절차/산출물 스토리**다. 산출물은 ① git tag, ② 설정·상태 백업 zip, ③ sanitized 설정 샘플 문서, ④ `docs/qa/` 기준선 기록 문서다.

## Acceptance Criteria

**AC1 — 기준선 tag·백업·기록 생성 (P0-01)**
1. **Given** 현재 정상 동작하는 known-good 브랜치(`main` 또는 배포 브랜치)와 로컬 `runtime/state/ui_settings.json`, `config.json`, `.env`(및 `.env.example`)가 존재할 때 **When** 기준선 고정 절차를 수행하면 **Then** `baseline-local-ui-20260613` 형식(`baseline-local-ui-YYYYMMDD`)의 annotated git tag가 생성된다.
2. **And** 현재 설정/상태 폴더를 묶은 백업 zip이 존재한다.
3. **And** tag 이름·태그가 가리키는 commit SHA·백업 zip 경로·체크섬(sha256)·생성 일시가 `docs/qa/`(기준선 기록 문서)에 기록된다.

**AC2 — sanitized 설정 샘플 (P0-02, NFR-5, ADD-15)**
4. **Given** 민감값이 포함된 실제 설정 파일을 문서화해야 할 때 **When** `ui_settings.json`, `config.json`, `.env.example`의 sanitized 샘플을 작성하면 **Then** 토큰·비밀번호·`chat_id`·`message_thread_id`·OTP 등 민감값이 placeholder로 대체된 sanitized 설정 샘플이 `docs/` 아래에 존재한다.
5. **And** 어떤 실제 secret 값(`telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`, 쿠팡 비밀번호, Gmail token/OTP 등)도 문서·git 커밋에 남지 않는다.

**AC3 — 원본 보존 (NFR-18, FR-1)**
6. **Given** 기존 원본 상태를 보존해야 할 때 **When** 백업을 생성하면 **Then** 기존 `runtime/`, `logs/`, `runtime/state/ui_settings.json`, `crawlingN`(예: `runtime/state/crawling1`, `crawling2`) 상태 폴더 원본이 삭제·변형되지 않고 그대로 유지된다.

## Tasks / Subtasks

- [x] **Task 1 — known-good 기준선 commit 확정 및 annotated tag 생성 (AC: 1, 3)**
  - [x] 기준선 대상 브랜치를 확정한다: 기본은 `main`(원격 `origin/main`과 동기 확인). 운영 배포본이 다른 브랜치/커밋이면 그 commit을 기준으로 한다. **현재 작업 브랜치 `refactoring`을 기준선으로 태깅하지 않는다.**
  - [x] 태그 대상 commit SHA를 기록한다: `git rev-parse <baseline-ref>` (예: `git rev-parse origin/main`).
  - [x] annotated tag 생성: `git tag -a baseline-local-ui-20260613 <commit-sha> -m "Pre-refactor baseline (P0-01): known-good local UI operation"`. (날짜 `20260613`은 생성일 기준 `YYYYMMDD`.)
  - [x] 동일 이름 tag가 이미 있으면 덮어쓰지 말고 중단 후 기록에 사유를 남긴다(기준선은 불변이어야 함).
  - [x] tag는 **체크아웃/리셋/머지 없이** 생성만 한다. 작업 트리(`refactoring`)를 건드리지 않는다.
- [x] **Task 2 — 설정·상태 백업 zip 생성 (AC: 1, 3)**
  - [x] 백업 대상: `runtime/`(특히 `runtime/state/ui_settings.json`, `runtime/state/crawling1`, `runtime/state/crawling2`, `run_locks/`, `telegram_offsets/`), `config.json`, `.env`, `.env.example`, `logs/`(선택, 용량 크면 제외 가능하되 제외 사실을 기록).
  - [x] **원본을 옮기지 말고 복사**해서 zip으로 묶는다(읽기 only). 원본 폴더 경로/내용을 변경하지 않는다(AC3).
  - [x] zip 파일명: `baseline-config-backup-20260613.zip`.
  - [x] zip 저장 위치는 **git에 커밋되지 않는 경로**여야 한다. `backups/` 디렉터리를 만들고 `.gitignore`에 `backups/` 줄을 추가하거나, 저장소 밖 경로(예: 사용자 지정 백업 폴더)에 둔다. **이 zip은 실제 secret(`.env`, 실제 `ui_settings.json`)을 포함하므로 절대 git에 추가·커밋하지 않는다.**
  - [x] zip의 sha256 체크섬을 계산해 기록용으로 보관한다.
- [x] **Task 3 — `docs/qa/` 기준선 기록 문서 작성 (AC: 1)**
  - [x] `docs/qa/` 디렉터리를 생성한다(현재 없음). 이 폴더는 P0 산출물(기준선·pytest 결과) 보관소로 architecture에 정의됨.
  - [x] `docs/qa/baseline-record-20260613.md`를 작성한다. 포함 항목: tag 이름, tag가 가리키는 commit SHA(전체), 기준선 브랜치, 백업 zip의 **경로**와 sha256 체크섬, 생성 일시(KST), Python 버전/실행 환경, 작성자.
  - [x] 기록 문서에는 **실제 secret 값·실제 zip 내용물·실제 토큰을 적지 않는다.** 위치(경로)와 체크섬·메타데이터만 적는다.
- [x] **Task 4 — sanitized 설정 샘플 작성 (AC: 2)**
  - [x] `docs/` 아래에 sanitized 샘플 3종을 둔다(권장 위치: `docs/config-samples/`). 파일명 예: `ui_settings.sample.json`, `config.sample.json`, `.env.sample`(또는 기존 `.env.example` 재확인·정본화).
  - [x] `ui_settings.sample.json`: 실제 `runtime/state/ui_settings.json` 구조를 따르되 민감 필드를 placeholder로 치환한다 — `telegram_bot_token` → `"<TELEGRAM_BOT_TOKEN>"`, `telegram_chat_id` → `"<TELEGRAM_CHAT_ID>"`, `telegram_message_thread_id` → `"<TELEGRAM_MESSAGE_THREAD_ID>"`. 운영 식별자(`kakao_chat_name`, `baemin_center_name`, `baemin_center_id`)는 **마스킹 옵션 정책에 따라** placeholder(예: `"<KAKAO_CHAT_NAME>"`, `"<CENTER_NAME>"`)로 둔다(외부에 커밋되는 문서이므로 보수적으로 마스킹).
  - [x] `crawlings` 배열은 탭 1개 분량의 대표 샘플만 남겨 구조를 보여준다(실제 9개 탭 값 전체를 복사하지 않는다).
  - [x] `config.sample.json`: `keywords`는 일반 예시로, `auto_message`의 실제 보험사 전화번호는 placeholder(예: `"☎️예시생명 : 0000-0000"`)로 치환한다.
  - [x] `.env.sample`: 기존 `.env.example`을 기준으로 하되 `TELEGRAM_*`은 빈/placeholder 유지, `KAKAO_CHAT_NAME`·`BAEMIN_CENTER_NAME`·경로 등 식별 가능한 실제값은 placeholder로 바꾼다. (기존 `.env.example`이 이미 대부분 sanitized이면 그 사실과 차이를 기록 문서에 명시한다.)
  - [x] JSON 샘플은 프로젝트 규칙대로 `ensure_ascii=False, indent=2` 스타일을 유지한다.
- [x] **Task 5 — 누출 방지 검증 및 마무리 (AC: 2, 3)**
  - [x] `git status`/`git diff --cached`로 **백업 zip·실제 `.env`·실제 `ui_settings.json`이 스테이징/커밋 대상에 없음**을 확인한다.
  - [x] sanitized 샘플과 `docs/qa/` 기록 문서에 실제 토큰·`chat_id`·비밀번호·전화번호 문자열이 남지 않았는지 grep으로 점검한다(예: 실제 bot token 패턴, `telegram_chat_id` 실제값, 보험사 번호).
  - [x] 원본 `runtime/`, `logs/`, `runtime/state/ui_settings.json`, `crawlingN`이 변경되지 않았음을 확인한다(`git status`에 의도치 않은 변경 없음 + 파일 mtime/내용 보존).
  - [x] 산출물 경로를 File List에 모두 기록한다.

## Dev Notes

### 이 스토리의 성격과 경계

- **코드 기능 변경이 아니다.** `src/rider_crawl/` 제품 코드는 손대지 않는다. 산출물은 git tag, 백업 zip, `docs/` sanitized 샘플, `docs/qa/` 기록 문서뿐이다.
- **불변 기준선 원칙:** tag와 백업은 "지금 잘 동작하는 상태"의 스냅샷이다. 만들고 나면 수정하지 않는다. 동명 tag가 이미 있으면 덮어쓰지 말고 중단한다.
- **기준선 대상은 `main`/배포 브랜치이지 `refactoring`이 아니다.** 현재 HEAD는 `refactoring`(`506c30b`)이며 BMAD 스캐폴딩 등 리팩토링 준비 커밋이 섞여 있다. 운영 기준선은 known-good 운영본(`origin/main`)을 가리켜야 한다. 어떤 commit을 기준선으로 잡았는지 SHA로 기록 문서에 남긴다.

### Secret/민감값 처리 (NFR-5, ADD-15 — 절대 위반 금지)

- 마스킹 대상: password, token, refresh token, authorization code, OTP, full phone number, full email, `telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`. (operations-security-test-contract §Log And Artifact Redaction)
- 실제 `runtime/state/ui_settings.json`의 민감 필드는 정확히 3개다: `telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`(각 `crawlings[]` 항목 안). 나머지 식별자(`kakao_chat_name`, `baemin_center_name`)는 운영 식별자로, 외부 커밋 문서에서는 보수적으로 마스킹한다.
- `config.json`의 `auto_message`에는 실제 보험사 전화번호가 들어 있다 → 샘플에서는 placeholder로 치환.
- **백업 zip은 실제 secret을 포함하므로 git에 절대 올리지 않는다.** `.gitignore`에 `backups/`를 추가하거나 저장소 밖에 둔다. 이 한 줄 누락이 secret 평문 커밋(ADD-15 금지 행위)으로 직결된다.
- secret을 로그/예외/문서에 남기지 않는다. 기록 문서에는 경로·체크섬·메타데이터만 적는다.

### 현재 저장소 상태 (확인 완료)

- 기존 git tag: **없음** → `baseline-local-ui-20260613`이 첫 tag.
- 현재 브랜치: `refactoring`. `main`, `origin/main`, `legacy-origin/main` 존재.
- `.gitignore`가 이미 제외 중: `runtime/`, `logs/`, `logs*/`, `.env`, `secrets/google/*.json|pickle|token*|credentials*`, `build/`. → 실제 secret 파일은 추적되지 않음(좋음). 단, **백업 zip 경로는 아직 ignore되지 않으므로 `backups/`를 추가해야 함**.
- `docs/qa/`: **아직 없음** → 생성 필요.
- 추적되는 설정 파일: `config.json`(실제 보험사 전화번호 포함), `.env.example`(대부분 sanitized이나 `KAKAO_CHAT_NAME`·`BAEMIN_CENTER_NAME`·`BAEMIN_CENTER_ID`에 실제 운영 식별자값이 남아 있음 → sanitized 정본화 시 마스킹 대상). 실제 `.env`는 ignore됨.
- `runtime/state/` 구성: `ui_settings.json`(7.4KB), `crawling1/`, `crawling2/`, `run_locks/`, `telegram_offsets/`.

### 실제 `ui_settings.json` 키 구조 (sanitized 샘플 작성용 참고)

- 최상위/탭 공통 키: `platform_name`, `crawlings`(탭 배열), `messenger_name`, `browser_mode`, `cdp_url`, `browser_user_data_dir`, `headless`, `interval_minutes`, `page_timeout_seconds`, `run_lock_timeout_seconds`, `send_enabled`, `send_only_on_change`, `timezone`, `log_dir`.
- 탭별 식별/식자: `baemin_center_id`, `baemin_center_name`, `kakao_chat_name`, `peak_dashboard_url`, `performance_url`.
- 탭별 **민감 필드(마스킹 필수)**: `telegram_bot_token`, `telegram_chat_id`, `telegram_message_thread_id`.

### 권장 산출물 경로

- git tag: `baseline-local-ui-20260613` (annotated, `origin/main` 기준 권장).
- 백업 zip: `backups/baseline-config-backup-20260613.zip` (git ignore 대상) — 또는 저장소 밖 경로.
- 기준선 기록: `docs/qa/baseline-record-20260613.md`.
- sanitized 샘플: `docs/config-samples/ui_settings.sample.json`, `docs/config-samples/config.sample.json`, `docs/config-samples/.env.sample`.

### 검증 방법 (이 스토리는 자동 pytest 대상이 아님 — 수동 체크리스트)

- `git tag -l` → `baseline-local-ui-20260613` 존재, `git rev-list -n1 baseline-local-ui-20260613`로 SHA 확인.
- 백업 zip 존재 + `sha256sum`(또는 PowerShell `Get-FileHash`) 값이 기록 문서와 일치.
- `git status`에 백업 zip·실제 `.env`·실제 `ui_settings.json` 미포함.
- sanitized 샘플에 실제 토큰/`chat_id`/전화번호 문자열 부재(grep).
- 원본 `runtime/`·`logs/`·`crawlingN` 미변경.
- 후속 Story 1.2가 `docs/qa/`에 pytest 기준선 리포트를 추가하므로, 폴더·기록 컨벤션을 1.2와 호환되게 둔다.

### Project Structure Notes

- 산출물은 모두 architecture가 정의한 위치와 정렬된다: `docs/qa/`(P0 기준선·pytest 결과), `docs/`(샘플/문서). [Source: architecture.md#Source-Tree(462-465), #Decision-Log(672-673)]
- 제품 코드(`src/rider_crawl/`)·테스트(`tests/`) 변경 없음. `.agents/`, `.claude/`, `_bmad/` 등 도구 파일도 변경 대상 아님. [Source: project-context.md#코드품질규칙]
- 충돌/변이 요소: 백업 zip의 git ignore 처리(현재 `.gitignore`에 `backups/` 없음) — 본 스토리에서 `backups/` 한 줄 추가가 유일한 추적 파일 변경 후보다. `.env.example`이 이미 추적되고 일부 실제 식별자를 포함하는 점은 sanitized 정본화 시 함께 정리한다.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.1(242-263)] — 본 스토리 user story·AC 원문.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md#Phase-P0(22-30)] — P0-01(tag+백업 zip 존재), P0-02(sanitized 설정 샘플 존재) 계약·수용 조건.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Log-And-Artifact-Redaction(13-19)] — 마스킹 대상 민감값 정의, 운영 식별자 마스킹 옵션.
- [Source: _bmad-output/specs/spec-riderbot-refactoring/operations-security-test-contract.md#Secret-Storage(3-11)] — secret 저장/비노출 원칙.
- [Source: _bmad-output/planning-artifacts/architecture.md#Source-Tree(462-468)] — `docs/qa/`, `runtime/`, `logs/`, `secrets/google/` 경계.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision-Log(672-673)] — P0 기준선 고정 정의(branch/tag, settings 백업, pytest 결과, redaction, 회귀 시나리오).
- [Source: _bmad-output/project-context.md#절대놓치면안되는규칙(84-95)] — secret 비노출, 원본 보존, 로컬 상태 경로 정책.
- 요구사항 추적: FR-1(기준선 저장), FR-2(자산 재사용 보장), NFR-5(secret 비노출), NFR-18(호환/원본 보존), ADD-15(secret 평문 저장 금지). [Source: _bmad-output/planning-artifacts/epics.md#FR-Coverage-Map(154-189)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code, dev-story workflow)

### Debug Log References

- annotated tag 생성 1차 실패: git committer identity 미설정(`empty ident name`) → 저장소-로컬(`--local`)로 `user.name=lsy9344`, `user.email=dltnduf4318@gmail.com` 설정 후 재생성 성공. 전역 config는 변경하지 않음.
- 누출 스캔 1차에서 2건 적발 후 수정: (a) `docs/qa/baseline-record`의 제외 디렉터리 경로명 `coupang-jojo0805-profile`이 coupang login-id를 포함 → `coupang-<COUPANG_LOGIN_ID>-profile`로 마스킹, (b) `.env.example`의 기존 주석에 남아 있던 실제 센터명 단편(`표준서울마포...`) → 비식별 문구로 교체. 재스캔 clean.
- 회귀 검증: 시스템 `python3`에는 pytest 미설치. 프로젝트 Windows venv(`.venv/Scripts/python.exe`)를 WSL에서 실행 → **397 passed in 3.06s**.

### Completion Notes List

- 본 스토리는 코드 기능 변경이 아니라 운영 기준선 고정 절차/산출물 스토리다. `git diff -w -- src/ tests/`로 제품 코드 실질 변경 0건 확인(작업 트리의 `M src/...`는 세션 이전부터 있던 CRLF/LF EOL 노이즈로, 본 스토리와 무관 — 프로젝트 규칙대로 되돌리지 않음).
- **Task 1 (AC1,3):** 기준선 ref를 `origin/main`(== 로컬 `main`, 동기 확인)으로 확정. annotated tag `baseline-local-ui-20260613` → `a34d0d0fc19b52c48c40943d3c14354b5c2bd9fe` 생성. 체크아웃/리셋/머지 없이 tag 객체만 생성 → 작업 트리(`refactoring`, HEAD `506c30b`) 무변경. 기존 `baseline*` tag 없음 확인(불변 기준선, 동명 충돌 시 중단 절차 적용).
- **Task 2 (AC1,3):** `backups/baseline-config-backup-20260613.zip`(32K, 21항목) 생성. 포함: `runtime/state/` 전체, `config.json`, `.env`, `.env.example`, `logs/`. 용량 큰 재생성 가능 Chrome 프로필 캐시(약 744MB)는 제외하고 사유·크기를 기록 문서에 명시(스토리 Task2 "용량 크면 제외 가능하되 제외 사실 기록" 정책). 원본은 읽기만 함(zip은 원본을 이동/변형하지 않음). sha256 `36372aa1cda5552d5f31f4f6bf38b1730b204cade8ecbd2400335fc801d6c38b`. `.gitignore`에 `backups/` 추가 → zip은 git 추적 안 됨(`git check-ignore` 통과).
- **Task 3 (AC1):** `docs/qa/` 신규 생성, `docs/qa/baseline-record-20260613.md` 작성. tag명·commit SHA(full)·기준선 브랜치·zip 경로·sha256·생성 일시(2026-06-13 01:37 KST)·실행 환경·작성자·제외 대상/사유 기록. 실제 secret/토큰/zip 내용물은 미기재(경로·체크섬·메타데이터만).
- **Task 4 (AC2):** `docs/config-samples/`에 sanitized 샘플 3종 생성: `ui_settings.sample.json`(대표 탭 1개, telegram 토큰/chat_id/thread_id + 운영 식별자 placeholder 치환), `config.sample.json`(키워드 일반화·보험사 전화번호 placeholder), `.env.sample`(TELEGRAM_* 빈값 유지, 식별자·경로 placeholder). JSON은 `ensure_ascii=False, indent=2` 스타일 유지. 추가로 추적 파일 `.env.example`의 실제 운영 식별자 3종(`BAEMIN_CENTER_NAME`,`BAEMIN_CENTER_ID`,`KAKAO_CHAT_NAME`) + 주석 1건을 in-place 마스킹(사용자 승인). JSON 유효성 검증 통과.
- **Task 5 (AC2,3):** ① 비밀 보유 파일(`backups/...zip`, `.env`, `runtime/state/ui_settings.json`)이 모두 git-ignored 확인. ② 실제 토큰/chat_id/비밀번호/식별자 13종 패턴으로 커밋 대상 산출물(`docs/config-samples`, `docs/qa/baseline-record`, `.env.example`) 누출 스캔 → 최종 clean. ③ 원본 무변경(AC3): `runtime/state/ui_settings.json`·`config.json`·`.env` sha256가 백업 전 스냅샷과 일치, `runtime/`·`logs/` 추적 변경 없음.
- ⚠️ git 저장소-로컬 config에 `user.name`/`user.email`을 설정했다(annotated tag 작성에 필요). 전역(global) config는 미변경.

### File List

**신규 (git 추적):**
- `docs/qa/baseline-record-20260613.md`
- `docs/config-samples/ui_settings.sample.json`
- `docs/config-samples/config.sample.json`
- `docs/config-samples/.env.sample`

**수정 (git 추적):**
- `.gitignore` (`backups/` 무시 규칙 1줄 추가)
- `.env.example` (실제 운영 식별자 3종 + 주석 1건 placeholder 마스킹)
- `_bmad-output/implementation-artifacts/1-1-기준선-branch-tag와-설정-백업-생성.md` (frontmatter `baseline_commit`, Task 체크박스, Dev Agent Record, Change Log, Status)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (`1-1-...` 상태 ready-for-dev→review)

**산출물 (git에 커밋되지 않음 — 기록만):**
- git tag `baseline-local-ui-20260613` (annotated, → `a34d0d0fc19b52c48c40943d3c14354b5c2bd9fe`)
- `backups/baseline-config-backup-20260613.zip` (`.gitignore`로 제외, sha256 `36372aa1…d6c38b`)

## Change Log

| 날짜 | 변경 | 작성자 |
| --- | --- | --- |
| 2026-06-13 | Story 1.1 구현: 기준선 annotated tag(`baseline-local-ui-20260613`) 생성, 설정·상태 백업 zip(+sha256) 생성·gitignore, `docs/qa/` 기준선 기록 문서 작성, `docs/config-samples/` sanitized 샘플 3종 작성, `.env.example` 실제 식별자 마스킹, 누출/원본보존 검증 완료. 회귀 테스트 397 passed. Status → review. | lsy9344 (claude-opus-4-8) |
| 2026-06-13 | 코드 리뷰(story-automator-review, auto-fix): AC1~3 모두 IMPLEMENTED·Task 5종 [x] 검증 일치·실제 secret 누출 0건 확인. MEDIUM 1건 자동 수정 — `.env.example` 편집 시 혼입된 전체 파일 EOL flip(LF→CRLF, 50줄 통째 diff)을 LF로 정규화해 마스킹 변경 4줄만 남도록 정리. CRITICAL 0건 → Status → done. | Noah Lee (review, claude-opus-4-8) |

## Senior Developer Review (AI)

- **리뷰어:** Noah Lee · **일시:** 2026-06-13 · **방식:** story-automator-review (adversarial, auto-fix) · **결과: Approve (done)**

### 검증한 클레임 (git reality 대조)

- **AC1 (P0-01) — IMPLEMENTED.** `baseline-local-ui-20260613`은 annotated tag(`git cat-file -t` = `tag`)이며 `a34d0d0fc19b52c48c40943d3c14354b5c2bd9fe`를 가리킨다. 이 SHA는 `origin/main` == 로컬 `main`과 정확히 일치하고, 작업 브랜치 `refactoring`(HEAD `506c30b`)과 다름을 확인 — 체크아웃/리셋 없이 tag만 생성되어 작업 트리 무변경. 백업 zip(`backups/baseline-config-backup-20260613.zip`) 실재, sha256 `36372aa1…d6c38b`가 기록 문서값과 일치.
- **AC1.3 — IMPLEMENTED.** `docs/qa/baseline-record-20260613.md`에 tag명·full SHA·기준선 브랜치·zip 경로·sha256·생성 일시(KST)·실행 환경·제외 대상이 모두 기록됨. 실제 secret/토큰/zip 내용물은 미기재(경로·체크섬·메타데이터만).
- **AC2 (P0-02) — IMPLEMENTED.** sanitized 샘플 3종 실재. `ui_settings.sample.json` 키 구조가 실제 `runtime/state/ui_settings.json`의 `crawlings[]` 21개 키와 정확히 일치(대표 탭 1개로 축약, Task 4 정책대로). `config.sample.json`은 실제 키워드·보험사명·전화번호를 일반 예시/placeholder로 치환.
- **AC2.5 (secret 비노출) — PASS.** 실제 `ui_settings.json`/`.env`/`config.json`에서 추출한 민감값(telegram_bot_token·chat_id·message_thread_id·실제 센터명/ID·kakao 채팅방명·보험사 전화번호 등 18개 패턴)으로 커밋 대상 산출물 전체(`docs/config-samples/*`, `docs/qa/*`, `.env.example`, 스토리 파일, sprint-status)를 스캔 → **실제 secret 0건**. `.env.example` 마스킹 diff(`git diff -w`)도 실제 센터명·센터ID·kakao 채팅방명 3종이 각각 `<BAEMIN_CENTER_NAME>`·`<BAEMIN_CENTER_ID>`·`<KAKAO_CHAT_NAME>` placeholder로 치환됨을 확인(실제 식별자값은 본 기록 문서에 전재하지 않음). (스캔이 부수적으로 `secrets/google/*.json` 경로·`120`초 폴링 간격을 매칭했으나 이는 secret이 아닌 예시 경로/상수로, example 파일에 있는 것이 정상 — 오탐.)
- **AC3 (원본 보존) — PASS.** 백업 zip 내 사본과 현재 라이브 파일의 sha256 동일: `ui_settings.json`(7413B), `config.json`(209B), `.env`(298B) 모두 `live==backup` True. 원본 `runtime/`·`logs/`는 `.gitignore`로 추적 제외되어 git 변경 없음.
- **File List 정합성 — OK.** 스토리가 주장한 추적 변경(`.gitignore`에 `backups/` 1줄, `.env.example` 마스킹, 신규 docs 4종)이 git 실제 상태와 일치. 허위 클레임·미기록 변경 없음.

### 자동 수정 적용 (1건)

- **[MEDIUM][diff-hygiene] `.env.example` 전체 파일 EOL flip → LF 정규화.** dev 편집 과정에서 파일 전체가 LF(HEAD, CRLF 0줄)→CRLF(작업본 50줄)로 바뀌어 보안 관련 4줄 마스킹 변경이 50줄 통째 rewrite diff에 묻혀 있었다. `core.autocrlf` 미설정·`.gitattributes` 규칙 없음을 확인 후 CRLF→LF로 정규화(내용 바이트 보존). 결과: `git diff`가 의도한 4줄(센터명/센터ID/kakao명 마스킹 + 주석 1줄)만 노출 → 리뷰·커밋 가독성 회복.

### 남은 관찰(LOW, 미수정 — 차단 아님)

- `.env.sample`은 `BROWSER_USER_DATA_DIR`·`LOG_DIR`을 placeholder로 둔 반면 `.env.example`은 일반 기본 경로(`C:\rider_crawl\…`)를 유지 — 두 sanitized 산출물 간 표기 차이. 해당 경로는 민감값이 아니고 `.env.example`은 운영자가 복사해 쓰는 정본 템플릿이므로 기본값 유지가 합리적. 누출 아님.
- 비밀 포함 백업 zip의 보호는 (이 스토리에서 추가한) `backups/` ignore 규칙에 의존한다. `git check-ignore`로 현재 작업 트리에서 무시됨을 확인했고, 스토리 변경 커밋 시 `.gitignore` 한 줄도 함께 커밋되면 영구 보호됨.

### 결론

CRITICAL 0건. AC 3종 전부 충족, secret 누출 없음, 원본 보존 확인. MEDIUM 1건 자동 수정 완료. **Status → done.**
