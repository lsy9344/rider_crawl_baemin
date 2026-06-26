"""BrowserProfileManager — per-target Chrome 프로필/CDP 격리 + 대상 검증 (Story 4.5 / P3-05, FR-14·20).

이 모듈이 책임지는 것(범위 — 격리 오케스트레이션 + 검증 매핑 primitive 만):

* :class:`ProfileAssignment` — (tenant_id, target_id) 에 묶인 프로필/포트 할당. ``profile_dir``
  는 내부 보관용이며 **server 로 내보내는 표면(:meth:`BrowserProfileManager.browser_profiles`)
  에는 raw 경로를 넣지 않는다**(data-api-contract: "server stores profile id/ref, not raw
  sensitive path").
* :class:`BrowserProfileManager` — 대상마다 독립 User Data Dir + 사용 가능한 ``127.0.0.1:<port>``
  를 할당하고, 이미 할당된 포트/프로필을 다른 대상에 **재배정하지 않으며**(in-Agent 등록부),
  실제 격리 가드는 :func:`~rider_crawl.browser_launcher.prepare_chrome` 를 **재사용**한다
  (CDP-unused·profile-free·local-addr·CDP 대기 — 재구현 금지). 포트/프로필 중복·원격 CDP·
  prepare 가드 위반 **중 하나라도** 감지되면 그 대상은 시작하지 않는다(fail-closed). CDP 미응답
  은 재시작(bounded)하고 로그인 필요는 ``AUTH_REQUIRED`` 로 전이한다(무한 재시도 금지, NFR-4).
* 대상(센터/상점) 검증 매핑 — :func:`classify_target_risk` 는 ``config.coupang_center_name_risk``
  를 **그대로 호출**(재구현 금지)하고, :func:`map_target_validation_failure` 는 쿠팡 센터
  exact-match 검증(이미 ``RuntimeError`` 로 fail-closed)을 흡수해 ``CENTER_MISMATCH`` +
  ``TARGET_VALIDATION_FAILURE`` 어휘로 매핑한다(검증을 우회/재구현하지 않는다).

소유 분리(스코프 경계):

* **실제 crawl 수집/Snapshot 업로드(``CRAWL_*`` ``execute_job`` 오케스트레이션)는 본 스토리
  범위가 아니다.** manager 는 후속 crawl 워커가 주입할 **primitive** 로만 제공된다.
* **상태/오류 enum 값은 평문 문자열 상수로 반영**한다 — ``rider_server.domain.states`` 를 직접
  import 하면 단방향 가드 위반이므로 값만 베낀다(``BrowserProfileState``/``BaeminAuthState``/
  ``FailureCategory`` 값 정합). enum/"정확히 N개" lock 은 두지 않는다(``secure_store``·
  ``heartbeat`` 평문 상수 선례).

자기(own) 코드는 **순수 동기**이고 ``rider_crawl``/자기 패키지만 import 한다(역방향/
``rider_server`` import 0, ``asyncio`` 0) — 4.1 의 AST 가드가 자동 검사한다.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from rider_crawl.redaction import redact

from rider_agent.reuse import (
    BrowserActionRequiredError,
    BrowserLaunchError,
    CdpUnavailableError,
    coupang_center_name_risk,
    ensure_local_cdp_address,
    find_existing_chrome_debug_endpoint,
    prepare_chrome,
)

# ── 프로필 상태 어휘 — **평문 상수**(BrowserProfileState 값 정합), enum/"정확히 N개" lock 금지.
# 후속 상태 추가가 다른 테스트를 깨지 않게 평문 상수로 둔다(secure_store ``TOKEN_STATUS_*``·
# heartbeat ``DEFAULT_CAPABILITIES`` 선례). [Source: rider_server/domain/states.py(114-120)]
STATE_UNKNOWN = "UNKNOWN"
STATE_READY = "READY"
STATE_IN_USE = "IN_USE"
STATE_INACTIVE = "INACTIVE"

# 건강/복구 전이 어휘 — BaeminAuthState 값 정합(직접 import 는 단방향 위반이라 값만 반영).
# [Source: rider_server/domain/states.py(55·58)]
STATE_AUTH_REQUIRED = "AUTH_REQUIRED"
STATE_CENTER_MISMATCH = "CENTER_MISMATCH"

# 운영 오류 카테고리/불일치 어휘 — FailureCategory.TARGET_VALIDATION_FAILURE 값 정합.
# [Source: rider_server/domain/states.py(186·58)]
ERROR_TARGET_VALIDATION_FAILURE = "TARGET_VALIDATION_FAILURE"
MISMATCH_CENTER_MISMATCH = "CENTER_MISMATCH"

# CDP 미응답 재시작 한도(무한 재시도 금지, NFR-4) + backoff 기본값.
DEFAULT_MAX_RESTART_ATTEMPTS = 3
DEFAULT_RESTART_BACKOFF_SECONDS = 1.0
DEFAULT_BROWSER_PROCESS_CLOSE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ProfileAssignment:
    """(tenant_id, target_id) 에 묶인 프로필/포트 할당.

    ``profile_dir`` 은 내부 보관용 raw 경로다 — server 로 내보내는 투영
    (:meth:`BrowserProfileManager.browser_profiles`)에는 넣지 않는다(id/ref 만).
    """

    id: str
    tenant_id: str
    target_id: str
    agent_id: str
    profile_dir: Path
    cdp_port: int
    cdp_url: str
    state: str = STATE_UNKNOWN
    last_used_at: float = 0.0


class TargetValidationError(RuntimeError):
    """대상(센터/상점) 검증 실패 — 작업 미시작·메시지 미생성/미발송(fail-closed).

    ``error_code`` 는 운영 카테고리 ``TARGET_VALIDATION_FAILURE``(문자열 상수)이고 ``reason``
    은 :func:`~rider_crawl.redaction.redact` 통과값이다(raw 경로/secret 비노출). ``mismatch``
    는 화면 불일치면 ``CENTER_MISMATCH``, 위험 분류면 ``None``.
    """

    def __init__(self, reason: str, *, mismatch: str | None = None) -> None:
        self.error_code = ERROR_TARGET_VALIDATION_FAILURE
        self.mismatch = mismatch
        self.reason = redact(reason)
        super().__init__(self.reason)


def _profile_key(profile_dir: Path) -> str:
    """프로필 경로 정규화 키 — ``browser_launcher`` 와 **동일 정책**(case-fold + resolve).

    private ``_profile_dir_key`` 를 직접 import 하지 않고 같은 규칙을 자체 적용한다
    (우회/약화 금지). [Source: rider_crawl/browser_launcher.py(253-258)]
    """

    try:
        resolved = profile_dir.expanduser().resolve()
    except OSError:
        resolved = profile_dir
    return str(resolved).casefold()


def _profile_id(tenant_id: str, target_id: str) -> str:
    """heartbeat ``browser_profiles.id`` 용 안정 식별자(난수 없음 — 결정적)."""

    return f"{tenant_id}:{target_id}"


def _allocate_local_port() -> int:
    """stdlib ``socket`` 로 사용 가능한 ``127.0.0.1`` 포트를 얻는다(새 의존 0).

    bind(0) → getsockname → close 패턴. 경합(TOCTOU)은 ``prepare_chrome`` 의
    ``_ensure_cdp_endpoint_unused`` 가 후속 방어한다(할당 직후 사용 중이면 fail-closed).
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def classify_target_risk(platform_name: str, center_name: str) -> tuple[bool, str]:
    """쿠팡 기대 센터/상점명 위험 분류 — ``config.coupang_center_name_risk`` 를 **그대로 호출**.

    비었거나 배민 기본값이면 ``(True, reason)``. **이 분류기를 재구현하지 않는다**(reuse only).
    배민 탭은 위험으로 보지 않는다(배민 센터 규칙은 rider_crawl 소유).
    [Source: rider_crawl/config.py(300-324)]
    """

    return coupang_center_name_risk(platform_name, center_name)


