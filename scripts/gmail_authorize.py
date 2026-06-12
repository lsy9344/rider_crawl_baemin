"""Gmail 받은편지함 1개를 인증해 token 파일을 만드는 1회용 부트스트랩 스크립트.

운영 앱(``rider_crawl``)은 무인 실행이라 대화형 OAuth 승인을 띄우지 않고, 이미
만들어진 ``token.*.json``이 있어야 인증번호 메일을 읽는다(``auth/gmail.py`` 참고).
이 스크립트가 그 token을 "받은편지함마다 한 번씩" 만들어 준다.

구성 정책(계정마다 Gmail이 다른 경우, 권장):

- ``credentials.gmail.json`` (OAuth 클라이언트, 앱 자격증명)은 **계정 수와 무관하게 1개**를
  모든 계정이 공유한다.
- ``token.<계정구분>.json`` 은 **Gmail 받은편지함마다 1개**씩 만든다. 브라우저에서 그
  쿠팡 계정의 인증메일을 받는 Gmail 계정으로 로그인/동의하면 그 계정의 token이 저장된다.
- 만든 token 경로를 UI의 해당 크롤링 탭 "Gmail 토큰 파일 경로"에 입력한다.

사용 예 (프로젝트 루트에서):

    python scripts/gmail_authorize.py --token secrets/google/token.uijeongbu-nambu.json

기본 OAuth 클라이언트 경로는 ``secrets/google/credentials.gmail.json`` 이며,
``--credentials`` 로 바꿀 수 있다. 권한은 읽기 전용(gmail.readonly)만 요청한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

# auth/gmail.py 의 GMAIL_READONLY_SCOPE 와 동일해야 한다(이 스크립트는 rider_crawl
# 패키지 임포트 없이 단독 실행되도록 값을 직접 둔다). 둘이 어긋나면 앱이 token을
# 못 읽으니 변경 시 함께 맞춘다.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def authorize(credentials_path: Path, token_path: Path) -> str:
    """Run the local OAuth consent flow once and save the token. Return the email."""

    from google_auth_oauthlib.flow import InstalledAppFlow

    if not credentials_path.is_file():
        raise SystemExit(
            f"OAuth 클라이언트 파일이 없습니다: {credentials_path}\n"
            "Google Cloud Console에서 받은 credentials.gmail.json을 먼저 그 경로에 두세요."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), [GMAIL_READONLY_SCOPE]
    )
    # 브라우저가 열리고, 로그인한 Gmail 계정의 읽기 전용 권한 동의를 받는다.
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return _authorized_email(creds)


def _authorized_email(creds: Any) -> str:
    # 어떤 Gmail 계정으로 인증됐는지 확인용. 잘못된 받은편지함으로 로그인했는지
    # 바로 알 수 있게 이메일 주소를 돌려준다. 실패해도 token 저장 자체엔 영향 없다.
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return str(profile.get("emailAddress") or "(이메일 주소 확인 불가)")
    except Exception:
        return "(이메일 주소 확인 실패 — token은 저장됨)"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Gmail 받은편지함 인증 token 생성(받은편지함마다 1회 실행)"
    )
    parser.add_argument(
        "--credentials",
        default="secrets/google/credentials.gmail.json",
        help="OAuth 클라이언트 파일 경로(모든 계정 공유). 기본값: secrets/google/credentials.gmail.json",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="저장할 token 파일 경로(받은편지함마다 다르게). 예: secrets/google/token.uijeongbu-nambu.json",
    )
    args = parser.parse_args(argv)

    email = authorize(Path(args.credentials), Path(args.token))
    print(f"인증 완료: {email}")
    print(f"token 저장: {args.token}")
    print("이 경로를 UI의 해당 크롤링 탭 'Gmail 토큰 파일 경로'에 입력하세요.")


if __name__ == "__main__":
    main()
