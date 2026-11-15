"""예약 창 UI 개편 테스트 — 토큰, 다이얼 표시 규칙, 링 계산, 설정 화면 상호작용, 키보드.

실제 tk 창이 필요한 테스트는 화면(DISPLAY)이 없으면 건너뛴다.
"""

import math
import tkinter as tk
import types
from datetime import datetime
from typing import Any, Callable, Iterator, Optional
from unittest import mock

import pytest

from test_monitor_off import appdata, ss  # noqa: F401  (appdata 는 autouse 픽스처)
from test_monitor_schedule import _Timer, _due_timers, _sched_app
from cfg import theme
from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    DIALOG_HEIGHT,
    DIALOG_WIDTH,
    MAX_SCHEDULE_SECONDS,
    MONITOR_LEAD_MAX_MIN,
    SETTINGS_KEY_MONITOR_OFF,
    UNIT_HOUR,
    UNIT_MINUTE,
    UNIT_SECOND,
)

# ------------------------------------------------------------------ #
# 토큰                                                                 #
# ------------------------------------------------------------------ #

GH_EXPECTED = {
    "background": "#0d1117",
    "surface": "#161b22",
    "surfaceAlt": "#21262d",
    "border": "#30363d",
    "text": "#e6edf3",
    "textMuted": "#8b949e",
    "success": "#3fb950",
    "warning": "#d29922",
    "actions": {
        "shutdown": {
            "accent": "#ff7b72",
            "accentText": "#ff7b72",
            "tabFill": "#ff7b72",
            "onTab": "#0d1117",
            "buttonFill": "#d0443d",
            "onButton": "#ffffff",
        },
        "restart": {
            "accent": "#f0883e",
            "accentText": "#f0883e",
            "tabFill": "#f0883e",
            "onTab": "#0d1117",
            "buttonFill": "#b85c22",
            "onButton": "#ffffff",
        },
        "monitor": {
            "accent": "#58a6ff",
            "accentText": "#58a6ff",
            "tabFill": "#58a6ff",
            "onTab": "#0d1117",
            "buttonFill": "#1f6feb",
            "onButton": "#ffffff",
        },
    },
}


# T-1. gh 값은 설계안 p08·p10 표와 같고 키 구성이 BASE 8 + ACTION 6×3
def test_gh_tokens_match_design() -> None:
    gh = theme.get_theme("gh")
    assert gh["tokens"] == GH_EXPECTED
    assert len(theme.BASE_KEYS) == 8 and len(theme.ACTION_KEYS) == 6
    assert theme.validate_theme(gh) is True
    assert theme.SHAPE["ring"]["size"] == 196
    assert theme.SHAPE["ring"]["radius"] == 84
    assert theme.SHAPE["ring"]["stroke"] == 12
    assert theme.SHAPE["spacing"] == {"outer": 16, "gap": 12, "card": 14, "chip": 6}


# T-2. 필수 키가 빠지거나 색 형식이 틀리면 버리고, 모르는 id 는 기본 테마
@pytest.mark.parametrize(
    "breaker",
    [
        lambda t: t["tokens"].pop("warning"),
        lambda t: t["tokens"]["actions"].pop("monitor"),
        lambda t: t["tokens"]["actions"]["restart"].pop("onButton"),
        lambda t: t["tokens"].__setitem__("text", "#12345"),
        lambda t: t["tokens"].__setitem__("text", "#zzzzzz"),
        lambda t: t.pop("tokens"),
    ],
)
def test_validate_theme_rejects_broken(breaker: Callable[[dict], Any]) -> None:
    import copy

    broken = copy.deepcopy(theme.THEMES["gh"])
    breaker(broken)
    assert theme.validate_theme(broken) is False
    assert theme.validate_theme("gh") is False


def test_get_theme_fallback_and_action_tokens() -> None:
    assert theme.get_theme("없는테마")["id"] == "gh"
    gh = theme.get_theme()
    assert theme.action_tokens(gh, ACTION_SHUTDOWN)["accent"] == "#ff7b72"
    assert theme.action_tokens(gh, ACTION_RESTART)["accent"] == "#f0883e"
    assert theme.action_tokens(gh, ACTION_MONITOR_OFF)["accent"] == "#58a6ff"
    assert theme.action_tokens(gh, "unknown")["accent"] == "#ff7b72"


@pytest.mark.parametrize(
    "a, b, ratio, expected",
    [
        ("#ffffff", "#000000", 0.5, "#808080"),
        ("#ffffff", "#000000", 0.0, "#000000"),
        ("#ffffff", "#000000", 1.0, "#ffffff"),
        ("#ff7b72", "#161b22", 0.16, "#3b2a2f"),
        ("#ffffff", "#000000", 2.0, "#ffffff"),
    ],
)
def test_mix(a: str, b: str, ratio: float, expected: str) -> None:
    assert theme.mix(a, b, ratio) == expected


