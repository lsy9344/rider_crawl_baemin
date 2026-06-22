# done_imap 브랜치 크롤링 기능 누락 조사

작성일: 2026-06-20
기준 브랜치: `design_develop` 로컬 작업트리
비교 대상: `origin/imap`
상태: 적용 완료
적용일: 2026-06-20

적용 기록: 본 문서의 지시대로 메시지 10칸 게이지, 쿠팡 거절률 보정, 배민 취소율 보강 수집/표시, 배민 배달현황 `rowspan`/`colspan` 파싱을 코드와 테스트에 적용했다. 쿠팡 센터 검증 fail-closed 정책과 rider-performance 센터 불일치 fail-fast 정책은 문서 권장대로 유지했다.

## 1. 결론 요약

현재 로컬 코드에서 `origin/imap` 대비 실제로 빠진 핵심은 세 가지다.

1. 메시지 10칸 게이지 표시
   - 쿠팡 피크 구간별 게이지가 빠져 있다.
   - 배민 달성현황 구간별 게이지가 빠져 있다.

2. 배민 달성현황 메시지의 취소율 보강
   - `origin/imap`은 배민 달성현황을 읽은 뒤 별도로 배민 `배달현황(history)` 페이지를 읽어 `(거절 + 취소) / (완료 + 거절 + 취소)` 합산율을 `cancel_rate`로 붙인다.
   - 현재 로컬은 이 별도 조회와 병합이 없고, 메시지에 `거절율 : X%`를 출력한다.

3. 배민 배달현황 표의 `rowspan`/`colspan` 정렬 처리
   - 실제 배민 배달현황 표는 2단 헤더를 쓴다.
   - `origin/imap`은 `colspan`/`rowspan`을 펼쳐 열 밀림을 막는다.
   - 현재 로컬은 평면 추출이라 `완료`, `배차취소`, `배달취소(라이더귀책)` 열이 밀릴 수 있다.

반대로, `수행중인원`은 현재 로컬에도 있다. 완전 누락 기능이 아니다. 다만 `origin/imap`은 쿠팡 보조 페이지(`rider-performance`) 실패를 더 넓게 best-effort 처리해서 `peak-dashboard` 전송을 계속하고 `수행중인원`만 생략하는 정책이다. 현재 로컬은 센터 불일치 같은 일부 오류를 더 엄격하게 실패시킨다.

## 2. 조사 범위와 방법

확인 명령은 체크아웃 없이 읽기 전용으로 수행했다.

- `git show origin/imap:<path>`
- `git diff -w --ignore-blank-lines origin/imap -- <path>`
- `git grep origin/imap`
- 현재 로컬 파일 라인 번호 확인

비교한 주요 파일:

- `src/rider_crawl/message.py`
- `src/rider_crawl/crawler.py`
- `src/rider_crawl/parser.py`
- `src/rider_crawl/platforms/coupang/crawler.py`
- `src/rider_crawl/platforms/coupang/parser.py`
- `tests/test_message.py`
- `tests/test_crawler.py`
- `tests/test_baemin_parser.py`
- `tests/test_coupang_message.py`
- `tests/test_coupang_crawler.py`

관련 `origin/imap` 커밋 힌트:

- `94661cf feat(message): 피크 구간별 10칸 진행 게이지 추가`
- `4d890cd feat(baemin): 배달현황 취소율(거절+취소 합산) 메시지 추가, 거절율 줄 제거`
- `456d9a6 feat(baemin): 달성현황 메시지를 '오늘 수행건수/주간 목표건수'로 결합(센터 ID 매칭)`
- `37a18f2 feat(coupang): 모든 쿠팡이츠 탭에서 '수행중인원' 수집 + rider-performance 탭 부재 false 만료 해결`

주의: 위 커밋 중 `456d9a6`, `37a18f2`의 핵심 일부는 현재 로컬에 이미 들어와 있다. 그대로 전체 적용하면 현재 로컬의 엄격한 센터 검증과 2FA 보강을 되돌릴 수 있다.

