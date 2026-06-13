# (폐기) Google Auth Files

> **현행 아님.** 쿠팡 이메일 2차 인증 자동복구는 이제 **Gmail OAuth가 아니라 IMAP**으로
> 동작합니다(Gmail/Naver 공용). 인증 이메일 주소와 **앱 비밀번호**를 UI의 각 크롤링 탭에
> 직접 입력하며, 이 폴더의 OAuth 클라이언트/토큰 파일은 더 이상 사용하지 않습니다.

## 현행 방식(요약)

- 메일 환경설정에서 **IMAP 사용**을 켜고, 2단계 인증 계정은 **앱 비밀번호**를 발급합니다.
  - 네이버: `imap.naver.com:993(SSL)`
  - Gmail: `imap.gmail.com:993(SSL)` (OAuth 동의/토큰 부트스트랩 불필요)
- UI 탭의 "인증 이메일 주소(naver/gmail)"와 "인증 이메일 비밀번호(앱 비밀번호)"에 입력합니다.
  공급자(naver/gmail)는 주소 도메인으로 자동 결정됩니다.

자세한 내용은 저장소 루트의 `README.md`와 `docs/imap/MERGE_PLAN_naver_2fa.md`를 참고하세요.

## 이 폴더에 남아 있는 파일

`.gitignore`가 이 폴더의 `*.json`·`token*`·`credentials*`를 계속 제외하므로 과거에
만들어 둔 토큰/크리덴셜 파일이 로컬에 남아 있을 수 있습니다. 더 이상 앱이 읽지 않으니
필요 없으면 안전하게 삭제해도 됩니다.