# ------------------------------------------------------------------ #
# 다이얼 표시 규칙 (설계안 p05)                                        #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "seconds, action, expected",
    [
        (0, ACTION_SHUTDOWN, ("종료까지", "즉시", "누르면 바로 종료")),
        (0, ACTION_RESTART, ("재시작까지", "즉시", "누르면 바로 재시작")),
        (0, ACTION_MONITOR_OFF, ("모니터 끄기까지", "즉시", "누르면 바로 모니터 끄기")),
        (-5, ACTION_SHUTDOWN, ("종료까지", "즉시", "누르면 바로 종료")),
        (1, ACTION_SHUTDOWN, ("종료까지", "1", "초")),
        (59, ACTION_SHUTDOWN, ("종료까지", "59", "초")),
        (60, ACTION_SHUTDOWN, ("종료까지", "1", "분")),
        (1800, ACTION_SHUTDOWN, ("종료까지", "30", "분")),
        (1725, ACTION_SHUTDOWN, ("종료까지", "28:45", "분:초")),
        (61, ACTION_SHUTDOWN, ("종료까지", "1:01", "분:초")),
        (3599, ACTION_SHUTDOWN, ("종료까지", "59:59", "분:초")),
        (3600, ACTION_SHUTDOWN, ("종료까지", "1:00", "시간:분")),
        (9000, ACTION_SHUTDOWN, ("종료까지", "2:30", "시간:분")),
        (9059, ACTION_SHUTDOWN, ("종료까지", "2:30", "시간:분")),
        (90000, ACTION_SHUTDOWN, ("종료까지", "25:00", "시간:분")),
    ],
)
def test_dial_text(seconds: int, action: str, expected: tuple) -> None:
    assert ss._dial_text(seconds, action) == expected


def test_confirm_label() -> None:
    now = datetime(2026, 10, 31, 3, 3, 0)
    assert ss._confirm_label(1800, ACTION_SHUTDOWN, now) == "03:33 종료 예약"
    assert ss._confirm_label(1800, ACTION_RESTART, now) == "03:33 재시작 예약"
    assert ss._confirm_label(0, ACTION_SHUTDOWN, now) == "종료 예약"
    assert ss._confirm_label(23 * 3600, ACTION_SHUTDOWN, now) == "02:03 종료 예약"


# ------------------------------------------------------------------ #
# 링 계산                                                              #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, 0.0), (-10, 0.0), (1800, 0.25), (3600, 0.5), (7200, 1.0), (9000, 1.0)],
)
def test_ring_progress(seconds: float, expected: float) -> None:
    assert ss._ring_progress(seconds) == expected


@pytest.mark.parametrize(
    "seconds, lead, expected",
    [
        (1800, 300, (1500 / 7200, 1800 / 7200)),
        (180, 300, (0.0, 180 / 7200)),
        (1800, None, None),
        (1800, 0, None),
        (0, 300, None),
        (9000, 300, None),
        (7300, 300, (7000 / 7200, 1.0)),
    ],
)
def test_monitor_segment(seconds: int, lead: Optional[int], expected: Any) -> None:
    assert ss._monitor_segment(seconds, lead) == expected


# ------------------------------------------------------------------ #
# 실제 tk 예약 창                                                      #
# ------------------------------------------------------------------ #


@pytest.fixture
def tk_root() -> Iterator[tk.Tk]:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("화면(DISPLAY)이 없어 tk 창을 만들 수 없음")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def open_dialog(
    monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk
) -> Iterator[Callable[..., tuple[Any, Any]]]:
    """예약 창을 여는 함수를 돌려준다. 끝나면 남은 창을 닫는다."""
    opened: list[Any] = []
    monkeypatch.setattr(ss, "_bring_to_front", lambda window: None)
    monkeypatch.setattr(ss, "is_autostart_enabled", lambda: False)

    def _open(
        monitor_default: bool = True, prepare: Optional[Callable[[Any], None]] = None
    ) -> tuple[Any, Any]:
        app = _sched_app(monkeypatch)
        app._settings = {SETTINGS_KEY_MONITOR_OFF: monitor_default}
        app._active_dialog = None
        app._tk_root = tk_root
        if prepare is not None:
            prepare(app)
        app._open_schedule_dialog()
        dlg = app._active_dialog
        opened.append(dlg)
        dlg.top.update()
        return app, dlg

    yield _open
    for dlg in opened:
        try:
            dlg.top.destroy()
        except tk.TclError:
            pass


def _key(dlg: Any, keysym: str, focus: Any = None) -> Optional[str]:
    """포커스를 정해 둔 채 _on_key 를 부른다."""
    dlg._focus_widget = lambda: focus
    return dlg._on_key(types.SimpleNamespace(keysym=keysym))


def _set_total(dlg: Any, seconds: int) -> None:
    dlg._total_seconds = seconds
    dlg._sync_value_text()
    dlg._update_preview()
    dlg._refresh_monitor_row()


