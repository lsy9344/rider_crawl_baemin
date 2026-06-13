"""rider_server — control plane 패키지.

본 Story 2.5는 이 패키지의 ``domain/`` 서브패키지(순수 도메인 dataclass + 상태 enum)만
추가하는 additive 작업이다. FastAPI/SQLAlchemy 의존이 0인 순수 정의라 서버 스캐폴딩
(``main.py``/``settings.py``/``db/``, Epic 5 Story 5.1)보다 먼저 두어도 부작용이 없다.
"""
