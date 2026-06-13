"""공용 redaction 유틸 단위 테스트 (Story 1.3 / P0-04).

순수 함수 단위 테스트라 외부 브라우저/텔레그램/카카오/Gmail 호출이 없다.
fixture 에는 실제 secret 을 넣지 않고 명백히 가짜인 값만 쓴다(NFR-5, ADD-15).
"""

import pytest

from rider_crawl.redaction import (
    REDACTED,
    redact,
    redact_mapping,
    redacted_error_event,
)

# 명백히 가짜인 fixture 값 (실제 토큰/전화/이메일/chat_id 아님).
FAKE_TOKEN = "8:AAE-fake-telegram-token-00000000"
FAKE_TOKEN_TAIL = "fake-telegram-token-00000000"
FAKE_REFRESH = "fake-refresh-token-11111111"
FAKE_AUTH_CODE = "fakeAUTHcode22222222"
FAKE_PASSWORD = "Fake-Pass-0000"
FAKE_OTP = "482913"
FAKE_PHONE = "010-0000-0000"
FAKE_EMAIL = "rider@example.com"
FAKE_CHAT_ID = "1234567"


# --- AC1: 정상 마스킹 --------------------------------------------------------


@pytest.mark.parametrize(
    "text, secret",
    [
        (f"password={FAKE_PASSWORD}", FAKE_PASSWORD),
        (f"token {FAKE_TOKEN} failed", FAKE_TOKEN),
        (f"refresh_token={FAKE_REFRESH}", FAKE_REFRESH),
        (f"authorization_code={FAKE_AUTH_CODE}", FAKE_AUTH_CODE),
        (f"인증번호 {FAKE_OTP} 입니다", FAKE_OTP),
        (f"otp={FAKE_OTP}", FAKE_OTP),
        (f"연락처 {FAKE_PHONE} 로 발송", FAKE_PHONE),
        (f"메일 {FAKE_EMAIL} 발송 실패", FAKE_EMAIL),
        # 이 프로젝트 고유 민감값
        (f"telegram_bot_token={FAKE_TOKEN}", FAKE_TOKEN),
        (f"chat_id={FAKE_CHAT_ID}", FAKE_CHAT_ID),
        (f"telegram_message_thread_id=42 chat_id={FAKE_CHAT_ID}", FAKE_CHAT_ID),
        (f"coupang_password={FAKE_PASSWORD}", FAKE_PASSWORD),
        (f'"gmail_refresh_token": "{FAKE_REFRESH}"', FAKE_REFRESH),
    ],
)
def test_ac1_known_secrets_are_masked(text, secret):
    out = redact(text)
    assert secret not in out
    assert REDACTED in out


def test_ac1_operational_words_survive():
    # 마스킹은 secret 값만 — 주변 운영 텍스트는 보존돼야 의미가 남는다.
    out = redact(f"token {FAKE_TOKEN} 발송 실패")
    assert "발송 실패" in out
    assert FAKE_TOKEN_TAIL not in out


# --- AC2: 원본 secret 부분 문자열 비잔존 ------------------------------------


def test_ac2_no_secret_substring_in_free_text():
    # 여러 secret 을 운영 로그 문장에 섞은 뒤, 어떤 연속 부분 문자열도 남지 않음을 단언.
    text = (
        f"sending to chat_id={FAKE_CHAT_ID} with token {FAKE_TOKEN} "
        f"otp={FAKE_OTP} phone {FAKE_PHONE} mail {FAKE_EMAIL} failed"
    )
    out = redact(text)
    for secret in (FAKE_CHAT_ID, FAKE_TOKEN, FAKE_TOKEN_TAIL, FAKE_OTP, FAKE_PHONE, FAKE_EMAIL):
        assert secret not in out
    # 토큰 끝 6자리 같은 부분 문자열도 없어야 한다(부분 노출 금지).
    assert FAKE_TOKEN[-6:] not in out


