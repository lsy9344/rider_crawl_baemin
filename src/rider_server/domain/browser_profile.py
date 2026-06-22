"""``BrowserProfile`` 도메인 모델(Story 2.5 / AC1) — 대상에 묶인 Chrome 프로필 상태.

``profile_path_ref`` 는 raw 경로가 아니라 ``SecretRef`` 다(data-api-contract: "server stores
profile id/ref, not raw sensitive path"). ``agent_id`` 의 Agent 모델은 Epic 4/5 소유라
아직 모델이 없어 ``str`` FK placeholder(forward-reference)로 둔다. ``target_id`` 역참조로
"대상에 연결된 브라우저 프로필"을 표현한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .secret_ref import SecretRef
from .states import BrowserProfileState


@dataclass(frozen=True)
class BrowserProfile:
    id: str
    agent_id: str  # → Agent (Epic 4/5 — str FK placeholder)
    target_id: str  # → MonitoringTarget
    profile_path_ref: SecretRef  # → SecretRef (raw 경로 아님)
    cdp_port: int | None = None
    state: BrowserProfileState = BrowserProfileState.UNKNOWN