## 3. 기능별 정확한 차이

### 3.1 메시지 게이지 표시

증거 등급: Confirmed

`origin/imap`에는 10칸 진행 게이지가 있다.

- `origin/imap:src/rider_crawl/message.py:27-35`
  - `GAUGE_CELLS = 10`
  - `_progress_gauge(ratio)`
  - 채운 칸은 `█`, 빈 칸은 `░`

현재 로컬에는 해당 상수와 함수가 없다.

- 로컬 [src/rider_crawl/message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/message.py:40)
  - `_peak_times()` 다음에 바로 `render_current_screen_message()`로 이어진다.
  - `GAUGE_CELLS`, `_progress_gauge()`가 없다.

#### 쿠팡 메시지 차이

`origin/imap`은 각 피크 구간 텍스트 바로 다음 줄에 게이지를 붙인다.

- `origin/imap:src/rider_crawl/message.py:106-118`
  - `아침` 뒤 `_period_gauge(dashboard.morning)`
  - `점심 피크` 뒤 `_period_gauge(dashboard.lunch_peak)`
  - `점심 논피크` 뒤 `_period_gauge(dashboard.lunch_non_peak)`
  - `저녁 피크` 뒤 `_period_gauge(dashboard.dinner_peak)`
  - `저녁 논피크` 뒤 `_period_gauge(dashboard.dinner_non_peak)`

현재 로컬은 구간 텍스트만 출력한다.

- 로컬 [src/rider_crawl/message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/message.py:84)
  - `아침`, `점심 피크`, `점심 논피크`, `저녁 피크`, `저녁 논피크` 줄만 있다.
  - 게이지 줄이 없다.

`origin/imap` 테스트 기대값:

- `origin/imap:tests/test_coupang_message.py:32-50`
  - `██████████`
  - `█████░░░░░`
  - `████░░░░░░`
  - `█░░░░░░░░░`
  - `거절률: 7.5%`
  - `수행중인원: 3명`

현재 로컬 테스트 기대값:

- 로컬 [tests/test_coupang_message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_message.py:32)
  - 게이지 줄이 없다.
  - `거절률: 6.5%` raw 값을 기대한다.

#### 쿠팡 거절률 보정

증거 등급: Confirmed

`origin/imap`은 쿠팡 거절률을 `+1%p` 보정하고 100%로 cap 한다.

- `origin/imap:src/rider_crawl/message.py:176-180`
  - `_format_adjusted_reject_rate(value)`
  - `Decimal(str(value)) + Decimal("1")`
  - `min(Decimal("100"), ...)`

현재 로컬은 raw 값을 그대로 출력한다.

- 로컬 [src/rider_crawl/message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/message.py:91)
  - `f"거절률: {_format_count(dashboard.reject_rate)}%"`

복원 대상 테스트:

- `origin/imap:tests/test_coupang_message.py:96-105`
  - 99.4% 입력이 100%로 cap 되는 테스트가 있다.
- `origin/imap:tests/test_coupang_message.py:162-182`
  - `done=0`, `total=0`인 완료 구간도 게이지가 `██████████`로 채워져야 한다.

#### 배민 메시지 게이지 차이

`origin/imap`은 배민 달성현황에도 게이지를 붙인다.

- `origin/imap:src/rider_crawl/message.py:190-208`
  - `_append_baemin_period(...)`
  - `_baemin_period_gauge(done, goal, rate)`
  - 목표가 있으면 `done / goal`
  - 목표가 없고 달성률이 있으면 `rate / 100`
  - 목표와 달성률이 모두 없으면 게이지를 생략

현재 로컬은 `_format_baemin_period()` 결과만 한 줄로 출력한다.

- 로컬 [src/rider_crawl/message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/message.py:58)
  - `오전오후피크 : ...`
  - `오후논피크 : ...`
  - `저녁피크 : ...`
  - `저녁논피크 : ...`
  - 각 구간 다음 게이지 줄 없음

