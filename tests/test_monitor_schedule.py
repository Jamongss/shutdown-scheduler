"""모니터 끄는 시간 옵션 테스트 — 종료 N분 전 끄기(linked)·모니터 단독 예약(solo).

test_monitor_off.py 의 가짜 DLL·픽스처를 그대로 쓰고, Timer 는 가짜로 바꿔
시간을 재지 않고 fire() 로 직접 발화시킨다.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from unittest import mock

import pytest

from test_monitor_off import (  # noqa: F401  (appdata 는 autouse 픽스처)
    FakeDdc,
    _install_run,
    _make_app,
    appdata,
    ddc,
    ss,
)
from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    CONFIRM_TOAST_DURATION_MS,
    MONITOR_OFF_WARN_SEC,
    TRAY_TOOLTIP_IDLE,
    UNIT_HOUR,
    UNIT_SECOND,
)


class _Timer:
    """cancel 여부를 기록하는 가짜 Timer."""

    created: list["_Timer"] = []

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        self.cancelled = False
        _Timer.created.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.function()


def _event_of(timer: _Timer) -> Optional[str]:
    """기본 인자로 묶인 이벤트 문자열 (모니터 Timer 만 가짐)."""
    defaults = timer.function.__defaults__
    if defaults and isinstance(defaults[0], str):
        return defaults[0]
    return None


def _monitor_timers(prefix: str = "monitor_") -> list[_Timer]:
    return [t for t in _Timer.created if (_event_of(t) or "").startswith(prefix)]


def _due_timers(kind: str) -> list[_Timer]:
    return _monitor_timers(f"monitor_due:{kind}:")


def _warn_timers(kind: str) -> list[_Timer]:
    return _monitor_timers(f"monitor_warn:{kind}:")


def _sched_app(monkeypatch: pytest.MonkeyPatch, action: str = ACTION_SHUTDOWN) -> Any:
    """Timer·토스트만 가짜로 두고 예약 상태 변수는 실제 코드를 쓴다."""
    _Timer.created = []
    monkeypatch.setattr(ss.threading, "Timer", _Timer)
    app = _make_app(action, False)
    app.is_active = False
    app.is_paused = False
    app._pause_remaining = 0.0
    app.scheduled_time = None
    app._shutdown_timer = None
    app._warning_timers = []
    app._tooltip_timer = None
    app._tk_root = mock.MagicMock()
    app._show_confirm_toast = mock.MagicMock()
    app._show_warning_toast = mock.MagicMock()
    return app


# 1. lead=600, total=1800 → 끄기 1200, 경고 1140
def test_linked_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)

    assert [t.interval for t in _due_timers("linked")] == [1200]
    assert [t.interval for t in _warn_timers("linked")] == [1200 - MONITOR_OFF_WARN_SEC]
    assert app._monitor_lead_sec == 600
    msg = app._show_confirm_toast.call_args.args[0]
    assert msg.endswith("\n모니터는 종료 10분 전에 끕니다.")


# 2. lead=0 → 모니터 Timer 없음, 종료 직전 쓰기
def test_lead_zero_writes_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    """종료 직전 끄기가 실행되는지 확인한다.

    종료 전 진단 읽기 추가로 "enum" 과 "run" 사이에 읽기 2건이 들어간다.
    (예전 기대값은 ddc.events[:2] == ["enum", "run"] 이었음)
    """
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 0)
    assert _monitor_timers() == []
    assert app._show_confirm_toast.call_args.args[0].endswith(
        "\n모니터는 종료 직전에 끕니다."
    )

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()
    assert ddc.events[:4] == ["enum", ("read", 1001, 0x10), ("read", 1002, 0x10), "run"]
    assert len(ddc.sets()) == 4


# 3. lead=None → 열거·쓰기 0회
def test_lead_none_untouched(monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    assert _monitor_timers() == []

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()
    assert ddc.events == ["run"]


# 4. 발화 전 취소 → Timer 취소, lead None, 토스트에 모니터 취소 문구
def test_cancel_before_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.cancel_shutdown()

    assert all(t.cancelled for t in _monitor_timers())
    assert app._monitor_lead_sec is None
    assert app._linked_monitor.armed is False
    assert "모니터 끄기도 함께 취소" in app._show_confirm_toast.call_args.args[0]


# 5. 발화 뒤(큐에 들어간 뒤) 취소 → 세대가 달라 끄기 0회
def test_cancel_after_fire_drops_stale(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    _due_timers("linked")[0].fire()
    _warn_timers("linked")[0].fire()
    assert app._queue.qsize() == 2

    app.cancel_shutdown()
    app._process_queue()

    assert ddc.events == []
    app._show_warning_toast.assert_not_called()


# 6-1. 일시정지 → 취소, 재개 → arm(remaining - lead) 1회
def test_pause_resume_rearms_once(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    first = _due_timers("linked")[0]
    app.scheduled_time = datetime.now() + timedelta(seconds=900)

    app.pause_shutdown()
    assert first.cancelled is True
    assert app._monitor_lead_sec == 600
    remaining = int(app._pause_remaining)
    app.resume_shutdown()

    live = [t for t in _due_timers("linked") if not t.cancelled]
    assert len(live) == 1 and live[0] is not first
    assert live[0].interval == remaining - 600


# 6-2. 재개 시 남은 시간이 lead 이하면 Timer 없이 바로 끔
def test_resume_past_lead_turns_off_now(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.scheduled_time = datetime.now() + timedelta(seconds=500)
    app.pause_shutdown()
    before = len(_due_timers("linked"))

    app.resume_shutdown()
    assert len(_due_timers("linked")) == before
    app._process_queue()

    assert ddc.events.count("enum") == 1
    assert len(ddc.sets()) == 4
    assert app._linked_done is True


def _turn_off_linked(app: Any) -> None:
    _due_timers("linked")[-1].fire()
    app._process_queue()


# 7. 끈 뒤 일시정지·재개 → 다시 걸지 않음
def test_done_not_rearmed_after_pause(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    _turn_off_linked(app)
    assert app._linked_done is True
    count = len(_due_timers("linked"))

    app.pause_shutdown()
    app.resume_shutdown()

    assert len(_due_timers("linked")) == count
    assert app._linked_monitor.armed is False


# 8. 끈 뒤 종료 실행 → 종료 직전 다시 씀
def test_rewrite_at_shutdown_after_early_off(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    _turn_off_linked(app)
    assert len(ddc.sets()) == 4

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()

    assert ddc.events.count("enum") == 2
    assert len(ddc.sets()) == 8


# 9. 재시작 + lead → None, Timer 0개
def test_restart_ignores_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch, ACTION_RESTART)
    app.schedule_action(ACTION_RESTART, 1800, 600)

    assert app._monitor_lead_sec is None
    assert _monitor_timers() == []
    assert "모니터" not in app._show_confirm_toast.call_args.args[0]


# 10-1. lead >= total → 0 (방어)
@pytest.mark.parametrize("lead", [300, 600, -60])
def test_lead_clamped_to_zero(monkeypatch: pytest.MonkeyPatch, lead: int) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 300, lead)

    assert app._monitor_lead_sec == 0
    assert _monitor_timers() == []


# 10-2. _monitor_plan_error 가 막는 조합
@pytest.mark.parametrize(
    "action, on, lead_min, total, blocked",
    [
        (ACTION_SHUTDOWN, True, 5, 300, True),
        (ACTION_SHUTDOWN, True, 6, 300, True),
        (ACTION_SHUTDOWN, True, 4, 300, False),
        (ACTION_SHUTDOWN, True, 0, 0, False),
        (ACTION_SHUTDOWN, True, 1, 0, False),
        (ACTION_SHUTDOWN, True, 1000, 10**6, True),
        (ACTION_SHUTDOWN, False, 5, 300, False),
        (ACTION_RESTART, True, 5, 300, False),
        (ACTION_MONITOR_OFF, True, 5, 300, False),
    ],
)
def test_monitor_plan_error(
    action: str, on: bool, lead_min: int, total: int, blocked: bool
) -> None:
    """_monitor_plan_error 가 막는 조합을 확인한다.

    PM 결정: 0초는 시안대로 막지 않음. 설계안 p05 경고 조건(남은 초 > 0)과 hi_p04 "0초"
    그림에 맞춰 (종료, 켬, N=1, 0초) 는 막지 않는다. (예전 기대값은 True 였음)
    """
    error = ss.ScheduleDialog._monitor_plan_error(action, on, lead_min, total)
    assert (error is not None) is blocked
    if (action, on, lead_min, total) == (ACTION_SHUTDOWN, True, 5, 300):
        assert error == "종료까지 남은 시간보다 짧게 입력하세요."


# 11. 단독 300초 → 지연 300, 발화 시 열거·쓰기·해제, shutdown 0회
def test_solo_fires_without_shutdown(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(300)

    assert [t.interval for t in _due_timers("solo")] == [300]
    assert [t.interval for t in _warn_timers("solo")] == [300 - MONITOR_OFF_WARN_SEC]
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_schedule"
    assert app._show_confirm_toast.call_args.args[0].startswith(
        "5분 후 모니터를 끕니다."
    )

    _warn_timers("solo")[0].fire()
    _due_timers("solo")[0].fire()
    app._process_queue()

    app._show_warning_toast.assert_called_once_with(
        "1분 후 모니터를 끕니다. 취소: Alt+S"
    )
    assert ddc.events[0] == "enum"
    assert len(ddc.sets()) == 4
    assert len(ddc.destroys()) == 2
    assert calls == []
    assert app._solo_monitor.armed is False
    assert (
        "phase=solo"
        in open(
            f"{appdata}/{ss.APP_NAME}/{ss.SHUTDOWN_TRACE_FILE_NAME}", encoding="utf-8"
        ).read()
    )


# 12. 단독 0초 → 확인 토스트 시간(3초) 뒤, 경고 없음
def test_solo_zero_uses_toast_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(0)

    assert [t.interval for t in _due_timers("solo")] == [
        CONFIRM_TOAST_DURATION_MS / 1000
    ]
    assert _warn_timers("solo") == []


# 13. 단독 대체 → 옛 세대 이벤트는 버림
def test_solo_replace_drops_old(monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(300)
    old = _due_timers("solo")[0]
    old.fire()

    app.schedule_monitor_only(600)
    app._process_queue()

    assert ddc.events == []
    live = [t for t in _due_timers("solo") if not t.cancelled]
    assert [t.interval for t in live] == [600]
    assert app._solo_monitor.armed is True


# 14-1. "cancel": 단독이 없으면 cancel_shutdown() 1회
def test_cancel_event_without_solo(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.cancel_shutdown = mock.MagicMock()
    app._queue.put("cancel")
    app._process_queue()
    app.cancel_shutdown.assert_called_once_with()


# 14-2. "cancel": 단독이 있으면 둘 다 취소 + 토스트 1개
@pytest.mark.parametrize("with_shutdown", [True, False])
def test_cancel_event_cancels_both(
    monkeypatch: pytest.MonkeyPatch, with_shutdown: bool
) -> None:
    app = _sched_app(monkeypatch)
    if with_shutdown:
        app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.schedule_monitor_only(300)
    app._show_confirm_toast.reset_mock()

    app._queue.put("cancel")
    app._process_queue()

    assert app.is_active is False
    assert app._solo_monitor.armed is False
    assert app._linked_monitor.armed is False
    assert all(t.cancelled for t in _monitor_timers())
    assert app._show_confirm_toast.call_count == 1
    expected = "cancel" if with_shutdown else "monitor_cancel"
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == expected


# 15. (a)·(b) 공존 시 서로 독립
def test_linked_and_solo_independent(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.schedule_monitor_only(300)

    app.cancel_monitor_only()
    assert app._linked_monitor.armed is True
    assert app.is_active is True

    app.schedule_monitor_only(300)
    _due_timers("solo")[-1].fire()
    app._process_queue()
    assert app._linked_monitor.armed is True
    assert app._linked_done is False

    app.cancel_shutdown()
    app.schedule_monitor_only(300)
    assert app._solo_monitor.armed is True
    assert app._linked_monitor.armed is False


# 16. _exit_app 이 두 Timer 를 모두 취소
def test_exit_cancels_both(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    monkeypatch.setattr(ss, "keyboard", mock.MagicMock())
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app.schedule_monitor_only(300)

    app._exit_app()

    assert app._linked_monitor.armed is False
    assert app._solo_monitor.armed is False
    assert all(t.cancelled for t in _monitor_timers())


# 17-1. 툴팁 문자열 조합
def test_tooltip_text_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    now = datetime(2026, 10, 30, 12, 0, 0)
    assert app._tooltip_text(now) == TRAY_TOOLTIP_IDLE

    app.is_active = True
    app.scheduled_time = now + timedelta(hours=1)
    app._monitor_lead_sec = 600
    app._linked_monitor.due = now + timedelta(minutes=50)
    assert app._tooltip_text(now) == (
        "종료 예약 중 - 남은 시간: 01:00:00\n모니터 끄기까지 00:50:00"
    )

    app._monitor_lead_sec = 0
    assert app._tooltip_text(now).endswith("\n모니터: 종료 직전")

    app._monitor_lead_sec = 600
    app._linked_done = True
    assert app._tooltip_text(now).endswith("\n모니터: 꺼짐")

    app._linked_done = False
    app._linked_monitor.due = None
    app.is_paused = True
    app._pause_remaining = 1800.0
    assert app._tooltip_text(now) == (
        "종료 일시정지 - 남은 시간: 00:30:00\n모니터: 종료 10분 전"
    )

    app.is_active = False
    app._solo_monitor.due = now + timedelta(seconds=65)
    assert app._tooltip_text(now) == "모니터 끄기까지 00:01:05 (PC 켜 둠)"


# 17-2. 툴팁 루프는 단독 예약만 있어도 돌고, 다시 시작하면 이전 Timer 를 끊음
def test_tooltip_loop_runs_for_solo_and_no_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _sched_app(monkeypatch)
    app.tray_icon = mock.MagicMock()
    app.schedule_monitor_only(300)
    loops = [t for t in _Timer.created if t.function == app._update_tooltip_loop]
    assert len(loops) == 1
    assert app.tray_icon.title.startswith("모니터 끄기까지 ")

    app._update_tooltip_loop()
    loops = [t for t in _Timer.created if t.function == app._update_tooltip_loop]
    assert len(loops) == 2 and loops[0].cancelled and not loops[1].cancelled

    app.cancel_monitor_only()
    assert app.tray_icon.title == TRAY_TOOLTIP_IDLE
    assert all(t.cancelled for t in loops)


class _Var:
    """tk 변수 대역 (get/set 만)."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