# D-1. 처음 열면 설정 화면, 너비 340, 높이 = reqheight ÷ 배율, 포커스는 링
def test_dialog_layout_and_focus(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    top = dlg.top
    top.update_idletasks()
    scale = dlg._window_scaling()
    assert DIALOG_WIDTH == 340
    assert dlg._fitted_height == max(
        DIALOG_HEIGHT, math.ceil(dlg._main.winfo_reqheight() / scale)
    )
    assert top.winfo_width() == round(DIALOG_WIDTH * scale)
    assert str(dlg._dial.canvas.cget("takefocus")) == "1"
    assert dlg._view == "setup"
    assert dlg._preview_var.get() == "종료 예약"
    assert dlg._dial._state["big"] == "즉시"


# D-2. 단위 버튼은 증감 단위만 바꾸고 값은 유지, ±·초기화는 단위만큼 (최소 0)
def test_unit_is_step_not_reset(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    _set_total(dlg, 1800)
    dlg._set_unit(UNIT_HOUR)
    assert dlg._total_seconds == 1800
    dlg._step(1)
    assert dlg._total_seconds == 5400 and dlg.var_value.get() == "1"
    dlg._set_unit(UNIT_SECOND)
    dlg._step(-1)
    assert dlg._total_seconds == 5399 and dlg.var_value.get() == "5399"
    dlg._set_unit(UNIT_HOUR)
    dlg._step(-5)
    assert dlg._total_seconds == 0
    dlg._quick_add(1800)
    dlg._quick_add(3600)
    assert dlg._total_seconds == 5400
    assert dlg._dial._state["big"] == "1:30"
    dlg._reset_input()
    assert dlg._total_seconds == 0


# D-3. 가운데 숫자 입력은 값 × 단위
def test_center_entry_is_value_times_unit(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog()
    dlg._open_value_entry()
    dlg.top.update()
    assert dlg._entry.winfo_ismapped()
    dlg.var_value.set("45")
    assert dlg._total_seconds == 45 * 60
    dlg._set_unit(UNIT_SECOND)
    assert dlg.var_value.get() == "2700"
    dlg._close_value_entry()
    dlg.top.update()
    assert not dlg._entry.winfo_ismapped()


# D-4. 칩은 기준값·입력란을 함께 바꾸고, 직접 입력이 칩 값이 아니면 강조를 모두 끔
def test_chips_and_direct_input(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    _set_total(dlg, 1800)
    calls: list[Any] = []
    real = ss.ScheduleDialog._on_lead_change
    dlg._on_lead_change = lambda *a: (calls.append(a), real(dlg, *a))
    dlg._on_chip(5)
    assert dlg._monitor_lead_min == 5 and dlg.var_monitor_lead.get() == "5"
    accent = dlg._tok("monitor.accent")
    assert dlg._chip_btns[5].cget("border_color") == accent
    assert all(dlg._chip_btns[m].cget("border_color") != accent for m in (0, 1, 10))
    assert dlg._dial._state["segment"] == (1500 / 7200, 1800 / 7200)
    assert dlg._monitor_desc[0].cget("text") == "종료 5분 전 · "

    dlg.var_monitor_lead.set("7")
    assert dlg._monitor_lead_min == 7
    assert all(btn.cget("border_color") != accent for btn in dlg._chip_btns.values())
    dlg._on_chip(0)
    assert dlg._monitor_desc[0].cget("text") == "종료 직전에 화면 끔"
    assert dlg._dial._state["segment"] is None
    assert dlg._selected_monitor_lead_sec() == 0


# D-5. 모니터 시간 ≥ 예약 시간이면 경고색 문구 + 예약 버튼 비활성, 고치면 풀림
def test_warning_blocks_confirm(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._on_chip(5)
    _set_total(dlg, 300)
    dlg.top.update_idletasks()
    assert dlg._monitor_warn.winfo_manager() == "grid"
    assert dlg._monitor_warn.cget("text") == (
        "예약 시간보다 길어요. 시간을 늘리거나 칩을 바꾸세요."
    )
    assert dlg._monitor_warn.cget("text_color") == dlg._tok("warning")
    assert dlg._btn_confirm.cget("state") == "disabled"
    dlg.callback = mock.MagicMock()
    dlg._on_confirm()
    dlg.callback.assert_not_called()

    _set_total(dlg, 301)
    assert dlg._monitor_warn.winfo_manager() == ""
    assert dlg._btn_confirm.cget("state") == "normal"


# D-6. 모니터 탭은 추가 옵션을 숨기고 높이가 줄며, 재시작 탭은 카드를 흐리게 비활성화
def test_tabs_hide_or_disable_monitor_card(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog()
    h_shutdown = dlg._fitted_height
    dlg._set_action(ACTION_MONITOR_OFF)
    assert dlg._monitor_card.winfo_manager() == ""
    assert dlg._options_label.winfo_manager() == ""
    assert dlg._fitted_height < h_shutdown
    assert dlg._header_title.cget("text") == "모니터 끄기 예약"
    assert dlg._preview_var.get() == "모니터 끄기 예약"
    blue = dlg._tok("monitor.buttonFill")
    assert dlg._btn_confirm.cget("fg_color") == blue

    dlg._set_action(ACTION_RESTART)
    assert dlg._monitor_card.winfo_manager() == "grid"
    assert dlg._monitor_switch.cget("state") == "disabled"
    assert all(b.cget("state") == "disabled" for b in dlg._chip_btns.values())
    assert dlg._monitor_desc[0].cget("text") == "재시작에는 적용하지 않습니다"
    assert dlg._header_title.cget("text") == "재시작 예약"
    assert dlg._btn_confirm.cget("fg_color") == "#b85c22"
    assert dlg._selected_monitor_lead_sec() is None
    assert dlg._dial._state["color"] == "#f0883e"
    assert dlg._fitted_height == h_shutdown


# D-7. 스위치를 끄면 칩 영역이 접혀 높이가 줄고, 링의 모니터 구간이 사라짐
def test_switch_off_collapses(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog(monitor_default=True)
    dlg._on_chip(5)
    _set_total(dlg, 1800)
    h_on = dlg._fitted_height
    dlg._monitor_on.set(False)
    dlg._refresh_monitor_row()
    assert dlg._monitor_expand.winfo_manager() == ""
    assert dlg._fitted_height < h_on
    assert dlg._dial._state["segment"] is None
    assert dlg._monitor_desc[0].cget("text") == "종료 전에 화면을 미리 끕니다"

    _, dlg2 = open_dialog(monitor_default=False)
    assert dlg2._monitor_on.get() is False
    assert dlg2._monitor_expand.winfo_manager() == ""


# K-1. ↑/↓ 는 선택 단위만큼 ± (최소 0), N 입력란에 포커스면 N 을 ±1
def test_keyboard_up_down(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    assert _key(dlg, "Up", dlg._dial.canvas) == "break"
    assert dlg._total_seconds == 60
    dlg._set_unit(UNIT_HOUR)
    _key(dlg, "Up")
    assert dlg._total_seconds == 3660
    _key(dlg, "Down")
    _key(dlg, "Down")
    assert dlg._total_seconds == 0

    lead = dlg._lead_entry._entry
    dlg._on_chip(10)
    _key(dlg, "Up", lead)
    assert dlg._monitor_lead_min == 11 and dlg.var_monitor_lead.get() == "11"
    assert dlg._total_seconds == 0
    dlg._on_chip(0)
    _key(dlg, "Down", lead)
    assert dlg._monitor_lead_min == 0
    dlg.var_monitor_lead.set(str(MONITOR_LEAD_MAX_MIN))
    _key(dlg, "Up", lead)
    assert dlg._monitor_lead_min == MONITOR_LEAD_MAX_MIN


# K-2. 1/2/3(키패드 포함)은 탭 전환, Entry 에 포커스가 있으면 숫자 입력으로 넘김
def test_keyboard_tabs(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    assert _key(dlg, "2", dlg._dial.canvas) == "break"
    assert dlg._action == ACTION_RESTART
    _key(dlg, "KP_3")
    assert dlg._action == ACTION_MONITOR_OFF
    _key(dlg, "1")
    assert dlg._action == ACTION_SHUTDOWN
    assert _key(dlg, "2", dlg._lead_entry._entry) is None
    assert _key(dlg, "3", dlg._entry._entry) is None
    assert dlg._action == ACTION_SHUTDOWN


# K-3. 실제 키 이벤트도 창 바인딩으로 들어옴
def test_keyboard_real_event(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._dial.canvas.focus_force()
    dlg.top.update()
    dlg._dial.canvas.event_generate("<KeyPress-Up>", keysym="Up")
    dlg.top.update()
    assert dlg._total_seconds == 60


# K-4. Esc 는 창만 닫음 (on_close 로 _active_dialog 정리)
def test_keyboard_escape_closes(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog()
    _key(dlg, "Escape")
    assert app._active_dialog is None
    assert not dlg.top.winfo_exists()


# K-5. Enter 는 설정 화면에서 예약 (0초면 즉시 실행 확인창)
def test_keyboard_enter_confirms(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._show_immediate_confirm = mock.MagicMock()
    _key(dlg, "Return")
    dlg._show_immediate_confirm.assert_called_once_with()
    _set_total(dlg, 600)
    dlg._on_chip(0)
    dlg.callback = mock.MagicMock()
    _key(dlg, "KP_Enter")
    dlg.callback.assert_called_once_with(ACTION_SHUTDOWN, 0, 10, 0, 0)


# D-8. 동작 색 바인딩 — 헤더 아이콘 배경은 "강조색 16% + 카드", 탭은 탭 배경/탭 글자
def test_action_theme_bindings(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    tab = dlg._action_btns[ACTION_SHUTDOWN]
    assert tab.cget("fg_color") == "#ff7b72" and tab.cget("text_color") == "#0d1117"
    action_bound = [
        w for w, c in dlg._bindings if any(dlg._uses_action(v) for v in c.values())
    ]
    box = dlg._header_icon.master
    assert box in action_bound
    assert box.cget("fg_color") == theme.mix("#ff7b72", "#161b22", 0.16)
    dlg._set_action(ACTION_RESTART)
    assert box.cget("fg_color") == theme.mix("#f0883e", "#161b22", 0.16)
    # 단위 선택·빠른 추가 글자는 동작과 상관없이 모니터 색
    assert dlg._unit_btns[UNIT_MINUTE].cget("fg_color") == "#58a6ff"
    quick = [w for w, c in dlg._bindings if c.get("text_color") == "monitor.accentText"]
    assert len(quick) >= 3
    assert all(w.cget("text_color") == "#58a6ff" for w in quick)


# ------------------------------------------------------------------ #
# 2단계 — 링 입력 계산                                                 #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "dx, dy, expected",
    [
        (0, -1, 0),
        (1, 0, 30),
        (0, 1, 60),
        (-1, 0, 90),
        (-0.001, -1, 120),
        (math.sin(math.radians(3)), -math.cos(math.radians(3)), 1),
        (math.sin(math.radians(2.9)), -math.cos(math.radians(2.9)), 1),
        (math.sin(math.radians(1.4)), -math.cos(math.radians(1.4)), 0),
    ],
)
def test_angle_to_minutes(dx: float, dy: float, expected: int) -> None:
    assert ss._angle_to_minutes(dx, dy) == expected


@pytest.mark.parametrize(
    "prev, new, expected",
    [
        (100, 2, 120),
        (91, 29, 120),
        (90, 2, 2),
        (120, 5, 120),
        (5, 118, 0),
        (29, 91, 0),
        (30, 118, 118),
        (0, 110, 0),
        (40, 41, 41),
    ],
)
def test_clamp_wrap(prev: int, new: int, expected: int) -> None:
    assert ss._clamp_wrap(prev, new) == expected


@pytest.mark.parametrize(
    "acc, delta, expected",
    [
        (0, 120, (1, 0)),
        (0, 60, (0, 60)),
        (60, 60, (1, 0)),
        (0, -240, (-2, 0)),
        (-60, 30, (0, -30)),
        (0, 250, (2, 10)),
        (90, -120, (0, -30)),
    ],
)
def test_wheel_steps(acc: int, delta: int, expected: tuple) -> None:
    assert ss._wheel_steps(acc, delta) == expected


def test_in_ring_band() -> None:
    assert ss._in_ring_band(84, 0, 84, 12) is True
    assert ss._in_ring_band(0, -96, 84, 12) is True
    assert ss._in_ring_band(0, -97, 84, 12) is False
    assert ss._in_ring_band(40, 0, 84, 12) is False


# ------------------------------------------------------------------ #
# 2단계 — 예약됨 화면 view model                                      #
# ------------------------------------------------------------------ #


def test_view_model_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 31, 3, 0, 0)
    app = _sched_app(monkeypatch)
    vm = ss._view_model(app, now)
    assert vm.target is None
    assert ss._view_model(None, now).target is None

    app.is_active = True
    app.scheduled_action = ACTION_SHUTDOWN
    app.scheduled_time = now + ss.timedelta(seconds=1724.2)
    vm = ss._view_model(app, now)
    assert (vm.target, vm.accent_action, vm.label) == (
        "shutdown",
        ACTION_SHUTDOWN,
        "종료까지 남음",
    )
    assert (vm.remaining, vm.paused, vm.show_pause, vm.solo_extra) == (
        1725,
        False,
        True,
        False,
    )
    assert vm.fixed_at == app.scheduled_time

    app.scheduled_action = ACTION_RESTART
    assert ss._view_model(app, now).label == "재시작까지 남음"

    app.is_paused = True
    app._pause_remaining = 600.0
    vm = ss._view_model(app, now)
    assert (vm.label, vm.remaining, vm.paused, vm.fixed_at) == (
        "일시정지",
        600,
        True,
        None,
    )

    app.is_paused = False
    app._solo_monitor.due = now + ss.timedelta(seconds=900)
    vm = ss._view_model(app, now)
    assert vm.target == "shutdown" and vm.solo_extra is True

    # 종료 시각이 지났으면(실행 직전) 단독 예약 기준으로
    app.scheduled_time = now - ss.timedelta(seconds=1)
    vm = ss._view_model(app, now)
    assert (vm.target, vm.accent_action, vm.show_pause) == (
        "solo",
        ACTION_MONITOR_OFF,
        False,
    )
    assert vm.label == "모니터 끄기까지 남음" and vm.remaining == 900

    app.is_active = False
    vm = ss._view_model(app, now)
    assert vm.target == "solo" and vm.solo_extra is False


# ------------------------------------------------------------------ #
# 2단계 — +10분 연장 (가짜 Timer)                                     #
# ------------------------------------------------------------------ #


def _live(timers: list[_Timer]) -> list[_Timer]:
    return [t for t in timers if not t.cancelled]


def _exec_timers() -> list[_Timer]:
    return [
        t
        for t in _Timer.created
        if getattr(t.function, "__name__", "") == "<lambda>"
        and not t.function.__defaults__
    ]


def _warning_timers() -> list[_Timer]:
    return [
        t
        for t in _Timer.created
        if t.function.__defaults__
        and isinstance(t.function.__defaults__[0], str)
        and not t.function.__defaults__[0].startswith("monitor_")
    ]


# X-1. 진행 중 종료 → 실행·경고 Timer 를 남은+600 으로 다시 걸고 linked 도 다시 걺
def test_extend_running_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    old_exec = _live(_exec_timers())
    old_gen = app._linked_monitor.gen
    before = app.scheduled_time

    app.extend_schedule(600)

    assert all(t.cancelled for t in old_exec)
    live_exec = _live(_exec_timers())
    assert len(live_exec) == 1 and abs(live_exec[0].interval - 2400) < 2
    warns = sorted(t.interval for t in _live(_warning_timers()))
    assert len(warns) == 3 and abs(warns[0] - 1800) < 2 and abs(warns[2] - 2340) < 2
    linked = _live(_due_timers("linked"))
    assert len(linked) == 1 and abs(linked[0].interval - 2100) < 2
    assert app._linked_monitor.gen > old_gen
    assert abs((app.scheduled_time - before).total_seconds() - 600) < 2
    msg = app._show_confirm_toast.call_args.args[0]
    assert msg == f"{app.scheduled_time:%H:%M}으로 10분 연장"
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "schedule"


# X-2. linked 가 이미 꺼졌으면 다시 걸지 않음
def test_extend_keeps_done_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    app._linked_monitor.cancel()
    app._linked_done = True
    n = len(_due_timers("linked"))
    app.extend_schedule(600)
    assert len(_due_timers("linked")) == n
    assert app._linked_monitor.armed is False


# X-3. 일시정지 중이면 남은 초만 +600, Timer 는 만들지 않음
def test_extend_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.pause_shutdown()
    paused = app._pause_remaining
    n = len(_Timer.created)
    app.extend_schedule(600)
    assert app._pause_remaining == pytest.approx(paused + 600)
    assert len(_Timer.created) == n
    app.resume_shutdown()
    assert abs(_live(_exec_timers())[0].interval - (paused + 600)) < 2


# X-4. 단독 예약만 있으면 단독을 남은+600 으로, 둘 다 있으면 종료에만 적용
def test_extend_solo_and_both(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_monitor_only(900)
    app.extend_schedule(600)
    live = _live(_due_timers("solo"))
    assert len(live) == 1 and abs(live[0].interval - 1500) < 2
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_schedule"

    solo_due = app._solo_monitor.due
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.extend_schedule(600)
    assert app._solo_monitor.due == solo_due
    assert abs(_live(_exec_timers())[0].interval - 2400) < 2


# X-5. 무시하는 경우 — 예약 없음, 시각 지남, 즉시 실행 경로(종료·단독)
def test_extend_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.extend_schedule(600)
    assert _Timer.created == [] and not app._show_confirm_toast.called

    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.scheduled_time = datetime.now() - ss.timedelta(seconds=1)
    n = len(_Timer.created)
    app.extend_schedule(600)
    assert len(_Timer.created) == n

    app.schedule_action(ACTION_SHUTDOWN, 0)
    app.is_active = True
    app.scheduled_time = datetime.now() + ss.timedelta(seconds=100)
    n = len(_Timer.created)
    app.extend_schedule(600)
    assert len(_Timer.created) == n

    app2 = _sched_app(monkeypatch)
    app2.schedule_monitor_only(0)
    due = app2._solo_monitor.due
    app2.extend_schedule(600)
    assert app2._solo_monitor.due == due


# ------------------------------------------------------------------ #
# 2단계 — 예약됨 화면 상호작용 (실제 tk)                              #
# ------------------------------------------------------------------ #


# S-1. 예약 버튼 → 예약됨 화면, Esc/닫기는 창만 닫고 예약 유지
def test_confirm_switches_to_scheduled(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    app, dlg = open_dialog()
    _set_total(dlg, 1800)
    dlg._on_chip(5)
    dlg._on_confirm()
    dlg.top.update()
    assert app.is_active is True and app._monitor_lead_sec == 300
    assert dlg._view == "scheduled"
    assert dlg._badge.winfo_ismapped() and not dlg._setup_view.winfo_ismapped()
    assert dlg._sched_dial._state["label"] == "종료까지 남음"
    assert dlg._sched_dial._state["segment"] is not None
    assert dlg._btn_pause_resume.winfo_ismapped()
    assert dlg._sched_monitor_card.winfo_manager() == "grid"
    assert dlg._fitted_height < 600

    dlg.callback = mock.MagicMock()
    _key(dlg, "Return")
    dlg.callback.assert_not_called()
    _key(dlg, "Escape")
    assert app.is_active is True and app._active_dialog is None


# S-2. 예약 취소 → 설정 화면 복귀, 다시 열면 예약 여부에 맞는 화면
def test_cancel_returns_to_setup(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_RESTART, 1725))
    assert dlg._view == "scheduled"
    assert dlg._sched_dial._state["color"] == "#f0883e"
    assert dlg._header_title.cget("text") == "재시작 예약"
    assert dlg._sched_monitor_card.winfo_manager() == ""
    dlg._on_cancel_schedule()
    assert app.is_active is False
    assert dlg._view == "setup" and dlg._badge.winfo_manager() == ""


# S-3. 모니터 단독 예약도 예약됨 화면 — 파랑, 일시정지 버튼 숨김, 취소·연장은 단독에 적용
def test_solo_scheduled_view(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog()
    dlg._set_action(ACTION_MONITOR_OFF)
    _set_total(dlg, 900)
    dlg._on_confirm()
    dlg.top.update()
    assert app._solo_monitor.armed and not app.is_active
    assert dlg._view == "scheduled"
    assert dlg._sched_dial._state["color"] == "#58a6ff"
    assert not dlg._btn_pause_resume.winfo_ismapped()
    due = app._solo_monitor.due
    dlg._on_extend()
    assert abs((app._solo_monitor.due - due).total_seconds() - 600) < 2
    _key(dlg, "space")
    assert app.is_paused is False
    dlg._on_cancel_schedule()
    assert app._solo_monitor.armed is False and dlg._view == "setup"


# S-4. 둘 다 있으면 링·버튼은 종료 기준, 모니터 카드의 ✕ 로 단독만 따로 취소
def test_both_solo_row(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    def prepare(a: Any) -> None:
        a.schedule_action(ACTION_SHUTDOWN, 1800, 300)
        a.schedule_monitor_only(900)

    app, dlg = open_dialog(prepare=prepare)
    assert dlg._sched_solo_row.winfo_manager() == "grid"
    assert dlg._sched_solo_label.cget("text").startswith("모니터만 ")
    assert dlg._sched_solo_label.cget("text").endswith("에 끔 (PC 켜 둠)")
    assert "PC 켜 둠" not in dlg._sched_monitor_title.cget("text")
    dlg._on_cancel_solo()
    assert app._solo_monitor.armed is False and app.is_active is True
    assert dlg._sched_solo_row.winfo_manager() == ""
    dlg._on_cancel_schedule()
    assert dlg._view == "setup"


# S-5. 일시정지 버튼·Space — 링은 보조 글자 색, 라벨 "일시정지", 아이콘 II↔▶
def test_pause_button_and_space(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, 1800))
    assert dlg._btn_pause_resume.cget("text") == "II"
    dlg._on_pause_resume()
    assert app.is_paused is True
    assert dlg._sched_dial._state["color"] == dlg._tok("textMuted")
    assert dlg._sched_dial._state["label"] == "일시정지"
    assert dlg._sched_dial._state["time"] == ""
    assert dlg._btn_pause_resume.cget("text") == "▶"
    assert _key(dlg, "space", dlg._sched_dial.canvas) == "break"
    assert app.is_paused is False
    assert dlg._btn_pause_resume.cget("text") == "II"


# S-6. +10분 연장 버튼과 "다시 설정" → 새로 예약하면 기존 예약을 대체
def test_extend_button_and_reset_view(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    app, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, 1800))
    before = app.scheduled_time
    dlg._on_extend()
    assert abs((app.scheduled_time - before).total_seconds() - 600) < 2
    # 창을 여는 시간만큼 남은 시간이 줄어드므로 고정값이 아니라 실제 남은 초로 비교함
    left = math.ceil((app.scheduled_time - datetime.now()).total_seconds())
    shown = {ss._dial_text(s, ACTION_SHUTDOWN)[1] for s in range(left, left + 3)}
    assert dlg._sched_dial._state["big"] in shown

    dlg._show_view("setup")
    assert dlg._view == "setup" and app.is_active is True
    _set_total(dlg, 600)
    dlg._monitor_on.set(False)
    dlg._on_confirm()
    remaining = (app.scheduled_time - datetime.now()).total_seconds()
    assert abs(remaining - 600) < 2 and dlg._view == "scheduled"


# R-1. 링 클릭·드래그 — 1분 단위, 12시 넘김 고정, 120분 넘는 값은 표시만
def test_ring_drag(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._on_ring_drag(30, True)
    assert dlg._total_seconds == 1800 and dlg.var_value.get() == "30"
    dlg._on_ring_drag(100, False)
    dlg._on_ring_drag(3, False)
    assert dlg._total_seconds == 7200
    dlg._on_ring_drag(110, False)
    assert dlg._total_seconds == 110 * 60
    dlg._on_ring_drag(5, True)
    dlg._on_ring_drag(117, False)
    assert dlg._total_seconds == 0

    dlg._quick_add(3 * 3600)
    assert dlg._dial._state["progress"] == 1.0 and dlg._dial._state["big"] == "3:00"


# R-2. 실제 마우스 이벤트 — 띠 안만 반응, 가운데 클릭은 숫자 입력란
def test_ring_mouse_events(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    canvas = dlg._dial.canvas
    c = dlg._dial._px / 2
    r = dlg._dial._scaled(84)
    canvas.event_generate("<Button-1>", x=int(c + r), y=int(c))
    canvas.event_generate("<B1-Motion>", x=int(c), y=int(c + r))
    canvas.event_generate("<ButtonRelease-1>", x=int(c), y=int(c + r))
    dlg.top.update()
    assert dlg._total_seconds == 3600
    canvas.event_generate("<B1-Motion>", x=int(c - r), y=int(c))
    dlg.top.update()
    assert dlg._total_seconds == 3600
    canvas.event_generate("<Button-1>", x=2, y=2)
    dlg.top.update()
    assert dlg._total_seconds == 3600
    canvas.event_generate("<Button-1>", x=int(c), y=int(c))
    dlg.top.update()
    assert dlg._entry.winfo_ismapped()


# R-3. 휠 — 120 단위로 모아 선택 단위만큼, 최소 0
def test_ring_wheel(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._dial._wheel(120)
    assert dlg._total_seconds == 60
    dlg._dial._wheel(60)
    assert dlg._total_seconds == 60
    dlg._dial._wheel(60)
    assert dlg._total_seconds == 120
    dlg._set_unit(UNIT_HOUR)
    dlg._dial._wheel(240)
    assert dlg._total_seconds == 120 + 7200
    dlg._dial._wheel(-600)
    assert dlg._total_seconds == 0
    dlg._dial.canvas.event_generate("<Button-4>")
    dlg.top.update()
    assert dlg._total_seconds == 3600


# R-4. 배율이 바뀌어도(다른 DPI 모니터로 이동) 숨긴 화면·줄이 다시 나타나지 않음
#      ctk 는 grid_remove 를 감싸지 않아 배율 변경 때 마지막 grid 호출을 다시 부름
def test_hidden_views_stay_hidden_on_scaling(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    import customtkinter as ctk

    _, dlg = open_dialog(prepare=lambda a: a.schedule_action(ACTION_SHUTDOWN, 1800))
    try:
        ctk.set_widget_scaling(1.5)
        dlg.top.update()
        assert dlg._setup_view.winfo_manager() == ""
        assert dlg._scheduled_view.winfo_manager() == "grid"
        assert dlg._sched_dial._px == round(196 * 1.5)
        dlg._show_view("setup")
        assert dlg._scheduled_view.winfo_manager() == ""
        assert dlg._badge.winfo_manager() == ""
    finally:
        ctk.set_widget_scaling(1.0)
        dlg.top.update()
    assert dlg._setup_view.winfo_manager() == "grid"
    assert dlg._scheduled_view.winfo_manager() == ""
    assert dlg._dial._px == 196


# R-5. PIL 이 없을 때만 create_arc 대체 경로 — 트랙 위에 진행 구간·핸들 순서로 쌓임
def test_ring_fallback_without_pil(
    monkeypatch: pytest.MonkeyPatch, open_dialog: Callable[..., tuple[Any, Any]]
) -> None:
    monkeypatch.setattr(ss, "PIL_AVAILABLE", False)
    _, dlg = open_dialog()
    _set_total(dlg, 1800)
    dlg._on_chip(5)
    canvas = dlg._dial.canvas
    items = dlg._dial._fallback_items
    assert [canvas.type(i) for i in items] == ["oval", "arc", "arc", "oval"]
    order = canvas.find_all()
    assert [i for i in order if i in items] == items
    assert order.index(items[-1]) < order.index(dlg._dial._big_item)
    assert dlg._header_icon.cget("text") == "⏻"


# Z-1. PM 결정: 0초 + 모니터 N>0 은 막지 않음 — 즉시 확인창, lead 는 0(직전), 지난 시각 안 보임
def test_zero_seconds_with_lead_not_blocked(
    open_dialog: Callable[..., tuple[Any, Any]],
) -> None:
    _, dlg = open_dialog(monitor_default=True)
    dlg._on_chip(5)
    _set_total(dlg, 0)
    dlg.top.update_idletasks()
    assert dlg._monitor_warn.winfo_manager() == ""
    assert dlg._btn_confirm.cget("state") == "normal"
    assert [lbl.cget("text") for lbl in dlg._monitor_desc] == [
        "즉시 실행이라 종료 직전에 화면 끔",
        "",
        "",
    ]
    assert dlg._selected_monitor_lead_sec() == 0
    dlg._show_immediate_confirm = mock.MagicMock()
    dlg._on_confirm()
    dlg._show_immediate_confirm.assert_called_once_with()

    _set_total(dlg, 300)
    assert dlg._btn_confirm.cget("state") == "disabled"
    _set_total(dlg, 1800)
    assert dlg._selected_monitor_lead_sec() == 300


# ------------------------------------------------------------------ #
# PM 결정: Windows TIMEOUT_MAX 4294967s 대응으로 상한 999시간         #
# ------------------------------------------------------------------ #

MAX_S = MAX_SCHEDULE_SECONDS


def test_max_below_windows_timeout() -> None:
    """상한은 Windows threading.TIMEOUT_MAX(4294967.0, PM 실측)보다 작아야 한다."""
    assert MAX_S == 999 * 3600
    assert MAX_S < 4294967


# M-1. 직접 입력 999시간은 그대로, 1000시간은 999시간으로 자르고 "최대 999시간" 표시
def test_cap_direct_input(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._set_unit(UNIT_HOUR)
    dlg.var_value.set("999")
    assert dlg._total_seconds == MAX_S
    assert dlg._dial._state["label"] == "종료까지"
    dlg.var_value.set("1000")
    assert dlg._total_seconds == MAX_S and dlg.var_value.get() == "999"
    assert dlg._dial._state["label"] == "최대 999시간"
    dlg._set_unit(UNIT_SECOND)
    dlg.var_value.set(str(MAX_S + 1))
    assert dlg._total_seconds == MAX_S and dlg.var_value.get() == str(MAX_S)
    dlg._step(-1)
    assert dlg._total_seconds == MAX_S - 1
    assert dlg._dial._state["label"] == "종료까지"


# M-2. ±·휠·빠른 추가로 넘겨도 상한에서 멈춤 (999h, 999h+1s 경계)
def test_cap_step_wheel_quick(open_dialog: Callable[..., tuple[Any, Any]]) -> None:
    _, dlg = open_dialog()
    dlg._set_unit(UNIT_SECOND)
    _set_total(dlg, MAX_S - 1)
    dlg._step(1)
    assert dlg._total_seconds == MAX_S and dlg._clipped is False
    dlg._step(1)
    assert dlg._total_seconds == MAX_S and dlg._clipped is True
    dlg._reset_input()
    assert dlg._clipped is False
    _set_total(dlg, MAX_S - 60)
    dlg._quick_add(3600)
    assert dlg._total_seconds == MAX_S
    assert dlg._dial._state["label"] == "최대 999시간"
    dlg._set_unit(UNIT_HOUR)
    dlg._dial._wheel(240)
    assert dlg._total_seconds == MAX_S
    dlg.callback = mock.MagicMock()
    dlg._monitor_on.set(False)
    dlg._on_confirm()
    dlg.callback.assert_called_once_with(ACTION_SHUTDOWN, 999, 0, 0, None)


# M-3. schedule_action·schedule_monitor_only 는 방어로 상한까지 자름
def test_cap_schedule_defense(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, MAX_S + 3600)
    assert _live(_exec_timers())[0].interval == MAX_S
    app.schedule_monitor_only(MAX_S + 1)
    assert _live(_due_timers("solo"))[0].interval == MAX_S


# M-4. 연장 결과가 상한을 넘으면 상한까지만, 이미 상한이면 무시
def test_cap_extend(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, MAX_S - 300)
    app.extend_schedule(600)
    live = _live(_exec_timers())
    assert len(live) == 1 and live[0].interval <= MAX_S
    assert live[0].interval > MAX_S - 2
    assert "으로 4분 59초 연장" in app._show_confirm_toast.call_args.args[0] or (
        "으로 5분 연장" in app._show_confirm_toast.call_args.args[0]
    )
    app._show_confirm_toast.reset_mock()
    # 시간이 흐르며 남은 초가 상한 밑으로 내려가지 않게 1초 여유를 둠
    app.scheduled_time = datetime.now() + ss.timedelta(seconds=MAX_S + 1)
    n = len(_Timer.created)
    app.extend_schedule(600)
    assert len(_Timer.created) == n
    app._show_confirm_toast.assert_not_called()

    app.pause_shutdown()
    app._pause_remaining = MAX_S - 100
    app.extend_schedule(600)
    assert app._pause_remaining == MAX_S
    app.extend_schedule(600)
    assert app._pause_remaining == MAX_S

    app2 = _sched_app(monkeypatch)
    app2.schedule_monitor_only(MAX_S - 120)
    app2.extend_schedule(600)
    assert _live(_due_timers("solo"))[0].interval <= MAX_S
    app2._solo_monitor.due = datetime.now() + ss.timedelta(seconds=MAX_S + 1)
    due = app2._solo_monitor.due
    n = len(_Timer.created)
    app2.extend_schedule(600)
    assert app2._solo_monitor.due == due and len(_Timer.created) == n
