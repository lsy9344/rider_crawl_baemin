# Coupang Rider Lookup — Feasibility Evaluation (Phase 7)

Date: 2026-07-02
Status: Evaluation plus implementation follow-up. The original static-code review
found Coupang rider lookup unknown; a later branch adds parser/worker support
behind focused tests without changing protected Coupang crawler or 2FA files.

## Question

Does the Coupang (Coupang Eats) crawl path expose **stable rider-level
cancellation data** — analogous to Baemin's delivery-history table, whose
per-rider rows carry `이름` (name), `휴대폰번호` (phone), `완료` (completed),
`거절` (rejected), `배차취소` (dispatch cancel), `배달취소(라이더귀책)`
(rider-fault cancel)? Only then is a Coupang `!!name####` lookup feasible.

## Original Verdict: (C) UNKNOWN — needed a live headed crawl. Static evidence leaned "aggregate-only".

The committed Coupang code proves the rider-record page carries a **per-rider
name/contact table**, but shows cancellation **only as a center-level aggregate**.
No per-rider cancellation breakdown is evidenced in code, fixtures, or tests. A
final decision needs one headed capture of the real page HTML.

## Implementation Follow-up

The follow-up implementation lifts the Baemin-only lookup gate for Coupang targets
only after adding a non-protected parser path for the rider-performance table:

- `parse_coupang_rider_performance_rows(html)` reads the rider-performance table
  and maps rows into the shared rider lookup shape.
- Coupang `취소` is mapped to `배차취소`; `배달취소(라이더귀책)` is set to `0` because the
  Coupang table exposes a single cancel count in this contract.
- `RIDER_LOOKUP` now accepts `platform="coupang"` and dispatches through the
  existing protected `fetch_page_html` function without editing the protected
  Coupang crawler or login/email 2FA files.
- Kakao inbound and Telegram command routing now treat Baemin and Coupang as
  supported lookup platforms; other platforms still receive the fixed
  unsupported-platform reply.

This does not change the protected Coupang login/email 2FA selector, timeout,
routing, or recovery contract.

## Evidence (file:line)

1. **Coupang parse output is a center-level aggregate, not per-rider rows.**
   - `src/rider_crawl/platforms/coupang/parser.py:37-104` — `parse_current_screen_html`
     → `CurrentScreenSnapshot`; `parse_peak_dashboard_html` → `PeakDashboardSnapshot`.
   - `src/rider_crawl/models.py` — `CurrentScreenSnapshot` / `PeakDashboardSnapshot`
     hold only aggregate center fields (online_riders, cancelled_count, …). There is
     **no per-rider data model** (contrast Baemin below).

2. **Only two Coupang pages are crawled, both parsed for aggregates.**
   - `src/rider_crawl/config.py:15-16` —
     `DEFAULT_COUPANG_RIDER_PERFORMANCE_URL = ".../page/rider-performance"`,
     `DEFAULT_COUPANG_PEAK_DASHBOARD_URL = ".../page/peak-dashboard"`.
   - `src/rider_crawl/platforms/coupang/crawler.py` (PROTECTED) fetches these two pages.

3. **The rider-record page DOES have a per-rider name/contact table — but the parser
   extracts only aggregate totals from it.**
   - `src/rider_crawl/platforms/coupang/parser.py:107-146`
     (`_parse_record_table_current_screen_text`): gated on the page containing both
     `"라이더 현황"` and `"이름 / 연락처"` (name / contact) — i.e. the "라이더 기록"
     (rider record) page has a name/contact column. Yet it pulls only
     `_required_number_after("온라인" / "거절/무시" / "취소" / "완료" / …)` aggregates
     and returns a center `CurrentScreenSnapshot`. It never iterates per-rider rows.

4. **Test captures show name/contact + status columns, with cancellation as a group
   total — no per-rider cancellation columns.**
   - `tests/test_coupang_parser.py:72-164` — real-looking captured text:
     `… "활성 라이더", "이름 / 연락처", "총 4명", "상태", "온라인 0명", "거절/무시 …",
     "취소 …", "완료 …" …`. The table has **"이름 / 연락처"** and **"상태"** columns and
     a rider count ("총 N명"), but `취소` appears only in the aggregate status block —
     there is **no per-rider 배차취소 / 배달취소(라이더귀책) breakdown** like Baemin.
   - The single fixture `tests/fixtures/coupang_current_screen.html` likewise contains
     no per-rider name/phone/cancellation rows.