def _bare_dialog(action: str = ACTION_SHUTDOWN) -> Any:
    """tk 없이 ScheduleDialog 메서드를 부를 수 있는 인스턴스."""
    dlg = object.__new__(ss.ScheduleDialog)
    dlg._action = action
    dlg._unit = ss.UNIT_MINUTE
    dlg._total_seconds = 1800
    dlg._updating = False
    dlg.var_value = _Var("30")
    dlg._preview_var = _Var("")
    dlg._monitor_on = _Var(True)
    dlg.var_monitor_lead = _Var("5")
    dlg._monitor_lead_min = 5
    dlg._refresh_unit_btns = mock.MagicMock()
    dlg._refresh_action_btns = mock.MagicMock()
    dlg._refresh_monitor_row = mock.MagicMock()
    dlg._close = mock.MagicMock()
    dlg._show_immediate_confirm = mock.MagicMock()
    dlg.callback = mock.MagicMock()
    return dlg


# 18-1. _set_unit·_quick_add·_reset_input 은 N 을 건드리지 않고 줄 갱신을 부름
def test_unit_and_quick_add_keep_lead() -> None:
    """단위·빠른 추가·초기화가 N 을 건드리지 않는지 확인한다.

    UI 개편 확정안 2-4 로 단위 버튼은 "증감 단위 선택"이 되어 값을 0 으로 되돌리지
    않는다. 그래서 _set_unit(시간) 뒤에도 1800초가 남고, +1시간 하면 5400초가 된다.
    (예전 기대값은 단위 전환 때 0 으로 초기화되어 3600 이었음)
    """
    dlg = _bare_dialog()
    dlg._set_unit(UNIT_HOUR)
    assert (dlg._monitor_lead_min, dlg.var_monitor_lead.get()) == (5, "5")
    assert dlg._total_seconds == 1800 and dlg.var_value.get() == "0"
    dlg._quick_add(3600)
    assert dlg._total_seconds == 5400 and dlg.var_value.get() == "1"
    dlg._set_unit(UNIT_SECOND)
    dlg._reset_input()
    assert (dlg._monitor_lead_min, dlg.var_monitor_lead.get()) == (5, "5")
    assert dlg._refresh_monitor_row.call_count == 4


