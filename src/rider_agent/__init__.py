"""rider_agent — Windows Local Agent 패키지 (Epic 4).

기존 ``rider_crawl`` 의 검증된 도메인(crawler/parser/renderer/email IMAP 2FA/Kakao sender)을
**단방향 import 로 재사용**하는 신규 Windows Local Agent 런타임이다. 새 프레임워크를
하나도 도입하지 않으며, 자기(own) 코드는 **동기(sync) 런타임**으로 동작한다 — Cloud 의
FastAPI/SQLAlchemy async 경계와 섞지 않는다.

의존성 방향(절대 규칙): ``rider_agent → rider_crawl`` import 만 허용한다. 역방향
(``rider_crawl → rider_agent``)과 ``rider_agent → rider_server`` 는 금지다 — 두 신규
런타임은 HTTP(JSON)로만 통신한다.

이번 스토리(4.1) 범위 — 패키지 토대(foundation)만:
- ``__init__.py``(이 파일, ``__version__`` 보유) +
  ``__main__.py``(``python -m rider_agent`` thin bootstrap) +
  ``reuse.py``(``rider_crawl`` 재사용 seam).

후속 스토리 소유 — 이번 스토리에서 만들지 않는다(빈 stub 도 금지):
- 등록 코드 입력 / agent_id·token DPAPI 보안 저장 → Story 4.2
- heartbeat 보고 → Story 4.3
- outbound HTTPS job polling/claim/complete + lease → Story 4.4
- BrowserProfileManager(프로필/CDP 격리·대상 검증) → Story 4.5
- KakaoSenderWorker(FIFO 직렬 queue) → Story 4.6
- interactive session 실행 조건 / 재부팅 autostart → Story 4.7
- 배민 사람 개입형 재인증 → Story 4.8 / 쿠팡 이메일 IMAP 2FA 메일함 분리·lock → Story 4.9
- 서버 측 job 생성·queue·Admin → Epic 5

``__init__`` 은 의도적으로 가볍게 유지한다 — 재사용 seam(``reuse``)을 eager import 하지
않는다. 그래야 ``import rider_agent`` 자체가 crawl4ai/google 등 무거운 import 를 끌지
않는다(seam 은 ``__main__`` 과 테스트가 필요할 때 import).
"""

__version__ = "0.1.0"
