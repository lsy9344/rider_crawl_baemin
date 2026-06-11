# Google Auth Files

이 폴더는 이 프로젝트에서만 쓰는 Google/Gmail 인증 파일을 따로 보관하는 위치입니다.

실제 인증 파일은 민감 정보이므로 Git에 올리지 않습니다. `.gitignore`가 이 폴더의
`*.json`, `token*`, `credentials*` 파일을 제외합니다.

권장 파일명:

- `credentials.gmail.json`: Google Cloud Console에서 받은 OAuth 클라이언트 파일
- `token.gmail.json`: 최초 Gmail OAuth 승인 뒤 생성되는 로컬 토큰 파일

Gmail API 권한은 인증번호 메일을 읽는 데 필요한 최소 범위인
`https://www.googleapis.com/auth/gmail.readonly`만 사용합니다.
