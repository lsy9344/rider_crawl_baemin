"""Agent-local secure store + identity 영속 + token 유효성 게이트 (Story 4.2 / P3-02).

이 모듈이 책임지는 것(범위):

* :class:`DpapiSecretStore` — ``rider_crawl.secret_store.SecretStore`` Protocol
  (``put``/``resolve``)을 구현하는 **새 백엔드**다. 값을 OS 보안 저장소(DPAPI,
  ``CryptProtectData``/``CryptUnprotectData``)로 암호화한 blob 으로 디스크에 보관한다.
  새 인터페이스를 만들지 않고 2.4 가 깐 seam 에 백엔드만 끼운다(재발명 금지).
* 식별값(``agent_id``/``tenant_scope``/``config_version``) ``agent_config.json`` 영속 +
  :class:`AgentIdentity` 로드.
* token 유효성 게이트 **primitive** (:func:`validate_agent_token`): "유효 token 이 있어야
  job 수신 가능". 실제 claim 루프 배선(이 게이트로 job 을 막는 곳)은 Story 4.4 소유다.

핵심 불변식(ADD-15·NFR-8):

* **secret ↔ config 물리 분리.** secret(``agent_token``)은 DPAPI store 파일에만, 비밀 아님
  식별/설정값은 ``agent_config.json`` 에만 둔다 — 두 파일은 반드시 다른 경로다.
* **평문 token 비노출.** store 파일에는 DPAPI 암호화 blob 만 들어가고, config·로그·예외
  메시지에 token 평문이 남지 않는다.
* **import-safety.** ``ctypes``/crypt32 로드와 DPAPI 호출은 **함수 내부 lazy + Windows-gated**
  라, ``import rider_agent.secure_store`` 가 비-Windows(WSL/CI)에서도 import-safe 하다
  (``rider_crawl.sender`` 가 pyautogui 를 함수 내부 import 하는 선례와 동형).

자기(own) 코드는 **순수 동기**이고 ``rider_crawl`` 만 import 한다(역방향/``rider_server``
import 0) — 4.1 의 AST 가드가 자동 검사한다.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rider_crawl.config import app_state_root
from rider_crawl.redaction import REDACTED
from rider_crawl.secret_store import SecretStore

# store 안에서 agent token 을 가리키는 고정 ref. per-machine 단일 identity 라 token 은 한 개이며,
# 이 ref 는 secret 이 아니라 불투명 핸들이다(redaction 이 ``*_ref`` 처럼 보존). config 파일에는
# 넣지 않는다 — 로드 시 이 상수로 store 에서 직접 resolve 한다.
AGENT_TOKEN_REF = "agent:agent_token"

# 식별값/secret store 파일 basename. 둘은 반드시 **다른 파일**이어야 한다(분리 불변식).
IDENTITY_FILENAME = "agent_config.json"
SECRET_STORE_FILENAME = "agent_secrets.dpapi.json"

# token 게이트 상태 — enum 은 만들지 않는다(rider_agent 자기 상수 3종). "정확히 N개" lock 으로
# 잠그지 않아 후속 스토리가 상태를 늘려도 다른 테스트를 깨지 않는다(memory: enum-member-count).
TOKEN_STATUS_VALID = "valid"  # 유효 token → job 수신 가능
TOKEN_STATUS_MISSING = "missing"  # 미등록/ token 없음 → job 미수신
TOKEN_STATUS_REVOKED = "revoked"  # 만료/revoke → job 미수신, 재등록 필요


# ── 식별 경로(주입 가능, per-machine 단일·cwd 독립) ──────────────────────────


def default_agent_state_dir() -> Path:
    """per-machine 단일 identity 루트. 텔레그램 offset 처럼 cwd 가 아니라 고정 state root 아래다.

    실제 배포 경로(``C:\\RiderBot\\agent\\data``)는 운영 패키징 소유이고, 개발/테스트에선
    ``app_state_root()`` 하위로 매핑한다(주입 가능 — 테스트는 ``tmp_path`` 로 격리).
    """

    return app_state_root() / "runtime" / "state" / "agent"


def default_identity_path() -> Path:
    return default_agent_state_dir() / IDENTITY_FILENAME


def default_secret_store_path() -> Path:
    return default_agent_state_dir() / SECRET_STORE_FILENAME


# ── DPAPI 백엔드(stdlib ctypes, Windows-gated) ────────────────────────────────

# codec seam: (protect, unprotect). 기본은 실제 DPAPI(아래). 테스트는 비-Windows 에서도
# round-trip 을 결정적으로 검증하려고 fake codec 을 주입한다 — 그래도 store 파일엔 평문이 아닌
# 인코딩 blob 만 남는다(분리·평문부재 불변식이 codec 과 무관하게 성립).
ProtectFn = Callable[[str], bytes]
UnprotectFn = Callable[[bytes], str]


def _dpapi_crypt(data: bytes, *, protect: bool) -> bytes:
    """crypt32 ``CryptProtectData``/``CryptUnprotectData`` 호출(함수 내부 lazy·Windows-gated).

    ``import ctypes``/crypt32 로드를 모듈 상단이 아니라 여기서 하기 때문에 비-Windows 에서
    ``import rider_agent.secure_store`` 가 안전하다(실 호출 시에만 Windows 를 요구).
    """

    import sys

    if sys.platform != "win32":
        raise RuntimeError(
            "DPAPI secure store는 Windows에서만 동작한다(비-Windows는 codec 주입으로 검증)"
        )

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    func.restype = wintypes.BOOL
    ok = func(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        winerror = ctypes.get_last_error()  # 에러 코드는 secret 이 아니다
        raise OSError(
            f"DPAPI {'protect' if protect else 'unprotect'} 실패 (winerror={winerror})"
        )
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_protect(plaintext: str) -> bytes:
    return _dpapi_crypt(plaintext.encode("utf-8"), protect=True)


def _dpapi_unprotect(blob: bytes) -> str:
    return _dpapi_crypt(blob, protect=False).decode("utf-8")


class DpapiSecretStore:
    """``SecretStore`` Protocol 구현 — 값을 DPAPI 암호화 blob 으로 파일에 보관한다.

    파일 포맷은 ``{ref: base64(dpapi_blob)}`` JSON 이다. ``LocalFileSecretStore`` 가 평문
    JSON 인 것과 달리(2.4 의 한계), 이 백엔드는 값 자체를 OS 보안 저장소로 암호화해 저장하므로
    store 파일을 디스크에서 읽어도 평문 token 이 없다(NFR-8/ADD-15). store 파일은 반드시
    ``agent_config.json`` 과 다른 경로여야 한다(분리 불변식). 없는 ref → ``None``(fail-closed).
    """

    def __init__(
        self,
        path: Path,
        *,
        protect: ProtectFn | None = None,
        unprotect: UnprotectFn | None = None,
    ) -> None:
        self.path = Path(path)
        # codec 주입(테스트). 기본은 실제 DPAPI — 호출 시점에만 Windows 를 요구한다.
        self._protect: ProtectFn = protect if protect is not None else _dpapi_protect
        self._unprotect: UnprotectFn = (
            unprotect if unprotect is not None else _dpapi_unprotect
        )

    def put(self, value: str, *, ref: str = "") -> str:
        if not ref:
            # 내용 기반 결정적 핸들(같은 값→같은 ref). ref 는 평문 해시가 아니라 잘린 sha256 이라
            # secret 이 아니다. DPAPI blob 은 매번 달라도 ref 는 안정적이라 테스트가 결정적이다.
            ref = "local:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        data = self._load()
        existing = data.get(ref)
        if existing is not None and self._safe_decode(existing) == value:
            # 멱등 쓰기: 같은 값이면 store 파일을 다시 쓰지 않는다(불필요한 churn·blob 회전 방지).
            return ref
        data[ref] = base64.b64encode(self._protect(value)).decode("ascii")
        self._save(data)
        return ref

    def resolve(self, ref: str) -> str | None:
        if not ref:
            return None
        encoded = self._load().get(ref)
        if encoded is None:
            return None
        return self._safe_decode(encoded)

    def _safe_decode(self, encoded: str) -> str | None:
        try:
            return self._unprotect(base64.b64decode(encoded.encode("ascii")))
        except Exception:
            # 손상/타 머신 blob/decode 실패 → fail-closed(None). 호출부가 재등록 필요로 처리한다.
            return None

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, str]) -> None:
        # store 쓰기도 ui_settings/secret_store 와 동일한 atomic write(2.2)를 재사용한다(재발명
        # 금지). 쓰기 시점 지연 import 로 import-safety 와 순환 import 회피를 함께 지킨다.
        from rider_crawl.ui_settings import _atomic_write_text

        _atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2))


# ── identity (식별값 영속 + token 로드) ───────────────────────────────────────


@dataclass(frozen=True, repr=False)
class AgentIdentity:
    """로컬 agent identity. ``agent_token`` 만 secret(메모리 보유), 나머지는 비밀 아님.

    ``__repr__`` 은 token 을 :data:`REDACTED` 로 가린다 — 로그/예외에 repr 이 섞여도 평문
    token 이 새지 않게 한다(부분 노출 없이 통째 치환).
    """

    agent_id: str
    agent_token: str
    tenant_scope: dict[str, Any] = field(default_factory=dict)
    config_version: str = ""

    def __repr__(self) -> str:
        return (
            f"AgentIdentity(agent_id={self.agent_id!r}, agent_token={REDACTED!r}, "
            f"tenant_scope={self.tenant_scope!r}, config_version={self.config_version!r})"
        )


def _identity_config_payload(identity: AgentIdentity) -> dict[str, Any]:
    # agent_config.json 에는 **비밀 아님** 값만 — token 은 절대 넣지 않는다.
    return {
        "agent_id": identity.agent_id,
        "tenant_scope": identity.tenant_scope,
        "config_version": identity.config_version,
    }


def save_agent_identity(
    identity: AgentIdentity, *, store: SecretStore, identity_path: Path
) -> None:
    """token 은 ``store`` 로(DPAPI), 식별값은 ``agent_config.json`` 으로 분리 저장한다.

    config 쓰기는 2.2 atomic write 를 재사용해 손상/``.tmp`` 잔여물을 막는다.
    """

    store.put(identity.agent_token, ref=AGENT_TOKEN_REF)

    from rider_crawl.ui_settings import _atomic_write_text

    _atomic_write_text(
        Path(identity_path),
        json.dumps(_identity_config_payload(identity), ensure_ascii=False, indent=2),
    )


def load_agent_config(identity_path: Path) -> dict[str, Any] | None:
    """``agent_config.json`` 을 읽어 dict 로 반환(없거나 손상 시 ``None``)."""

    path = Path(identity_path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_local_agent_identity(
    *, store: SecretStore, identity_path: Path
) -> AgentIdentity | None:
    """로컬 identity(config 의 식별값 + store 의 token)를 로드한다.

    ``agent_id`` 또는 token 이 없으면 ``None`` — "유효 identity 없음"(미등록)으로 본다.
    architecture-contract startup 의 ``load_local_agent_identity()`` 계약명과 정합한다.
    """

    config = load_agent_config(identity_path)
    if not config:
        return None
    agent_id = config.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    token = store.resolve(AGENT_TOKEN_REF)
    if not token:
        return None
    tenant_scope = config.get("tenant_scope")
    return AgentIdentity(
        agent_id=agent_id,
        agent_token=token,
        tenant_scope=tenant_scope if isinstance(tenant_scope, dict) else {},
        config_version=str(config.get("config_version") or ""),
    )


# ── token 유효성 게이트 primitive (NFR-7, FR-16) ──────────────────────────────


@dataclass(frozen=True)
class TokenValidation:
    """token 게이트 결과. ``can_receive_jobs`` 만 True 일 때 job 을 받을 수 있다."""

    status: str

    @property
    def can_receive_jobs(self) -> bool:
        return self.status == TOKEN_STATUS_VALID

    @property
    def needs_registration(self) -> bool:
        # 미등록(missing) · revoke/만료(revoked) 모두 "재등록 필요" 상태.
        return self.status in (TOKEN_STATUS_MISSING, TOKEN_STATUS_REVOKED)


def validate_agent_token(
    identity: AgentIdentity | None,
    *,
    server_check: Callable[[AgentIdentity], bool] | None = None,
) -> TokenValidation:
    """token 유효성을 판정한다(primitive — 실제 claim 루프 배선은 4.4 소유).

    * identity 없음/ token 없음 → ``missing``(미수신).
    * ``server_check`` 미주입 → 로컬 존재만으로 ``valid``(서버 확인 경로는 4.4 가 배선).
    * ``server_check`` 주입(stub transport) → True 면 ``valid``, False(401·revoked/만료)면
      ``revoked``. 어느 경로도 token 평문을 노출하지 않는다.
    """

    if identity is None or not (identity.agent_token or "").strip():
        return TokenValidation(TOKEN_STATUS_MISSING)
    if server_check is None:
        return TokenValidation(TOKEN_STATUS_VALID)
    return TokenValidation(
        TOKEN_STATUS_VALID if server_check(identity) else TOKEN_STATUS_REVOKED
    )
