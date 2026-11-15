"""왓슨 보강 테스트 — 셜록 설계 7장 추가 시나리오 1~8 중 해머 테스트가 빠뜨린 조건.

실제 tk 창이 필요한 테스트는 화면(DISPLAY)이 없으면 건너뛴다.
모든 테스트에서 종료 명령(subprocess.run)·DDC 쓰기는 가짜로 막는다.
"""

import copy
import json
import math
import os
import time
import types
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from unittest import mock

import pytest

from test_monitor_off import appdata, ss  # noqa: F401  (appdata 는 autouse 픽스처)
from test_monitor_schedule import _Timer, _due_timers, _sched_app
from test_ui_redesign import open_dialog, tk_root  # noqa: F401  (픽스처)
from cfg import theme
from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    RESTORE_CLOCK_SKEW_SEC,
    RESTORE_PAUSE_UNDER_SEC,
    SCHEDULE_FILE_VERSION,
    UNIT_HOUR,
    UNIT_SECOND,
)


@pytest.fixture(autouse=True)
def _no_real_power(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """실수로 실행 경로에 닿아도 실제 shutdown·DDC 가 나가지 않게 막는다."""
    runs: list[Any] = []
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: runs.append(a))
    monkeypatch.setattr(ss, "_acquire_physical_monitors", lambda: [])
    monkeypatch.setattr(ss, "_power_off_monitors", lambda groups: 0)
    return runs


# ------------------------------------------------------------------ #
# 1. theme                                                           #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("key", theme.BASE_KEYS)
def test_validate_rejects_each_missing_base_key(key: str) -> None:
    broken = copy.deepcopy(theme.THEMES["gh"])
    broken["tokens"].pop(key)
    assert theme.validate_theme(broken) is False


@pytest.mark.parametrize("name", theme.ACTION_NAMES)
@pytest.mark.parametrize("key", theme.ACTION_KEYS)
def test_validate_rejects_each_missing_action_key(name: str, key: str) -> None:
    broken = copy.deepcopy(theme.THEMES["gh"])
    broken["tokens"]["actions"][name].pop(key)
    assert theme.validate_theme(broken) is False


def test_get_theme_rejects_broken_registered_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = copy.deepcopy(theme.THEMES["gh"])
    broken["id"] = "bad"
    broken["tokens"].pop("surface")
    monkeypatch.setitem(theme.THEMES, "bad", broken)
    assert theme.get_theme("bad")["id"] == "gh"


def test_action_tokens_mapping_and_mix_props() -> None:
    gh = theme.get_theme("gh")
    for action, name in (
        (ACTION_SHUTDOWN, "shutdown"),
        (ACTION_RESTART, "restart"),
        (ACTION_MONITOR_OFF, "monitor"),
    ):
        assert theme.action_tokens(gh, action) is gh["tokens"]["actions"][name]
    assert theme.mix("#123456", "#123456", 0.37) == "#123456"
    assert theme.mix("#ffffff", "#000000", -1.0) == "#000000"
    # 헤더 아이콘 배경 = 강조색 16% + 카드
    assert (
        theme.mix("#58a6ff", "#161b22", 0.16) == "#213145"
    )  # 손계산: 0x58*.16+0x16*.84=33 …


# ------------------------------------------------------------------ #
# 2. _dial_text 표 (셜록 지정 값 전체)                               #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "seconds, big, unit",
    [
        (0, "즉시", "누르면 바로 종료"),
        (59, "59", "초"),
        (60, "1", "분"),
        (61, "1:01", "분:초"),
        (1800, "30", "분"),
        (1830, "30:30", "분:초"),
        (3599, "59:59", "분:초"),
        (3600, "1:00", "시간:분"),
        (9000, "2:30", "시간:분"),
    ],
)
def test_dial_text_sherlock_table(seconds: int, big: str, unit: str) -> None:
    assert ss._dial_text(seconds, ACTION_SHUTDOWN) == ("종료까지", big, unit)


# ------------------------------------------------------------------ #
# 3. 링 계산 경계                                                    #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "seconds, lead, expected",
    [
        (7500, 600, (6900 / 7200, 1.0)),
        (7200, 7200, (0.0, 1.0)),
        (60, 60, (0.0, 60 / 7200)),
        (7800, 300, None),
    ],
)
def test_monitor_segment_clip_edges(
    seconds: int, lead: int, expected: Optional[tuple]
) -> None:
    assert ss._monitor_segment(seconds, lead) == expected


@pytest.mark.parametrize(
    "prev, new, expected",
    [
        (91, 30, 30),
        (90, 29, 29),
        (29, 90, 90),
        (30, 91, 91),
        (120, 0, 120),
        (0, 120, 0),
    ],
)
def test_clamp_wrap_thresholds(prev: int, new: int, expected: int) -> None:
    assert ss._clamp_wrap(prev, new) == expected


def test_wheel_small_deltas_accumulate() -> None:
    acc, total = 0, 0
    for _ in range(4):
        steps, acc = ss._wheel_steps(acc, 30)
        total += steps
    assert (total, acc) == (1, 0)
    steps, acc = ss._wheel_steps(0, -119)
    assert (steps, acc) == (0, -119)


# ------------------------------------------------------------------ #
# 4. 예약 창 상호작용                                                #
# ------------------------------------------------------------------ #