def map_target_validation_failure(exc: BaseException) -> dict[str, Any]:
    """쿠팡 센터 exact-match 검증의 ``RuntimeError`` 를 운영 어휘로 매핑한다(검증 우회/재구현 0).

    쿠팡 센터 불일치는 ``platforms/coupang/crawler.py._validate_coupang_center*`` 가 이미
    ``RuntimeError`` 로 수집을 중단(=메시지 미생성)해 fail-closed 다. 본 매핑 계층은 그 예외를
    **흡수**해 ``state=CENTER_MISMATCH`` + ``error_code=TARGET_VALIDATION_FAILURE`` +
    ``mismatch=CENTER_MISMATCH`` dict 로 표면화한다.

    사유는 헤드라인(첫 줄)만 ``redact`` 통과시켜 둔다 — 검증 ``RuntimeError`` 본문 후속 줄에는
    "설정 센터명: …/화면 센터명: …" 같은 **운영 식별자(raw 센터명)** 가 들어가므로 그 줄들을
    surfacing 하지 않는다(NFR-9/ADD-15 raw 비노출). [Source: rider_crawl/platforms/coupang/
    crawler.py(50-110), operations-security-test-contract.md(11·16·87-95)]
    """

    text = str(exc).strip()
    headline = text.splitlines()[0].strip() if text else "대상 검증 실패"
    return {
        "error_code": ERROR_TARGET_VALIDATION_FAILURE,
        "mismatch": MISMATCH_CENTER_MISMATCH,
        "state": STATE_CENTER_MISMATCH,
        "reason": redact(headline),
    }


