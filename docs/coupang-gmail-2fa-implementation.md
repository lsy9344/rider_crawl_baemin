# 쿠팡이츠 이메일 2차 인증 자동복구

> **현행 아님:** 이 문서의 파일명은 과거 이력 때문에 남아 있지만, 현재 구현은 Gmail API나
> Google OAuth를 사용하지 않습니다. 쿠팡 인증 메일은 Gmail/Naver 모두 IMAP으로 읽습니다.

## 현행 방식

- UI의 각 크롤링 탭에서 쿠팡 로그인 아이디, 쿠팡 비밀번호, 인증 이메일 주소, 인증 이메일 앱 비밀번호를 입력합니다.
- 인증 이메일 주소의 도메인으로 IMAP 호스트를 고릅니다.
  - Gmail: `imap.gmail.com:993(SSL)`
  - Naver: `imap.naver.com:993(SSL)`
- 메일 계정에서 IMAP 사용을 켜고, 2단계 인증 계정은 앱 비밀번호를 발급해야 합니다.
- 인증번호 메일은 요청 시각 이후 도착한 메일만 후보로 삼고, 제목/발신자 키워드로 좁힙니다.
- 메일은 읽음 처리하지 않습니다. IMAP `BODY.PEEK`와 readonly 연결을 사용합니다.

## 보안 규칙

- 인증번호, 앱 비밀번호, 쿠팡 비밀번호는 로그, 예외, audit, result JSON에 쓰지 않습니다.
- 서버/Admin 경로에서는 인증 이메일 주소와 앱 비밀번호를 `*_ref` 핸들로만 저장합니다.
- 같은 메일함은 동시에 여러 인증 요청을 보내지 않도록 mailbox lock을 사용합니다.
- CAPTCHA나 비정상 로그인 화면은 자동으로 우회하지 않고 사용자 조치 필요 상태로 멈춥니다.

## 관련 파일

- `src/rider_crawl/auth/imap_2fa.py`
- `src/rider_crawl/auth/coupang_email_2fa.py`
- `src/rider_crawl/ui_settings.py`
- `tests/test_imap_2fa.py`
- `tests/test_coupang_email_2fa.py`