def _set_total(dlg: Any, seconds: int) -> None:
    dlg._total_seconds = seconds
    dlg._sync_value_text()
    dlg._update_preview()
    dlg._refresh_monitor_row()


def _key(dlg: Any, keysym: str, focus: Any = None) -> Optional[str]:
    dlg._focus_widget = lambda: focus
    return dlg._on_key(types.SimpleNamespace(keysym=keysym))


# 4-1. 드래그는 분*60 으로 바꿔 쓰고(120분 상한), 120분 넘는 값은 표시만 하다가 드래그하면 대체
def test_drag_replaces_over_120_value(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog()
    dlg._quick_add(3 * 3600)
    assert dlg._total_seconds == 10800
    dlg._on_ring_drag(110, False)
    # 직전 값은 120분으로 잘라 넘김 고정을 판단함
    assert dlg._total_seconds == 110 * 60
    dlg._on_ring_drag(120, True)
    assert dlg._total_seconds == 7200
    dlg._set_unit(UNIT_HOUR)
    assert dlg.var_value.get() == "2"


# 4-2. 120분 넘는 값도 예약은 그대로 보냄 (표시만 링 가득)
def test_over_120_confirm_sends_full_value(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog(monitor_default=False)
    _set_total(dlg, 9000)
    assert dlg._dial._state["progress"] == 1.0
    dlg.callback = mock.MagicMock()
    dlg._show_view = mock.MagicMock()
    dlg._on_confirm()
    dlg.callback.assert_called_once_with(ACTION_SHUTDOWN, 2, 30, 0, None)


# 4-3. 칩을 누를 때 입력란 감시 콜백이 다시 돌지 않음 (갱신 1회)
def test_chip_does_not_retrigger_trace(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog()
    _set_total(dlg, 1800)
    real = dlg._refresh_monitor_row
    calls: list[int] = []
    dlg._refresh_monitor_row = lambda: (calls.append(1), real())
    dlg._on_chip(10)
    assert calls == [1]
    assert dlg.var_monitor_lead.get() == "10" and dlg._monitor_lead_min == 10
    calls.clear()
    dlg.var_monitor_lead.set("1")
    assert calls == [1] and dlg._monitor_lead_min == 1
    accent = dlg._tok("monitor.accent")
    assert dlg._chip_btns[1].cget("border_color") == accent


# 4-4. 스위치를 끄면 lead 는 None, 경고·막기도 풀림, 콜백 lead 도 None
def test_switch_off_lead_none_and_no_block(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog()
    dlg._on_chip(10)
    _set_total(dlg, 300)
    assert dlg._btn_confirm.cget("state") == "disabled"
    dlg._monitor_on.set(False)
    dlg._refresh_monitor_row()
    assert dlg._selected_monitor_lead_sec() is None
    assert dlg._monitor_warn.winfo_manager() == ""
    assert dlg._btn_confirm.cget("state") == "normal"
    dlg.callback = mock.MagicMock()
    dlg._show_view = mock.MagicMock()
    dlg._on_confirm()
    dlg.callback.assert_called_once_with(ACTION_SHUTDOWN, 0, 5, 0, None)


# 4-5. 재시작·모니터 탭에서는 lead ≥ 예약 시간이어도 경고·막기 없음
@pytest.mark.parametrize("action", [ACTION_RESTART, ACTION_MONITOR_OFF])
def test_other_tabs_never_blocked(
    open_dialog: Callable[..., tuple[Any, Any]], action: str
) -> None:
    _, dlg = open_dialog()
    dlg._on_chip(10)
    _set_total(dlg, 300)
    dlg._set_action(action)
    assert dlg._monitor_warn.winfo_manager() == ""
    assert dlg._btn_confirm.cget("state") == "normal"
    assert dlg._dial._state["segment"] is None
    assert dlg._selected_monitor_lead_sec() is None


# 4-6. 예약이 밖에서 모두 사라지면(Alt+S) 1초 갱신 때 설정 화면으로 돌아감
def test_external_cancel_returns_to_setup(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    def prepare(a: Any) -> None:
        a.schedule_action(ACTION_SHUTDOWN, 1800)
        a.schedule_monitor_only(900)

    app, dlg = open_dialog(prepare=prepare)
    assert dlg._view == "scheduled"
    app._cancel_all()
    dlg._tick_countdown()
    assert dlg._view == "setup"
    assert dlg._badge.winfo_manager() == ""


# 4-7. 일시정지 중인 예약이 있으면 예약됨 화면으로 열리고 ▶ 표시
def test_open_with_paused_schedule(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    def prepare(a: Any) -> None:
        a.schedule_action(ACTION_SHUTDOWN, 1800)
        a.pause_shutdown()

    _, dlg = open_dialog(prepare=prepare)
    assert dlg._view == "scheduled"
    assert dlg._btn_pause_resume.cget("text") == "▶"
    assert dlg._sched_dial._state["label"] == "일시정지"


# 4-8. 둘 다 있을 때 예약 취소는 종료 먼저, 그다음 누르면 단독
def test_cancel_order_shutdown_then_solo(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    def prepare(a: Any) -> None:
        a.schedule_action(ACTION_SHUTDOWN, 1800)
        a.schedule_monitor_only(900)

    app, dlg = open_dialog(prepare=prepare)
    dlg._on_cancel_schedule()
    assert app.is_active is False and app._solo_monitor.armed is True
    assert dlg._view == "scheduled"
    assert dlg._sched_dial._state["color"] == "#58a6ff"
    assert not dlg._btn_pause_resume.winfo_ismapped()
    dlg._on_cancel_schedule()
    assert app._solo_monitor.armed is False and dlg._view == "setup"


# ------------------------------------------------------------------ #
# 5. 키보드                                                          #
# ------------------------------------------------------------------ #


# 5-1. 키패드 숫자도 Entry 포커스면 탭을 바꾸지 않음
def test_keypad_digits_in_entry(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    for keysym in ("KP_2", "KP_3", "1"):
        assert _key(dlg, keysym, dlg._lead_entry._entry) is None
        assert _key(dlg, keysym, dlg._entry._entry) is None
    assert dlg._action == ACTION_SHUTDOWN


# 5-2. 초 단위에서 0 밑으로 안 내려감, Space 는 설정 화면에서 아무 일도 안 함
def test_down_floor_and_space_in_setup(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    app, dlg = open_dialog()
    dlg._set_unit(UNIT_SECOND)
    _key(dlg, "Down")
    assert dlg._total_seconds == 0
    _key(dlg, "Up")
    assert dlg._total_seconds == 1
    assert _key(dlg, "space", dlg._dial.canvas) is None
    assert app.is_paused is False and app.is_active is False


# 5-3. 예약됨 화면에서 Enter·↑↓·1/2/3 은 아무 일도 안 함
def test_scheduled_view_ignores_setup_keys(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, 1800))
    dlg._total_seconds = 600
    target = app.scheduled_time
    dlg.callback = mock.MagicMock()
    for keysym in ("Return", "KP_Enter", "Up", "Down", "2", "KP_3"):
        _key(dlg, keysym, dlg._sched_dial.canvas)
    dlg.callback.assert_not_called()
    assert dlg._total_seconds == 600 and dlg._action == ACTION_SHUTDOWN
    assert app.scheduled_time == target and dlg._view == "scheduled"


# 5-4. 단독 예약만 있으면 Space 로 일시정지를 부르지 않음
def test_space_ignored_for_solo(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_monitor_only(900))
    app.pause_shutdown = mock.MagicMock()
    assert _key(dlg, "space", dlg._sched_dial.canvas) == "break"
    app.pause_shutdown.assert_not_called()


# 5-5. 실제 Space 키 이벤트도 예약됨 화면에서 일시정지·재개로 들어감
def test_space_real_event(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, 1800))
    canvas = dlg._sched_dial.canvas
    canvas.focus_force()
    dlg.top.update()
    canvas.event_generate("<KeyPress-space>", keysym="space")
    dlg.top.update()
    assert app.is_paused is True
    canvas.event_generate("<KeyPress-space>", keysym="space")
    dlg.top.update()
    assert app.is_paused is False


# ------------------------------------------------------------------ #
# 6. 연장                                                            #
# ------------------------------------------------------------------ #


def _live(timers: list[_Timer]) -> list[_Timer]:
    return [t for t in timers if not t.cancelled]


def _warn_intervals() -> list[float]:
    return sorted(
        t.interval
        for t in _Timer.created
        if not t.cancelled
        and t.function.__defaults__
        and isinstance(t.function.__defaults__[0], str)
        and not t.function.__defaults__[0].startswith("monitor_")
    )


def _exec_intervals() -> list[float]:
    return [
        t.interval
        for t in _Timer.created
        if not t.cancelled
        and getattr(t.function, "__name__", "") == "<lambda>"
        and not t.function.__defaults__
    ]


# 6-1. 남은 100초에서 연장하면 새 남은 시간(700)보다 짧은 경고 3개를 모두 다시 건다
def test_extend_adds_new_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 100)
    assert len(_warn_intervals()) == 1
    app.extend_schedule(600)
    warns = _warn_intervals()
    assert len(warns) == 3
    for got, want in zip(warns, [100, 400, 640]):
        assert abs(got - want) < 2
    assert len(_exec_intervals()) == 1 and abs(_exec_intervals()[0] - 700) < 2


# 6-2. lead 0(직전)은 linked Timer 를 걸지 않음, 실행 때 끔
def test_extend_lead_zero_no_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 0)
    app.extend_schedule(600)
    assert _live(_due_timers("linked")) == []
    assert app._monitor_lead_sec == 0 and app._linked_monitor.armed is False


# 6-3. 일시정지 중 연장 → 재개 때 linked 를 새 남은 시간 - lead 로 건다
def test_extend_paused_then_resume_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    app.pause_shutdown()
    app.extend_schedule(600)
    gen = app._linked_monitor.gen
    app.resume_shutdown()
    linked = _live(_due_timers("linked"))
    assert len(linked) == 1 and abs(linked[0].interval - 2100) < 2
    assert app._linked_monitor.gen > gen


# 6-4. 단독 연장도 파일에 저장 (target 이 600초 늘어남)
def test_extend_solo_saves(monkeypatch: pytest.MonkeyPatch, appdata: Any) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(900)
    path = os.path.join(str(appdata), ss.APP_NAME, "schedule.json")
    with open(path, encoding="utf-8") as f:
        before = json.load(f)["solo"]["target"]
    app.extend_schedule(600)
    with open(path, encoding="utf-8") as f:
        after = json.load(f)["solo"]["target"]
    assert abs(after - before - 600) < 2


# 6-5. 비활성·실행 직전(남은 0 이하)에는 저장·토스트 모두 없음
def test_extend_ignored_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    app = _sched_app(monkeypatch)
    app._save_schedule = mock.MagicMock()
    app.extend_schedule(600)
    app.is_active = True
    app.scheduled_time = datetime.now()
    app.extend_schedule(600)
    app._save_schedule.assert_not_called()
    app._show_confirm_toast.assert_not_called()


# ------------------------------------------------------------------ #
# 7. 복원 — 경계값·전수 안전성                                       #
# ------------------------------------------------------------------ #

_NOW = 1_800_000_000.0


def _schedule_file(appdata: Any) -> str:
    return os.path.join(str(appdata), ss.APP_NAME, "schedule.json")


def _write_raw(appdata: Any, text: str) -> None:
    path = _schedule_file(appdata)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_data(appdata: Any, data: dict) -> None:
    _write_raw(appdata, json.dumps(data))


def _sd(
    target: Optional[float],
    total: float,
    action: str = ACTION_SHUTDOWN,
    lead: Optional[int] = None,
    done: bool = False,
    paused: Optional[float] = None,
) -> dict:
    return {
        "action": action,
        "target": target,
        "paused_remaining": paused,
        "total": total,
        "monitor_lead_sec": lead,
        "linked_done": done,
    }


def _data(
    sd: Optional[dict] = None,
    solo_target: Optional[float] = None,
    saved_at: float = _NOW - 5,
) -> dict:
    return {
        "version": SCHEDULE_FILE_VERSION,
        "saved_at": saved_at,
        "shutdown": sd,
        "solo": None if solo_target is None else {"target": solo_target},
    }


@pytest.fixture
def fixed_app(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """time.time() 을 _NOW 로 고정하고 실행 경로를 모두 세는 앱."""
    fake_time = types.SimpleNamespace(time=lambda: _NOW, monotonic=time.monotonic)
    monkeypatch.setattr(ss, "time", fake_time)
    runs: list[Any] = []
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: runs.append(a))
    app = _sched_app(monkeypatch)
    app._turn_off_monitors_now = mock.MagicMock()
    app._execute_action = mock.MagicMock()
    return {"app": app, "runs": runs}


def _assert_safe(g: dict[str, Any]) -> None:
    """복원 직후 종료·즉시 끄기가 없고, 600초 안에 터지는 실행 Timer 도 없음."""
    app = g["app"]
    assert g["runs"] == []
    app._turn_off_monitors_now.assert_not_called()
    app._execute_action.assert_not_called()
    assert app._queue.empty()
    # 트레이 재확인 after(250)·큐 폴링 after(50) 만 허용, 즉시 실행 경로 after 는 금지
    for call in app._tk_root.after.call_args_list:
        assert call.args[0] in (50, 250) and call.args[1] is not app._execute_action
    for interval in _exec_intervals():
        assert interval >= RESTORE_PAUSE_UNDER_SEC
    for timer in _live(_due_timers("linked")) + _live(_due_timers("solo")):
        assert timer.interval > 0


# 7-1. 남은 시간이 정확히 600초면 정상 복원(일시정지 아님), 599초면 일시정지
@pytest.mark.parametrize("remaining, paused", [(600, False), (599, True)])
def test_restore_pause_boundary(
    fixed_app: dict[str, Any], appdata: Any, remaining: int, paused: bool
) -> None:
    _write_data(appdata, _data(_sd(_NOW + remaining, 1800)))
    app = fixed_app["app"]
    app._restore_schedule()
    assert app.is_active is True and app.is_paused is paused
    assert len(_exec_intervals()) == (0 if paused else 1)
    _assert_safe(fixed_app)


# 7-2. 단독 예약도 600초면 복원, 599초면 버림
@pytest.mark.parametrize("remaining, armed", [(600, True), (599, False)])
def test_restore_solo_boundary(
    fixed_app: dict[str, Any], appdata: Any, remaining: int, armed: bool
) -> None:
    _write_data(appdata, _data(solo_target=_NOW + remaining))
    app = fixed_app["app"]
    app._restore_schedule()
    assert app._solo_monitor.armed is armed
    _assert_safe(fixed_app)


# 7-3. 시계 이상 판정 경계 — 허용치(60초) 안이면 복원, 넘으면 버림
@pytest.mark.parametrize(
    "saved_at, target_extra, restored",
    [
        (_NOW + RESTORE_CLOCK_SKEW_SEC - 1, 0, True),
        (_NOW + RESTORE_CLOCK_SKEW_SEC + 1, 0, False),
        (_NOW - 5, RESTORE_CLOCK_SKEW_SEC - 1, True),
        (_NOW - 5, RESTORE_CLOCK_SKEW_SEC + 1, False),
    ],
)
def test_restore_clock_skew_boundary(
    fixed_app: dict[str, Any],
    appdata: Any,
    saved_at: float,
    target_extra: float,
    restored: bool,
) -> None:
    total = 1800
    data = _data(_sd(_NOW + total + target_extra, total), saved_at=saved_at)
    _write_data(appdata, data)
    app = fixed_app["app"]
    app._restore_schedule()
    assert app.is_active is restored
    if not restored:
        assert app._show_confirm_toast.call_args.args[0] == (
            "시계가 바뀌어 복원하지 않았습니다."
        )
        assert not os.path.exists(_schedule_file(appdata))
    _assert_safe(fixed_app)


# 7-4. 일시정지 남은 시간이 0 이면 아무것도 세우지 않고 파일을 지움
def test_restore_paused_zero(fixed_app: dict[str, Any], appdata: Any) -> None:
    _write_data(appdata, _data(_sd(None, 0, paused=0.0)))
    app = fixed_app["app"]
    app._restore_schedule()
    assert app.is_active is False
    assert not os.path.exists(_schedule_file(appdata))
    _assert_safe(fixed_app)


# 7-5. 전수 — 남은 시간·lead·done·동작·일시정지·단독 조합 어디서도 복원 중 실행 없음
@pytest.mark.parametrize("action", [ACTION_SHUTDOWN, ACTION_RESTART])
@pytest.mark.parametrize("remaining", [-100, 0, 0.5, 1, 299, 599, 600, 601, 3600])
@pytest.mark.parametrize("lead", [None, 0, 60, 599, 600, 700, 3000])
@pytest.mark.parametrize("done", [False, True])
@pytest.mark.parametrize("as_paused", [False, True])
def test_restore_never_fires_matrix(
    fixed_app: dict[str, Any],
    appdata: Any,
    action: str,
    remaining: float,
    lead: Optional[int],
    done: bool,
    as_paused: bool,
) -> None:
    if as_paused:
        sd = _sd(None, max(remaining, 0), action, lead, done, paused=max(remaining, 0))
    else:
        sd = _sd(_NOW + remaining, max(remaining, 1), action, lead, done)
    _write_data(appdata, _data(sd, solo_target=_NOW + remaining))
    app = fixed_app["app"]
    app._restore_schedule()
    _assert_safe(fixed_app)
    # 복원 뒤 큐를 돌려도 실행·즉시 끄기가 없어야 함
    app._process_queue()
    _assert_safe(fixed_app)
    if app.is_active and not app.is_paused:
        lead_now = app._monitor_lead_sec
        assert lead_now is None or lead_now == 0 or done or lead_now < remaining


# 7-6. _execute_action 은 shutdown 명령이 실패해도 그 전에 종료 부분을 지워 둠
def test_execute_clears_even_if_run_raises(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_RESTART, 1800)
    seen: list[bool] = []

    def _boom(*a: Any, **k: Any) -> Any:
        seen.append(os.path.exists(_schedule_file(appdata)))
        raise OSError("no shutdown")

    monkeypatch.setattr(ss.subprocess, "run", _boom)
    app._execute_action()
    assert seen == [False]


# 7-7. 숫자가 아닌 값(NaN·Infinity)·범위 밖 값은 "깨진 파일"로 버리고 지워야 함
@pytest.mark.parametrize(
    "shutdown_json, solo_json",
    [
        (
            '{"action": "shutdown", "target": NaN, "paused_remaining": null, '
            '"total": 1800, "monitor_lead_sec": null, "linked_done": false}',
            "null",
        ),
        (
            '{"action": "shutdown", "target": Infinity, "paused_remaining": null, '
            '"total": Infinity, "monitor_lead_sec": null, "linked_done": false}',
            "null",
        ),
        (
            '{"action": "shutdown", "target": null, "paused_remaining": 1e12, '
            '"total": 1e12, "monitor_lead_sec": null, "linked_done": false}',
            "null",
        ),
        ("null", '{"target": NaN}'),
    ],
)
def test_restore_non_finite_is_broken_file(
    fixed_app: dict[str, Any], appdata: Any, shutdown_json: str, solo_json: str
) -> None:
    _write_raw(
        appdata,
        f'{{"version": {SCHEDULE_FILE_VERSION}, "saved_at": {_NOW - 5}, '
        f'"shutdown": {shutdown_json}, "solo": {solo_json}}}',
    )
    app = fixed_app["app"]
    try:
        app._restore_schedule()
    finally:
        _assert_safe(fixed_app)
        assert app.is_active is False and app._solo_monitor.armed is False
    assert not os.path.exists(_schedule_file(appdata))


# ------------------------------------------------------------------ #
# 8. _view_model 나머지 경우                                         #
# ------------------------------------------------------------------ #


def test_view_model_remaining_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 31, 3, 0, 0)
    app = _sched_app(monkeypatch)

    # 활성이지만 시각 없음 + 단독 없음 → 예약 없음
    app.is_active = True
    app.scheduled_time = None
    assert ss._view_model(app, now).target is None

    # 일시정지 + 단독 → 종료 기준, 일시정지 버튼 보임, 단독 줄 추가
    app.scheduled_action = ACTION_RESTART
    app.is_paused = True
    app._pause_remaining = 59.2
    app._solo_monitor.due = now + timedelta(seconds=900)
    vm = ss._view_model(app, now)
    assert (vm.target, vm.accent_action, vm.label) == (
        "shutdown",
        ACTION_RESTART,
        "일시정지",
    )
    assert (vm.remaining, vm.paused, vm.show_pause, vm.solo_extra, vm.fixed_at) == (
        60,
        True,
        True,
        True,
        None,
    )

    # 진행 중 남은 0.2초 → 올림 1
    app.is_paused = False
    app._solo_monitor.due = None
    app.scheduled_time = now + timedelta(seconds=0.2)
    assert ss._view_model(app, now).remaining == 1

    # 단독 시각이 지났으면 남은 0, 대상은 단독
    app.is_active = False
    app._solo_monitor.due = now - timedelta(seconds=5)
    vm = ss._view_model(app, now)
    assert (vm.target, vm.remaining, vm.show_pause) == ("solo", 0, False)
    assert math.isclose(vm.fixed_at.timestamp(), app._solo_monitor.due.timestamp())


def test_restore_pause_constant() -> None:
    assert RESTORE_PAUSE_UNDER_SEC == 600
    assert RESTORE_CLOCK_SKEW_SEC == 60


# ------------------------------------------------------------------ #
# 재검증 — NaN·거대값 수정, 0초 + N>0 결정                            #
# ------------------------------------------------------------------ #

_SPAN = ss.RESTORE_MAX_SPAN_SEC


def _all_live_timers() -> list[_Timer]:
    return [t for t in _Timer.created if not t.cancelled]


# 9-1. 되돌리기 경로 — 토스트 없음, 파일 최종 삭제, Timer 0개 (부분 복원: 종료 뒤 단독에서 예외)
@pytest.mark.parametrize("exc", [ValueError("x"), OverflowError("x"), OSError("x")])
def test_rollback_partial_restore(
    monkeypatch: pytest.MonkeyPatch,
    fixed_app: dict[str, Any],
    appdata: Any,
    exc: Exception,
) -> None:
    _write_data(
        appdata,
        _data(_sd(_NOW + 1800, 1800, lead=300), solo_target=_NOW + 900),
    )
    app = fixed_app["app"]
    writes: list[Any] = []
    real_write = ss._write_schedule_file

    def _spy_write(data: dict) -> bool:
        writes.append(copy.deepcopy(data))
        return real_write(data)

    def _boom(delay: float) -> None:
        raise exc

    monkeypatch.setattr(ss, "_write_schedule_file", _spy_write)
    app._solo_monitor.arm = _boom
    app._restore_schedule()
    # 종료 부분은 먼저 세워졌다가 되돌려져야 함
    assert app.is_active is False and app.is_paused is False
    assert app._monitor_lead_sec is None
    assert _all_live_timers() == []
    assert app._linked_monitor.armed is False and app._solo_monitor.due is None
    assert not os.path.exists(_schedule_file(appdata))
    app._show_confirm_toast.assert_not_called()
    assert writes == []
    _assert_safe(fixed_app)


# 9-2. 둘 다 세운 뒤 예외 — 되돌리는 중 중간 쓰기가 있어도 최종 파일은 없고 토스트 없음
def test_rollback_after_both_armed(fixed_app: dict[str, Any], appdata: Any) -> None:
    _write_data(appdata, _data(_sd(_NOW + 1800, 1800), solo_target=_NOW + 900))
    app = fixed_app["app"]

    def _boom(*a: Any, **k: Any) -> None:
        raise OSError("tray")

    app._sync_tray_after_restore = _boom
    app._restore_schedule()
    assert app.is_active is False and app._solo_monitor.armed is False
    assert _all_live_timers() == []
    assert not os.path.exists(_schedule_file(appdata))
    app._show_confirm_toast.assert_not_called()
    _assert_safe(fixed_app)


# 9-3. 상한(999시간) 경계 — 딱 상한은 통과, 1초 넘으면 깨진 파일
@pytest.mark.parametrize(
    "data, valid",
    [
        (_data(_sd(_NOW + _SPAN, _SPAN)), True),
        (_data(_sd(_NOW + _SPAN + 1, _SPAN)), False),
        (_data(_sd(_NOW + 1800, _SPAN + 1)), False),
        (_data(_sd(None, _SPAN, paused=float(_SPAN))), True),
        (_data(_sd(None, _SPAN, paused=float(_SPAN + 1))), False),
        (_data(_sd(_NOW + 1800, 1800, lead=_SPAN)), True),
        (_data(_sd(_NOW + 1800, 1800, lead=_SPAN + 1)), False),
        (_data(solo_target=_NOW + _SPAN), True),
        (_data(solo_target=_NOW + _SPAN + 1), False),
        (_data(solo_target=_NOW - _SPAN), True),
        (_data(solo_target=_NOW - _SPAN - 1), False),
        (_data(solo_target=_NOW + 900, saved_at=_NOW - _SPAN), True),
        (_data(solo_target=_NOW + 900, saved_at=_NOW - _SPAN - 1), False),
    ],
)
def test_valid_schedule_one_year_boundary(
    fixed_app: dict[str, Any], data: dict, valid: bool
) -> None:
    """PM 결정: Windows TIMEOUT_MAX 4294967s 대응으로 상한 999시간.

    경계값 _SPAN 은 RESTORE_MAX_SPAN_SEC(= MAX_SCHEDULE_SECONDS, 999시간)을 따른다.
    (예전에는 1년이었음, 단언은 그대로)
    """
    assert ss._valid_schedule(data) is valid


# 9-4. 상한(999시간) 경계 값으로 실제 복원해도 예외·실행 없음 (통과 쪽은 세워지고, 넘는 쪽은 지워짐)
@pytest.mark.parametrize(
    "data, active",
    [
        (_data(_sd(_NOW + _SPAN, _SPAN, lead=_SPAN - 1)), True),
        (_data(_sd(None, _SPAN, paused=float(_SPAN), lead=_SPAN)), True),
        (_data(_sd(_NOW + _SPAN + 1, _SPAN)), False),
    ],
)
def test_restore_one_year_boundary(
    fixed_app: dict[str, Any], appdata: Any, data: dict, active: bool
) -> None:
    """PM 결정: Windows TIMEOUT_MAX 4294967s 대응으로 상한 999시간.

    경계값 _SPAN 은 RESTORE_MAX_SPAN_SEC(= MAX_SCHEDULE_SECONDS, 999시간)을 따른다.
    (예전에는 1년이었음, 단언은 그대로)
    """
    _write_data(appdata, data)
    app = fixed_app["app"]
    app._restore_schedule()
    assert app.is_active is active
    if not active:
        assert not os.path.exists(_schedule_file(appdata))
    _assert_safe(fixed_app)


# 9-5. 0초 + N>0 → 즉시 확인창 [즉시 종료] → schedule_action(lead=0) → 실행 때 종료 뒤 끄기 1회
def test_zero_seconds_lead_full_path(
    monkeypatch: pytest.MonkeyPatch, open_dialog: Callable[..., tuple[Any, Any]]
) -> None:
    import customtkinter as ctk

    app, dlg = open_dialog(monitor_default=True)
    dlg._on_chip(5)
    _set_total(dlg, 0)
    calls: list[tuple] = []
    real_schedule = app.schedule_action
    app.schedule_action = lambda *a: (calls.append(a), real_schedule(*a))
    fired: list[Any] = []
    app._execute_action = lambda: fired.append(1)
    before = set(dlg.top.winfo_children())
    dlg._show_immediate_confirm()
    dlg.top.update()
    confirm = [w for w in dlg.top.winfo_children() if w not in before][0]

    def _buttons(w: Any) -> list[Any]:
        found = [w] if isinstance(w, ctk.CTkButton) else []
        for c in w.winfo_children():
            found += _buttons(c)
        return found

    run_btn = [b for b in _buttons(confirm) if b.cget("text") == "즉시 종료"][0]
    run_btn.invoke()
    assert calls == [(ACTION_SHUTDOWN, 0, 0)]
    assert app._monitor_lead_sec == 0 and app._shutdown_immediate is True
    assert fired == []

    events: list[str] = []

    def _run(args: list[str], **k: Any) -> Any:
        events.append("run:" + " ".join(args))
        return mock.MagicMock(returncode=0)

    monkeypatch.setattr(ss.subprocess, "run", _run)
    monkeypatch.setattr(ss, "_acquire_physical_monitors", lambda: [(1, ["h"])])
    monkeypatch.setattr(
        ss, "_power_off_monitors", lambda groups: (events.append("off"), 1)[1]
    )
    monkeypatch.setattr(ss, "_release_physical_monitors", lambda groups: None)
    ss.ShutdownScheduler._execute_action(app)
    assert events == ["run:shutdown /s /t 0 /f", "off"]


# 9-6. 0초 판정은 범위 밖 N 은 여전히 막고, 재시작·끔에서는 lead 없음
def test_zero_seconds_edge_rules(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    assert (
        ss.ScheduleDialog._monitor_plan_error(ACTION_SHUTDOWN, True, 1000, 0)
        is not None
    )
    assert ss.ScheduleDialog._monitor_plan_error(ACTION_SHUTDOWN, True, 999, 0) is None
    _, dlg = open_dialog(monitor_default=True)
    dlg._on_chip(10)
    _set_total(dlg, 0)
    assert dlg._dial._state["segment"] is None
    dlg._set_action(ACTION_RESTART)
    assert dlg._selected_monitor_lead_sec() is None
    dlg._set_action(ACTION_SHUTDOWN)
    dlg._monitor_on.set(False)
    dlg._refresh_monitor_row()
    assert dlg._selected_monitor_lead_sec() is None


# 9-7. 키가 빠진 파일도 깨진 파일로 버려야 함 (지금은 KeyError 로 파일이 남음)
@pytest.mark.parametrize(
    "drop",
    ["top:shutdown", "top:solo", "sd:monitor_lead_sec", "sd:paused_remaining"],
)
def test_restore_missing_keys_is_broken_file(
    fixed_app: dict[str, Any], appdata: Any, drop: str
) -> None:
    data = _data(_sd(_NOW + 1800, 1800, lead=300))
    where, key = drop.split(":")
    (data if where == "top" else data["shutdown"]).pop(key)
    _write_data(appdata, data)
    app = fixed_app["app"]
    try:
        app._restore_schedule()
    finally:
        _assert_safe(fixed_app)
        assert app.is_active is False
    assert not os.path.exists(_schedule_file(appdata))


# ------------------------------------------------------------------ #
# 최종 재검증 — 예약 상한 999시간 (Windows TIMEOUT_MAX 4294967 대응)  #
# ------------------------------------------------------------------ #

_MAX = ss.MAX_SCHEDULE_SECONDS
# PM 실측 Windows threading.TIMEOUT_MAX — 이 값을 넘는 Timer 는 스레드 안에서 죽음
_WIN_TIMEOUT_MAX = 4294967.0


def _assert_timers_under_win_max() -> None:
    for t in _Timer.created:
        assert t.interval <= _MAX < _WIN_TIMEOUT_MAX, t.interval


# 10-1. 어떤 예약·연장·일시정지·재개·단독 조합에서도 만든 Timer 가 상한을 넘지 않음
@pytest.mark.parametrize("total", [_MAX - 300, _MAX - 1, _MAX, _MAX + 1, 10**9])
@pytest.mark.parametrize("lead", [None, 0, 600])
@pytest.mark.parametrize("ops", ["e", "eee", "pe", "pee r", "per e", "s e"])
def test_no_timer_over_windows_max(
    monkeypatch: pytest.MonkeyPatch, total: int, lead: Optional[int], ops: str
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, total, lead)
    for op in ops.replace(" ", ""):
        if op == "e":
            app.extend_schedule(600)
        elif op == "p":
            app.pause_shutdown()
        elif op == "r":
            app.resume_shutdown()
        elif op == "s":
            app.schedule_monitor_only(total)
            app.cancel_shutdown(show_toast=False)
    _assert_timers_under_win_max()
    if app.is_paused:
        assert app._pause_remaining <= _MAX
    elif app.scheduled_time is not None and app.is_active:
        remaining = (app.scheduled_time - datetime.now()).total_seconds()
        assert remaining <= _MAX + 1


# 10-2. 상한 근처 복원(진행·일시정지·단독)도 Timer 가 상한 안
@pytest.mark.parametrize(
    "data",
    [
        _data(_sd(_NOW + _MAX, _MAX, lead=600)),
        _data(_sd(None, _MAX, paused=float(_MAX), lead=600)),
        _data(solo_target=_NOW + _MAX),
    ],
)
def test_restore_at_cap_timers(
    fixed_app: dict[str, Any], appdata: Any, data: dict
) -> None:
    _write_data(appdata, data)
    app = fixed_app["app"]
    app._restore_schedule()
    assert app.is_active or app._solo_monitor.armed
    _assert_timers_under_win_max()
    if app.is_paused:
        app.extend_schedule(600)
        app.resume_shutdown()
        _assert_timers_under_win_max()
    _assert_safe(fixed_app)


# 10-3. 모든 입력 경로가 상한·하한을 지킴 (직접 입력·±·휠·빠른 추가·드래그·초기화)
def test_every_input_path_capped(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog(monitor_default=False)
    dlg._set_unit(UNIT_HOUR)
    dlg.var_value.set("99999")
    assert dlg._total_seconds == _MAX and dlg._dial._state["label"] == "최대 999시간"
    dlg._step(5)
    assert dlg._total_seconds == _MAX
    dlg._dial._wheel(120 * 3)
    assert dlg._total_seconds == _MAX
    dlg._quick_add(3600)
    assert dlg._total_seconds == _MAX
    dlg._on_ring_drag(30, True)
    assert dlg._total_seconds == 1800 and dlg._dial._state["label"] == "종료까지"
    dlg._step(-10)
    assert dlg._total_seconds == 0
    dlg._quick_add(-100)
    assert dlg._total_seconds == 0
    dlg._quick_add(_MAX)
    dlg._reset_input()
    assert dlg._total_seconds == 0 and dlg._clipped is False


# 10-4. 상한 표시 — 다이얼 글자 "999:00 시간:분", 큰 숫자가 링 안쪽 지름 안에 들어감
def test_cap_dial_display_fits(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    assert ss._dial_text(_MAX, ACTION_SHUTDOWN) == ("종료까지", "999:00", "시간:분")
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, _MAX))
    st = dlg._sched_dial._state
    assert st["big"] in ("999:00", "998:59") and st["progress"] == 1.0
    ring = dlg._sched_dial
    if ring._font_big.actual("family") != "Segoe UI":
        pytest.skip("Segoe UI 가 없어 숫자 폭을 Windows 와 같게 잴 수 없음")
    inner = 2 * (ring._scaled(84) - ring._scaled(12) / 2)
    assert ring._font_big.measure(st["big"]) < inner


# 10-5. 연장 토스트 — 상한에 걸리면 실제로 늘린 만큼만 적음
@pytest.mark.parametrize("room, word", [(300, "5분"), (59, "59초")])
def test_extend_toast_shows_capped_amount(
    monkeypatch: pytest.MonkeyPatch, room: int, word: str
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.scheduled_time = datetime.now() + timedelta(seconds=_MAX - room + 0.6)
    app.extend_schedule(600)
    msg = app._show_confirm_toast.call_args.args[0]
    assert msg.endswith(f"으로 {word} 연장") or msg.endswith(
        f"으로 {ss._format_duration(room - 1)} 연장"
    )


# 10-6. 남은 시간이 상한 1초 안이면 늘릴 것이 없으므로 "0초 연장" 토스트를 띄우면 안 됨
@pytest.mark.xfail(
    strict=True,
    reason="왓슨 최종 재검증 발견: _capped_add 가 0 을 돌려도 Timer 를 다시 걸고 "
    "'HH:MM으로 0초 연장' 토스트를 띄움 (남은 이슈, 수정 후 xfail 제거)",
)
def test_extend_no_zero_second_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.scheduled_time = datetime.now() + timedelta(seconds=_MAX - 0.5)
    n = len(_Timer.created)
    app.extend_schedule(600)
    assert not app._show_confirm_toast.called
    assert len(_Timer.created) == n