def test_ac2_no_secret_substring_in_mapping():
    data = {
        "telegram_bot_token": FAKE_TOKEN,
        "password": FAKE_PASSWORD,
        "chat_id": int(FAKE_CHAT_ID),
        "nested": {"otp": FAKE_OTP, "note": f"{FAKE_EMAIL} 로 발송"},
        "items": [{"refresh_token": FAKE_REFRESH}],
    }
    out = redact_mapping(data)
    flat = repr(out)
    for secret in (FAKE_TOKEN, FAKE_PASSWORD, FAKE_CHAT_ID, FAKE_OTP, FAKE_EMAIL, FAKE_REFRESH):
        assert secret not in flat
    assert out["telegram_bot_token"] == REDACTED
    assert out["password"] == REDACTED
    assert out["chat_id"] == REDACTED
    assert out["nested"]["otp"] == REDACTED
    assert FAKE_EMAIL not in out["nested"]["note"]
    assert out["items"][0]["refresh_token"] == REDACTED


def test_ac2_ref_keys_are_preserved():
    # ``*_ref`` 는 secret 이 아니라 참조 — 추적용으로 보존돼야 한다.
    data = {"password_ref": "vault://coupang/pw", "username_ref": "vault://coupang/id"}
    out = redact_mapping(data)
    assert out["password_ref"] == "vault://coupang/pw"
    assert out["username_ref"] == "vault://coupang/id"
    # 자유 텍스트에서도 _ref 는 건드리지 않는다.
    assert redact("password_ref=vault://x") == "password_ref=vault://x"


def test_story_2_4_secret_ref_keys_preserved_and_plaintext_keys_masked():
    # Story 2.4: 새 ``*_ref`` 키(로컬 store 핸들)는 참조라 보존되고, 평문 secret 어간 키는
    # 여전히 마스킹된다(redaction 무약화). ref 값은 secret 이 아니므로 그대로 추적 가능하다.
    data = {
        "telegram_bot_token_ref": "local:mt-1/telegram_bot_token",
        "coupang_login_password_ref": "local:mt-1/coupang_login_password",
        "coupang_login_id_ref": "local:mt-1/coupang_login_id",
        "telegram_bot_token": FAKE_TOKEN,
        "coupang_login_password": FAKE_PASSWORD,
    }
    out = redact_mapping(data)
    assert out["telegram_bot_token_ref"] == "local:mt-1/telegram_bot_token"
    assert out["coupang_login_password_ref"] == "local:mt-1/coupang_login_password"
    assert out["coupang_login_id_ref"] == "local:mt-1/coupang_login_id"
    assert out["telegram_bot_token"] == REDACTED
    assert out["coupang_login_password"] == REDACTED


# --- AC2: 운영 식별자 (보존 기본 / 옵션 마스킹) ----------------------------


def test_ac2_operational_ids_preserved_by_default():
    data = {"customer_name": "홍길동", "center_name": "강남센터", "baemin_center_name": "송파상점"}
    out = redact_mapping(data)
    assert out["customer_name"] == "홍길동"
    assert out["center_name"] == "강남센터"
    assert out["baemin_center_name"] == "송파상점"


def test_ac2_operational_ids_masked_when_opted_in():
    data = {
        "customer_name": "홍길동",
        "center_name": "강남센터",
        "telegram_bot_token": FAKE_TOKEN,
    }
    out = redact_mapping(data, mask_operational_ids=True)
    assert out["customer_name"] == REDACTED
    assert out["center_name"] == REDACTED
    # 운영 식별자 옵션과 무관하게 secret 은 항상 마스킹.
    assert out["telegram_bot_token"] == REDACTED


# --- AC3: 에러 이벤트 헬퍼 ---------------------------------------------------


def test_ac3_redacted_error_event_masks_message_and_error():
    event = redacted_error_event(
        "GMAIL_FETCH_FAILED",
        f"otp={FAKE_OTP} 발송 실패 token {FAKE_TOKEN}",
        RuntimeError(f"{FAKE_EMAIL} 인증 실패 인증번호 {FAKE_OTP}"),
    )
    assert event["code"] == "GMAIL_FETCH_FAILED"  # code 는 보존
    assert FAKE_OTP not in event["message_redacted"]
    assert FAKE_TOKEN not in event["message_redacted"]
    assert FAKE_EMAIL not in event["error_message_redacted"]
    assert FAKE_OTP not in event["error_message_redacted"]


