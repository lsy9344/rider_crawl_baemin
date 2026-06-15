---
name: rider_server 운영 대시보드 (Darkmatter Ops)
description: 비개발 운영자가 업체 ~100곳의 크롤링·전송 건강을 지키는 다크 우선 운영 콘솔. tweakcn darkmatter 토큰을 정본으로 계승하고, 대규모 트리아지·반응형·접근성을 위해 확장한다.
status: final
updated: 2026-06-15
sources:
  - src/rider_server/admin/templates/dashboard.html   # darkmatter 인라인 토큰 정본
  - _bmad-output/project-context.md
colors:
  # ── 라이트(기본 :root) ──────────────────────────────────────────────
  background: 'oklch(1.0000 0 0)'
  foreground: 'oklch(0.2101 0.0318 264.6645)'
  card: 'oklch(1.0000 0 0)'
  card-foreground: 'oklch(0.2101 0.0318 264.6645)'
  popover: 'oklch(1.0000 0 0)'
  primary: 'oklch(0.6716 0.1368 48.5130)'          # 골든-오렌지
  primary-foreground: 'oklch(1.0000 0 0)'
  secondary: 'oklch(0.5360 0.0398 196.0280)'       # 틸
  secondary-foreground: 'oklch(1.0000 0 0)'
  muted: 'oklch(0.9670 0.0029 264.5419)'
  muted-foreground: 'oklch(0.5510 0.0234 264.3637)'
  accent: 'oklch(0.9491 0 0)'
  accent-foreground: 'oklch(0.2101 0.0318 264.6645)'
  border: 'oklch(0.9276 0.0058 264.5313)'
  input: 'oklch(0.9276 0.0058 264.5313)'
  ring: 'oklch(0.6716 0.1368 48.5130)'
  sidebar: 'oklch(0.9670 0.0029 264.5419)'
  # 의미색(상태) — 라이트는 명도 L 을 낮춰 밝은 틴트 위 가독성 확보
  ok: 'oklch(0.5200 0.1300 155)'                   # 정상(그린)
  warn: 'oklch(0.5300 0.1250 70)'                  # 주의(앰버)
  crit: 'oklch(0.5400 0.2200 27)'                  # 위험(레드)
  info: 'oklch(0.5200 0.1700 255)'                 # 정보/배민(블루)
  violet: 'oklch(0.5200 0.2000 295)'              # 보조강조/쿠팡(바이올렛)
  neutral: 'oklch(0.4500 0.0150 265)'             # 중지(뉴트럴)
  # ── 다크(html.dark — darkmatter 기본 모드) ─────────────────────────
  background-dark: 'oklch(0.1797 0.0043 308.1928)'  # 매우 어두운 블루그레이
  foreground-dark: 'oklch(0.8109 0 0)'
  card-dark: 'oklch(0.2150 0.0040 308)'
  card-foreground-dark: 'oklch(0.8109 0 0)'
  popover-dark: 'oklch(0.1822 0 0)'
  primary-dark: 'oklch(0.7214 0.1337 49.9802)'      # 밝은 골든
  primary-foreground-dark: 'oklch(0.1797 0.0043 308.1928)'
  secondary-dark: 'oklch(0.5940 0.0443 196.0233)'   # 시안
  muted-dark: 'oklch(0.2520 0 0)'
  muted-foreground-dark: 'oklch(0.6750 0 0)'
  accent-dark: 'oklch(0.3211 0 0)'
  border-dark: 'oklch(0.2900 0 0)'
  input-dark: 'oklch(0.2520 0 0)'
  ring-dark: 'oklch(0.7214 0.1337 49.9802)'
  ok-dark: 'oklch(0.8000 0.1500 160)'
  warn-dark: 'oklch(0.8300 0.1400 80)'
  crit-dark: 'oklch(0.7100 0.1900 25)'
  info-dark: 'oklch(0.7300 0.1300 250)'
  violet-dark: 'oklch(0.7600 0.1500 300)'
  neutral-dark: 'oklch(0.7000 0.0150 265)'
