"""rider_crawl 재사용 seam — 단일 chokepoint (Story 4.1).

후속 워커(crawl_worker 4.5, kakao_sender 4.6, auth 4.8·4.9)가 ``rider_crawl`` 도메인을
모듈마다 흩어 import 하지 않고 **이 한 곳에서** 가져오도록 의도된 문서화된 경계다.
여기 노출되는 모든 심볼은 ``rider_crawl`` 의 검증된 빌딩블록을 **재구현 없이 그대로
re-export** 한다(동일 객체 identity — 테스트가 ``is`` 로 잠근다). 시그니처 변경·래핑·
재구현은 각 후속 스토리 소유이며 이 seam 에서는 하지 않는다.

이 모듈은 **순수 동기**다: ``async def``/``await`` 가 없고 직접 ``import asyncio`` 하지
않는다(re-export 와 docstring 뿐). re-export 는 **import 만** 하고 함수를 실행
(crawl/send/fetch)하지 않으므로, ``rider_crawl`` 이 갖춘 lazy 경계
(pyautogui/pywinauto/crawl4ai/google 등은 함수 내부에서 import)를 깨지 않는다 — 따라서
이 seam 을 eager import 해도 import-safe 하다.

주의: ``rider_crawl.crawler`` 는 동기 표면을 제공하되 내부에서 ``asyncio.run(...)`` 으로
crawl4ai async 를 감싼다(crawler.py). 그 transitive asyncio 는 ``rider_crawl`` 의 내부
관심사이며 본 seam 의 sync 규약 위반이 아니다 — 본 모듈 자기 코드만 sync 면 된다.
"""

from __future__ import annotations

# 수집 — registry 진입(crawl_snapshot) + 배민 legacy(crawler/parser) + 쿠팡(coupang).
from rider_crawl import crawler, parser
from rider_crawl.platforms import coupang, crawl_snapshot

# 렌더 — 현재 화면/실적 스냅샷 → 메시지 문자열.
from rider_crawl.message import render_current_screen_message

# Gmail 2FA — 쿠팡 이메일 인증번호 조회 + 세션 복구(4.9 가 이 seam 으로 import).
from rider_crawl.auth.coupang_email_2fa import recover_coupang_session_with_email_2fa
from rider_crawl.auth.gmail import fetch_latest_verification_code

# Kakao sender — 직접 전송 함수 + 예외, 또는 messenger 추상화(4.6 worker 가 래핑).
from rider_crawl.messengers import KakaoMessenger, dispatch_text_message
from rider_crawl.sender import (
    KakaoSendError,
    KakaoUnsafeSelectionError,
    send_kakao_text,
)

__all__ = [
    # 수집
    "crawl_snapshot",
    "crawler",
    "parser",
    "coupang",
    # 렌더
    "render_current_screen_message",
    # Gmail 2FA
    "fetch_latest_verification_code",
    "recover_coupang_session_with_email_2fa",
    # Kakao sender
    "send_kakao_text",
    "KakaoSendError",
    "KakaoUnsafeSelectionError",
    "KakaoMessenger",
    "dispatch_text_message",
]
