"""Agent / 외부 service token 의 server-side revoke·rotate — Story 5.8 / AC3 (NFR-7).

token 자체는 Agent-local DPAPI(서버 비저장)다. 서버는 ``agents.token_revoked_at``/
``token_rotated_at`` **시각** 만 두어 ``api/jobs.py::resolve_agent_id`` 가 revoked agent 의
bearer 를 거부(→None→401)하게 한다. revoke/rotate 자체는 AC1 audit(SECRET_ADMIN 역할)에
기록된다 — write+audit 는 service(+repository) 동일 트랜잭션(액션 성공·audit 누락 불가).

외부 service token(Telegram bot token 등)은 Secrets Manager + DB ``*_ref`` 만이라(평문 DB 0)
**ref 회전/무효화 절차 + audit** 로 처리한다(:meth:`AgentTokenService.rotate_external_token`).
평문 token 을 응답/로그/audit 에 노출하지 않는다(``redact`` 통과값이 아니면 fail-closed 거부).

순수 의미(revoke 후 resolver 가 None 반환)는 :func:`revocation_aware_resolver` 로 추출해
always-run 단위로 잠근다(resolve_agent_id seam 은 sync — memory pg-gated-files-hide-pure-helpers).
단방향 import: ``rider_server`` → ``rider_crawl`` 만.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from rider_crawl.redaction import redact
from rider_server.domain import AuditResult

from .admin_action_service import (
    ACTION_AGENT_TOKEN_REVOKE,
    ACTION_AGENT_TOKEN_ROTATE,
    ACTION_EXTERNAL_TOKEN_ROTATE,
    TARGET_TYPE_AGENT,
    TARGET_TYPE_CHANNEL,
    AuditEntry,
    build_diff_redacted,
)


# ══════════════════════════════════════════════════════════════════════════
# 순수 helper — revoke 반영 resolver(resolve_agent_id seam 은 sync)
# ══════════════════════════════════════════════════════════════════════════

def revocation_aware_resolver(
    base_resolver: Callable[[str], str | None],
    is_revoked: Callable[[str], bool],
) -> Callable[[str], str | None]:
    """``base_resolver``(token→agent_id) 를 감싸 revoked agent 를 거부하는 resolver 를 만든다.

    revoke 가 반영되면 같은 bearer 가 ``None`` 으로 해석돼 claim/heartbeat/complete 가 401 이 된다
    (AC3 — revoked → None → 401). 순수 함수라 always-run 단위로 잠근다. ``is_revoked`` 는 sync
    callable(``agent_id → bool``)이다 — 운영은 ``agents.token_revoked_at`` 조회, 테스트는 set 멤버십.
    """

    def _resolve(token: str) -> str | None:
        agent_id = base_resolver(token)
        if agent_id is None or is_revoked(agent_id):
            return None
        return agent_id

    return _resolve


def looks_like_plaintext_secret(value: str) -> bool:
    """``value`` 가 평문 secret(token/OTP/password 등 패턴)이면 True — ``*_ref`` 핸들이면 False.

    ``redact`` 가 값을 바꾸면 알려진 secret 패턴이 들어있는 것이다(token shape/OTP/key=value).
    ``vault://telegram/bot`` 같은 ref 핸들은 redact 가 보존하므로 False. 외부 token rotate 가
    평문을 받는 것을 fail-closed 로 막는 데 쓴다(평문 DB 0).
    """

    return redact(value) != value


# ══════════════════════════════════════════════════════════════════════════
# repository 포트 + in-memory 구현(무-DB 기본값 + always-run fake)
# ══════════════════════════════════════════════════════════════════════════

class AgentTokenRepository(Protocol):
    """Agent token revoke/rotate 영속 포트 — 시각 UPDATE + audit INSERT 동일 트랜잭션."""

    async def set_revoked(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None: ...

    async def set_rotated(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None: ...

    async def is_revoked(self, agent_id: str) -> bool: ...

    async def record_audit(self, audit: AuditEntry) -> None: ...


class InMemoryAgentTokenRepository:
    """프로세스-내 token 상태 repository(무-DB 기본값 + 테스트 fake).

    revoke/rotate 시각을 dict 로 두고 audit 를 append 한다(같은 트랜잭션 의미 모사 — 둘 다 반영).
    ``revoked_ids`` 는 sync 노출이라 :func:`revocation_aware_resolver` 의 ``is_revoked`` 에 직접
    쓸 수 있다(resolve_agent_id seam 은 sync).
    """

    def __init__(self) -> None:
        self._revoked: dict[str, datetime] = {}
        self._rotated: dict[str, datetime] = {}
        self.audits: list[AuditEntry] = []

    async def set_revoked(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None:
        self._revoked[agent_id] = at
        self.audits.append(audit)

    async def set_rotated(self, agent_id: str, *, at: datetime, audit: AuditEntry) -> None:
        self._rotated[agent_id] = at
        self.audits.append(audit)

    async def is_revoked(self, agent_id: str) -> bool:
        return agent_id in self._revoked

    async def record_audit(self, audit: AuditEntry) -> None:
        self.audits.append(audit)

    # ── sync 노출(resolver 반영용) ──────────────────────────────────────────────
    @property
    def revoked_ids(self) -> set[str]:
        return set(self._revoked)

    def revoked_at(self, agent_id: str) -> datetime | None:
        return self._revoked.get(agent_id)

    def rotated_at(self, agent_id: str) -> datetime | None:
        return self._rotated.get(agent_id)


# ══════════════════════════════════════════════════════════════════════════
# token service(revoke/rotate 단일 소유처 — SECRET_ADMIN 역할 게이트 뒤에서 호출)
# ══════════════════════════════════════════════════════════════════════════

class AgentTokenService:
    """server-side token revoke/rotate + audit(SECRET_ADMIN). write 는 repository 동일 트랜잭션."""

    def __init__(self, repository: AgentTokenRepository) -> None:
        self._repo = repository

    @staticmethod
    def _audit(
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor_id: str | None,
        source: str | None,
        reason: str | None,
        at: datetime,
        extra: dict | None = None,
    ) -> AuditEntry:
        diff = dict(extra or {})
        return AuditEntry(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            diff_redacted=build_diff_redacted(diff),
            created_at=at,
            source=redact(source) if source else None,
            reason=redact(reason) if reason else None,
            result=AuditResult.SUCCESS.value,
        )

    async def revoke(
        self,
        agent_id: str,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Agent token 을 server-side revoke 한다 — 이후 resolver 가 None 반환(→401). audit 동반."""

        audit = self._audit(
            action=ACTION_AGENT_TOKEN_REVOKE,
            target_type=TARGET_TYPE_AGENT,
            target_id=agent_id,
            actor_id=actor_id,
            source=source,
            reason=reason,
            at=at,
        )
        await self._repo.set_revoked(agent_id, at=at, audit=audit)

    async def rotate(
        self,
        agent_id: str,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Agent token 을 rotate 한다 — 기존을 무효화(rotate 시각 기록)하고 재발급 경로를 연다.

        실 재발급은 4.x registration 흐름 재사용(서버는 무효화 + 시각 마킹만). audit 동반.
        """

        audit = self._audit(
            action=ACTION_AGENT_TOKEN_ROTATE,
            target_type=TARGET_TYPE_AGENT,
            target_id=agent_id,
            actor_id=actor_id,
            source=source,
            reason=reason,
            at=at,
        )
        await self._repo.set_rotated(agent_id, at=at, audit=audit)

    async def rotate_external_token(
        self,
        *,
        channel_id: str,
        new_secret_ref: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> str:
        """외부 service token(Telegram bot token 등)의 ``*_ref`` 바인딩을 회전·무효화한다(AC3).

        secret 은 Secrets Manager 에 있고 DB 는 ``*_ref`` 만 두므로 코드는 **ref 회전 절차 + audit**
        만 한다(평문 DB 0). ``new_secret_ref`` 가 평문 secret(token/OTP 패턴)이면 fail-closed 거부
        (:func:`looks_like_plaintext_secret`) — 평문을 응답/로그/audit 에 절대 싣지 않는다. 실제
        Secrets Manager 호출은 배포 인프라(runbook 절차) — 본 메서드는 바인딩 갱신 의도를 audit 한다.
        """

        if looks_like_plaintext_secret(new_secret_ref):
            raise ValueError("평문 secret 금지 — *_ref 핸들만 허용(fail-closed)")
        audit = self._audit(
            action=ACTION_EXTERNAL_TOKEN_ROTATE,
            target_type=TARGET_TYPE_CHANNEL,
            target_id=channel_id,
            actor_id=actor_id,
            source=source,
            reason=reason,
            at=at,
            extra={"new_secret_ref": new_secret_ref},  # *_ref 는 secret 아님(redact 보존)
        )
        await self._repo.record_audit(audit)
        return new_secret_ref