복원 대상 테스트:

- `origin/imap:tests/test_message.py:172-186`
  - 각 배민 구간 뒤 `██████████`가 붙는 형식을 기대한다.

이식 지시:

1. `origin/imap:src/rider_crawl/message.py`에서 다음을 가져온다.
   - `from decimal import Decimal`
   - `GAUGE_CELLS`
   - `_progress_gauge`
   - `_period_gauge`
   - `_format_adjusted_reject_rate`
   - `_append_baemin_period`
   - `_baemin_period_gauge`
   - `_cancel_rate_lines`
   - `_format_plain_rate`
2. 로컬 `_render_performance_message()`의 period 출력 배열에 `_period_gauge(...)` 줄을 복원한다.
3. 로컬 `_render_baemin_current_screen_message()`는 현재의 `lines.extend([...])` 방식 대신 `origin/imap`의 `_append_baemin_period(...)` 호출 방식으로 복원한다.
4. 로컬 `_rate_line("거절율", snapshot.reject_rate)`는 제거하고, `cancel_rate` 기반 `_cancel_rate_lines(snapshot.cancel_rate)`로 바꾼다.

주의:

- 배민 일반 실시간 스냅샷에서 `cancel_rate`가 없으면 취소율 줄은 없어야 한다.
- 쿠팡 `수행중인원` 줄은 현재 로컬처럼 `current_screen is not None`일 때만 유지한다.
- `수행중인인원` 오타는 복원하면 안 된다.

### 3.2 배민 취소율 보강 크롤링

증거 등급: Confirmed

`origin/imap`의 배민 `crawl_current_screen()`은 달성현황을 읽은 뒤 배달현황 페이지를 별도로 읽어 취소율을 병합한다.

- `origin/imap:src/rider_crawl/crawler.py:26-51`
  - `crawl_current_screen(..., fetch_cancel_summary=...)`
  - 달성현황이면 `parse_achievement_report_text(...)`
  - 그 뒤 `fetch_cancel_summary` 또는 `crawl_baemin_cancel_summary(config)` 호출
  - 값이 있으면 `_merge_cancel_rate(snapshot, cancel)`

현재 로컬은 달성현황을 바로 반환한다.

- 로컬 [src/rider_crawl/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/crawler.py:16)
  - `fetch_cancel_summary` 인자가 없다.
  - 달성현황이면 `parse_achievement_report_text(...)`를 바로 `return`한다.

`origin/imap` 병합 함수:

- `origin/imap:src/rider_crawl/crawler.py:57-63`
  - `_merge_cancel_rate(snapshot, cancel)`
  - `replace(snapshot, cancel_rate=cancel.reject_rate)`
  - 이유: 배달현황 스냅샷의 `reject_rate`가 `(거절+취소)/전체` 합산율이기 때문이다.

`origin/imap` 배달현황 수집 함수:

- `origin/imap:src/rider_crawl/crawler.py:66-77`
  - `crawl_baemin_cancel_summary(config)`
  - `fetch_baemin_delivery_history_html(config)`
  - `parse_baemin_delivery_history_html(html)`
  - `baemin_delivery_history_to_snapshot(table)`
  - 예외는 모두 삼켜 `None` 반환

`origin/imap` 배달현황 HTML fetch:

- `origin/imap:src/rider_crawl/crawler.py:80-153`
  - `fetch_baemin_delivery_history_html`
  - `_fetch_baemin_history_via_cdp`
  - `_fetch_baemin_history_via_cdp_async`
  - `_fetch_baemin_history_via_persistent_context`
  - `_collect_baemin_history_html`
  - `DEFAULT_BAEMIN_DELIVERY_HISTORY_URL`로 이동
  - CDP에서는 새 탭을 열고 닫아 사용자가 보는 달성현황 탭을 건드리지 않는다.

현재 로컬 import도 부족하다.

