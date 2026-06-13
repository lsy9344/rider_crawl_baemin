"""이메일 인증번호 파싱(공용).

Gmail/Naver 어느 공급자든 IMAP으로 받은 메일 본문에서 인증번호를 뽑는 규칙을 한 곳에
모은다. 본문 추출(IMAP/Gmail 구조)과는 분리해, 코드 파싱 규칙만 순수 함수로 둔다.

보안 규칙: 이 모듈은 입력 텍스트만 다루며 인증번호 값을 로그/예외에 넣지 않는다.
"""

from __future__ import annotations

import re

# 숫자 바로 옆에서 코드를 뽑을 때 쓰는, 좁고 정확한 단어 패턴. 잘못된 인접 매칭을
# 줄이려고 "인증번호/인증 코드/코드/verification code/code/otp"로 한정한다.
_CODE_CONTEXT_KEYWORDS = r"(?:인증번호|인증\s*코드|코드|verification\s*code|code|otp)"

# fallback(유일한 N자리 숫자) 채택 여부를 가르는, 조금 더 넓은 "이 메일이 인증 메일인가"
# 게이트. 본문 어딘가에 인증 의도 단어가 있을 때만 fallback을 허용해, 비인증 메일의
# 숫자를 코드로 오인하지 않게 한다. 단독 "인증"/"verify"까지 포함한다.
_VERIFICATION_INTENT_RE = re.compile(
    r"(?:인증|코드|verification|verify|\bcode\b|otp|일회용\s*비밀번호|one[-\s]*time)",
    flags=re.IGNORECASE,
)


def extract_verification_code(text: str, *, code_digits: int) -> str | None:
    """Extract the verification code from mail body text.

    먼저 "인증번호 …123456"처럼 주변 단어를 함께 본다. 실패하면 같은 자리수 독립 숫자
    (``\\b\\d{N}\\b``) fallback을 쓰되, **두 조건을 모두** 만족할 때만 채택한다.

    1. 본문 어딘가에 인증 관련 단어(인증번호/verification code/otp 등)가 있다. 넓은
       메일 검색에 비인증 쿠팡 메일이 섞여 들어와도, 인증 단어가 전혀 없는 메일의
       숫자를 코드로 오인하지 않게 한다.
    2. 같은 자리수 숫자가 본문에 정확히 하나다(여럿이면 어느 게 코드인지 알 수 없음).

    전달(Fwd)·인용으로 같은 인증번호가 본문에 여러 번 들어와도, 컨텍스트 매칭은 첫
    매치를 반환하고 fallback은 값의 집합 크기로 판단하므로 동일 코드가 단일값으로 나온다.
    """

    if not text or code_digits <= 0:
        return None

    context_pattern = rf"{_CODE_CONTEXT_KEYWORDS}[^\d]{{0,20}}(\d{{{code_digits}}})"
    context_match = re.search(context_pattern, text, flags=re.IGNORECASE)
    if context_match:
        return context_match.group(1)

    # fallback: 인증 의도 단어가 본문에 있을 때만, 그리고 같은 자리수 숫자가 유일할 때만.
    if not _VERIFICATION_INTENT_RE.search(text):
        return None
    unique = set(re.findall(rf"\b\d{{{code_digits}}}\b", text))
    if len(unique) == 1:
        return next(iter(unique))
    return None
