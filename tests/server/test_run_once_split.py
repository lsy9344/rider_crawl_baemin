"""Story 3.1 / AC1~AC3 (P2-01, FR-7·FR-8·FR-9, NFR-20·FR-2) — run_once 3-분리 잠금.

``run_once`` 를 ``CrawlService``/``MessageRenderService``/``DispatchService`` 로 분리한
구조를 단언한다: (1) 각 서비스 독립 호출 + 주입 가능 fake, (2) 같은 입력 합성이
``run_once`` 의 ``message``/``sent``/``message_hash`` 를 재현(parity), (3) 호환 경로·
의존성 방향(역방향 import 0), (4) crawl 예외 시 render/dispatch 미진입(FR-7).

외부 호출 없음 — fake/monkeypatch·가짜 값만. fake sender는 메모리 리스트로
메시지를 수집한다(실제 Telegram/Kakao 미호출). 평문 secret/식별자 금지.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from rider_crawl import messengers, platforms
from rider_crawl.app import run_once
from rider_crawl.config import AppConfig
from rider_crawl.models import (
    CurrentScreenSnapshot,
    PeakDashboardSnapshot,
    PeakPeriodSnapshot,
    PerformanceSnapshot,
)
from rider_server.services import (
    CrawlService,
    DispatchResult,
    DispatchService,
    MessageRenderService,
)


# ── fixture: test_app.py 의 _config/_snapshot/_performance_snapshot 동등 헬퍼 ──
# (평면 tests/server/ 컨벤션 — conftest 공유 없이 자급자족. 가짜 값만 사용.)


def _config(
    tmp_path,
    *,
    crawl_name: str = "",
    send_enabled: bool = False,
    send_only_on_change: bool = False,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    telegram_message_thread_id: str = "",
    platform_name: str = "baemin",
    baemin_center_name: str = "",
) -> AppConfig:
    return AppConfig(
        coupang_eats_url="https://partner.coupangeats.com/page/rider-performance",
        platform_name=platform_name,
        baemin_center_name=baemin_center_name,
        baemin_center_id="",
        browser_mode="cdp",
        cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path / "browser-profile",
        headless=False,
        kakao_chat_name="",
        log_dir=tmp_path / "logs",
        send_enabled=send_enabled,
        send_only_on_change=send_only_on_change,
        timezone="Asia/Seoul",
        run_lock_timeout_seconds=900,
        page_timeout_seconds=60000,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        telegram_message_thread_id=telegram_message_thread_id,
        crawl_name=crawl_name,
    )


def _snapshot() -> CurrentScreenSnapshot:
    return CurrentScreenSnapshot(
        center_name="제이앤에이치플러스 의정부남부",
        date_label="5월 21일(오늘)",
        shift_label="오후논피크",
        shift_time_range="13:00~16:55",
        shift_status="할당량 소진 중",
        updated_at="14:02",
        available_current=7,
        available_total=25,
        waiting_count=0,
        online_riders=7,
        rejected_ignored_count=2.4,
        cancelled_count=0,
        completed_count=102.4,
        sequence_violation_count=0,
        lunch_peak_count=60.6,
        afternoon_non_peak_count=41.8,
        dinner_peak_count=0,
        dinner_non_peak_count=0,
        non_peak_count=41.8,
        active_riders=5,
    )


def _performance_snapshot() -> PerformanceSnapshot:
    return PerformanceSnapshot(
        current_screen=_snapshot(),
        peak_dashboard=PeakDashboardSnapshot(
            updated_at="20:38",
            assigned_count=103,
            processed_count=67,
            reject_rate=6.5,
            morning=PeakPeriodSnapshot(done=9, total=9),
            lunch_peak=PeakPeriodSnapshot(done=45, total=45),
            lunch_non_peak=PeakPeriodSnapshot(done=10, total=19),
            dinner_peak=PeakPeriodSnapshot(done=17, total=39),
            dinner_non_peak=PeakPeriodSnapshot(done=2, total=27),
        ),
    )


def _source_label(config: AppConfig) -> str:
    # run_once(app.py 39)와 동일 derive 규칙. 호출부 책임(서비스는 인자로만 받음).
    return config.baemin_center_name.strip() or config.crawl_name


# ── AC1·AC2 — 각 서비스 독립 호출 + 주입 가능 fake ─────────────────────────


def test_crawl_service_returns_injected_snapshot(tmp_path):
    config = _config(tmp_path)
    snap = _snapshot()
    calls: list[AppConfig] = []

    def fake_crawl(c: AppConfig) -> CurrentScreenSnapshot:
        calls.append(c)
        return snap

    result = CrawlService.crawl(config, crawl_snapshot=fake_crawl)

    assert result is snap
    assert calls == [config]


def test_render_service_reuses_existing_renderer(tmp_path):
    message = MessageRenderService.render(_snapshot(), source_label="센터")

    assert "[택트런 실적봇]" in message
    assert "[센터]" in message
    assert "오전오후피크 : 60.6건" in message


def test_render_service_default_source_label_is_blank():
    # source_label 미지정 시 라벨 줄이 붙지 않는다(서비스는 config-bound 아님).
    message = MessageRenderService.render(_snapshot())

    assert "[택트런 실적봇]" in message
    assert "[센터]" not in message


def test_dispatch_service_sends_when_enabled(tmp_path):
    config = _config(tmp_path, send_enabled=True)
    sent: list[str] = []

    result = DispatchService.dispatch(
        config, "메시지", send_message=lambda _c, m: sent.append(m)
    )

    assert isinstance(result, DispatchResult)
    assert result.sent is True
    assert result.skipped is False  # 3.1은 dedup 미수행 — 항상 False(Story 3.5가 채움)
    assert sent == ["메시지"]


def test_dispatch_service_dry_run_does_not_send(tmp_path):
    config = _config(tmp_path, send_enabled=False)
    sent: list[str] = []

    result = DispatchService.dispatch(
        config, "메시지", send_message=lambda _c, m: sent.append(m)
    )

    assert result.sent is False
    assert result.skipped is False
    assert sent == []


def test_dispatch_result_is_frozen(tmp_path):
    config = _config(tmp_path, send_enabled=False)
    result = DispatchService.dispatch(config, "메시지", send_message=lambda _c, _m: None)

    with pytest.raises(Exception):
        result.sent = True  # type: ignore[misc]  # frozen dataclass


# ── AC1.2 — 기본(미주입) adapter가 run_once와 동일 경로에 위임 ────────────────
# (주입 fake 경로만 테스트하면 정작 parity를 보장하는 기본 adapter 코드가
#  미커버다. 여기서 platforms/messengers registry 위임을 monkeypatch로 잠근다.)


def test_crawl_default_adapter_delegates_to_platform_registry(tmp_path, monkeypatch):
    config = _config(tmp_path, platform_name="coupang")
    snap = _performance_snapshot()
    calls: list[dict] = []

    def fake_registry(c, *, platform_name=None):
        calls.append({"config": c, "platform_name": platform_name})
        return snap

    monkeypatch.setattr(platforms, "crawl_snapshot", fake_registry)

    # fake 미주입 → 기본 adapter(_default_crawl_snapshot)가 호출돼야 한다.
    result = CrawlService.crawl(config)

    assert result is snap
    # run_once._crawl_snapshot(app.py 131-134)와 동일하게 platform_name을 넘긴다.
    assert calls == [{"config": config, "platform_name": "coupang"}]


def test_dispatch_default_adapter_delegates_to_messenger_registry(tmp_path, monkeypatch):
    config = _config(tmp_path, send_enabled=True)
    sent: list[tuple] = []

    monkeypatch.setattr(
        messengers, "dispatch_text_message", lambda c, m: sent.append((c, m))
    )

    # fake 미주입 + send_enabled → 기본 adapter(_default_send_message) 위임.
    result = DispatchService.dispatch(config, "메시지")

    assert result.sent is True
    assert sent == [(config, "메시지")]  # run_once._send_message(app.py 137-140)와 동일 경로


def test_dispatch_default_adapter_not_called_in_dry_run(tmp_path, monkeypatch):
    config = _config(tmp_path, send_enabled=False)
    sent: list[tuple] = []

    monkeypatch.setattr(
        messengers, "dispatch_text_message", lambda c, m: sent.append((c, m))
    )

    # dry-run(send_enabled=False)이면 기본 adapter가 호출되지 않는다(게이팅 우선).
    result = DispatchService.dispatch(config, "메시지")

    assert result.sent is False
    assert sent == []


# ── AC1 — message_hash 가 run_once 와 동일한 sha256(message) ──────────────────


@pytest.mark.parametrize("send_enabled", [False, True])
def test_dispatch_message_hash_is_sha256_of_message(tmp_path, send_enabled):
    config = _config(tmp_path, send_enabled=send_enabled)
    message = "실적 메시지 본문"
    expected = hashlib.sha256(message.encode("utf-8")).hexdigest()

    result = DispatchService.dispatch(config, message, send_message=lambda _c, _m: None)

    # run_once(app.py 41)와 동일 계산 — 전송/dry-run 분기와 무관하게 동일.
    assert result.message_hash == expected
    assert result.message == message


# ── AC3 (FR-7) — crawl 예외가 render/dispatch로 이어지지 않음 ────────────────


class _CrawlBoom(Exception):
    pass


def test_crawl_service_propagates_adapter_exception(tmp_path):
    config = _config(tmp_path)

    def boom(_c: AppConfig):
        raise _CrawlBoom("crawl failed")

    # 서비스는 예외를 삼키지 않고 그대로 전파한다(빈/기본 Snapshot 미생성).
    with pytest.raises(_CrawlBoom):
        CrawlService.crawl(config, crawl_snapshot=boom)


def test_crawl_failure_does_not_reach_render_or_dispatch(tmp_path):
    config = _config(tmp_path, send_enabled=True)
    render_calls: list[str] = []
    dispatch_calls: list[str] = []

    def boom(_c: AppConfig):
        raise _CrawlBoom("crawl failed")

    # crawl 예외가 전파되면 호출부는 render/dispatch에 진입하지 않는다(FR-7).
    with pytest.raises(_CrawlBoom):
        snapshot = CrawlService.crawl(config, crawl_snapshot=boom)
        render_calls.append(MessageRenderService.render(snapshot))
        dispatch_calls.append(
            DispatchService.dispatch(
                config, "x", send_message=lambda _c, m: dispatch_calls.append(m)
            ).message
        )

    assert render_calls == []
    assert dispatch_calls == []


class _RenderBoom(Exception):
    pass


def test_render_failure_does_not_reach_dispatch(tmp_path, monkeypatch):
    # FR-7 구조 불변식을 render→dispatch 경계로 확장: render 예외 시 dispatch 미진입.
    config = _config(tmp_path, send_enabled=True)
    dispatch_calls: list[str] = []

    def boom(_snapshot, *, source_label=""):
        raise _RenderBoom("render failed")

    monkeypatch.setattr(
        "rider_server.services.message_render_service.render_current_screen_message",
        boom,
    )

    with pytest.raises(_RenderBoom):
        message = MessageRenderService.render(_snapshot(), source_label="센터")
        dispatch_calls.append(
            DispatchService.dispatch(
                config, message, send_message=lambda _c, m: dispatch_calls.append(m)
            ).message
        )

    assert dispatch_calls == []


def test_dispatch_sender_failure_propagates(tmp_path):
    # 전송 단계도 예외를 삼키지 않는다 — sender 실패가 전파되고 성공 결과를 날조하지 않는다.
    config = _config(tmp_path, send_enabled=True)

    class _SendBoom(Exception):
        pass

    def boom(_c, _m):
        raise _SendBoom("send failed")

    with pytest.raises(_SendBoom):
        DispatchService.dispatch(config, "메시지", send_message=boom)


# ── AC1 — run_once parity(핵심): 합성 결과가 run_once를 재현 ─────────────────


@pytest.mark.parametrize("snapshot_factory", [_snapshot, _performance_snapshot])
@pytest.mark.parametrize("send_enabled", [False, True])
def test_split_services_reproduce_run_once_result(tmp_path, snapshot_factory, send_enabled):
    # send_only_on_change=False → run_once의 dedup 분기가 관여하지 않는 경로에서
    # message/sent/message_hash 동일성을 잠근다(dedup 경로는 무변경 test_app.py 소유).
    config = _config(
        tmp_path,
        crawl_name="크롤링1",
        baemin_center_name="표준서울마포",
        send_enabled=send_enabled,
        send_only_on_change=False,
    )
    snap = snapshot_factory()

    run_sent: list[str] = []
    run_result = run_once(
        config,
        crawl_snapshot=lambda _c: snap,
        send_message=lambda _c, m: run_sent.append(m),
    )

    split_sent: list[str] = []
    crawled = CrawlService.crawl(config, crawl_snapshot=lambda _c: snap)
    message = MessageRenderService.render(crawled, source_label=_source_label(config))
    split_result = DispatchService.dispatch(
        config, message, send_message=lambda _c, m: split_sent.append(m)
    )

    assert split_result.message == run_result.message
    assert split_result.sent == run_result.sent
    assert split_result.skipped == run_result.skipped  # dedup 비관여 경로 → 둘 다 False
    assert split_result.message_hash == run_result.message_hash
    assert split_sent == run_sent


def test_split_parity_source_label_falls_back_to_crawl_name(tmp_path):
    # source_label derive의 다른 분기(baemin_center_name 빈 값 → crawl_name)도
    # run_once와 동일하게 합성돼야 한다(기존 parity는 center_name 경로만 잠금).
    config = _config(
        tmp_path,
        crawl_name="크롤링7",
        baemin_center_name="",  # 빈 값 → fallback 분기
        send_enabled=True,
        send_only_on_change=False,
    )
    snap = _snapshot()

    run_sent: list[str] = []
    run_result = run_once(
        config,
        crawl_snapshot=lambda _c: snap,
        send_message=lambda _c, m: run_sent.append(m),
    )

    split_sent: list[str] = []
    crawled = CrawlService.crawl(config, crawl_snapshot=lambda _c: snap)
    message = MessageRenderService.render(crawled, source_label=_source_label(config))
    split_result = DispatchService.dispatch(
        config, message, send_message=lambda _c, m: split_sent.append(m)
    )

    assert "[크롤링7]" in split_result.message  # crawl_name 라벨로 렌더됐는지 확인
    assert split_result.message == run_result.message
    assert split_result.message_hash == run_result.message_hash
    assert split_sent == run_sent


# ── AC2 — 의존성 방향: rider_crawl 는 rider_server 를 import하지 않는다 ───────


def _imports_rider_server(source: str) -> bool:
    # 단순 문자열 매칭이 아니라 실제 import 엣지만 본다(docstring/주석의 언급은 무시).
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "rider_server" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "rider_server":
                return True
    return False


def test_rider_crawl_never_imports_rider_server():
    import rider_crawl

    pkg_dir = Path(rider_crawl.__file__).resolve().parent
    offenders = [
        py.relative_to(pkg_dir).as_posix()
        for py in pkg_dir.rglob("*.py")
        if _imports_rider_server(py.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"rider_crawl 는 rider_server 를 import하면 안 된다(단방향): {offenders}"


# ── Task 5 — services 재노출이 additive(3 서비스 추가 + 2.6 심볼 보존) ────────


def test_services_reexport_is_additive():
    import rider_server.services as services

    # 3.1이 추가한 신규 심볼 — 패키지 루트에서 바로 import 가능해야 한다(AC1).
    for name in ("CrawlService", "MessageRenderService", "DispatchService", "DispatchResult"):
        assert hasattr(services, name), f"신규 서비스 심볼 누락: {name}"
        assert name in services.__all__, f"__all__ 재노출 누락: {name}"

    # 2.6 SubscriptionGate 규약 심볼은 그대로 유지(무삭제 — additive).
    for name in ("SubscriptionGate", "GateDecision", "SubscriptionStateChange"):
        assert hasattr(services, name), f"2.6 심볼이 사라졌다(회귀): {name}"
        assert name in services.__all__
