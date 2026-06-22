"""이메일 인증번호 파싱(공용)."""

from __future__ import annotations

import re

_CODE_CONTEXT_KEYWORDS = r"(?:인증번호|인증\s*코드|코드|verification\s*code|code|otp)"
_VERIFICATION_INTENT_RE = re.compile(
    r"(?:인증|코드|verification|verify|\bcode\b|otp|일회용\s*비밀번호|one[-\s]*time)",
    flags=re.IGNORECASE,
)


def extract_verification_code(text: str, *, code_digits: int) -> str | None:
    """Extract the verification code from mail body text."""

    if not text or code_digits <= 0:
        return None

    context_pattern = rf"{_CODE_CONTEXT_KEYWORDS}[^\d]{{0,20}}(\d{{{code_digits}}})"
    context_match = re.search(context_pattern, text, flags=re.IGNORECASE)
    if context_match:
        return context_match.group(1)

    if not _VERIFICATION_INTENT_RE.search(text):
        return None
    unique = set(re.findall(rf"\b\d{{{code_digits}}}\b", text))
    if len(unique) == 1:
        return next(iter(unique))
    return None