class BrowserProfileManager:
    """per-target Chrome 프로필/CDP 격리 manager + 중복 거부 + 건강/복구 (AC1·3).

    모든 외부 부작용(Chrome 실행·포트 할당·시간)을 주입 가능하게 해 테스트가 실 Chrome/실
    네트워크/실 대기 없이 결정적으로 검증한다. 등록부(``threading.Lock`` 보호)는 heartbeat
    thread 가 :meth:`browser_profiles` 로 동시 읽으므로 thread-safe 하다.
    """

    def __init__(
        self,
        *,
        profiles_root: Path,
        agent_id: str,
        prepare: Callable[..., Any] = prepare_chrome,
        allocate_port: Callable[[], int] = _allocate_local_port,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        run_command: Any = None,
        cdp_probe: Any = None,
        restart_backoff_seconds: float = DEFAULT_RESTART_BACKOFF_SECONDS,
        max_profiles: int | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._profiles_root = Path(profiles_root)
        self._agent_id = agent_id
        self._prepare = prepare
        self._allocate_port = allocate_port
        self._sleep = sleep
        self._now = now
        self._run_command = run_command
        self._cdp_probe = cdp_probe
        self._restart_backoff_seconds = restart_backoff_seconds
        self._max_profiles = max_profiles if max_profiles is None or max_profiles > 0 else None
        self._log = log
        self._lock = threading.Lock()
        #: (tenant_id, target_id) → ProfileAssignment.
        self._registry: dict[tuple[str, str], ProfileAssignment] = {}
        #: 할당된 포트/프로필-키 → 소유 대상(중복 거부용 역인덱스).
        self._ports: dict[int, tuple[str, str]] = {}
        self._profile_keys: dict[str, tuple[str, str]] = {}
        self._processes: dict[tuple[str, str], Any] = {}
        self._reservations: dict[tuple[str, str], threading.Event] = {}
        self._reserved_ports: dict[int, tuple[str, str]] = {}
        self._reserved_profile_keys: dict[str, tuple[str, str]] = {}

    # ── 프로필 경로 정책 ──────────────────────────────────────────────────────

    def _profile_dir_for(self, tenant_id: str, target_id: str) -> Path:
        """``profiles/<tenant_id>/<target_id>/`` — 대상별 독립 경로(기본 Chrome 프로필 미재사용)."""

        return self._profiles_root / str(tenant_id) / str(target_id)

    # ── per-target 할당 + 중복 거부 + prepare 재사용 ─────────────────────────

    def ensure_profile(
        self,
        tenant_id: str,
        target_id: str,
        *,
        build_config: Callable[..., Any],
    ) -> ProfileAssignment:
        """대상의 프로필/포트를 확보한다(이미 있으면 재사용, 없으면 신규 할당).

        흐름: 기존 할당 재사용(idempotent) → 포트 할당 + 경로 정책 → **중복 거부**(다른 대상의
        포트/프로필 재배정 금지) → ``build_config`` 로 대상 설정 구성 → **대상 검증**(센터 위험이면
        시작 안 함) → 원격 CDP 차단(reuse) → ``prepare_chrome`` 재사용(격리 가드). 중복/원격/가드
        위반 중 하나라도 걸리면 그 대상은 시작하지 않고 오류로 surfacing 한다(fail-closed).

        ``build_config(*, tenant_id, target_id, cdp_url, user_data_dir)`` 는 호출자가 주입하는
        ``AppConfig`` 호환 객체 팩토리다(``cdp_url``/``browser_user_data_dir``/``platform_name``/
        ``baemin_center_name``/``coupang_eats_url`` 보유).
        """

        key = (str(tenant_id), str(target_id))
        last_used_at = self._now()

        while True:
            adopted_endpoint = None
            process_to_close = None
            wait_for_reservation = None
            with self._lock:
                existing = self._registry.get(key)
                if existing is not None:
                    process = self._processes.get(key)
                    if _process_has_exited(process):
                        _assignment, process_to_close = self._release_key_locked(key)
                    else:
                        # 이미 할당된 대상은 그 할당을 재사용한다 — 다른 대상에 재배정하지 않는다(AC1.2).
                        object.__setattr__(existing, "last_used_at", last_used_at)
                        return existing
                if process_to_close is None:
                    reservation = self._reservations.get(key)
                    if reservation is not None:
                        wait_for_reservation = reservation
                    else:
                        processes_to_close = self._enforce_max_profiles_locked()
                        profile_dir = self._profile_dir_for(*key)
                        profile_key = _profile_key(profile_dir)
                        adopted_endpoint = find_existing_chrome_debug_endpoint(
                            profile_dir, cdp_probe=self._cdp_probe
                        )
                        port = (
                            int(adopted_endpoint.cdp_port)
                            if adopted_endpoint is not None
                            else int(self._allocate_port())
                        )
                        self._reserve_launch_locked(key, port, profile_key)
                        reservation = self._reservations[key]
                        break
            if process_to_close is not None:
                self._close_process(process_to_close)
                continue
            wait_for_reservation.wait()
        for process in processes_to_close:
            self._close_process(process)

        launched_processes: list[Any] = []

        try:
            cdp_url = (
                str(adopted_endpoint.cdp_url)
                if adopted_endpoint is not None
                else f"http://127.0.0.1:{port}"
            )
            config = build_config(
                tenant_id=key[0],
                target_id=key[1],
                cdp_url=cdp_url,
                user_data_dir=profile_dir,
            )

            # 대상 검증: 쿠팡 기대 센터/상점명이 비었/배민기본값(위험)이면 작업을 진행하지 않는다(AC2).
            is_risky, reason = classify_target_risk(
                getattr(config, "platform_name", ""),
                getattr(config, "baemin_center_name", ""),
            )
            if is_risky:
                raise TargetValidationError(reason)

            # 원격 CDP 차단 + 격리 가드(CDP-unused·profile-free·CDP 대기)를 prepare_chrome 으로 재사용.
            ensure_local_cdp_address(getattr(config, "cdp_url", cdp_url))

            if adopted_endpoint is None:

                def run_command(command: list[str], check: bool) -> Any:
                    runner = self._run_command or _default_run_command
                    result = runner(command, check)
                    if _is_process_handle(result):
                        launched_processes.append(result)
                    return result

                self._prepare(config, run_command=run_command, cdp_probe=self._cdp_probe)
        except Exception:
            for process in launched_processes:
                self._close_process(process)
            with self._lock:
                self._clear_reservation_locked(key, port, profile_key)
                reservation.set()
            raise

        assignment = ProfileAssignment(
            id=_profile_id(*key),
            tenant_id=key[0],
            target_id=key[1],
            agent_id=self._agent_id,
            profile_dir=profile_dir,
            cdp_port=port,
            cdp_url=cdp_url,
            state=STATE_READY,
            last_used_at=last_used_at,
        )
        with self._lock:
            self._clear_reservation_locked(key, port, profile_key)
            self._registry[key] = assignment
            self._ports[port] = key
            self._profile_keys[profile_key] = key
            process = (
                getattr(adopted_endpoint, "process", None)
                if adopted_endpoint is not None
                else (launched_processes[-1] if launched_processes else None)
            )
            if process is not None:
                self._processes[key] = process
            reservation.set()
        return assignment

    def release(self, tenant_id: str, target_id: str) -> None:
        """등록부에서 대상을 제거하고 포트/프로필 키를 회수한다(누수 없이 재할당 가능)."""

        key = (str(tenant_id), str(target_id))
        with self._lock:
            _assignment, process = self._release_key_locked(key)
        if process is not None:
            self._close_process(process)

    def cleanup_idle_profiles(self, *, max_idle_seconds: float) -> list[str]:
        """Release assignments idle longer than ``max_idle_seconds`` and return released ids."""

        now = self._now()
        released: list[str] = []
        processes: list[Any] = []
        with self._lock:
            idle_keys = [
                key
                for key, assignment in self._registry.items()
                if now - assignment.last_used_at > max_idle_seconds
            ]
            for key in idle_keys:
                assignment, process = self._release_key_locked(key)
                if assignment is not None:
                    released.append(assignment.id)
                if process is not None:
                    processes.append(process)
        for process in processes:
            self._close_process(process)
        return released

    def close_all(self) -> None:
        """Release every assignment and terminate all tracked browser processes."""

        with self._lock:
            processes = list(self._processes.values())
            reservations = list(self._reservations.values())
            self._registry.clear()
            self._ports.clear()
            self._profile_keys.clear()
            self._processes.clear()
            self._reservations.clear()
            self._reserved_ports.clear()
            self._reserved_profile_keys.clear()
        for reservation in reservations:
            reservation.set()
        for process in processes:
            self._close_process(process)

    # ── 건강 점검 / 복구(재시작 bounded + AUTH_REQUIRED 전이) ─────────────────

    def check_health(self, tenant_id: str, target_id: str) -> str:
        """주입 ``cdp_probe`` 로 CDP 응답을 확인해 상태(``READY``/``UNKNOWN``)를 보고한다.

        등록부에 없으면 ``UNKNOWN``. probe 미주입이면 현재 상태를 그대로 돌려준다(주입 없는
        결정적 테스트 허용).
        """

        key = (str(tenant_id), str(target_id))
        with self._lock:
            assignment = self._registry.get(key)
        if assignment is None:
            return STATE_UNKNOWN
        if self._cdp_probe is None:
            return assignment.state
        try:
            self._cdp_probe(assignment.cdp_url)
        except Exception:  # noqa: BLE001 — 어떤 probe 실패든 미응답(UNKNOWN)으로 본다.
            return self._set_state(key, STATE_UNKNOWN)
        return self._set_state(key, STATE_READY)

    def recover_profile(
        self,
        tenant_id: str,
        target_id: str,
        *,
        build_config: Callable[..., Any],
        max_attempts: int = DEFAULT_MAX_RESTART_ATTEMPTS,
    ) -> ProfileAssignment:
        """CDP 미응답 시 재시작(재 ``prepare``)하되 무한 재시도하지 않는다(NFR-4).

        ``CdpUnavailableError`` 는 흡수해 backoff(주입 ``sleep``) 후 재시도하고, 한도
        (``max_attempts``)를 넘으면 마지막 오류를 올린다(bounded). ``BrowserActionRequiredError``
        (로그인 필요)는 ``AUTH_REQUIRED`` 로 **전이하고 즉시 멈춘다**(재시도하지 않는다).
        """

        key = (str(tenant_id), str(target_id))
        with self._lock:
            assignment = self._registry.get(key)
        if assignment is None:
            raise BrowserLaunchError("복구 대상 프로필이 등록부에 없습니다.")

        config = build_config(
            tenant_id=key[0],
            target_id=key[1],
            cdp_url=assignment.cdp_url,
            user_data_dir=assignment.profile_dir,
        )

        attempts = 0
        last_error: BaseException | None = None
        while attempts < max_attempts:
            attempts += 1
            launched_processes: list[Any] = []

            def run_command(command: list[str], check: bool) -> Any:
                runner = self._run_command or _default_run_command
                result = runner(command, check)
                if _is_process_handle(result):
                    launched_processes.append(result)
                return result

            try:
                self._prepare(
                    config, run_command=run_command, cdp_probe=self._cdp_probe
                )
            except BrowserActionRequiredError:
                for process in launched_processes:
                    self._close_process(process)
                # 로그인 필요 → AUTH_REQUIRED 전이(무한 재시도 금지).
                self._set_state_obj(key, STATE_AUTH_REQUIRED)
                with self._lock:
                    return self._registry[key]
            except CdpUnavailableError as exc:
                for process in launched_processes:
                    self._close_process(process)
                last_error = exc
                if attempts < max_attempts:
                    self._sleep(self._restart_backoff_seconds * attempts)
                continue
            else:
                self._set_state_obj(key, STATE_READY)
                process = launched_processes[-1] if launched_processes else None
                old_process = None
                if process is not None:
                    with self._lock:
                        old_process = self._processes.get(key)
                        self._processes[key] = process
                if old_process is not None and old_process is not process:
                    self._close_process(old_process)
                with self._lock:
                    return self._registry[key]

        # 재시작 한도 소진 — 무한 재시도하지 않고 마지막 오류를 surfacing 한다.
        self._set_state_obj(key, STATE_UNKNOWN)
        raise CdpUnavailableError(
            "CDP 복구 실패: 재시작 한도를 초과했습니다(무한 재시도 금지)."
        ) from last_error

    # ── heartbeat provider(id/ref 만 — raw 경로 비노출) ───────────────────────

    def browser_profiles(self) -> list[dict[str, Any]]:
        """현재 등록부를 heartbeat ``browser_profiles`` 표면으로 투영한다(thread-safe 스냅샷).

        각 항목은 ``id``/``target_id``/``agent_id``/``cdp_port``/``state`` 만 — **raw
        ``profile_dir``/secret 은 넣지 않는다**(server stores profile id/ref, not raw path).
        [Source: data-api-contract.md(29·67-69), operations-security-test-contract.md(11·16)]
        """

        with self._lock:
            return [
                {
                    "id": assignment.id,
                    "target_id": assignment.target_id,
                    "agent_id": assignment.agent_id,
                    "cdp_port": assignment.cdp_port,
                    "state": assignment.state,
                }
                for assignment in self._registry.values()
            ]

    # ── 내부 상태 전이(thread-safe) ───────────────────────────────────────────

    def _set_state(self, key: tuple[str, str], state: str) -> str:
        self._set_state_obj(key, state)
        return state

    def _set_state_obj(self, key: tuple[str, str], state: str) -> None:
        with self._lock:
            assignment = self._registry.get(key)
            if assignment is None:
                return
            self._registry[key] = replace(assignment, state=state)

    def _release_key_locked(self, key: tuple[str, str]) -> tuple[ProfileAssignment | None, Any | None]:
        assignment = self._registry.pop(key, None)
        if assignment is None:
            return None, None
        self._ports.pop(assignment.cdp_port, None)
        self._profile_keys.pop(_profile_key(assignment.profile_dir), None)
        process = self._processes.pop(key, None)
        return assignment, process

    def _enforce_max_profiles_locked(self) -> list[Any]:
        processes: list[Any] = []
        if self._max_profiles is None:
            return processes
        while self._reserved_profile_count_locked() >= self._max_profiles and self._registry:
            oldest_key = min(
                self._registry,
                key=lambda key: self._registry[key].last_used_at,
            )
            _assignment, process = self._release_key_locked(oldest_key)
            if process is not None:
                processes.append(process)
        if self._reserved_profile_count_locked() >= self._max_profiles:
            raise BrowserLaunchError(
                "프로필 최대 개수 초과: 예약된 프로필이 한도에 도달해 이 대상은 시작하지 않습니다."
            )
        return processes

    def _reserved_profile_count_locked(self) -> int:
        return len(self._registry) + len(self._reservations)

    def _reserve_launch_locked(
        self, key: tuple[str, str], port: int, profile_key: str
    ) -> None:
        owner = self._ports.get(port) or self._reserved_ports.get(port)
        if owner is not None and owner != key:
            raise BrowserLaunchError(
                "CDP 포트 중복: 이미 다른 대상에 할당된 포트라 이 대상은 시작하지 않습니다."
            )
        owner = self._profile_keys.get(profile_key) or self._reserved_profile_keys.get(
            profile_key
        )
        if owner is not None and owner != key:
            raise BrowserLaunchError(
                "프로필 중복: 이미 다른 대상에 할당된 프로필이라 이 대상은 시작하지 않습니다."
            )
        self._reservations[key] = threading.Event()
        self._reserved_ports[port] = key
        self._reserved_profile_keys[profile_key] = key

    def _clear_reservation_locked(
        self, key: tuple[str, str], port: int, profile_key: str
    ) -> None:
        self._reservations.pop(key, None)
        if self._reserved_ports.get(port) == key:
            self._reserved_ports.pop(port, None)
        if self._reserved_profile_keys.get(profile_key) == key:
            self._reserved_profile_keys.pop(profile_key, None)

    def _close_process(self, process: Any) -> None:
        try:
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                return
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(process, "wait", None)
            if callable(wait):
                wait(timeout=DEFAULT_BROWSER_PROCESS_CLOSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
        except Exception:  # noqa: BLE001 - cleanup must not break agent loop.
            if self._log is not None:
                self._log(redact("browser process cleanup failed"))

    def _kill_process(self, process: Any) -> None:
        try:
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
            wait = getattr(process, "wait", None)
            if callable(wait):
                wait(timeout=DEFAULT_BROWSER_PROCESS_CLOSE_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - cleanup must not break agent loop.
            if self._log is not None:
                self._log(redact("browser process kill failed"))


def _default_run_command(command: list[str], check: bool) -> Any:
    if check:
        return subprocess.run(command, check=True)
    return subprocess.Popen(command)


def _is_process_handle(value: Any) -> bool:
    return any(callable(getattr(value, name, None)) for name in ("terminate", "kill", "poll"))


def _process_has_exited(value: Any) -> bool:
    poll = getattr(value, "poll", None)
    if not callable(poll):
        return False
    try:
        return poll() is not None
    except Exception:
        return False
