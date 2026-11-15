"""모니터 끄는 시간 옵션 보강 테스트 — 경계값, 취소 경로, 일시정지 상태, 툴팁 루프.

test_monitor_schedule.py 의 가짜 Timer·헬퍼를 그대로 쓴다.
"""

import tkinter as tk
from datetime import datetime, timedelta
from typing import Any
from unittest import mock

import pytest

from test_monitor_off import (  # noqa: F401  (appdata 는 autouse 픽스처)
    FakeDdc,
    _install_run,
    appdata,
    ddc,
    ss,
)
from test_monitor_schedule import (
    _Timer,
    _bare_dialog,
    _due_timers,
    _monitor_timers,
    _sched_app,
)
from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    CONFIRM_TOAST_DURATION_MS,
    MONITOR_LEAD_MAX_MIN,
    SETTINGS_KEY_MONITOR_OFF,
)

RANGE_ERR = f"0~{MONITOR_LEAD_MAX_MIN} 사이로 입력하세요."
SHORT_ERR = "종료까지 남은 시간보다 짧게 입력하세요."


def _tooltip_timers(app: Any) -> list[_Timer]:
    return [t for t in _Timer.created if t.function == app._update_tooltip_loop]


# P-1. 일시정지 중 새 예약 → 일시정지가 풀리고 linked 끄기가 실제로 동작함
def test_new_schedule_while_paused_clears_pause(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.pause_shutdown()
    assert app.is_paused is True and app._pause_remaining > 0

    app.schedule_action(ACTION_SHUTDOWN, 1200, 300)

    assert app.is_paused is False
    assert app._pause_remaining == 0.0
    live = [t for t in _due_timers("linked") if not t.cancelled]
    assert [t.interval for t in live] == [900]
    live[0].fire()
    app._process_queue()
    assert len(ddc.sets()) == 4
    assert app._linked_done is True


# P-2. 일시정지 중 새 예약 뒤 툴팁은 일시정지 문구가 아니라 진행 중 문구
def test_new_schedule_while_paused_tooltip_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _sched_app(monkeypatch)
    app.tray_icon = mock.MagicMock()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.pause_shutdown()
    app.schedule_action(ACTION_SHUTDOWN, 1200)

    assert app.tray_icon.title.startswith("종료 예약 중 - 남은 시간: ")


# I-1. 즉시 실행(0초) + N=0 → Timer 없이 after 로 실행, 종료 직전 끄기
def test_immediate_with_lead_zero(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    """종료 직전 끄기가 실행되는지 확인한다.

    종료 전 진단 읽기 추가로 "enum" 과 "run" 사이에 읽기 2건이 들어간다.
    (예전 기대값은 ddc.events[:2] == ["enum", "run"] 이었음)
    """
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 0, 0)

    assert app._monitor_lead_sec == 0
    assert _Timer.created == []
    app._tk_root.after.assert_called_once_with(
        CONFIRM_TOAST_DURATION_MS, app._execute_action
    )
    assert app._show_confirm_toast.call_args.args[0] == (
        "시스템을 즉시 종료합니다.\n모니터는 종료 직전에 끕니다."
    )

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()
    assert ddc.events[:4] == ["enum", ("read", 1001, 0x10), ("read", 1002, 0x10), "run"]
    assert len(ddc.sets()) == 4


# I-2. 즉시 실행 + 검증을 거치지 않은 N>0 → 0 으로 낮춤 / 재시작은 None
@pytest.mark.parametrize(
    "action, lead, expected",
    [
        (ACTION_SHUTDOWN, 600, 0),
        (ACTION_RESTART, 0, None),
        (ACTION_SHUTDOWN, None, None),
    ],
)
def test_immediate_lead_variants(
    monkeypatch: pytest.MonkeyPatch, action: str, lead: Any, expected: Any
) -> None:
    app = _sched_app(monkeypatch, action)
    app.schedule_action(action, 0, lead)
    assert app._monitor_lead_sec == expected
    assert _monitor_timers() == []


# I-3. 예약 창: 0초 + 스위치 켬 + N=0 은 막지 않고 즉시 확인창으로, 넘길 lead 는 0
def test_dialog_immediate_with_lead_zero() -> None:
    dlg = _bare_dialog()
    dlg._total_seconds = 0
    dlg._monitor_lead_min = 0
    dlg._on_confirm()

    dlg._show_immediate_confirm.assert_called_once_with()
    dlg.callback.assert_not_called()
    assert dlg._selected_monitor_lead_sec() == 0


# S-1. 단독 0초 예약 → 3초 안의 Alt+S("cancel") 로 취소되어 끄지 않음
def test_solo_zero_cancel_before_fire(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(0)
    timer = _due_timers("solo")[0]

    app._queue.put("cancel")
    app._process_queue()
    timer.fire()
    app._process_queue()

    assert timer.cancelled is True
    assert app._solo_monitor.armed is False
    assert ddc.events == []
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_cancel"


# S-2. 단독 0초 — 발화가 먼저 큐에 들어간 뒤 Alt+S 가 오면 같은 폴링에서 끔 (순서 그대로)
#      취소가 다음 폴링 전에 메인 스레드에서 처리되면 세대가 달라 버림
def test_solo_zero_cancel_after_fire(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(0)
    _due_timers("solo")[0].fire()

    app._cancel_all()  # 다음 폴링 전 트레이·배너 취소
    app._process_queue()

    assert ddc.events == []
    assert app._solo_monitor.armed is False


def _dialog_for(app: Any) -> Any:
    dlg = _bare_dialog()
    dlg._scheduler = app
    dlg._tick_countdown = mock.MagicMock()
    return dlg


# B-1. (a)·(b) 공존 시 배너 ■ 는 종료(딸린 a 포함)만 취소, 두 번째 ■ 가 단독 취소
def test_banner_stop_cancels_shutdown_first(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.schedule_monitor_only(300)
    dlg = _dialog_for(app)

    dlg._on_cancel_schedule()

    assert app.is_active is False
    assert app._linked_monitor.armed is False
    assert app._monitor_lead_sec is None
    assert app._solo_monitor.armed is True
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "cancel"
    dlg._tick_countdown.assert_called_once_with()

    solo = [t for t in _due_timers("solo") if not t.cancelled]
    assert len(solo) == 1
    dlg._on_cancel_schedule()
    assert app._solo_monitor.armed is False
    assert solo[0].cancelled is True
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_cancel"
    solo[0].fire()
    app._process_queue()
    assert ddc.events == []


# B-2. 예약이 하나도 없을 때 ■ 는 아무것도 부르지 않고 배너만 갱신
def test_banner_stop_with_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    dlg = _dialog_for(app)
    dlg._on_cancel_schedule()
    app._show_confirm_toast.assert_not_called()
    dlg._tick_countdown.assert_called_once_with()


# T-1. 일시정지 중에도 툴팁 루프가 이어지고 일시정지 문구를 유지
def test_tooltip_loop_continues_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.tray_icon = mock.MagicMock()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.pause_shutdown()

    live = [t for t in _tooltip_timers(app) if not t.cancelled]
    assert len(live) == 1
    assert app.tray_icon.title.startswith("종료 일시정지 - 남은 시간: ")
    assert app.tray_icon.title.endswith("\n모니터: 종료 10분 전")

    live[0].fire()
    live = [t for t in _tooltip_timers(app) if not t.cancelled]
    assert len(live) == 1
    assert app.tray_icon.title.startswith("종료 일시정지 - 남은 시간: ")


# T-2. 일시정지가 예약 시각을 넘겨 길어져도 툴팁 루프가 예약을 비활성으로 돌리지 않음
def test_tooltip_loop_keeps_active_when_paused_past_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _sched_app(monkeypatch)
    app.tray_icon = mock.MagicMock()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.pause_shutdown()
    app.scheduled_time = datetime.now() - timedelta(seconds=10)

    app._update_tooltip_loop()

    assert app.is_active is True
    assert app.scheduled_time is not None
    app.resume_shutdown()
    live = [t for t in _due_timers("linked") if not t.cancelled]
    assert len(live) == 1


# T-3. (나) 위험 재현 — 옛 루프 Timer 가 고아로 남아도 다음 1회 발화에서 하나로 합쳐짐
def test_tooltip_orphan_merges_on_next_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.tray_icon = mock.MagicMock()
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    orphan = [t for t in _tooltip_timers(app) if not t.cancelled][0]
    # 메인 스레드가 참조를 바꾸는 사이 옛 Timer 가 취소되지 못한 상황을 흉내 냄
    replacement = ss.threading.Timer(1.0, app._update_tooltip_loop)
    app._tooltip_timer = replacement

    orphan.fire()

    live = [t for t in _tooltip_timers(app) if not t.cancelled and t is not orphan]
    assert replacement.cancelled is True
    assert len(live) == 1


# (가) 위험 재현 — 실행 Timer 발화 뒤 50ms 안에 취소하면 종료는 진행되고 직전 끄기는 빠짐
def test_cancel_after_execute_fired_skips_monitor(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 0)
    exec_timer = [t for t in _Timer.created if not t.function.__defaults__][0]
    exec_timer.fire()
    calls = _install_run(monkeypatch, ddc, returncode=0)

    app.cancel_shutdown()
    app._process_queue()

    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert ddc.events == ["run"]


# E. _monitor_plan_error 경계값
@pytest.mark.parametrize(
    "lead_min, total, expected",
    [
        (0, 0, None),
        (0, 1, None),
        (0, 10**7, None),
        (1, 0, None),
        (1, 60, SHORT_ERR),
        (1, 61, None),
        (MONITOR_LEAD_MAX_MIN, MONITOR_LEAD_MAX_MIN * 60, SHORT_ERR),
        (MONITOR_LEAD_MAX_MIN, MONITOR_LEAD_MAX_MIN * 60 + 1, None),
        (MONITOR_LEAD_MAX_MIN + 1, 10**7, RANGE_ERR),
        (MONITOR_LEAD_MAX_MIN + 1, 0, RANGE_ERR),
        (-1, 10**7, RANGE_ERR),
    ],
)
def test_monitor_plan_error_boundaries(
    lead_min: int, total: int, expected: Any
) -> None:
    """_monitor_plan_error 경계값을 확인한다.

    PM 결정: 0초는 시안대로 막지 않음. 설계안 p05 경고 조건(남은 초 > 0)에 맞춰
    (N=1, 0초) 는 None 이다. (예전 기대값은 SHORT_ERR 였음) 범위 밖 N 은 0초여도 막는다.
    """
    assert MONITOR_LEAD_MAX_MIN == 999
    error = ss.ScheduleDialog._monitor_plan_error(
        ACTION_SHUTDOWN, True, lead_min, total
    )
    assert error == expected


# E-2. 스위치 꺼짐·재시작·모니터 동작이면 범위 밖 N 도 막지 않음
@pytest.mark.parametrize(
    "action, on",
    [(ACTION_SHUTDOWN, False), (ACTION_RESTART, True), (ACTION_MONITOR_OFF, True)],
)
def test_monitor_plan_error_not_applicable(action: str, on: bool) -> None:
    for lead_min, total in [(1000, 0), (5, 300), (-1, 0)]:
        assert (
            ss.ScheduleDialog._monitor_plan_error(action, on, lead_min, total) is None
        )


# E-3. N=999 · 충분한 시간 → 59940초로 넘어가고 linked 는 total-59940 에 걸림
def test_max_lead_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    dlg = _bare_dialog()
    dlg._monitor_lead_min = MONITOR_LEAD_MAX_MIN
    dlg._total_seconds = MONITOR_LEAD_MAX_MIN * 60 + 60
    dlg._on_confirm()
    action, h, m, s, lead = dlg.callback.call_args.args
    assert (action, lead) == (ACTION_SHUTDOWN, 59940)

    app = _sched_app(monkeypatch)
    app._on_dialog_confirm(action, h, m, s, lead)
    assert [t.interval for t in _due_timers("linked")] == [60]
    assert app._monitor_lead_sec == 59940


# D. 트레이 토글은 다음에 여는 예약 창 스위치 기본값만 바꿈 (실제 tk, 화면 없으면 건너뜀)
def test_toggle_changes_next_dialog_default(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("화면(DISPLAY)이 없어 tk 창을 만들 수 없음")
    root.withdraw()
    monkeypatch.setattr(ss, "_bring_to_front", lambda window: None)
    app = _sched_app(monkeypatch)
    app._settings = {SETTINGS_KEY_MONITOR_OFF: False}
    app._active_dialog = None
    app._tk_root = root
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    try:
        app._open_schedule_dialog()
        assert app._active_dialog._monitor_on.get() is False
        app._active_dialog.top.destroy()
        app._active_dialog = None

        app._toggle_monitor_off()
        assert app._monitor_lead_sec is None

        app._open_schedule_dialog()
        assert app._active_dialog._monitor_on.get() is True
        app._active_dialog.top.destroy()
    finally:
        root.destroy()