def test_ac3_no_error_means_no_error_key():
    event = redacted_error_event("PLAIN_CODE", "그냥 메시지")
    assert event == {"code": "PLAIN_CODE", "message_redacted": "그냥 메시지"}
    assert "error_message_redacted" not in event


def test_ac3_envelope_composition_matches_add13():
    # ADD-13: {"error": {"code", "message_redacted"}} 형태로 그대로 합성 가능.
    envelope = {"error": redacted_error_event("X_FAILED", f"token {FAKE_TOKEN}")}
    assert envelope["error"]["code"] == "X_FAILED"
    assert "message_redacted" in envelope["error"]
    assert FAKE_TOKEN not in envelope["error"]["message_redacted"]


# --- idempotency -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"password={FAKE_PASSWORD} token {FAKE_TOKEN} {FAKE_EMAIL} {FAKE_PHONE}",
        f"인증번호 {FAKE_OTP} chat_id={FAKE_CHAT_ID} refresh_token={FAKE_REFRESH}",
        f'"telegram_bot_token": "{FAKE_TOKEN}"',
    ],
)
def test_redact_is_idempotent(text):
    once = redact(text)
    assert redact(once) == once


# === QA gap coverage (bmad-qa-generate-e2e-tests, Story 1.3) =================
# 위 케이스가 다루지 않던 문서상 동작을 잠그는 보강 테스트. 모든 값은 명백한 가짜다.


# --- AC1: 전화번호 변형 (하이픈 없음 / 국제 +82 / 011·017 등) ----------------
# AC1·Task1 은 "한국 01X-XXXX-XXXX/하이픈 없는 형태 + 국제 +82" 를 명시하는데,
# 기존 테스트는 하이픈 있는 010 한 케이스만 덮었다.


@pytest.mark.parametrize(
    "phone",
    [
        "010-0000-0000",      # 하이픈 있는 010 (회귀 고정)
        "01000000000",        # 하이픈 없는 형태
        "+82 10-0000-0000",   # 국제 표기 (공백)
        "+82-10-0000-0000",   # 국제 표기 (하이픈)
        "011-0000-0000",      # 다른 통신사 식별자
        "017-000-0000",       # 구형 7자리 가입자번호
    ],
)
def test_ac1_phone_variants_are_masked(phone):
    out = redact(f"연락처 {phone} 로 발송")
    assert phone not in out
    assert REDACTED in out
    # 운영 문맥은 보존돼야 메시지 의미가 남는다.
    assert "발송" in out


# --- AC1: OTP/인증번호 문맥 라벨 변형 ----------------------------------------
# regex 는 code/verification code/auth code/인증 코드 라벨을 지원하지만
# 기존 테스트는 "인증번호"·"otp=" 만 덮었다.


@pytest.mark.parametrize(
    "text",
    [
        f"your code: {FAKE_OTP} here",
        f"verification code {FAKE_OTP}",
        f"auth code={FAKE_OTP}",
        f"인증 코드 {FAKE_OTP}",
        f"인증코드={FAKE_OTP}",
    ],
)
def test_ac1_otp_context_label_variants_are_masked(text):
    out = redact(text)
    assert FAKE_OTP not in out
    assert REDACTED in out


# --- AC1: 추가 민감 키 (key=value) ------------------------------------------
# client_secret / access_token / id_token / api_key / credential / bot_token 등
# _SENSITIVE_KEY 에 있으나 테스트로 잠그지 않았던 키들.

FAKE_SECRET_VAL = "fake-secret-value-abcdef0123"


@pytest.mark.parametrize(
    "key",
    [
        "client_secret",
        "access_token",
        "id_token",
        "bot_token",
        "api_key",
        "apikey",
        "credential",
        "credentials",
    ],
)
def test_ac1_additional_sensitive_keys_are_masked(key):
    out = redact(f"{key}={FAKE_SECRET_VAL}")
    assert FAKE_SECRET_VAL not in out
    assert out.startswith(f"{key}=")  # 키·분리자는 보존
    assert REDACTED in out


# --- AC2: redact_mapping 컨테이너 의미 보존 ---------------------------------
# 원본 비변경(no-mutation), tuple 타입 보존, 최상위 list 입력 재귀 처리는
# docstring 계약("원본은 변경하지 않는다")인데 미검증이었다.