- 로컬 [src/rider_crawl/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/crawler.py:4)
  - `dataclasses.replace`가 없다.
- 로컬 [src/rider_crawl/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/crawler.py:11)
  - `DEFAULT_BAEMIN_DELIVERY_HISTORY_URL` import가 없다.
- 로컬 [src/rider_crawl/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/crawler.py:13)
  - `baemin_delivery_history_to_snapshot`, `parse_baemin_delivery_history_html` import가 없다.

복원 대상 테스트:

- `origin/imap:tests/test_crawler.py:121-155`
  - `fetch_cancel_summary`가 배달현황 스냅샷을 주면 `snapshot.cancel_rate == 4.7`
- `origin/imap:tests/test_crawler.py:158-187`
  - `fetch_cancel_summary`가 `None`이면 달성현황은 유지되고 `cancel_rate is None`
- `origin/imap:tests/test_message.py:73-105`
  - `cancel_rate=5.0`이면 `취소율 : 5%`
  - `거절율` 줄은 없어야 한다.
- `origin/imap:tests/test_message.py:108-136`
  - `cancel_rate=12.5`는 `12.5%` 그대로 출력
  - 배민 취소율에는 `+1%p` 보정이 적용되면 안 된다.
- `origin/imap:tests/test_message.py:190-220`
  - `reject_rate`가 있어도 별도 `거절율` 줄은 출력하지 않고 `취소율 : 8.3%`만 출력

이식 지시:

1. `src/rider_crawl/crawler.py`에 `origin/imap`의 배민 취소율 보강 함수 세트를 가져온다.
2. `crawl_current_screen()`에 `fetch_cancel_summary` 선택 인자를 추가한다.
3. 실제 실행 경로에서는 `fetch_html is None`일 때만 `crawl_baemin_cancel_summary(config)`를 호출한다.
4. 테스트 주입 경로에서는 `fetch_cancel_summary`가 주어졌을 때만 호출한다.
5. 실패해도 달성현황 전송은 막지 않는다. 이 정책은 중요하다.

주의:

- 이 기능은 메시지 렌더링 변경과 같이 들어가야 의미가 있다. `cancel_rate`만 채우고 메시지가 계속 `거절율`을 출력하면 사용자에게 보이는 기능은 복구되지 않는다.

### 3.3 배민 배달현황 2단 헤더 정렬

증거 등급: Confirmed

`origin/imap`의 `_HtmlTableParser`는 `colspan`/`rowspan`을 보존한다.

- `origin/imap:src/rider_crawl/parser.py:133-185`
  - 각 셀을 `(text, colspan, rowspan)`으로 저장
  - `handle_starttag()`에서 `colspan`, `rowspan` 값을 읽음
  - 테이블 종료 시 `_expand_table_grid(...)` 호출

`origin/imap`의 span 처리 함수:

- `origin/imap:src/rider_crawl/parser.py:187-220`
  - `_span_value(raw)`
  - `_expand_table_grid(raw_rows)`
  - `rowspan`으로 아래 행까지 이어지는 셀을 active map으로 채움
  - `colspan`은 같은 텍스트를 여러 leaf 열로 확장

현재 로컬은 span 정보를 버린다.

- 로컬 [src/rider_crawl/parser.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/parser.py:134)
  - `_current_table: list[list[str]]`
  - `_current_row: list[str]`
  - `handle_starttag()`의 `_attrs`를 사용하지 않음
  - 테이블 종료 시 `self.tables.append(self._current_table)`

`origin/imap`은 서브헤더 행을 라이더로 오인하지 않도록 방어한다.

- `origin/imap:src/rider_crawl/parser.py:612-625`
  - `mapped = _map_row_by_headers(headers, data_row)`
  - `name in {"이름", "운행상태", "수행상태"}`이면 continue

현재 로컬은 비정상 행 로깅이 추가되어 있지만, 서브헤더 skip 조건은 없다.