# 18-2. 확인: 오류면 창 유지, 정상이면 5인자 콜백
def test_on_confirm_passes_lead_or_blocks() -> None:
    dlg = _bare_dialog()
    dlg._total_seconds = 300
    dlg._on_confirm()
    dlg.callback.assert_not_called()
    dlg._close.assert_not_called()

    dlg._total_seconds = 1800
    dlg._on_confirm()
    dlg.callback.assert_called_once_with(ACTION_SHUTDOWN, 0, 30, 0, 300)

    for action, on in [(ACTION_RESTART, True), (ACTION_MONITOR_OFF, True)]:
        d = _bare_dialog(action)
        d._monitor_on = _Var(on)
        d._on_confirm()
        assert d.callback.call_args.args[4] is None
    d = _bare_dialog()
    d._monitor_on = _Var(False)
    d._on_confirm()
    assert d.callback.call_args.args[4] is None


# 18-3. N 입력 검증과 모니터 동작 미리보기
@pytest.mark.parametrize(
    "value, ok",
    [("", True), ("0", True), ("999", True), ("1000", False), ("-1", False)]
    + [("１", False), (" 5", False), ("a", False), ("+5", False)],
)
def test_validate_lead_input(value: str, ok: bool) -> None:
    assert ss.ScheduleDialog._validate_lead_input(value) is ok