5. **Baemin contrast (why Baemin works).**
   - `src/rider_crawl/parser.py` — `BaeminDeliveryHistoryTable.riders: list[dict[str,str]]`
     with columns `이름`, `휴대폰번호`, `완료`, `거절`, `배차취소`, `배달취소(라이더귀책)`;
     `parse_baemin_delivery_history_html` extracts per-rider rows. This is the exact
     per-rider cancellation shape the shared `rider_crawl.rider_lookup` core matches on.
     Coupang has no equivalent layer.

## What is proven vs. unknown

Proven from code:
- Coupang's `/page/rider-performance` ("라이더 기록") page has a per-rider table keyed
  by **이름 / 연락처** (name / contact) and **상태** (status), with a rider count.
- The current parser deliberately reduces that page to center aggregates.
- Cancellation (`취소`) is present as a **center total**, not per-rider in any captured
  sample.

Unknown (blocks go/no-go):
1. Whether each per-rider row exposes an **individual cancellation count** (and,
   ideally, a dispatch-vs-rider-fault split) — or only an online/offline `상태`.
2. Whether the full phone number / a stable last-4 suffix is present per rider
   (the table header says "연락처" but samples don't include the values).
3. Whether the row layout is **stable** across centers/time for reliable parsing.
4. Whether the data is in an HTML table (parseable like Baemin) or only rendered UI.

## Protected-file boundary (must not change)

Per `CLAUDE.md`:
- `src/rider_crawl/platforms/coupang/crawler.py` — PROTECTED (login/2FA/session recovery).
- `src/rider_crawl/auth/coupang_email_2fa.py`, `src/rider_agent/auth/coupang_gmail_2fa.py`,
  `src/rider_agent/worker_composition.py` — PROTECTED.

`src/rider_crawl/platforms/coupang/parser.py` is **not** protected. A future per-rider
extraction can live there (or in a new module) and reuse the shared
`rider_crawl.rider_lookup` matcher/renderer, **without touching the protected crawler
or 2FA path** — provided the crawler already fetches the rider-record page HTML it needs
(it fetches `/page/rider-performance` today).

## Recommended next step (operator — cannot be done in this sandbox)

Per `CLAUDE.md`, selector/DOM claims about Coupang require a real headed browser
verification. To resolve the unknowns:

1. Open `https://partner.coupangeats.com/page/rider-performance` in a headed session
   for a center that currently has active riders.
2. Capture the raw HTML of the "라이더 기록 / 라이더 현황" table (save a fixture, no
   real names/phones committed — redact).
3. Confirm, per rider row, whether these exist: individual **취소** count, a
   dispatch-vs-rider-fault split, and a phone number / stable last-4.
4. Record whether the layout is stable across ≥2 centers and ≥2 time windows.

## Conditional design sketch (kept as historical context)

If confirmed:
- Add a `parse_coupang_rider_performance_html(html) -> list[dict[str,str]]` to the
  **non-protected** `platforms/coupang/parser.py`, emitting rows shaped like Baemin's
  (`이름`, phone, and whatever cancellation columns Coupang exposes).
- Map Coupang columns onto the shared `rider_crawl.rider_lookup` contract (it already
  does NFC name match + last-4 phone match + cancel-rate calc + reply render). If
  Coupang lacks a dispatch/rider-fault split, define a documented single `취소` mapping
  and a Coupang-specific `cancel_rate` definition.
- Lift phase 1's Baemin-only gate for Coupang targets in the lookup worker/service
  (`src/rider_agent/workers/rider_lookup.py`, server mapping) as a separate tested
  change; keep the unsupported-platform reply as the fallback until proven.
- **Do not** modify the protected Coupang crawler/2FA. If the rider-record HTML is not
  already returned by the existing fetch, add a new non-protected fetch helper rather
  than editing `coupang/crawler.py`; if that proves impossible, escalate per the
  protected-contract process in `CLAUDE.md` before any edit.

If step 3 shows Coupang exposes only aggregate cancellation (current lean), then a
Coupang `!!` rider lookup is **not feasible** without a new Coupang data source, and the
scoped "라이더 조회 명령은 배민 탭에서만 지원합니다." reply should remain.
