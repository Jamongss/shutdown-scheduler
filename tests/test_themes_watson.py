"""테마 작업(v1.3.0) 왓슨 보강 테스트 — 상태 보존·실패 경로·창 수명.

실제 창이 필요한 테스트는 가상 화면(SHUTDOWN_SCHEDULER_REAL_WINDOWS=1)에서만 돈다.
"""

import os
import subprocess
import sys
import textwrap
import tkinter as tk
from typing import Any, Callable, Iterator
from unittest import mock

import customtkinter as ctk
import pytest

from conftest import REAL_WINDOWS_ENV
from test_monitor_off import appdata, ss  # noqa: F401  (appdata 는 autouse 픽스처)
from test_monitor_schedule import _sched_app
from test_themes import (
    ACTIONS,
    THEME_IDS,
    _allowed,
    _contrast,
    _other_mode,
    _read_settings,
    _restore_appearance,  # noqa: F401  (autouse)
    _rgb,
    _stray,
    _switch,
    _write_settings,
    settings_app,  # noqa: F401
)
from test_ui_redesign import open_dialog, tk_root  # noqa: F401
from cfg import theme
from cfg.config import (
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    SETTINGS_KEY_CONTRAST_FIX,
    SETTINGS_KEY_DARK_THEME,
    SETTINGS_KEY_FOLLOW_SYSTEM,
    SETTINGS_KEY_LIGHT_THEME,
    SETTINGS_KEY_MONITOR_OFF,
    SETTINGS_KEY_THEME,
    UNIT_HOUR,
)

OpenDialog = Callable[..., tuple[Any, Any]]
SettingsApp = tuple[Any, Any, Any]


def _tick_chains(dlg: Any) -> int:
    """걸려 있는 _tick_countdown after 개수를 센다."""
    count = 0
    for after_id in dlg.top.tk.splitlist(dlg.top.tk.call("after", "info")):
        script = dlg.top.tk.call("after", "info", after_id)
        if "_tick_countdown" in str(script):
            count += 1
    return count


def _set_value(dlg: Any, seconds: int) -> None:
    dlg._total_seconds = seconds
    dlg._sync_value_text()
    dlg._refresh_monitor_row()


# ------------------------------------------------------------------ #
# 설정 화면 상태 보존                                                  #
# ------------------------------------------------------------------ #


