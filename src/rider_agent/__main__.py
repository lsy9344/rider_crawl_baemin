"""``python -m rider_agent`` 진입점 — 동기(sync) thin bootstrap (Story 4.1).

본 스토리는 패키지 토대만 세운다. 따라서 이 ``main()`` 은 재사용 wiring(``reuse`` seam)이
import 가능함을 확인하고 한 줄 sync 시작 배너를 출력한 뒤 ``0`` 으로 정상 종료하는
**얇은** bootstrap 이다. GUI(tkinter)/브라우저/네트워크/KakaoTalk 같은 **부작용을 일으키지
않는다**.

실제 startup/main_loop(등록 4.2 · heartbeat 4.3 · job claim/lease 루프 4.4 ...)는 후속
스토리가 이 ``main()`` 을 additive 로 확장한다. 레거시 tkinter UI 진입
(``rider_crawl.ui`` / ``rider_crawl.app.run_once``)은 ``python -m rider_crawl`` 소유이며
Agent 는 여기서 import·호출하지 않는다(별도 sync 진입).
"""

from rider_agent import __version__, reuse


def main() -> int:
    # reuse seam 은 이 모듈 상단 import 로 이미 로드·검증된다(import 실패 시 모듈 로드
    # 자체가 ImportError 로 중단). 배너에 로드된 재사용 seam 개수를 실어 그 import 를
    # 실제로 사용한다 — assert(`python -O` 에서 제거됨) 대신. 함수는 실행하지 않는다.
    print(
        f"rider_agent {__version__} (sync runtime; reuses rider_crawl "
        f"[{len(reuse.__all__)} seams], no new framework)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