- 로컬 [src/rider_crawl/parser.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/parser.py:527)
  - `skipped_malformed_rows`
  - 이름이 비어 있는 행만 malformed로 집계
  - `name in {"이름", "운행상태", "수행상태"}` skip 없음

복원 대상 테스트:

- `origin/imap:tests/test_baemin_parser.py:160-189`
  - 실제 배민 2단 헤더 HTML fixture
  - `완료 colspan=4`
  - `이름`, `배차취소`, `배달취소(라이더귀책)`는 `rowspan=2`
- `origin/imap:tests/test_baemin_parser.py:192-206`
  - `완료`는 합계 하위 열을 취해야 한다.
  - `배달취소(라이더귀책)`에 `완료` 값이 섞이면 안 된다.
  - 서브헤더는 라이더로 잡히면 안 된다.
- `origin/imap:tests/test_baemin_parser.py:209-218`
  - `completed_count == 49`
  - `cancelled_count == 1`
  - `cancel_rate == 2.0`
  - `reject_rate == 3.9`

이식 지시:

1. `origin/imap:src/rider_crawl/parser.py:133-220`의 span-aware `_HtmlTableParser`, `_span_value`, `_expand_table_grid`를 가져온다.
2. 현재 로컬의 `logger.debug("skipped %s malformed Baemin delivery rows", ...)`는 유지한다.
3. `_parse_baemin_table()`에는 두 조건을 모두 반영한다.
   - 이름이 비어 있고 데이터가 있으면 malformed count 증가
   - `name in {"이름", "운행상태", "수행상태"}`이면 skip
4. `tests/test_baemin_parser.py`에 `origin/imap`의 colspan/rowspan fixture와 두 테스트를 복원한다.

주의:

- 이 기능은 `배민 취소율 보강`의 안전장치다. 배달현황 페이지를 별도로 읽더라도 2단 헤더를 잘못 파싱하면 취소율이 틀릴 수 있다.

## 4. 이미 로컬에 들어온 기능

### 4.1 쿠팡 수행중인원 메시지

증거 등급: Confirmed

`수행중인원` 메시지 줄은 로컬에도 있다.

- `origin/imap:src/rider_crawl/message.py:121-123`
  - `if snapshot.current_screen is not None`
  - `수행중인원: {snapshot.current_screen.active_riders}명`
- 로컬 [src/rider_crawl/message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/message.py:94)
  - 같은 정책으로 `current_screen`이 있을 때만 출력

현재 로컬 테스트도 이를 확인한다.

- 로컬 [tests/test_coupang_message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_message.py:45)
  - `수행중인원: 3명`
- 로컬 [tests/test_coupang_message.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_message.py:85)
  - `current_screen=None`이면 `수행중인원` 없음
  - `수행중인인원` 오타도 없음

판정: 누락 아님.

### 4.2 쿠팡 rider-performance 보조 조회와 stale 재시도

증거 등급: Confirmed

`origin/imap`:

- `origin/imap:src/rider_crawl/platforms/coupang/crawler.py:40-65`
  - `rider-performance`를 먼저 읽어 `current_screen`을 채운다.
  - 파싱 실패 시 `force_new_tab=True`로 한 번 재시도한다.

현재 로컬:

- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:46)
  - 같은 보조 조회 구조가 있다.
- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:53)
  - `MissingPerformanceDataError`면 `force_new_tab=True`로 재시도한다.

현재 로컬 테스트:

- 로컬 [tests/test_coupang_crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_crawler.py:177)
  - stale 화면이면 fresh tab으로 재시도
  - `active_riders == 18`

판정: 기능은 로컬에 있음.

### 4.3 쿠팡 peak/rider 한쪽 탭 부재 시 임시 탭 열기

증거 등급: Confirmed

`origin/imap`:

- `origin/imap:src/rider_crawl/platforms/coupang/crawler.py:495-548`
  - 대상 탭이 없으면 `_open_target_in_new_tab(...)` 시도