typography:
  font-sans:
    fontFamily: '"Geist Mono", ui-monospace, "Apple SD Gothic Neo", "Malgun Gothic", "Pretendard", sans-serif'
    note: '한글은 시스템 고딕으로 폴백(스택 후순위). darkmatter 모노 감성 유지.'
  font-mono:
    fontFamily: '"JetBrains Mono", ui-monospace, Menlo, "Apple SD Gothic Neo", monospace'
    note: '수치·id·시각·상대시간 등 데이터 토큰 전용.'
  body:
    fontSize: 14px
    lineHeight: '1.55'
    letterSpacing: 0rem
  brand-title:
    fontSize: 1.02rem
    fontWeight: '600'
  section-h2:
    fontSize: 0.92rem
    fontWeight: '600'
  kpi-value:
    fontFamily: '{typography.font-mono.fontFamily}'
    fontSize: 1.05rem
    fontWeight: '600'
  metric-value:
    fontFamily: '{typography.font-mono.fontFamily}'
    fontSize: 2rem
    fontWeight: '600'
  table-head:
    fontSize: 0.68rem
    fontWeight: '600'
    letterSpacing: 0.05em
    note: 'UPPERCASE. muted-foreground.'
  badge:
    fontSize: 0.7rem
    fontWeight: '600'
  relative-time:
    fontFamily: '{typography.font-mono.fontFamily}'
    fontSize: 0.7rem
rounded:
  sm: 0.40rem      # calc(radius - 0.35rem) — 입력/배지칩/작은 버튼
  DEFAULT: 0.75rem # 카드·드로어·폼·KPI칩
  lg: 1rem         # calc(radius + 0.25rem) — 큰 표면
  full: 999px      # 알약(상태배지·필터칩·pill)
spacing:
  container-max: 1240px
  gutter: 1.25rem      # 좌우 페이지 여백
  card-pad: 1.05rem    # 카드 본문 패딩
  grid-gap: 0.85rem    # KPI/메트릭 그리드 간격
  row-pad-y: 0.7rem    # 대상 행 세로 패딩
  touch-min: 44px      # 모바일 터치 타깃 하한
components:
  mode-tab:
    color-active: '{colors.foreground}'
    color-idle: '{colors.muted-foreground}'
    underline-active: '{colors.primary}'
    note: '상단 [모니터링][관리] 탭. 활성=하단 2px primary 보더 + foreground.'
  kpi-filter-chip:
    background: '{colors.card}'
    border: '{colors.border}'
    radius: '{rounded.sm}'
    pressed-ring: '{colors.ring}'
    dot-ok: '{colors.ok}'
    dot-warn: '{colors.warn}'
    dot-crit: '{colors.crit}'
    dot-stop: '{colors.neutral}'
    note: 'KPI 숫자칩 = 클릭 가능한 필터. aria-pressed 시 ring.'
  filter-chip:
    background-idle: '{colors.card}'
    background-pressed: '{colors.accent}'
    radius: '{rounded.full}'
    note: '플랫폼/인증필요 토글칩.'
  target-row:
    sevbar-crit: '{colors.crit}'
    sevbar-warn: '{colors.warn}'
    sevbar-ok: '{colors.ok}'
    sevbar-stop: '{colors.neutral}'
    hover: 'color-mix(in srgb, {colors.accent} 50%, transparent)'
    note: '심각도 좌측 바 + 이름/센터 + 플랫폼배지 + 심각도배지 + 사유/상대시간 + 상황맞춤 액션.'
  severity-badge:
    ok: '{colors.ok}'
    warn: '{colors.warn}'
    crit: '{colors.crit}'
    stop: '{colors.neutral}'
    radius: '{rounded.full}'
    note: '색 14~16% 틴트 배경 + 점(dot) + 한글 라벨(정상/주의/위험/중지). 색 단독 금지.'
  platform-badge:
    baemin: '{colors.info}'
    coupang: '{colors.violet}'
    radius: '{rounded.full}'
  inline-action-primary:
    background: '{colors.primary}'
    foreground: '{colors.primary-foreground}'
    radius: '{rounded.sm}'
    note: '상황맞춤 1차 액션(인증 확인/활성화/센터명 설정 등).'
  inline-action-danger:
    color: '{colors.crit}'
    border: 'color-mix(in srgb, {colors.crit} 38%, {colors.border})'
    radius: '{rounded.sm}'
    note: '비활성/폐기/중지. 항상 이름 박은 확인 동반.'
  target-drawer:
    background: '{colors.card}'
    border-left: '{colors.border}'
    shadow: '{shadow.lg}'
    width: 'min(440px, 100%)'
    note: '데스크톱 우측 슬라이드. 모바일 전체폭. 탭: 상세 / 편집. 왜→사실→이력→조치.'
  guided-form:
    step-num-bg: 'color-mix(in srgb, {colors.primary} 16%, {colors.card})'
    radius: '{rounded.DEFAULT}'
    note: '새 업체 추가 단일 폼. 모든 연결 id = 드롭다운(직접 입력 0).'
  status-banner:
    bar-ok: '{colors.ok}'
    bar-warn: '{colors.warn}'
    bar-crit: '{colors.crit}'
    note: '좌측 4px 심각도 바 + orb. crit 은 orbpulse.'
