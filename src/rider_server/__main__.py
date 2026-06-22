"""``python -m rider_server`` — 개발 실행 진입점(uvicorn 기동).

운영 정본은 Docker 의 uvicorn 커맨드(``deploy/Dockerfile.server``)다. 이 모듈은 로컬
개발 편의용 thin wrapper 이며, app 객체는 import string(``rider_server.main:app``)으로
넘겨 uvicorn 의 reload 가 동작하게 한다.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "rider_server.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("APP_ENV", "development") == "development",
    )


if __name__ == "__main__":
    main()