def test_repaint_keeps_monitor_switch_off(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    app, dlg = open_dialog(monitor_default=False)
    dlg.top.update()
    height = dlg.top.winfo_height()
    assert not dlg._monitor_on.get()
    assert not dlg._monitor_expand.winfo_ismapped()

    _switch(app, "light")
    dlg.top.update()

    assert not dlg._monitor_on.get()
    assert not dlg._monitor_expand.winfo_ismapped()
    assert dlg._selected_monitor_lead_sec() is None
    assert dlg.top.winfo_height() == height


def test_repaint_keeps_monitor_tab_hidden_card(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg._set_action(ACTION_MONITOR_OFF)
    _set_value(dlg, 600)
    dlg.top.update()
    height = dlg.top.winfo_height()

    _switch(app, "cat")
    dlg.top.update()

    assert dlg._action == ACTION_MONITOR_OFF
    assert dlg._theme_action == ACTION_MONITOR_OFF
    assert not dlg._monitor_card.winfo_ismapped()
    assert not dlg._options_label.winfo_ismapped()
    assert dlg.top.winfo_height() == height


def test_repaint_keeps_restart_disabled_controls(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg._set_action(ACTION_RESTART)
    _set_value(dlg, 600)

    _switch(app, "light")
    dlg.top.update()

    tokens = theme.THEMES["light"]["tokens"]
    assert dlg._monitor_switch.cget("state") == "disabled"
    assert dlg._lead_entry.cget("state") == "disabled"
    assert dlg._monitor_title.cget("text_color") == tokens["textMuted"]


def test_repaint_keeps_warning_text(open_dialog: OpenDialog) -> None:  # noqa: F811
    app, dlg = open_dialog()
    dlg._on_chip(5)
    _set_value(dlg, 60)
    dlg.top.update()
    text = dlg._monitor_warn.cget("text")
    height = dlg.top.winfo_height()
    assert dlg._monitor_warn.winfo_ismapped()

    _switch(app, "light")
    dlg.top.update()

    assert dlg._monitor_warn.winfo_ismapped()
    assert dlg._monitor_warn.cget("text") == text
    assert dlg._confirm_enabled is False
    assert dlg.top.winfo_height() == height


def test_repaint_keeps_open_value_entry(open_dialog: OpenDialog) -> None:  # noqa: F811
    """폴링으로 바뀌면 포커스 이동이 없어 입력란이 열린 채로 재칠됨."""
    app, dlg = open_dialog()
    dlg._set_unit(UNIT_HOUR)
    _set_value(dlg, 7200)
    dlg._open_value_entry()
    dlg.top.update()
    assert dlg._entry.winfo_ismapped()

    _switch(app, "light")
    dlg.top.update()

    tokens = theme.THEMES["light"]["tokens"]
    assert dlg._entry.winfo_ismapped()
    assert dlg._entry.cget("text_color") == tokens["text"]
    assert dlg.var_value.get() == "2"
    assert dlg._total_seconds == 7200
    assert _stray(dlg.top, app._theme) == {}


def test_repaint_does_not_multiply_countdown(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg.top.update()
    assert _tick_chains(dlg) == 1
    for theme_id in ("light", "nord", "gh", "light"):
        _switch(app, theme_id)
    assert _tick_chains(dlg) == 1

    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    dlg._show_view("scheduled")
    for theme_id in ("gh", "light"):
        _switch(app, theme_id)
    assert _tick_chains(dlg) == 1


def test_repaint_does_not_touch_autostart(
    monkeypatch: pytest.MonkeyPatch, open_dialog: OpenDialog  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    enable = mock.Mock()
    disable = mock.Mock()
    monkeypatch.setattr(ss, "enable_autostart", enable)
    monkeypatch.setattr(ss, "disable_autostart", disable)
    before = dlg._autostart_var.get()
    _switch(app, "light")
    dlg.top.update()
    assert dlg._autostart_var.get() == before
    enable.assert_not_called()
    disable.assert_not_called()


# ------------------------------------------------------------------ #
# 예약됨 화면 상태 보존                                                #
# ------------------------------------------------------------------ #


def _schedule_snapshot(app: Any) -> tuple[Any, ...]:
    return (
        app.is_active,
        app.is_paused,
        app._pause_remaining,
        app.scheduled_time,
        app.scheduled_action,
        app._monitor_lead_sec,
        app._linked_monitor.due,
        app._solo_monitor.armed,
        app._solo_monitor.due,
    )


def test_repaint_keeps_paused_schedule(open_dialog: OpenDialog) -> None:  # noqa: F811
    app, dlg = open_dialog()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    dlg._show_view("scheduled")
    dlg._on_pause_resume()
    assert app.is_paused
    before = _schedule_snapshot(app)
    timers = list(ss.threading.Timer.created)

    _switch(app, "light")
    dlg.top.update()

    assert _schedule_snapshot(app) == before
    assert ss.threading.Timer.created == timers
    assert dlg._view == "scheduled"
    assert dlg._btn_pause_resume.cget("text") == "▶"
    # 일시정지 링은 새 테마의 textMuted 여야 함
    muted = theme.THEMES["light"]["tokens"]["textMuted"]
    assert dlg._sched_dial._state["color"] == muted
    assert _stray(dlg.top, app._theme) == {}


def test_repaint_keeps_solo_only_schedule(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    app.schedule_monitor_only(1800)
    dlg._show_view("scheduled")
    dlg.top.update()
    before = _schedule_snapshot(app)
    title = dlg._sched_monitor_title.cget("text")
    height = dlg.top.winfo_height()

    _switch(app, "nord")
    dlg.top.update()

    assert _schedule_snapshot(app) == before
    assert dlg._theme_action == ACTION_MONITOR_OFF
    assert dlg._sched_monitor_title.cget("text") == title
    assert dlg.top.winfo_height() == height
    assert _stray(dlg.top, app._theme) == {}


def test_repaint_keeps_solo_extra_row(open_dialog: OpenDialog) -> None:  # noqa: F811
    app, dlg = open_dialog()
    app.schedule_action(ACTION_SHUTDOWN, 3600, None)
    app.schedule_monitor_only(600)
    dlg._show_view("scheduled")
    dlg.top.update()
    assert dlg._sched_solo_row.winfo_ismapped()
    before = _schedule_snapshot(app)
    text = dlg._sched_solo_label.cget("text")

    _switch(app, "light")
    dlg.top.update()

    assert _schedule_snapshot(app) == before
    assert dlg._sched_solo_row.winfo_ismapped()
    assert dlg._sched_solo_label.cget("text") == text
    assert _stray(dlg.top, app._theme) == {}


@pytest.mark.parametrize("action", ACTIONS)
def test_back_to_setup_after_repaint_on_scheduled(
    open_dialog: OpenDialog, action: str  # noqa: F811
) -> None:
    """예약됨에서 바꾼 뒤 취소해 설정으로 돌아와도 새 테마·입력값이 그대로여야 함."""
    app, dlg = open_dialog()
    dlg._set_action(action)
    _set_value(dlg, 1800)
    if action == ACTION_MONITOR_OFF:
        app.schedule_monitor_only(1800)
    else:
        app.schedule_action(action, 1800, None)
    dlg._show_view("scheduled")
    _switch(app, "light")

    dlg._on_cancel_schedule()
    dlg.top.update()

    assert dlg._view == "setup"
    assert dlg._action == action
    assert dlg._total_seconds == 1800
    assert _stray(dlg.top, app._theme) == {}
    tab = dlg._action_btns[action]
    expected = theme.action_tokens(app._theme, action)
    assert tab.cget("fg_color") == expected["tabFill"]
    assert dlg._btn_confirm.cget("fg_color") == expected["buttonFill"]


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_paused_and_warning_have_no_stray_color(
    open_dialog: OpenDialog, theme_id: str  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg._on_chip(5)
    _set_value(dlg, 60)
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    assert _stray(dlg.top, app._theme) == {}, "warning"

    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    app.pause_shutdown()
    dlg._show_view("scheduled")
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    assert _stray(dlg.top, app._theme) == {}, "paused"


# ------------------------------------------------------------------ #
# 숨은 색 (누르거나 올려야 보이는 색)                                  #
# ------------------------------------------------------------------ #


def _walk(widget: tk.Misc) -> Iterator[Any]:
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_hover_and_disabled_colors_are_tokens(
    settings_app: SettingsApp, theme_id: str  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    allowed = _allowed(app._theme)
    stray: dict[str, str] = {}
    for top in (dlg.top, settings.top):
        top.update()
        for widget in _walk(top):
            if not isinstance(widget, ctk.CTkButton) or not widget.winfo_ismapped():
                continue
            options = ["hover_color"]
            # 켜진 버튼의 비활성 글자색은 화면에 나오지 않음
            if widget.cget("state") == "disabled":
                options.append("text_color_disabled")
            for option in options:
                value = widget.cget(option)
                if not isinstance(value, str) or _rgb(widget, value) not in allowed:
                    stray[f"{widget}.{option}"] = str(value)
    assert stray == {}


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_dropdown_menu_colors_are_tokens(
    settings_app: SettingsApp, theme_id: str  # noqa: F811
) -> None:
    """펼친 메뉴는 winfo_ismapped 가 거짓이라 보이는 색 검사에 안 걸림."""
    app, _, settings = settings_app
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    allowed = _allowed(app._theme)
    for menu_widget in (settings._dark_menu, settings._light_menu):
        menu = menu_widget._dropdown_menu
        for option in ("bg", "fg", "activebackground"):
            color = str(tk.Menu.cget(menu, option))
            assert _rgb(menu, color) in allowed, (option, color)


@pytest.mark.parametrize("start, target", [("gh", "light"), ("light", "gh")])
def test_dropdown_hover_text_follows_theme(
    open_dialog: OpenDialog, start: str, target: str  # noqa: F811
) -> None:
    app, _ = open_dialog(prepare=lambda a: _switch(a, start))
    app._open_settings_dialog()
    settings = app._settings_dialog
    try:
        _switch(app, target)
        settings.top.update()
        menu = settings._dark_menu._dropdown_menu
        active_text = str(tk.Menu.cget(menu, "activeforeground"))
        active_back = str(tk.Menu.cget(menu, "activebackground"))
        assert active_text == app._theme["tokens"]["text"]
        assert _contrast(active_text, active_back) >= 4.5
    finally:
        settings._close()


@pytest.mark.parametrize("theme_id", THEME_IDS)
@pytest.mark.parametrize("action", ACTIONS)
def test_confirm_opened_after_repaint_uses_new_theme(
    open_dialog: OpenDialog, theme_id: str, action: str  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    dlg._set_action(action)
    before = set(dlg.top.winfo_children())
    dlg._show_immediate_confirm()
    confirm = next(w for w in dlg.top.winfo_children() if w not in before)
    confirm.update()
    assert confirm.cget("fg_color") == app._theme["tokens"]["background"]
    assert _stray(confirm, app._theme) == {}
    confirm.destroy()


# ------------------------------------------------------------------ #
# 설정 파일                                                            #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b'"gh"',
        b"{broken",
        b"\xff\xfe\x00",
        b'\xef\xbb\xbf{"theme": "nord"}',
    ],
)
def test_broken_settings_file_gives_defaults(
    appdata: Any, raw: bytes  # noqa: F811
) -> None:
    folder = os.path.join(str(appdata), ss.APP_NAME)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ss.SETTINGS_FILE_NAME), "wb") as f:
        f.write(raw)
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: ss.MONITOR_OFF_DEFAULT}


@pytest.mark.parametrize(
    "data, light, expected",
    [
        # 따라가기 + 모드가 틀린 ID 는 그 키만 버려 기본 ID 로 돌아감
        (
            {SETTINGS_KEY_FOLLOW_SYSTEM: True, SETTINGS_KEY_DARK_THEME: "light"},
            False,
            "gh",
        ),
        (
            {SETTINGS_KEY_FOLLOW_SYSTEM: True, SETTINGS_KEY_LIGHT_THEME: "nord"},
            True,
            "light",
        ),
        # 따라가기 값이 깨지면 끈 것으로 보고 theme 를 씀
        ({SETTINGS_KEY_FOLLOW_SYSTEM: "yes", SETTINGS_KEY_THEME: "nord"}, True, "nord"),
        ({SETTINGS_KEY_FOLLOW_SYSTEM: None, SETTINGS_KEY_THEME: "cat"}, True, "cat"),
        ({SETTINGS_KEY_THEME: None, SETTINGS_KEY_DARK_THEME: "nord"}, False, "gh"),
        ({SETTINGS_KEY_THEME: True}, False, "gh"),
        # 레지스트리를 못 읽으면 다크로 봄
        (
            {
                SETTINGS_KEY_FOLLOW_SYSTEM: True,
                SETTINGS_KEY_DARK_THEME: "drac",
                SETTINGS_KEY_LIGHT_THEME: "light",
            },
            None,
            "drac",
        ),
    ],
)
def test_broken_combination_resolves_to_usable_theme(
    appdata: Any, data: dict[str, Any], light: Any, expected: str  # noqa: F811
) -> None:
    _write_settings(appdata, data)
    settings = ss.load_settings()
    assert theme.resolve_theme_id(settings, light) == expected
    assert theme.validate_theme(theme.get_theme(expected))


def test_contrast_fix_non_bool_is_dropped_and_default_is_on(
    appdata: Any,  # noqa: F811
) -> None:
    _write_settings(appdata, {SETTINGS_KEY_CONTRAST_FIX: "false"})
    settings = ss.load_settings()
    assert SETTINGS_KEY_CONTRAST_FIX not in settings
    fix = settings.get(SETTINGS_KEY_CONTRAST_FIX, ss.CONTRAST_FIX_DEFAULT)
    assert ss.get_theme("gh", fix) is theme.THEMES["gh"]


def test_real_save_failure_keeps_old_file_and_memory(
    monkeypatch: pytest.MonkeyPatch, appdata: Any, tmp_path: Any  # noqa: F811
) -> None:
    """파일 시스템을 가짜로 두지 않고 실제 쓰기 실패를 만든다."""
    _write_settings(appdata, {SETTINGS_KEY_THEME: "nord"})
    old = _read_settings(appdata)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    good_path = ss._settings_path()
    monkeypatch.setattr(ss, "_settings_path", lambda: str(blocker / "settings.json"))
    monkeypatch.setattr(ss.ctk, "set_appearance_mode", lambda mode: None)

    app = _sched_app(monkeypatch)
    app._settings = {SETTINGS_KEY_MONITOR_OFF: False, SETTINGS_KEY_THEME: "nord"}
    app._active_dialog = None
    app._theme = theme.THEMES["nord"]

    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})

    assert app._theme is theme.THEMES["light"]
    app._show_confirm_toast.assert_called_once()
    assert _read_settings(appdata) == old

    # 다시 쓸 수 있게 되면 다음 변경에서 메모리 값이 함께 저장됨
    monkeypatch.setattr(ss, "_settings_path", lambda: good_path)
    app._show_confirm_toast.reset_mock()
    app._update_theme_settings({SETTINGS_KEY_CONTRAST_FIX: False})
    app._show_confirm_toast.assert_not_called()
    saved = _read_settings(appdata)
    assert saved[SETTINGS_KEY_THEME] == "light"
    assert saved[SETTINGS_KEY_CONTRAST_FIX] is False


def test_follow_toggle_preserves_theme_key_in_file(
    monkeypatch: pytest.MonkeyPatch, appdata: Any  # noqa: F811
) -> None:
    monkeypatch.setattr(ss, "_read_system_light", lambda: True)
    monkeypatch.setattr(ss.ctk, "set_appearance_mode", lambda mode: None)
    app = _sched_app(monkeypatch)
    app._settings = {SETTINGS_KEY_MONITOR_OFF: True, SETTINGS_KEY_THEME: "nord"}
    app._active_dialog = None
    app._theme = theme.THEMES["nord"]

    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: True})
    assert app._theme is theme.THEMES["light"]
    assert _read_settings(appdata)[SETTINGS_KEY_THEME] == "nord"

    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: False})
    assert app._theme is theme.THEMES["nord"]
    saved = _read_settings(appdata)
    assert saved[SETTINGS_KEY_THEME] == "nord"
    assert saved[SETTINGS_KEY_MONITOR_OFF] is True