shadow:
  xs: '0 1px 2px 0 hsl(0 0% 0% / 0.04)'   # 라이트
  sm: '0 1px 4px 0 hsl(0 0% 0% / 0.05)'
  md: '0 1px 4px 0 hsl(0 0% 0% / 0.05), 0 2px 6px -1px hsl(0 0% 0% / 0.06)'
  lg: '0 1px 4px 0 hsl(0 0% 0% / 0.05), 0 8px 20px -4px hsl(0 0% 0% / 0.10)'
  xs-dark: '0 1px 2px 0 hsl(0 0% 0% / 0.30)'
  sm-dark: '0 1px 3px 0 hsl(0 0% 0% / 0.40)'
  md-dark: '0 2px 8px -1px hsl(0 0% 0% / 0.45)'
  lg-dark: '0 12px 30px -6px hsl(0 0% 0% / 0.55)'
---

# rider_server 운영 대시보드 — Visual Identity (DESIGN.md)

> 시각 정체성의 정본. `EXPERIENCE.md`(동작·IA·상태)와 짝을 이루며, 어떤 목업/와이어프레임과 충돌해도 **이 문서가 이긴다**.
> 모든 토큰은 tweakcn **darkmatter**(`darkmatter.json`, oklch 원값)를 계승한다. 값은 임의로 바꾸지 않는다.

## Brand & Style

이것은 **소비자 앱이 아니라 운영 콘솔**이다. "예쁘게"가 아니라 *"무엇이 막혔는지 0.5초 안에 보이게"* 가 미감의 기준이다. darkmatter 의 성격 — **다크 우선, 모노 타이포, 둥근 모서리(0.75rem), 매우 은은한 그림자(다크 30~55%)와 도트 텍스처** — 이 그 톤을 만든다: 차분한 콘솔 위에서 **상태색(ok/warn/crit)만 발화**한다.

핵심 규율은 **"무채색 표면 + 의미색 신호"**. 카드·표·툴바·폼은 전부 무채(card/border/muted)로 가라앉히고, 색(그린/앰버/레드)은 *오직 상태와 조치*에만 쓴다. 골든-오렌지 `primary` 는 브랜드 + "지금 누를 1차 액션"을 뜻하고, 배민=블루(`info`)·쿠팡=바이올렛(`violet`)으로 플랫폼을 구분한다. 색이 의미를 갖지 않으면 칠하지 않는다.

## Colors

darkmatter 는 **다크가 기본**이고 라이트는 토글 대안이다. 두 모드는 `-dark` 접미 토큰으로 분리 보관한다(`{colors.primary}` / `{colors.primary-dark}`).

- **무채 표면** — `background`/`card`/`muted`/`accent`/`border`. 콘솔의 바닥. 여기엔 채도를 거의 두지 않는다(다크 카드는 `oklch(0.2150 …)`).
- **Primary 골든-오렌지 (`{colors.primary}` / `{colors.primary-dark}`)** — 브랜드 마크, 활성 모드탭 밑줄, 1차 액션 버튼(인증 확인·활성화·업체 추가), 포커스 링. "지금 이걸 누르면 된다"는 단 하나의 길.
- **상태 4색** — `ok`(정상·그린) / `warn`(주의·앰버) / `crit`(위험·레드) / `neutral`(중지). 심각도 바·배지·KPI 점·신선도색에만 쓴다. **상태 외 장식엔 절대 금지.**
- **플랫폼 2색** — `info`(배민·블루) / `violet`(쿠팡·바이올렛). 플랫폼 배지에만.
- **대비 보정** — 의미색은 라이트(L 낮춤)/다크(L 높임)로 **모드별 직접 보정**되어 있다. 이 보정값을 깨지 않는다(AA 가독성 근거).

피할 것: 상태가 아닌 곳의 채색, 그라데이션 남용(브랜드 마크 1곳 제외), 상태 4색 외 새 의미색 추가.

## Typography

**모노 타이포가 정체성**이다. `font-sans`(Geist Mono)가 본문/라벨/버튼을, `font-mono`(JetBrains Mono)가 **데이터 토큰**(수치·id·시각·상대시간·KPI/메트릭 값)을 맡는다. 한글은 시스템 고딕으로 자연 폴백한다.

- `kpi-value`/`metric-value` — 모노, 큰 수치. 한눈 스캔용.
- `relative-time` — 모노 0.7rem. "47분 전" 같은 신선도 텍스트(색과 함께 의미 전달, 색 단독 금지).
- `table-head` — 0.68rem UPPERCASE, muted. 표 헤더는 조용히.
- `badge` — 0.7rem 600. 심각도/상태 라벨.