def test_ac2_mapping_does_not_mutate_original():
    original = {
        "password": FAKE_PASSWORD,
        "nested": {"otp": FAKE_OTP},
        "items": [{"refresh_token": FAKE_REFRESH}],
    }
    snapshot = {
        "password": FAKE_PASSWORD,
        "nested": {"otp": FAKE_OTP},
        "items": [{"refresh_token": FAKE_REFRESH}],
    }
    redact_mapping(original)
    assert original == snapshot  # 입력은 그대로, 새 객체만 반환


def test_ac2_mapping_preserves_tuple_type_and_recurses():
    out = redact_mapping({"pair": (1, 2, f"token={FAKE_TOKEN}")})
    assert isinstance(out["pair"], tuple)  # list 로 바뀌지 않는다
    assert out["pair"][:2] == (1, 2)       # 비문자열 원소 보존
    assert FAKE_TOKEN not in out["pair"][2]  # 튜플 안 문자열도 마스킹


def test_ac2_mapping_handles_top_level_list():
    out = redact_mapping([{"password": FAKE_PASSWORD}, f"token={FAKE_TOKEN}"])
    assert out[0]["password"] == REDACTED
    assert FAKE_TOKEN not in out[1]


def test_ac2_mapping_is_idempotent():
    data = {
        "a": {"password": FAKE_PASSWORD, "phone": f"call {FAKE_PHONE}"},
        "b": [{"telegram_bot_token": FAKE_TOKEN}],
    }
    once = redact_mapping(data)
    assert redact_mapping(once) == once


# --- 견고성: 비문자열 입력 강제 변환 ----------------------------------------
# redact() 는 str 가 아니면 str() 로 강제 변환한다 — 호출자 실수에도 죽지 않아야 한다.


@pytest.mark.parametrize("value", [12345, None, 3.14, ["a", "b"]])
def test_redact_coerces_non_string_without_error(value):
    out = redact(value)
    assert isinstance(out, str)
    assert out == str(value)


# === Review follow-up (bmad-story-automator-review, Story 1.3) ===============
# AC2 위반 누출 회귀 가드. ``Authorization: Bearer <jwt>`` 형태에서 일반 key=value 규칙은
# 값이 첫 공백에서 끊겨 스킴 단어만 가리고 토큰 본문을 흘렸다. 헤더 전체가 가려지는지 잠근다.
# 값은 명백한 가짜 JWT 모양(실제 토큰 아님 — NFR-5/ADD-15).

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.ZmFrZS1wYXlsb2Fk.fake-signature-00000000"


@pytest.mark.parametrize(
    "header",
    [
        f"Authorization: Bearer {FAKE_JWT}",
        f"authorization={FAKE_JWT}",
        f"Proxy-Authorization: Basic {FAKE_JWT}",
        f"Authorization: Token {FAKE_JWT}",
        f"sending Authorization: Bearer {FAKE_JWT} to api",
    ],
)
def test_review_authorization_header_credential_fully_masked(header):
    out = redact(header)
    assert FAKE_JWT not in out          # 자격증명 본문 전체 비잔존
    assert FAKE_JWT[-8:] not in out     # 꼬리 부분 문자열도 없음 (부분 노출 금지)
    assert REDACTED in out


def test_review_authorization_header_is_idempotent():
    once = redact(f"Authorization: Bearer {FAKE_JWT}")
    assert redact(once) == once


def test_review_authorization_code_key_still_handled_by_keyvalue():
    # ``authorization_code`` 는 헤더 규칙(sep 가 ``_``)이 아니라 key=value 규칙이 처리해야 한다.
    out = redact(f"authorization_code={FAKE_AUTH_CODE}")
    assert FAKE_AUTH_CODE not in out
    assert out.startswith("authorization_code=")  # 키·분리자 보존
    assert REDACTED in out


def test_review_non_auth_word_not_masked():
    # ``reauthorization`` 같은 단어는 헤더가 아니므로 건드리지 않는다(과잉 마스킹 가드).
    out = redact("reauthorization status is normal")
    assert out == "reauthorization status is normal"