def test_preview_for_monitor_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """모니터 동작의 미리보기 글자를 확인한다.

    UI 개편 확정안으로 미리보기는 예약 버튼 글자("HH:MM {동작} 예약", 설계안 3장 9번)가
    되어 문구가 바뀌었다. 시각이 들어가므로 지금 시각을 03:03 으로 고정한다.
    (예전 기대값은 "30분 · PC는 켜 둔 채 모니터만 끕니다." 였음)
    """

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "_Fixed":
            return cls(2026, 10, 31, 3, 3, 0)

    monkeypatch.setattr(ss, "datetime", _Fixed)
    dlg = _bare_dialog(ACTION_MONITOR_OFF)
    dlg._update_preview()
    assert dlg._preview_var.get() == "03:33 모니터 끄기 예약"
    dlg._total_seconds = 0
    dlg._update_preview()
    assert dlg._preview_var.get() == "모니터 끄기 예약"


# 다이얼로그 콜백 라우팅 — 모니터 동작은 단독 예약으로
def test_dialog_confirm_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only = mock.MagicMock()
    app.schedule_action = mock.MagicMock()

    app._on_dialog_confirm(ACTION_MONITOR_OFF, 0, 5, 0, None)
    app.schedule_monitor_only.assert_called_once_with(300)
    app._on_dialog_confirm(ACTION_SHUTDOWN, 1, 0, 0, 600)
    app.schedule_action.assert_called_once_with(ACTION_SHUTDOWN, 3600, 600)