규칙: 영문 약어·기계 코드를 큰 타이포로 노출하지 않는다. 사용자에게 보이는 건 사람 문장, 코드가 필요하면 괄호 보조.

## Layout & Spacing

- **중앙 1열, 최대폭 `{spacing.container-max}`(1240px)**, 좌우 거터 `{spacing.gutter}`. 운영 콘솔은 와이드 스프레드시트가 아니다 — 트리아지 목록 1열에 집중.
- KPI/메트릭은 `repeat(auto-fit, minmax(...))` 자동 그리드, 간격 `{spacing.grid-gap}`.
- **상단 고정 바**(앱바 + 모드탭)는 `sticky` + `backdrop-blur`. 스크롤해도 모드 전환·LIVE 상태가 따라온다.
- 카드/드로어는 `overflow:hidden` 으로 모서리 정리, 본문 패딩 `{spacing.card-pad}`.

## Elevation & Depth

그림자는 **위계 장치가 아니라 분리 장치**다 — 매우 은은하게(라이트 4~10%, 다크 30~55%). `{shadow.xs}`(KPI/메트릭) < `{shadow.sm}`(카드/워크벤치) < `{shadow.lg}`(드로어, 떠 있음). 추가로 `body::before` 의 미세 도트 텍스처가 다크에서 깊이를 준다(opacity 0.5).

## Shapes

`{rounded.DEFAULT}`(0.75rem)가 기본 — 카드·드로어·폼·KPI칩. 입력·작은 버튼·배지칩은 `{rounded.sm}`(0.40rem)로 조금 더 또렷하게. 상태배지·필터칩·pill·스크롤 썸은 `{rounded.full}`(알약). 둥근 톤이 "콘솔이지만 딱딱하지 않게" 만든다.

## Components

핵심 신규/확장 컴포넌트(행동 명세는 `EXPERIENCE.md.Component Patterns`):

- **mode-tab** — 상단 [모니터링][관리]. 활성 = `{colors.primary}` 하단 보더 + `{colors.foreground}`. 모니터링 탭엔 대상 총수(100) 카운트.
- **kpi-filter-chip** — KPI 숫자칩이자 **필터 버튼**. 점(ok/warn/crit/stop) + 큰 모노 수치 + 라벨. `aria-pressed` 시 `{colors.ring}` 링. "위험 3" 클릭 → 목록이 위험만.
- **target-row** — 앱의 심장. `심각도 바 │ 이름+센터 │ 플랫폼 배지 │ 심각도 배지 │ 사유+상대시간 │ 상황맞춤 액션`. hover 시 accent 틴트. 행 전체가 클릭 타깃(→ 드로어).
- **severity-badge** — 색 14~16% 틴트 배경 + 점 + 한글 라벨. **색만으로 구분 금지**(점+텍스트 동반).
- **platform-badge** — 배민=`{colors.info}`, 쿠팡=`{colors.violet}`, 알약, 모노.
- **inline-action (primary/danger/ghost)** — primary=골든(1차 조치), danger=레드(이름 박은 확인 필수), ghost=투명(상세 보기 등 저강도).
- **target-drawer** — 데스크톱 우측 슬라이드(440px), 모바일 전체폭. 탭 `상세`/`편집`. 본문 순서 = **왜(설명 배너) → 핵심 사실(dl) → 최근 이력(타임라인) → 조치 버튼 → 딥링크**. 모니터링·관리가 같은 드로어 재사용.
- **guided-form** — 새 업체 추가 단일 폼. 번호 스텝(계정→정보→채널). **모든 연결 id = 드롭다운**.
- **status-banner / 미니 상태** — 좌측 4px 심각도 바 + orb(crit 펄스), 미니 상태(Agent·Kakao·Telegram).

## Do's and Don'ts

| Do | Don't |
|---|---|
| darkmatter oklch 토큰을 정본으로 계승 | 토큰 값 임의 변경·새 팔레트 도입 |
| 색은 상태(ok/warn/crit/neutral)·플랫폼·primary 에만 | 장식/크롬에 상태색 사용, 5번째 의미색 추가 |
| 심각도 = 색 + 점 + 한글 라벨 | 색 단독으로 상태 구분 |
| 데이터(수치·시각·id)는 `font-mono` | 기계 코드를 큰 타이포로 노출 |
| 상대시간 텍스트 + 신선도색 동반 | 절대시각 문자열만 표시 |
| 위험 버튼은 레드 + 대상 이름 박은 확인 | 익명 confirm("하시겠습니까?") |
| 1열 트리아지 목록, 최대폭 1240px | 와이드 다열 표·가로 스크롤 의존 |
| secret 은 핸들(`*_ref`)만 | 토큰/OTP/비밀번호 화면 노출 |