- `origin/imap:src/rider_crawl/platforms/coupang/crawler.py:610-667`
  - 로그인된 쿠팡 컨텍스트에 임시 탭을 열고 읽은 뒤 닫음

현재 로컬:

- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:491)
  - 대상 탭이 없으면 `_open_target_in_new_tab(...)` 시도
- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:599)
  - `rider-performance` 또는 `peak-dashboard` 대상이면 임시 탭을 열 수 있음

현재 로컬 테스트:

- 로컬 [tests/test_coupang_crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_crawler.py:667)
  - peak만 열려 있고 rider가 없으면 rider 임시 탭 열기
- 로컬 [tests/test_coupang_crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_crawler.py:688)
  - rider만 열려 있고 peak가 없으면 peak 임시 탭 열기

판정: 기능은 로컬에 있음.

### 4.4 배민 오늘 수행건수와 주간 목표건수 결합

증거 등급: Confirmed

이 기능도 로컬에 이미 있다.

- `origin/imap:src/rider_crawl/parser.py:269-286`
  - 오늘 배달현황의 수행건수/달성률과 주간 배달현황의 목표건수를 결합
- 로컬 [src/rider_crawl/parser.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/parser.py:207)
  - `_combine_today_and_weekly(...)`를 사용해 같은 결합을 수행
- 로컬 [src/rider_crawl/parser.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/parser.py:466)
  - `has_today_delivery_status(...)` 존재

판정: 누락 아님. 단, 위 3.2의 별도 배달현황 취소율 보강은 빠져 있다.

## 5. 로컬이 의도적으로 더 엄격한 부분

### 5.1 쿠팡 peak-dashboard 센터 헤딩 누락 처리

증거 등급: Confirmed

`origin/imap`은 피크 HTML에서 센터 헤딩이 없으면 검증을 건너뛴다.

- `origin/imap:src/rider_crawl/platforms/coupang/crawler.py:160-162`
  - `if not heading_centers: return`

현재 로컬은 기대 센터명이 있는데 피크 헤딩이 없으면 실패한다.

- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:153)
  - `if not heading_centers: raise CoupangCenterValidationError(...)`

현재 로컬 테스트가 이 정책을 고정한다.

- 로컬 [tests/test_coupang_crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_crawler.py:249)
  - 기대 센터가 있으면 센터 헤딩 누락 시 fail-closed

판정: `origin/imap`을 그대로 덮으면 안전 정책이 약해진다. 이 부분은 누락 기능이 아니라 정책 차이다.

### 5.2 쿠팡 rider-performance RuntimeError 처리

증거 등급: Confirmed

`origin/imap`은 optional `rider-performance` 조회에서 `RuntimeError`도 삼킨다.

- `origin/imap:src/rider_crawl/platforms/coupang/crawler.py:64`
  - `except (BrowserActionRequiredError, MissingPerformanceDataError, RuntimeError):`
  - 결과: `current_screen = None`
  - 즉, 여러 보조 페이지 실패는 `수행중인원`만 생략하고 peak는 계속 보낸다.

현재 로컬은 `RuntimeError`를 삼키지 않는다.

- 로컬 [src/rider_crawl/platforms/coupang/crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/platforms/coupang/crawler.py:57)
  - `except (BrowserActionRequiredError, MissingPerformanceDataError):`

현재 로컬 테스트가 이 정책을 고정한다.

- 로컬 [tests/test_coupang_crawler.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/tests/test_coupang_crawler.py:282)
  - rider-performance 센터 불일치를 삼키지 않고 `RuntimeError`로 실패시킴

판정: 운영 성공률만 보면 `origin/imap` 방식이 관대하다. 하지만 현재 로컬은 다른 계정/오래된 탭 전송을 막는 안전성을 더 우선한다. 구현 시 이 부분은 그대로 가져오지 말고 정책 결정을 먼저 해야 한다.

## 6. 권장 이식 순서

