from rider_crawl.auth.codes import extract_verification_code


def test_extract_verification_code_prefers_context_keyword():
    text = "주문번호 100200 입니다. 인증번호: 123456 을 입력하세요. 고객센터 987654"
    assert extract_verification_code(text, code_digits=6) == "123456"


def test_extract_verification_code_uses_english_keyword():
    text = "Your verification code is 778899."
    assert extract_verification_code(text, code_digits=6) == "778899"


def test_extract_verification_code_fallback_when_single_number():
    text = "코드를 입력하세요 555444"
    assert extract_verification_code(text, code_digits=6) == "555444"


def test_extract_verification_code_fallback_rejects_multiple_numbers():
    # 같은 자리수 숫자가 여럿이고 주변 단어 매칭이 없으면 잘못된 추측을 하지 않는다.
    text = "주문 100200, 결제 300400 안내"
    assert extract_verification_code(text, code_digits=6) is None


def test_extract_verification_code_fallback_requires_context_keyword():
    # 인증 관련 단어가 본문에 전혀 없으면, 유일한 6자리 숫자라도 코드로 쓰지 않는다.
    text = "주문번호 778899 가 접수되었습니다. 감사합니다."
    assert extract_verification_code(text, code_digits=6) is None


def test_extract_verification_code_fallback_accepts_with_distant_keyword():
    # 인증 단어가 숫자와 떨어져 있어도(주변 매칭 실패), 본문에 인증 단어가 있고 유일한
    # 6자리 숫자면 fallback으로 채택한다.
    text = "이메일 인증 안내입니다. 아래 값을 입력하세요. 778899"
    assert extract_verification_code(text, code_digits=6) == "778899"


def test_extract_verification_code_respects_code_digits():
    text = "인증번호 1234"
    assert extract_verification_code(text, code_digits=4) == "1234"
    assert extract_verification_code(text, code_digits=6) is None


def test_extract_verification_code_handles_forwarded_duplicate_code():
    # 전달(Fwd) 메일은 원문+전달본으로 같은 인증번호가 2번 들어온다. 컨텍스트 매칭은
    # 첫 매치를, fallback은 값 집합(크기 1)을 보므로 단일 코드를 돌려준다.
    text = "[쿠팡] 인증번호630873 입니다.\n---------- 전달 메시지 ----------\n[쿠팡] 인증번호630873 입니다."
    assert extract_verification_code(text, code_digits=6) == "630873"
