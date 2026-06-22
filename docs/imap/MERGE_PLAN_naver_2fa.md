# 네이버/Gmail IMAP 2FA 병합 배경

이 문서는 쿠팡 이메일 2차 인증 자동복구를 Gmail OAuth 방식에서 Gmail/Naver 공용 IMAP 방식으로
바꾼 배경과 현행 운영 기준을 기록한다.

## 현행 정책

- `.env`에서 쿠팡 자동복구 자격증명을 읽지 않는다.
- UI의 각 크롤링 탭에서 쿠팡 아이디, 쿠팡 비밀번호, 인증 이메일 주소, 인증 이메일 앱 비밀번호를 입력한다.
- 인증 이메일 공급자는 주소 도메인으로 자동 결정한다.
  - `gmail.com`, `googlemail.com` → `imap.gmail.com`
  - `naver.com` → `imap.naver.com`
- 제목 키워드 기본값은 `인증번호`, 발신자 키워드 기본값은 `coupang`이다.
- 요청 시각 이후 도착한 메일만 후보로 삼고, 메일은 읽음 처리하지 않는다.

## 운영 준비

- Gmail: 설정에서 IMAP 사용을 켠다. 2단계 인증 계정은 앱 비밀번호를 발급한다.
- Naver: 메일 환경설정에서 IMAP/SMTP 사용을 켠다. 2단계 인증 계정은 앱 비밀번호를 발급한다.
- UI에 앱 비밀번호를 붙여넣을 때 공백이 포함되어도 IMAP 계층에서 정규화한다.

## 삭제된 방식

- Google Cloud OAuth 클라이언트 생성
- Gmail API scope 승인
- 로컬 token 파일 생성
- `scripts/gmail_authorize.py` 실행

위 방식은 현행 자동복구 경로가 아니다.