### Step 1. 메시지 게이지와 표시 정책 복원

대상:

- `src/rider_crawl/message.py`
- `tests/test_message.py`
- `tests/test_coupang_message.py`

가져올 것:

- `origin/imap:src/rider_crawl/message.py:27-35`
- `origin/imap:src/rider_crawl/message.py:106-118`
- `origin/imap:src/rider_crawl/message.py:150-180`
- `origin/imap:src/rider_crawl/message.py:190-220`
- `origin/imap:tests/test_message.py:73-136`
- `origin/imap:tests/test_message.py:172-220`
- `origin/imap:tests/test_coupang_message.py:32-50`
- `origin/imap:tests/test_coupang_message.py:96-105`
- `origin/imap:tests/test_coupang_message.py:162-182`

검증:

```powershell
uv run pytest tests/test_message.py tests/test_coupang_message.py
```

### Step 2. 배민 취소율 보강 크롤링 복원

대상:

- `src/rider_crawl/crawler.py`
- `tests/test_crawler.py`

가져올 것:

- `origin/imap:src/rider_crawl/crawler.py:11-23`의 관련 import
- `origin/imap:src/rider_crawl/crawler.py:26-63`
- `origin/imap:src/rider_crawl/crawler.py:66-153`
- `origin/imap:tests/test_crawler.py:14-34`
- `origin/imap:tests/test_crawler.py:121-187`

검증:

```powershell
uv run pytest tests/test_crawler.py tests/test_message.py
```

### Step 3. 배민 배달현황 span-aware 파서 복원

대상:

- `src/rider_crawl/parser.py`
- `tests/test_baemin_parser.py`

가져올 것:

- `origin/imap:src/rider_crawl/parser.py:133-220`
- `origin/imap:src/rider_crawl/parser.py:612-625`
- `origin/imap:tests/test_baemin_parser.py:160-218`

로컬 유지할 것:

- 로컬 [src/rider_crawl/parser.py](C:/Users/KimYS/Desktop/개발외주/rider_result_mornitoring/src/rider_crawl/parser.py:527)의 malformed row logging

검증:

```powershell
uv run pytest tests/test_baemin_parser.py tests/test_crawler.py tests/test_message.py
```

### Step 4. 쿠팡은 누락이 아니라 정책 차이만 검토

대상:

- `src/rider_crawl/platforms/coupang/crawler.py`
- `tests/test_coupang_crawler.py`

권장:

- 게이지와 메시지 복원은 해야 한다.
- `rider-performance`, `peak-dashboard` 임시 탭 보강은 이미 있으므로 중복 이식하지 않는다.
- `RuntimeError`를 삼킬지 여부는 별도 정책 결정 후 수정한다.
- `peak-dashboard` 센터 헤딩 누락 fail-closed는 현재 로컬의 안전 강화이므로 유지 권장.

검증:

```powershell
uv run pytest tests/test_coupang_crawler.py tests/test_coupang_parser.py tests/test_coupang_message.py
```

## 7. 구현 완료 판단 기준

다음 조건을 모두 만족하면 `origin/imap`의 누락 기능이 로컬에 복구됐다고 판단한다.

1. 쿠팡 메시지
   - 각 피크 구간 뒤에 10칸 게이지가 나온다.
   - `reject_rate=6.5`가 메시지에서 `거절률: 7.5%`로 표시된다.
   - `reject_rate=99.4`는 `100%`로 cap 된다.
   - `current_screen is None`이면 `수행중인원` 줄이 없다.
   - `current_screen`이 있으면 `수행중인원: N명`이 나온다.

2. 배민 메시지
   - 목표/달성률이 있는 구간마다 10칸 게이지가 나온다.
   - `cancel_rate`가 있으면 `취소율 : X%`가 나온다.
   - `거절율 : X%` 줄은 더 이상 나오지 않는다.
   - 배민 취소율에는 `+1%p` 보정을 하지 않는다.

