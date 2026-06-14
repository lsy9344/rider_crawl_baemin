"""rider_server metrics 패키지 — Story 5.9 (7개 모니터링 지표·최소 알림).

5.4 ``scheduler/``·5.6 ``admin/`` 구조를 **동형**으로 계승한다(정책 분리: 순수 policy /
async service+port / PostgreSQL impl). 본 패키지는 5.1~5.8 이 이미 만든 **흩어진 집계를 비식별
fleet 지표로 조립·노출**하고 **순수 알림 판정**을 더하는 얇은 레이어다 — 새 계측을 발명하지
않는다. 핵심 불변식: (1) ``/metrics/operational`` payload 에 tenant_id·고객명·센터/상점명·
target 식별 텍스트 **금지**(집계 수치만), (2) 임계는 기존 정본(``severity``/``scheduler``)
**재사용**(drift 0), (3) 신규 DB 컬럼/테이블/enum 멤버 **0**(14표·count-lock 불변), (4) 읽기
전용(write/상태전이 없음), (5) ``rider_agent`` import 0(단방향 import).
"""

from __future__ import annotations
