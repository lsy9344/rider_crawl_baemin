"""``SecretRef`` 값 객체(Story 2.5 / AC1·AC2, NFR-8·ADD-15).

설정/DB 밖에 저장된 secret을 가리키는 **참조 핸들만** 들고 **평문 secret 값을 절대
필드로 갖지 않는다** — SecretRef의 존재 이유가 "평문을 모델 밖에 두는 것"이다
(data-api-contract). ``ref`` 는 Story 2.4 ``secret_store`` 의 ``vault://…``/``local:…``
핸들과 호환되는 불투명 문자열이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .states import SecretStorageClass


@dataclass(frozen=True)
class SecretRef:
    ref: str
    storage_class: SecretStorageClass
    secret_kind: str = ""  # 예: "telegram_bot_token" — 분류/라우팅용 메타데이터(평문 아님)