# ------------------------------------------------------------------ #
# 창 수명                                                              #
# ------------------------------------------------------------------ #


def test_close_schedule_keeps_settings_working(
    settings_app: SettingsApp,  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    dlg._on_cancel()
    assert app._active_dialog is None
    assert settings.top.winfo_exists()

    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    settings.top.update()

    light = theme.THEMES["light"]["tokens"]
    assert settings.top.cget("fg_color") == light["background"]
    assert _stray(settings.top, app._theme) == {}


def test_close_settings_keeps_schedule_working(
    settings_app: SettingsApp,  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    settings._close()
    assert app._settings_dialog is None
    assert dlg.top.winfo_exists()

    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    dlg.top.update()
    assert dlg.top.cget("fg_color") == theme.THEMES["light"]["tokens"]["background"]

    dlg._settings_btn.invoke()
    assert app._settings_dialog is not None
    # 다시 열면 지금 테마로 시작해야 함
    assert app._settings_dialog._theme is theme.THEMES["light"]
    app._settings_dialog._close()


def test_reopened_schedule_uses_theme_changed_while_closed(
    settings_app: SettingsApp,  # noqa: F811
) -> None:
    app, dlg, _settings = settings_app
    dlg._on_cancel()
    app._update_theme_settings({SETTINGS_KEY_THEME: "cat"})
    app._open_schedule_dialog()
    reopened = app._active_dialog
    reopened.top.update()
    assert reopened._theme is theme.THEMES["cat"]
    assert _stray(reopened.top, app._theme) == {}
    reopened.top.destroy()


def test_window_closed_during_mode_change_is_silent(
    monkeypatch: pytest.MonkeyPatch, settings_app: SettingsApp  # noqa: F811
) -> None:
    """Windows 의 ctk 는 모드 전환 중 update() 를 돌려 이벤트가 끼어들 수 있음."""
    app, dlg, settings = settings_app
    real = ctk.set_appearance_mode

    def _closing(mode: str) -> None:
        dlg._on_cancel()
        settings._close()
        real(mode)

    monkeypatch.setattr(ss.ctk, "set_appearance_mode", _closing)
    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    assert app._theme is theme.THEMES["light"]
    assert app._active_dialog is None
    assert app._settings_dialog is None


def test_apply_theme_on_destroyed_schedule_is_silent(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    _app, dlg = open_dialog()
    dlg.top.destroy()
    dlg.apply_theme(theme.THEMES["light"])


def test_nested_theme_change_during_mode_change_ends_on_latest(
    monkeypatch: pytest.MonkeyPatch, settings_app: SettingsApp  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    real = ctk.set_appearance_mode
    fired: list[str] = []

    def _nested(mode: str) -> None:
        if not fired:
            fired.append(mode)
            app._update_theme_settings({SETTINGS_KEY_THEME: "nord"})
        real(mode)

    monkeypatch.setattr(ss.ctk, "set_appearance_mode", _nested)
    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    dlg.top.update()
    assert app._theme is theme.THEMES["nord"]
    assert dlg._theme is theme.THEMES["nord"]
    assert settings._theme is theme.THEMES["nord"]


def test_settings_open_with_confirm_grab_keeps_grab(
    open_dialog: OpenDialog,  # noqa: F811
) -> None:
    """트레이 메뉴는 grab 과 무관하게 설정 창을 열 수 있음."""
    app, dlg = open_dialog()
    before = set(dlg.top.winfo_children())
    dlg._show_immediate_confirm()
    confirm = next(w for w in dlg.top.winfo_children() if w not in before)
    confirm.update()
    assert confirm.grab_current() is confirm

    app._dispatch_queue_event("open_settings")
    settings = app._settings_dialog
    settings.top.update()
    assert confirm.grab_current() is confirm

    # grab 이 있어도 값은 바뀌고, 확인 창은 예전 색을 유지함
    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    assert confirm.grab_current() is confirm
    gh_background = theme.THEMES["gh"]["tokens"]["background"]
    assert confirm.cget("fg_color") == gh_background

    confirm.destroy()
    dlg.top.update()
    assert dlg.top.grab_current() is None
    settings._close()
    assert app._settings_dialog is None


def test_immediate_execute_with_settings_open_keeps_settings(
    settings_app: SettingsApp,  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    calls: list[tuple[Any, ...]] = []
    dlg.callback = lambda *args: calls.append(args)
    before = set(dlg.top.winfo_children())
    dlg._show_immediate_confirm()
    confirm = next(w for w in dlg.top.winfo_children() if w not in before)
    confirm.update()
    confirm.focus_force()
    confirm.update()
    confirm.event_generate("<Return>")
    settings.top.update()

    assert len(calls) == 1
    assert app._active_dialog is None
    assert settings.top.winfo_exists()
    settings._close()


def test_execute_action_with_settings_open_uses_t_zero_only(
    monkeypatch: pytest.MonkeyPatch, settings_app: SettingsApp  # noqa: F811
) -> None:
    app, _, settings = settings_app
    run = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(ss.subprocess, "run", run)
    app.scheduled_action = ACTION_SHUTDOWN
    app._monitor_lead_sec = None

    app._execute_action()

    assert run.call_args.args[0] == ["shutdown", "/s", "/t", "0", "/f"]
    assert settings.top.winfo_exists()


_MAINLOOP_SCRIPT = textwrap.dedent("""
    import sys
    from unittest import mock
    sys.dont_write_bytecode = True
    sys.path.insert(0, sys.argv[1])
    sys.modules["pystray"] = mock.MagicMock()
    sys.modules["keyboard"] = mock.MagicMock()
    import shutdown_scheduler as ss
    ss._bring_to_front = lambda window: None
    ss.is_autostart_enabled = lambda: False
    light = [False]
    reads = []
    def _read():
        reads.append(1)
        return light[0]
    ss._read_system_light = _read
    ss.SYSTEM_THEME_POLL_MS = 100
    app = ss.ShutdownScheduler()
    root = app._tk_root
    python_errors = []
    background = []
    root.report_callback_exception = lambda *a: python_errors.append(repr(a[1]))
    # 기본 처리기는 tkinter 의 _tkerror(아무것도 안 함)라 세려고 바꿔 끼움
    if sys.argv[2] == "count":
        root.tk.createcommand("tkerror", background.append)
    beats = []
    holder = {}
    def first():
        app._open_settings_dialog()
        app._settings_dialog._close()
        app._open_schedule_dialog()
        dlg = app._active_dialog
        app._update_theme_settings({"theme": "nord"})
        dlg._on_cancel()
    def flip():
        app._update_theme_settings({"followSystem": True})
        light[0] = True
    def reopen():
        app._open_settings_dialog()
        app._open_schedule_dialog()
    def finish():
        polls = [
            i for i in root.tk.splitlist(root.tk.call("after", "info"))
            if "_poll_system_theme" in str(root.tk.call("after", "info", i))
        ]
        print("beats", len(beats))
        print("python_errors", python_errors)
        print("background", len(background))
        print("theme", app._theme["id"])
        print("poll_chains", len(polls))
        print("reads", len(reads) > 1)
        print("settings_open", app._settings_dialog is not None)
        holder["top"] = app._settings_dialog.top
        app._queue.put("exit")
        root.after(300, lambda: print("exit_not_reached"))
    root.after(100, first)
    root.after(500, flip)
    root.after(900, reopen)
    for ms in range(200, 1500, 100):
        root.after(ms, lambda: beats.append(1))
    root.after(1500, finish)
    app.tray_icon = mock.MagicMock()
    app._register_hotkeys = lambda: None
    app._start_tray = lambda: None
    app.run()
    print("mainloop_returned")
    try:
        holder["top"].winfo_exists()
        print("settings_alive")
    except Exception:
        print("settings_gone")
    print("tray_stopped", app.tray_icon.stop.called)
    """)


@pytest.mark.skipif(
    os.environ.get(REAL_WINDOWS_ENV) != "1", reason="실제 창은 가상 화면에서만 만듦"
)
@pytest.mark.parametrize("mode", ["count", "default"])
def test_mainloop_survives_after_scripts_of_closed_windows(
    appdata: Any, mode: str  # noqa: F811
) -> None:
    """닫힌 창의 after 는 배경 오류가 될 뿐 앱·폴링·종료를 막지 않음."""
    root = os.path.dirname(os.path.abspath(ss.__file__))
    result = subprocess.run(
        [sys.executable, "-c", _MAINLOOP_SCRIPT, root, mode],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "APPDATA": str(appdata)},
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.split("\n")
    assert "beats 13" in out, result.stdout
    assert "python_errors []" in out, result.stdout
    assert "theme light" in out, result.stdout
    assert "poll_chains 1" in out, result.stdout
    assert "reads True" in out, result.stdout
    assert "settings_open True" in out, result.stdout
    assert "mainloop_returned" in out, result.stdout
    assert "settings_gone" in out, result.stdout
    assert "tray_stopped True" in out, result.stdout
    assert "exit_not_reached" not in out
    assert "Traceback" not in result.stderr
    if mode == "count":
        assert "background 0" not in out, result.stdout
    else:
        # 실제 앱 경로에서는 tkinter 가 삼켜 stderr 에도 안 나옴
        assert "invalid command name" not in result.stderr


# ------------------------------------------------------------------ #
# 화면에 실제로 쓰는 글자/배경 대비 (보정 켬)                          #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_contrast_of_pairs_on_screen(theme_id: str) -> None:
    """확정안 4쌍 밖이지만 화면에 있는 글자 쌍도 4.5 이상이어야 함."""
    tokens = theme.get_theme(theme_id)["tokens"]
    for background in ("background", "surface", "surfaceAlt"):
        assert _contrast(tokens["text"], tokens[background]) >= 4.5, background
        assert _contrast(tokens["textMuted"], tokens[background]) >= 4.5, background
        for name, group in tokens["actions"].items():
            assert _contrast(group["accentText"], tokens[background]) >= 4.5, (
                name,
                background,
            )


# 확정안의 4.5 대상 밖이라 합격 기준 위반은 아님. 토큰을 고치면 strict 로 알 수 있게 둠
BADGE_BELOW_4_5 = {"rose", "gruv", "dim", "one", "nord", "light"}


@pytest.mark.parametrize(
    "theme_id",
    [
        pytest.param(
            theme_id,
            marks=(
                pytest.mark.xfail(strict=True, reason="배지 글자 대비 4.5 미만")
                if theme_id in BADGE_BELOW_4_5
                else ()
            ),
        )
        for theme_id in THEME_IDS
    ],
)
def test_contrast_of_scheduled_badge(theme_id: str) -> None:
    tokens = theme.get_theme(theme_id)["tokens"]
    for name, group in tokens["actions"].items():
        badge = theme.mix(group["accent"], tokens["surface"], 0.16)
        assert _contrast(group["accentText"], badge) >= 4.5, name


def _nested_dicts(node: Any) -> Iterator[dict[str, Any]]:
    yield node
    for value in node.values():
        if isinstance(value, dict):
            yield from _nested_dicts(value)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_original_palette_shares_nothing_with_themes(theme_id: str) -> None:
    """복원이 두 번째부터는 값이 같아 지문 비교로는 얕은 복사를 못 잡음. 객체로 확인함."""
    fixed = {id(node) for node in _nested_dicts(theme.THEMES[theme_id]["tokens"])}
    restored = theme.get_theme(theme_id, False)["tokens"]
    assert not fixed & {id(node) for node in _nested_dicts(restored)}