# linked 는 종료가 살아 있고 일시정지가 아닐 때만 끔
def test_linked_due_requires_active(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    _due_timers("linked")[0].fire()
    app.is_active = False  # 툴팁 루프가 비활성으로 돌린 경우 등
    app._process_queue()
    assert ddc.events == []
    assert app._linked_done is False


# 19. 소스 감시 — "execute" 에는 세대를 붙이지 않고, 모니터 Timer 는 기본 인자로 묶음
def test_source_guards_monitor_schedule() -> None:
    """소스 감시 — "execute" Timer 람다, 모니터 Timer 기본 인자, /t 0, ttk 미사용.

    UI 개편 확정안 8장으로 schedule_action·resume_shutdown·extend_schedule 의 Timer 걸기를
    _arm_shutdown_timers 하나로 묶어 "execute" 람다가 1곳만 남는다.
    (예전 기대값은 schedule_action·resume_shutdown 두 곳이라 2 였음)
    """
    with open(ss.__file__, encoding="utf-8") as f:
        src = f.read()
    assert len(re.findall(r'lambda: self\._queue\.put\("execute"\)', src)) == 1
    assert "lambda e=event: self._queue.put(e)" in src
    t_values = re.findall(r'"/t",\s*([^\],]+)', src)
    assert t_values and all(v.strip() == '"0"' for v in t_values)
    assert not re.search(r"\bttk\b", src)
