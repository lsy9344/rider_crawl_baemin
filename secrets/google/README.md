# Google Auth Files

이 폴더는 이 프로젝트에서만 쓰는 Google/Gmail 인증 파일을 따로 보관하는 위치입니다.

실제 인증 파일은 민감 정보이므로 Git에 올리지 않습니다. `.gitignore`가 이 폴더의
`*.json`, `token*`, `credentials*` 파일을 제외합니다.

권장 파일명:

- `credentials.gmail.json`: Google Cloud Console에서 받은 OAuth 클라이언트 파일
- `token.gmail.json`: 최초 Gmail OAuth 승인 뒤 생성되는 로컬 토큰 파일

Gmail API 권한은 인증번호 메일을 읽는 데 필요한 최소 범위인
`https://www.googleapis.com/auth/gmail.readonly`만 사용합니다.

## 여러 쿠팡 계정(받은편지함마다 다른 Gmail)

- `credentials.gmail.json`(OAuth 클라이언트)은 **계정 수와 무관하게 1개**를 모든
  크롤링 탭이 공유합니다. 여러 개 만들 필요가 없습니다.
- `token.*.json`은 **Gmail 받은편지함마다 1개**씩 둡니다. 받은편지함이 분리돼 있으면
  검색식 충돌이 없어 가장 안전합니다. 예: `token.uijeongbu-nambu.json`,
  `token.account3.json` …
- 만든 토큰 경로를 UI의 해당 크롤링 탭 **"Gmail 토큰 파일 경로"**에 입력합니다.
  (단일 계정이면 기본값 `token.gmail.json`을 그대로 써도 됩니다.)

### 토큰 만들기(받은편지함마다 1회)

프로젝트 루트에서 부트스트랩 스크립트를 실행하면 브라우저가 열립니다. 그 쿠팡 계정의
인증메일을 받는 Gmail로 로그인/동의하면 토큰이 저장됩니다.

```
python scripts/gmail_authorize.py --token secrets/google/token.<계정구분>.json
```

운영 앱은 무인 실행이라 대화형 승인을 띄우지 않으므로, 토큰은 위 스크립트로 미리
만들어 두어야 합니다(없으면 그 탭의 자동복구가 실패해 로그인 만료로 중지됩니다).

## 쿠팡 인증메일 검색식(GMAIL_2FA_QUERY)

탭의 "Gmail 인증메일 검색식"에 넣습니다. 실측 발신자/제목 기준 예시:

```
from:(donotreply@coupang.com) subject:(인증번호) newer_than:1d
```

주의: Gmail `newer_than`에는 분 단위가 없습니다(`d`=일, `m`=월, `y`=년). 정확한 시각
컷오프는 앱이 인증번호 발송 시각(`requested_after`)으로 따로 거르므로, 여기서는
`newer_than:1d`처럼 하루 정도로 좁혀 후보만 줄이면 됩니다.
