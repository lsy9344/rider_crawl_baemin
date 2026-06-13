"""로컬 secret store seam + 파일 백엔드 + 저장 위치 분류(Story 2.4 / P1-06, NFR-8).

설정 JSON(``ui_settings.json``)에는 평문 token/password 대신 ``*_ref``(불투명 핸들)만
남기고, 실제 값은 설정 파일과 **분리된** 로컬 store에 둔다. store는 주입 가능한 seam이라
미래의 DPAPI(Epic 4)·AWS Secrets Manager(Epic 5)는 같은 ``put``/``resolve`` 인터페이스 뒤에
백엔드만 끼우면 된다. 정식 도메인 ``SecretRef``/``PlatformAccount``는 Story 2.5 소유라 여기서는
단순 문자열 ref + store seam만 쓴다(2.3이 enum 대신 단순 타입을 쓴 규율과 동일).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol


# secret 저장 위치 분류(NFR-8, 3분류). enum은 Story 2.5 소유라 만들지 않고 단순 문자열 3종을
# 쓴다. MVP에서는 ``central``·``agent_local``이 모두 같은 로컬 파일 백엔드로 가지만, 분류값은
# 백엔드 교체 시 라우팅을 결정하는 정책/메타데이터다: ``central``은 Epic 5(AWS Secrets
# Manager), ``agent_local``은 Epic 4(DPAPI/Credential Manager)에서 실제 백엔드로 교체된다.
# ``not_stored``는 어떤 store에도 영속하지 않는다(OTP/2FA — 읽어 입력 후 폐기).
SECRET_STORAGE_CENTRAL = "central"
SECRET_STORAGE_AGENT_LOCAL = "agent_local"
SECRET_STORAGE_NOT_STORED = "not_stored"

SECRET_STORAGE_CLASSIFICATION: dict[str, str] = {
    "telegram_bot_token": SECRET_STORAGE_CENTRAL,
    "coupang_login_password": SECRET_STORAGE_AGENT_LOCAL,
    "coupang_login_id": SECRET_STORAGE_AGENT_LOCAL,
    "gmail_oauth_token": SECRET_STORAGE_AGENT_LOCAL,
    "otp": SECRET_STORAGE_NOT_STORED,
}


def classify_secret_storage(secret_kind: str) -> str:
    """secret 종류를 3분류값(central/agent_local/not_stored) 중 정확히 하나로 반환한다."""

    return SECRET_STORAGE_CLASSIFICATION[secret_kind]


class SecretStore(Protocol):
    """주입 가능한 로컬 secret store seam.

    ``put``은 평문을 저장하고 불투명 ref를 돌려주며, ``resolve``는 ref로 평문을 복원한다
    (없으면 ``None`` — fail-closed). DPAPI(Epic 4)·AWS Secrets Manager(Epic 5)는 같은 seam에
    백엔드만 끼운다. OTP/2FA(not_stored)는 이 store가 다루지 않는다(영속 secret만 취급).
    """

    def put(self, value: str, *, ref: str = "") -> str: ...

    def resolve(self, ref: str) -> str | None: ...


class LocalFileSecretStore:
    """설정 파일과 분리된 로컬 파일 백엔드(MVP).

    **한계(과대 주장 금지):** 이 파일은 평문 JSON이다 — 암호화/OS 자격증명 저장소(DPAPI)·
    encryption-at-rest(BitLocker)는 Epic 4(NFR-9) 소유다. 본 store의 보장은 "설정/config
    파일(특히 ``ui_settings.json``)에 secret 평문이 없다"이지 "store 파일 자체가 암호화된다"가
    아니다. store 파일은 ``runtime/`` 하위(gitignore 대상)에 두고 반드시 ``ui_settings.json``과
    **다른 파일**이어야 한다. ref가 가리키는 값이 store에 없으면 ``resolve``가 ``None``을 돌려
    호출부가 빈 평문(fail-closed, 전송은 secret 재입력 전까지 비활성)으로 안전하게 처리한다.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def put(self, value: str, *, ref: str = "") -> str:
        # ref가 주어지면 그대로 쓰고(호출부의 결정적 per-field 핸들), 없으면 내용 기반 결정적
        # 핸들을 만든다(같은 값→같은 ref). 어느 쪽이든 재발급/재정렬에 안정적이라 테스트가
        # 결정적이다. ref 문자열은 secret이 아니라 참조라 redaction이 보존한다.
        if not ref:
            ref = "local:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        data = self._load()
        if data.get(ref) != value:
            # 멱등 쓰기: 값이 안 바뀌면 store 파일을 다시 쓰지 않는다(불필요한 churn 방지).
            data[ref] = value
            self._save(data)
        return ref

    def resolve(self, ref: str) -> str | None:
        if not ref:
            return None
        return self._load().get(ref)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, str]) -> None:
        # store 쓰기도 ui_settings.json과 동일한 atomic write(Story 2.2)를 공유한다(wheel 재발명
        # 금지). ui_settings ↔ secret_store 순환 import를 피하려 쓰기 시점에 지연 import한다.
        from .ui_settings import _atomic_write_text

        _atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2))
