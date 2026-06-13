"""rider_agent auth 서브패키지 — 플랫폼 인증 상태 감지 + 사람 개입형 재인증 (Story 4.8+).

architecture.md(456) 트리가 전제한 ``auth/`` 서브패키지의 첫 실현이다(``# 배민 auth open,
Gmail mailbox lock``). 4.5 가 forward-commit 한 "``workers/``·``auth/``·``autostart.py`` 는 각
후속 스토리가 만든다"의 한 조각으로, 4.8 의 :mod:`rider_agent.auth.baemin_auth`
(배민 auth 상태 분류기 + ``AUTH_CHECK``/``OPEN_AUTH_BROWSER`` 실행자 + bounded 재인증 대기 +
``build_auth_execute_job`` 라우터)를 담는다. 4.9 가 같은 패키지에 쿠팡 Gmail mailbox lock 을
추가한다.

**가벼운 패키지** — 무거운/플랫폼 의존을 끌지 않도록 여기서 하위 모듈을 eager import 하지
않는다(``import rider_agent`` 가 가벼워야 한다는 4.1 import-safety 규율 계승, ``workers/``
선례). 호출자(``job_loop`` execute_job 합성·테스트)가
``from rider_agent.auth.baemin_auth import …`` 로 직접 가져온다.
"""

from __future__ import annotations
