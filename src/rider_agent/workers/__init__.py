"""rider_agent 워커 서브패키지 — type 별 ``execute_job`` 워커 (Story 4.6+).

architecture.md(453-455) 트리가 전제한 ``workers/`` 서브패키지의 첫 실현이다. 4.5 가
forward-commit 한 "``workers/``·``auth/``·``autostart.py`` 는 각 후속 스토리(4.6~4.9)가
만든다"의 첫 모듈로, 4.6 의 :mod:`rider_agent.workers.kakao_sender` (KakaoSenderWorker)를
담는다.

**가벼운 패키지** — 무거운/플랫폼 의존을 끌지 않도록 여기서 하위 모듈을 eager import 하지
않는다(``import rider_agent`` 가 가벼워야 한다는 4.1 import-safety 규율 계승). 워커가 필요한
호출자(``job_loop`` startup 배선·테스트)가 ``from rider_agent.workers.kakao_sender import …``
로 직접 가져온다.
"""

from __future__ import annotations
