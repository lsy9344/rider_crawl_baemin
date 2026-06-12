# 기준선 기록 — 2026-06-13 (P0-01)

리팩토링 시작 전 known-good 운영본을 고정한 **기준선(baseline)** 기록 문서다.
이 문서에는 **실제 secret 값·실제 zip 내용물·실제 토큰을 적지 않는다.** 위치(경로)·체크섬·메타데이터만 기록한다.
(요구사항: FR-1 기준선 저장, NFR-5 secret 비노출, NFR-18 원본 보존, ADD-15 secret 평문 저장 금지)

## 1. Git 기준선 tag

| 항목 | 값 |
| --- | --- |
| Tag 이름 | `baseline-local-ui-20260613` |
| Tag 종류 | annotated tag (objecttype=tag) |
| 가리키는 commit SHA (full) | `a34d0d0fc19b52c48c40943d3c14354b5c2bd9fe` |
| commit 제목 | `chore: gitignore per-tab log dirs (logs*/)` |
| commit 일시 | 2026-06-12 19:57:55 +0900 |
| 기준선 브랜치 | `origin/main` (== 로컬 `main`, 동기 확인 완료) |
| Tag 메시지 | `Pre-refactor baseline (P0-01): known-good local UI operation` |
| Tagger | lsy9344 |

### 기준선 선정 근거

- 운영 기준선은 known-good 운영본인 `origin/main`(`a34d0d0`)을 가리킨다. **현재 작업 브랜치 `refactoring`(HEAD `506c30b`)은 기준선으로 태깅하지 않는다.** `refactoring`에는 BMAD 스캐폴딩 등 리팩토링 준비 커밋이 섞여 있다.
- 태깅 시 **체크아웃/리셋/머지 없이** tag 객체만 생성했다. 작업 트리(`refactoring`)는 변경되지 않았다(HEAD 그대로 `506c30b`).
- 동명 tag 충돌 검사: 기존 `baseline*` tag 없음을 확인 후 첫 생성. 불변 기준선 원칙상 동명 tag 발견 시 덮어쓰지 않고 중단하도록 절차를 구성했다.

### 검증 명령(재현용)

```bash
git tag -l "baseline-local-ui-20260613"        # 존재 확인
git rev-list -n1 baseline-local-ui-20260613     # → a34d0d0fc19b52c48c40943d3c14354b5c2bd9fe
git cat-file -t baseline-local-ui-20260613      # → tag (annotated)
```

## 2. 설정·상태 백업 zip

| 항목 | 값 |
| --- | --- |
| 백업 zip 경로 | `backups/baseline-config-backup-20260613.zip` |
| sha256 | `36372aa1cda5552d5f31f4f6bf38b1730b204cade8ecbd2400335fc801d6c38b` |
| 압축 크기 | 약 32 KB (원본 합계 약 836 KB, 21개 항목) |
| git 추적 여부 | **추적 안 함** — `.gitignore`에 `backups/` 추가됨. 이 zip은 실제 secret(`.env`, 실제 `runtime/state/ui_settings.json`)을 포함하므로 절대 커밋하지 않는다. |

### 백업에 **포함된** 대상

- `runtime/state/` 전체 — 운영 상태 핵심:
  - `ui_settings.json` (탭별 UI 설정 + 실제 secret)
  - `crawling1/`, `crawling2/` (`last_message.*.sha256` 중복 방지 해시)
  - `run_locks/`
  - `telegram_offsets/` (offset/started/completed)
- `config.json` (키워드 자동응답 설정 — 실제 보험사 전화번호 포함)
- `.env` (실제 환경값)
- `.env.example` (정본화 전 상태 스냅샷)
- `logs/` (`run_errors.log`, `kakao_diagnostics.log`, `current_baemin_page.html` 등 — 약 812 KB)

### 백업에서 **제외된** 대상과 사유

용량이 크고 재생성 가능한 Chrome 프로필 캐시는 기준선 운영 상태가 아니므로 제외했다(스토리 Task 2의 "용량 크면 제외 가능하되 제외 사실 기록" 정책 적용). 제외 대상과 당시 크기:

| 제외 경로 | 크기 | 사유 |
| --- | --- | --- |
| `runtime/browser-profile/` | 161 MB | Chrome 프로필 캐시(로그인 세션). 재로그인으로 재생성 가능, 운영 상태 아님 |
| `runtime/browser-profile-2/` | 174 MB | 동일 |
| `runtime/browser-profile.bak_20260610_204652/` | 118 MB | 과거 프로필 백업본 |
| `runtime/chrome-cdp-profile/` | 192 MB | CDP 연결용 Chrome 프로필 캐시 |
| `runtime/coupang-<COUPANG_LOGIN_ID>-profile/` | 99 MB | 쿠팡 탭 Chrome 프로필 캐시 (실제 경로의 login-id 부분은 마스킹) |
| `runtime/debug/` | 168 KB | 디버그 산출물(재생성 가능) |

> 참고: 제외한 Chrome 프로필에는 로그인 쿠키/세션이 들어 있다. 기준선 복원 시 운영 설정·상태(`runtime/state/`, `config.json`, `.env`)는 이 zip으로 복원하고, 브라우저 로그인 세션은 운영 PC에서 재로그인으로 복구한다.

### 검증 명령(재현용)

```bash
# Linux/WSL
sha256sum backups/baseline-config-backup-20260613.zip
# Windows PowerShell
Get-FileHash backups\baseline-config-backup-20260613.zip -Algorithm SHA256
# 기대값: 36372aa1cda5552d5f31f4f6bf38b1730b204cade8ecbd2400335fc801d6c38b
```

## 3. 생성 메타데이터

| 항목 | 값 |
| --- | --- |
| 생성 일시 (KST) | 2026-06-13 01:37:12 KST |
| 작성자 | lsy9344 (Noah Lee) |
| 기준선 작업 수행 환경 | WSL2 (Linux 6.6.87.2-microsoft-standard-WSL2), Python 3.12.3, git 2.43.0 |
| 프로젝트 대상 런타임 | Windows + `.venv` (Python `>=3.10`) — 실제 운영 실행 환경 |

## 4. sanitized 설정 샘플 (P0-02) 연계

외부 커밋 문서용 sanitized 설정 샘플을 함께 생성했다(실제 secret/식별자는 placeholder로 치환):

- `docs/config-samples/ui_settings.sample.json`
- `docs/config-samples/config.sample.json`
- `docs/config-samples/.env.sample`

또한 추적 파일 `.env.example`에 남아 있던 실제 운영 식별자를 in-place로 placeholder 치환(정본화)했다.
정본화 전/후 차이:

| 키 | 정본화 전 (`.env.example`) | 정본화 후 |
| --- | --- | --- |
| `BAEMIN_CENTER_NAME` | 실제 센터명(마포 계열) | `<BAEMIN_CENTER_NAME>` |
| `BAEMIN_CENTER_ID` | 실제 센터 ID(`DP`로 시작) | `<BAEMIN_CENTER_ID>` |
| `KAKAO_CHAT_NAME` | 실제 채팅방명(의정부 계열) | `<KAKAO_CHAT_NAME>` |

`TELEGRAM_*`는 정본화 전에도 빈 값이었다.

## 5. 후속 호환

후속 Story 1.2가 `docs/qa/`에 pytest 기준선 리포트를 추가한다. 본 문서는 `docs/qa/baseline-record-YYYYMMDD.md` 컨벤션을 따르며, 1.2의 기록물과 같은 폴더·명명 규칙을 공유한다.