3. 배민 크롤링
   - 달성현황 페이지 수집 후 배달현황(history)을 best-effort로 별도 수집한다.
   - 배달현황 수집 실패는 달성현황 전송을 막지 않는다.
   - 배달현황 스냅샷의 `reject_rate`가 달성현황 스냅샷의 `cancel_rate`로 병합된다.

4. 배민 파서
   - 실제 2단 헤더에서 `완료`, `거절`, `배차취소`, `배달취소(라이더귀책)` 열이 밀리지 않는다.
   - 서브헤더 행은 라이더로 잡히지 않는다.
   - `cancel_rate` 계산이 colspan/rowspan fixture에서 맞는다.

5. 쿠팡 안전 정책
   - 현재 로컬의 센터 헤딩 fail-closed 테스트가 계속 통과한다.
   - 다른 센터의 rider-performance를 조용히 삼키지 않는 현재 테스트가 계속 통과한다. 단, 정책을 `origin/imap`처럼 availability 우선으로 바꾸기로 명시 결정한 경우에는 테스트 기대값을 함께 바꾼다.

## 8. 빠른 체크 명령

문서 기준으로 구현 후 최소 확인:

```powershell
uv run pytest tests/test_message.py tests/test_coupang_message.py tests/test_crawler.py tests/test_baemin_parser.py tests/test_coupang_crawler.py tests/test_coupang_parser.py
```

더 넓은 회귀 확인:

```powershell
uv run pytest tests/test_app.py tests/test_parser.py tests/test_baemin_parser.py tests/test_crawler.py tests/test_message.py tests/test_coupang_crawler.py tests/test_coupang_message.py tests/test_coupang_parser.py
```

## 9. 남은 의사결정

1. 쿠팡 보조 `rider-performance` 오류를 어디까지 best-effort로 볼 것인가?
   - `origin/imap`: `RuntimeError`까지 삼켜 `수행중인원`만 생략
   - 현재 로컬: 센터 불일치 등은 실패
   - 권장: 현재 로컬 정책 유지. 다른 계정 실적 전송 위험을 줄이는 쪽이 더 안전하다.

2. 쿠팡 피크 HTML에서 센터 헤딩이 없을 때 통과시킬 것인가?
   - `origin/imap`: 통과
   - 현재 로컬: 기대 센터명이 있으면 실패
   - 권장: 현재 로컬 정책 유지. 운영 성공률보다 오전송 방지가 중요하다.

3. 배민 취소율 표시명은 `취소율`로 고정할 것인가?
   - `origin/imap`: `취소율 : X%`
   - 현재 로컬: `거절율 : X%`
   - 권장: `origin/imap` 방식으로 복원. 이 값은 거절만이 아니라 거절+취소 합산율이므로 `취소율` 또는 별도 명칭이 맞다.

## 10. 최종 판정

확실히 가져와야 하는 `origin/imap` 기능:

- 메시지 10칸 게이지
- 쿠팡 거절률 +1%p 보정 및 100% cap
- 배민 `cancel_rate` 기반 `취소율 : X%` 메시지
- 배민 달성현황 후 배달현황(history) best-effort 수집
- 배민 배달현황 2단 헤더 `rowspan`/`colspan` 정렬 처리

가져오지 말고 유지해야 할 로컬 기능:

- 쿠팡 peak-dashboard 센터 헤딩 fail-closed
- 쿠팡 rider-performance 센터 불일치 fail-fast
- 쿠팡 IMAP/2FA 관련 최신 로컬 보강
- 쿠팡 수행중인원 오타 방지(`수행중인인원` 금지)

사용자가 말한 `수행중인원`은 현재 로컬에도 있으므로 "누락"으로 분류하지 않는다. 다만 메시지 게이지와 배민 취소율/표 파싱은 명확히 빠져 있고, 이 부분은 `origin/imap`의 코드와 테스트를 거의 그대로 가져오는 것이 가장 빠른 성공 경로다.
