"""테마 10종·테마 설정 테스트. 실제 창 테스트는 가상 화면에서만 돈다 (conftest 참고)."""

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import tkinter as tk
import types
from typing import Any, Callable, Iterator, Optional
from unittest import mock

import customtkinter as ctk
import pytest
from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass
from PIL import ImageGrab

from conftest import REAL_WINDOWS_ENV
from test_monitor_off import appdata, ss  # noqa: F401  (appdata 는 autouse 픽스처)
from test_monitor_schedule import _sched_app
from test_ui_redesign import open_dialog, tk_root  # noqa: F401
from cfg import config, theme
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
    SYSTEM_THEME_POLL_MS,
    UNIT_HOUR,
    UNIT_MINUTE,
)

THEME_IDS = [
    "gh",
    "tokyo",
    "rose",
    "gruv",
    "dim",
    "one",
    "cat",
    "nord",
    "drac",
    "light",
]
ACTIONS = [ACTION_SHUTDOWN, ACTION_RESTART, ACTION_MONITOR_OFF]
# 자료 파일(설계안 기계 추출본)은 레포 밖에 있어 THEMES·PALETTE_OVERRIDES 의 지문으로 대조함
DATA_FILE_SHA256 = "5293be83811459431e503a3e9a6834282a820c07e8dedd1f0853c40729f564ce"


@pytest.fixture(autouse=True)
def _restore_appearance() -> Iterator[None]:
    """ctk 모드는 프로세스 전역이라 다음 테스트로 새지 않게 되돌림."""
    yield
    ctk.set_appearance_mode(config.CTK_APPEARANCE)


# ------------------------------------------------------------------ #
# 토큰 값                                                              #
# ------------------------------------------------------------------ #


def _fingerprint() -> str:
    data = {"themes": theme.THEMES, "overrides": theme.PALETTE_OVERRIDES}
    text = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def test_theme_ids_and_validity() -> None:
    assert list(theme.THEMES) == THEME_IDS
    assert all(theme.validate_theme(t) for t in theme.THEMES.values())
    assert theme.theme_ids("light") == ["light"]
    assert theme.theme_ids("dark") == THEME_IDS[:-1]


def test_values_match_data_file() -> None:
    assert _fingerprint() == DATA_FILE_SHA256


@pytest.mark.parametrize("mode", [None, "sepia", 1])
def test_validate_rejects_bad_mode(mode: Any) -> None:
    broken = {**theme.THEMES["gh"], "mode": mode}
    assert theme.validate_theme(broken) is False


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_swatch_colors(theme_id: str) -> None:
    item = theme.THEMES[theme_id]
    tokens = item["tokens"]
    assert theme.swatch_colors(item) == (
        tokens["background"],
        tokens["surface"],
        tokens["actions"]["shutdown"]["accent"],
        tokens["actions"]["monitor"]["accent"],
    )


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_contrast_fixed_tokens(theme_id: str) -> None:
    tokens = theme.get_theme(theme_id)["tokens"]
    assert _contrast(tokens["textMuted"], tokens["surfaceAlt"]) >= 4.5
    for group in tokens["actions"].values():
        assert _contrast(group["onTab"], group["tabFill"]) >= 4.5
        assert _contrast(group["onButton"], group["buttonFill"]) >= 4.5
        assert _contrast(group["accentText"], tokens["surfaceAlt"]) >= 4.5


# ------------------------------------------------------------------ #
# 보정 끔 (원본 팔레트)                                                #
# ------------------------------------------------------------------ #


def _node(tokens: dict[str, Any], path: str) -> str:
    node: Any = tokens
    for part in path.split("."):
        node = node[part]
    return node


def test_palette_restores_39_paths() -> None:
    assert sum(len(v) for v in theme.PALETTE_OVERRIDES.values()) == 39
    for theme_id, overrides in theme.PALETTE_OVERRIDES.items():
        tokens = theme.get_theme(theme_id, False)["tokens"]
        for path, color in overrides.items():
            assert _node(tokens, path) == color


def test_palette_leaves_other_values() -> None:
    fixed = theme.THEMES["tokyo"]["tokens"]
    restored = theme.get_theme("tokyo", False)["tokens"]
    assert restored["textMuted"] == "#737aa2"
    assert restored["actions"] == fixed["actions"]
    assert restored["surface"] == fixed["surface"]


def test_rose_has_nothing_to_restore() -> None:
    assert theme.get_theme("rose", False) == theme.THEMES["rose"]


def test_get_theme_does_not_mutate_themes() -> None:
    before = copy.deepcopy(theme.THEMES)
    for theme_id in THEME_IDS:
        theme.get_theme(theme_id, False)
    assert theme.THEMES == before


def test_contrast_fix_on_returns_same_object() -> None:
    assert theme.get_theme("nord", True) is theme.THEMES["nord"]


def test_unknown_id_without_fix_is_gh_original() -> None:
    result = theme.get_theme("없는ID", False)
    assert result["id"] == "gh"
    assert result["tokens"]["actions"]["shutdown"]["buttonFill"] == "#f85149"


# ------------------------------------------------------------------ #
# 설정 파일                                                            #
# ------------------------------------------------------------------ #


def _write_settings(appdata_dir: Any, data: Any) -> None:
    folder = os.path.join(str(appdata_dir), ss.APP_NAME)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ss.SETTINGS_FILE_NAME), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _read_settings(appdata_dir: Any) -> dict[str, Any]:
    path = os.path.join(str(appdata_dir), ss.APP_NAME, ss.SETTINGS_FILE_NAME)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_old_settings_file_adds_no_theme_keys(appdata: Any) -> None:  # noqa: F811
    _write_settings(appdata, {SETTINGS_KEY_MONITOR_OFF: False})
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}


def test_missing_settings_file_gives_default_only(appdata: Any) -> None:  # noqa: F811
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: ss.MONITOR_OFF_DEFAULT}


@pytest.mark.parametrize(
    "key, bad",
    [
        (SETTINGS_KEY_THEME, ["x"]),
        (SETTINGS_KEY_THEME, {"a": 1}),
        (SETTINGS_KEY_THEME, 1),
        (SETTINGS_KEY_THEME, "없는ID"),
        (SETTINGS_KEY_FOLLOW_SYSTEM, "yes"),
        (SETTINGS_KEY_FOLLOW_SYSTEM, 1),
        (SETTINGS_KEY_CONTRAST_FIX, 0),
        (SETTINGS_KEY_DARK_THEME, "light"),
        (SETTINGS_KEY_DARK_THEME, "없는ID"),
        (SETTINGS_KEY_LIGHT_THEME, "gh"),
    ],
)
def test_bad_key_is_dropped_alone(
    appdata: Any, key: str, bad: Any  # noqa: F811
) -> None:
    good_key = (
        SETTINGS_KEY_LIGHT_THEME
        if key != SETTINGS_KEY_LIGHT_THEME
        else SETTINGS_KEY_DARK_THEME
    )
    good_value = "light" if good_key == SETTINGS_KEY_LIGHT_THEME else "nord"
    _write_settings(
        appdata, {SETTINGS_KEY_MONITOR_OFF: False, key: bad, good_key: good_value}
    )
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False, good_key: good_value}


def test_valid_theme_keys_are_kept(appdata: Any) -> None:  # noqa: F811
    data = {
        SETTINGS_KEY_MONITOR_OFF: True,
        SETTINGS_KEY_THEME: "light",
        SETTINGS_KEY_FOLLOW_SYSTEM: True,
        SETTINGS_KEY_DARK_THEME: "nord",
        SETTINGS_KEY_LIGHT_THEME: "light",
        SETTINGS_KEY_CONTRAST_FIX: False,
    }
    _write_settings(appdata, data)
    assert ss.load_settings() == data


def test_toggle_monitor_off_keeps_theme_keys(
    monkeypatch: pytest.MonkeyPatch, appdata: Any  # noqa: F811
) -> None:
    app = _sched_app(monkeypatch)
    app._settings = {
        SETTINGS_KEY_MONITOR_OFF: True,
        SETTINGS_KEY_THEME: "nord",
        SETTINGS_KEY_CONTRAST_FIX: False,
    }
    app._toggle_monitor_off()
    assert _read_settings(appdata) == {
        SETTINGS_KEY_MONITOR_OFF: False,
        SETTINGS_KEY_THEME: "nord",
        SETTINGS_KEY_CONTRAST_FIX: False,
    }


def test_update_saves_and_applies(
    monkeypatch: pytest.MonkeyPatch, appdata: Any  # noqa: F811
) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = None
    app._update_theme_settings({SETTINGS_KEY_THEME: "light"})
    assert app._theme["id"] == "light"
    assert ctk.get_appearance_mode() == "Light"
    assert _read_settings(appdata)[SETTINGS_KEY_THEME] == "light"
    app._show_confirm_toast.assert_not_called()


def test_save_failure_applies_in_memory_with_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = None
    monkeypatch.setattr(ss, "save_settings", lambda settings: False)
    app._update_theme_settings({SETTINGS_KEY_THEME: "nord"})
    assert app._theme["id"] == "nord"
    app._show_confirm_toast.assert_called_once_with(
        "설정 저장에 실패해 이번 실행에만 적용됩니다.", toast_type="info"
    )


def test_enabling_follow_rereads_system(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = None
    monkeypatch.setattr(ss, "_read_system_light", lambda: True)
    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: True})
    assert app._system_light is True
    assert app._theme["id"] == "light"


def test_same_theme_does_not_repaint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = mock.MagicMock()
    app._apply_theme_settings()
    app._active_dialog.apply_theme.assert_not_called()


def test_changed_theme_repaints_both_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = mock.MagicMock()
    app._settings_dialog = mock.MagicMock()
    app._settings[SETTINGS_KEY_THEME] = "nord"
    app._apply_theme_settings()
    app._active_dialog.apply_theme.assert_called_once_with(app._theme)
    app._settings_dialog.apply_theme.assert_called_once_with(app._theme)


# ------------------------------------------------------------------ #
# 시작 시 테마 (저장된 라이트)                                          #
# ------------------------------------------------------------------ #


def test_init_applies_saved_light_theme(
    monkeypatch: pytest.MonkeyPatch, appdata: Any  # noqa: F811
) -> None:
    _write_settings(appdata, {SETTINGS_KEY_THEME: "light"})
    monkeypatch.setattr(ss.tk, "Tk", mock.MagicMock())
    app = ss.ShutdownScheduler()
    assert app._theme["id"] == "light"
    assert ctk.get_appearance_mode() == "Light"


def test_init_without_theme_keys_stays_default(
    monkeypatch: pytest.MonkeyPatch, appdata: Any  # noqa: F811
) -> None:
    monkeypatch.setattr(ss.tk, "Tk", mock.MagicMock())
    app = ss.ShutdownScheduler()
    assert app._theme is theme.THEMES["gh"]
    assert ctk.get_appearance_mode() == "Dark"


def test_class_defaults_for_bare_app() -> None:
    assert ss.ShutdownScheduler._theme is theme.THEMES["gh"]
    assert ss.ShutdownScheduler._settings_dialog is None
    assert ss.ShutdownScheduler._system_light is None


# ------------------------------------------------------------------ #
# 테마 결정                                                            #
# ------------------------------------------------------------------ #

_FOLLOW = {
    SETTINGS_KEY_FOLLOW_SYSTEM: True,
    SETTINGS_KEY_THEME: "nord",
    SETTINGS_KEY_DARK_THEME: "drac",
    SETTINGS_KEY_LIGHT_THEME: "light",
}


@pytest.mark.parametrize(
    "settings, system_light, expected",
    [
        (_FOLLOW, True, "light"),
        (_FOLLOW, False, "drac"),
        (_FOLLOW, None, "drac"),
        ({**_FOLLOW, SETTINGS_KEY_FOLLOW_SYSTEM: False}, True, "nord"),
        ({**_FOLLOW, SETTINGS_KEY_FOLLOW_SYSTEM: False}, False, "nord"),
        ({**_FOLLOW, SETTINGS_KEY_FOLLOW_SYSTEM: False}, None, "nord"),
        ({}, True, "gh"),
        ({SETTINGS_KEY_FOLLOW_SYSTEM: True}, True, "light"),
        ({SETTINGS_KEY_FOLLOW_SYSTEM: True}, False, "gh"),
    ],
)
def test_resolve_theme_id(
    settings: dict[str, Any], system_light: Optional[bool], expected: str
) -> None:
    assert theme.resolve_theme_id(settings, system_light) == expected


class _FakeRegKey:
    def __enter__(self) -> "_FakeRegKey":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _fake_winreg(value: Any = 1, error: Optional[Exception] = None) -> Any:
    def _open(_root: Any, path: str) -> _FakeRegKey:
        assert path == config.SYSTEM_THEME_REG_KEY
        if error is not None:
            raise error
        return _FakeRegKey()

    def _query(_key: Any, name: str) -> tuple[Any, int]:
        assert name == config.SYSTEM_THEME_REG_VALUE
        return value, 4

    return types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), OpenKey=_open, QueryValueEx=_query
    )


@pytest.mark.parametrize("value, expected", [(0, False), (1, True)])
def test_read_system_light_values(
    monkeypatch: pytest.MonkeyPatch, value: int, expected: bool
) -> None:
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(value))
    assert ss._read_system_light() is expected


def test_read_system_light_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_winreg(error=FileNotFoundError())
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert ss._read_system_light() is None


def test_read_system_light_no_module(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules 값이 None 이면 import 가 ImportError 를 냄
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert ss._read_system_light() is None


# ------------------------------------------------------------------ #
# 시스템 테마 폴링                                                      #
# ------------------------------------------------------------------ #


def _poll_app(monkeypatch: pytest.MonkeyPatch, follow: bool) -> tuple[Any, mock.Mock]:
    app = _sched_app(monkeypatch)
    app._settings[SETTINGS_KEY_FOLLOW_SYSTEM] = follow
    app._active_dialog = mock.MagicMock()
    reader = mock.Mock(return_value=None)
    monkeypatch.setattr(ss, "_read_system_light", reader)
    return app, reader


def test_poll_off_does_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    app, reader = _poll_app(monkeypatch, follow=False)
    app._poll_system_theme()
    reader.assert_not_called()
    app._tk_root.after.assert_called_once_with(
        SYSTEM_THEME_POLL_MS, app._poll_system_theme
    )


def test_poll_same_value_does_not_repaint(monkeypatch: pytest.MonkeyPatch) -> None:
    app, reader = _poll_app(monkeypatch, follow=True)
    app._system_light = False
    reader.return_value = False
    app._poll_system_theme()
    reader.assert_called_once()
    app._active_dialog.apply_theme.assert_not_called()


def test_poll_change_repaints(monkeypatch: pytest.MonkeyPatch) -> None:
    app, reader = _poll_app(monkeypatch, follow=True)
    app._system_light = False
    reader.return_value = True
    app._poll_system_theme()
    assert app._system_light is True
    assert app._theme["id"] == "light"
    app._active_dialog.apply_theme.assert_called_once_with(app._theme)


def test_poll_unreadable_keeps_last_value(monkeypatch: pytest.MonkeyPatch) -> None:
    app, reader = _poll_app(monkeypatch, follow=True)
    app._system_light = True
    reader.return_value = None
    app._poll_system_theme()
    assert app._system_light is True
    app._active_dialog.apply_theme.assert_not_called()


def test_poll_error_still_schedules_next(monkeypatch: pytest.MonkeyPatch) -> None:
    app, reader = _poll_app(monkeypatch, follow=True)
    reader.side_effect = RuntimeError("레지스트리 오류")
    with pytest.raises(RuntimeError):
        app._poll_system_theme()
    app._tk_root.after.assert_called_once_with(
        SYSTEM_THEME_POLL_MS, app._poll_system_theme
    )


def test_run_schedules_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    monkeypatch.setattr(ss.threading, "Thread", mock.MagicMock())
    app._register_hotkeys = mock.MagicMock()
    app.run()
    app._tk_root.after.assert_any_call(SYSTEM_THEME_POLL_MS, app._poll_system_theme)
    app._tk_root.mainloop.assert_called_once()


# ------------------------------------------------------------------ #
# 큐·트레이                                                            #
# ------------------------------------------------------------------ #


def test_queue_opens_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app._open_settings_dialog = mock.MagicMock()
    app._dispatch_queue_event("open_settings")
    app._open_settings_dialog.assert_called_once_with()


def test_open_twice_makes_one_window(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    factory = mock.MagicMock()
    monkeypatch.setattr(ss, "SettingsDialog", factory)
    front = mock.MagicMock()
    monkeypatch.setattr(ss, "_bring_to_front", front)
    app._open_settings_dialog()
    app._open_settings_dialog()
    factory.assert_called_once_with(app._tk_root, app, on_close=app._on_settings_closed)
    front.assert_called_once()
    app._on_settings_closed()
    assert app._settings_dialog is None
    app._open_settings_dialog()
    assert factory.call_count == 2


def test_open_during_construction_makes_no_second_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _sched_app(monkeypatch)
    made: list[Any] = []

    def _factory(*args: Any, **kwargs: Any) -> Any:
        made.append(args)
        # Windows 의 CTkToplevel 생성자가 돌리는 update() 중 끼어드는 경우를 흉내 냄
        app._dispatch_queue_event("open_settings")
        return mock.MagicMock()

    monkeypatch.setattr(ss, "SettingsDialog", _factory)
    app._open_settings_dialog()
    assert len(made) == 1
    assert app._settings_dialog is not None
    assert app._settings_opening is False


def test_open_again_after_construction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    factory = mock.MagicMock(side_effect=[RuntimeError("생성 실패"), mock.MagicMock()])
    monkeypatch.setattr(ss, "SettingsDialog", factory)
    with pytest.raises(RuntimeError):
        app._open_settings_dialog()
    assert app._settings_opening is False
    assert app._settings_dialog is None
    app._open_settings_dialog()
    assert factory.call_count == 2
    assert app._settings_dialog is not None


def test_sync_dropdown_survives_missing_ctk_internal() -> None:
    window = object.__new__(ss.SettingsDialog)
    window._theme = theme.THEMES["gh"]
    window._tokens = window._theme["tokens"]
    window._dark_menu = types.SimpleNamespace()
    window._light_menu = types.SimpleNamespace()
    window._sync_dropdown_colors()


def test_open_recreates_dead_window(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    dead = mock.MagicMock()
    dead.top.deiconify.side_effect = tk.TclError("죽은 창")
    app._settings_dialog = dead
    factory = mock.MagicMock()
    monkeypatch.setattr(ss, "SettingsDialog", factory)
    app._open_settings_dialog()
    factory.assert_called_once()


def test_tray_callback_only_puts_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    fake = mock.MagicMock()
    monkeypatch.setattr(ss, "pystray", fake)
    app._open_settings_dialog = mock.MagicMock()
    app._build_tray_menu()
    call = next(
        c for c in fake.MenuItem.call_args_list if c.args and c.args[0] == "테마 설정"
    )
    app._tk_root.reset_mock()
    call.args[1](None, None)
    assert app._queue.get_nowait() == "open_settings"
    assert app._queue.empty()
    app._open_settings_dialog.assert_not_called()
    assert app._tk_root.mock_calls == []


# ------------------------------------------------------------------ #
# 설정 창 위치                                                          #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "anchor_x, anchor_y, height, expected",
    [
        # 오른쪽에 들어감
        (100, 50, 476, (448, 50)),
        # 오른쪽 끝에 딱 맞음 (912 + 340 + 8 + 340 = 1600)
        (912, 50, 476, (1260, 50)),
        # 오른쪽이 1px 넘치면 왼쪽
        (913, 50, 476, (565, 50)),
        # 왼쪽 보조 모니터(음수 좌표)면 그 옆 오른쪽
        (-800, 50, 476, (-452, 50)),
        # 아래로 넘치면 화면 안으로 올림
        (100, 900, 476, (448, 724)),
        # 창이 화면보다 커도 y 는 0 아래로 내리지 않음
        (100, 900, 1300, (448, 0)),
    ],
)
def test_beside_position(
    anchor_x: int, anchor_y: int, height: int, expected: tuple[int, int]
) -> None:
    assert (
        ss._beside_position(anchor_x, anchor_y, 340, 340, height, 1600, 1200)
        == expected
    )


def test_beside_position_left_boundary() -> None:
    assert ss._beside_position(348, 50, 340, 340, 476, 700, 1200) == (0, 50)


def test_beside_position_gives_up_when_neither_side_fits() -> None:
    assert ss._beside_position(347, 50, 340, 340, 476, 700, 1200) is None


# ------------------------------------------------------------------ #
# 꺼진 토글 트랙                                                        #
# ------------------------------------------------------------------ #


def _tok_of(theme_id: str, spec: Any) -> str:
    window = object.__new__(ss.SettingsDialog)
    window._theme = theme.THEMES[theme_id]
    window._tokens = window._theme["tokens"]
    return window._tok(spec)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_switch_off_track_token(theme_id: str) -> None:
    tokens = theme.THEMES[theme_id]["tokens"]
    expected = tokens["textMuted"] if theme_id == "light" else tokens["border"]
    assert _tok_of(theme_id, "switchOff") == expected


def test_switch_off_does_not_change_light_tokens() -> None:
    assert theme.THEMES["light"]["tokens"]["border"] == "#d0d7de"


def test_all_switches_use_switch_off_spec() -> None:
    with open(ss.__file__, encoding="utf-8") as f:
        src = f.read()
    assert src.count('"fg_color": "switchOff"') == 3
    assert src.count('"button_color": "knob"') == 3


# ------------------------------------------------------------------ #
# 가드                                                                 #
# ------------------------------------------------------------------ #


def test_guard_no_ttk_and_only_t_zero() -> None:
    with open(ss.__file__, encoding="utf-8") as f:
        src = f.read()
    assert not re.search(r"\bttk\b", src)
    assert re.findall(r'"/t",\s*"?([^",\s]+)', src) == ["0"]


def test_guard_versions_match() -> None:
    root = os.path.dirname(os.path.abspath(ss.__file__))
    assert config.APP_VERSION == "1.3.0"
    with open(os.path.join(root, "installer.iss"), encoding="utf-8") as f:
        assert '#define MyAppVersion "1.3.0"' in f.read()
    with open(os.path.join(root, "build.bat"), encoding="utf-8") as f:
        assert "ShutdownScheduler_Setup_1.3.0.exe" in f.read()


def test_swatch_image_cells_and_border() -> None:
    colors = ("#000000", "#ff0000", "#00ff00", "#0000ff")
    img = ss._make_swatch_image(colors, "#ffffff", 80, 40)
    assert img.size == (80, 40)
    for index, expected in enumerate(
        [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    ):
        assert img.getpixel((index * 20 + 10, 20)) == expected
    assert img.getpixel((0, 0)) == (255, 255, 255)
    assert img.getpixel((79, 39)) == (255, 255, 255)


# ------------------------------------------------------------------ #
# 실제 창 (가상 화면에서만)                                             #
# ------------------------------------------------------------------ #


def _allowed(item: dict[str, Any]) -> set[str]:
    """이 테마에서 화면에 나와도 되는 색 (토큰 + 코드가 쓰는 섞은 색 + 손잡이)."""
    tokens = item["tokens"]
    allowed = {tokens[key] for key in theme.BASE_KEYS}
    allowed.add(theme.SWITCH_KNOB)
    for group in tokens["actions"].values():
        allowed.update(group[key] for key in theme.ACTION_KEYS)
        allowed.add(theme.mix(group["accent"], tokens["surface"], 0.16))
        allowed.add(theme.mix(group["accent"], tokens["surface"], 0.24))
        allowed.add(theme.mix(group["onButton"], group["buttonFill"], 0.12))
    return {color.lower() for color in allowed}


def _rgb(widget: tk.Misc, color: str) -> str:
    red, green, blue = widget.winfo_rgb(color)
    return f"#{red >> 8:02x}{green >> 8:02x}{blue >> 8:02x}"


def _visible_colors(top: tk.Misc) -> dict[str, str]:
    """보이는 위젯의 색을 {색: 어디서} 로 모은다. 숨은 위젯·빈 글자는 뺀다."""
    found: dict[str, str] = {}

    def _note(widget: tk.Misc, color: Any, where: str) -> None:
        if color:
            found.setdefault(_rgb(widget, str(color)), where)

    def _walk(widget: tk.Misc) -> None:
        if not widget.winfo_ismapped():
            return
        name = widget.__class__.__name__
        # ctk 위젯은 겉 프레임을 캔버스가 다 덮어 겉 프레임 bg 는 보이지 않음
        if not isinstance(widget, CTkBaseClass):
            _note(widget, tk.Misc.cget(widget, "bg"), f"{name}.bg")
        if isinstance(widget, tk.Label) and widget.cget("text"):
            _note(widget, widget.cget("fg"), f"{name}.fg")
        if isinstance(widget, tk.Entry):
            _note(widget, widget.cget("fg"), "Entry.fg")
            _note(widget, widget.cget("insertbackground"), "Entry.insert")
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                if widget.itemcget(item, "state") == "hidden":
                    continue
                kind = widget.type(item)
                if kind == "text" and not widget.itemcget(item, "text"):
                    continue
                if kind == "image":
                    continue
                _note(widget, widget.itemcget(item, "fill"), f"{name}.{kind}.fill")
                if kind != "text":
                    outline = widget.itemcget(item, "outline")
                    _note(widget, outline, f"{name}.{kind}.outline")
        for child in widget.winfo_children():
            _walk(child)

    _walk(top)
    return found


def _stray(top: tk.Misc, item: dict[str, Any]) -> dict[str, str]:
    top.update()
    allowed = _allowed(item)
    return {c: w for c, w in _visible_colors(top).items() if c not in allowed}


def _switch(app: Any, theme_id: str, contrast_fix: bool = True) -> None:
    app._settings[SETTINGS_KEY_THEME] = theme_id
    app._settings[SETTINGS_KEY_CONTRAST_FIX] = contrast_fix
    app._apply_theme_settings()


def _other_mode(theme_id: str) -> str:
    return "gh" if theme.THEMES[theme_id]["mode"] == "light" else "light"


def _arm(app: Any, action: str) -> None:
    if action == ACTION_MONITOR_OFF:
        app.schedule_monitor_only(1800)
    else:
        app.schedule_action(action, 1800, 300)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_repaint_leaves_no_stray_color(
    open_dialog: Callable[..., tuple[Any, Any]], theme_id: str  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    other = _other_mode(theme_id)
    dlg._total_seconds = 1800
    dlg._sync_value_text()
    dlg._on_chip(5)
    for action in ACTIONS:
        dlg._set_action(action)
        _switch(app, other)
        _switch(app, theme_id)
        assert _stray(dlg.top, app._theme) == {}, ("setup", action)

        _arm(app, action)
        dlg._show_view("scheduled")
        _switch(app, other)
        _switch(app, theme_id)
        assert _stray(dlg.top, app._theme) == {}, ("scheduled", action)

        app._cancel_all()
        dlg._show_view("setup")


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_repaint_without_contrast_fix(
    open_dialog: Callable[..., tuple[Any, Any]], theme_id: str  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id, contrast_fix=False)
    assert _stray(dlg.top, app._theme) == {}
    tokens = dlg._tokens
    assert tokens is app._theme["tokens"]
    if theme.PALETTE_OVERRIDES[theme_id]:
        assert tokens != theme.THEMES[theme_id]["tokens"]


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_opened_in_theme_has_no_stray_color(
    open_dialog: Callable[..., tuple[Any, Any]], theme_id: str  # noqa: F811
) -> None:
    def _prepare(app: Any) -> None:
        _switch(app, theme_id)

    app, dlg = open_dialog(prepare=_prepare)
    assert dlg._theme is app._theme
    for action in ACTIONS:
        dlg._set_action(action)
        assert _stray(dlg.top, app._theme) == {}, action


def test_scheduled_monitor_icon_follows_theme(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    dlg._show_view("scheduled")
    _switch(app, "light")
    color = theme.THEMES["light"]["tokens"]["actions"]["monitor"]["accent"]
    expected = dlg._icon_cache[("monitor", color, 16)]
    assert dlg._sched_monitor_icon.cget("image") is expected
    assert dlg._monitor_icon.cget("image") is expected


def test_ring_redraws_on_new_tokens(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    photo = dlg._dial._photo
    _switch(app, "light")
    surface = theme.THEMES["light"]["tokens"]["surface"]
    assert dlg._dial.canvas.cget("bg") == surface
    assert dlg._sched_dial.canvas.cget("bg") == surface
    assert dlg._dial._photo is not photo


def test_ring_redraws_when_only_surface_changes(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    _, dlg = open_dialog()
    photo = dlg._dial._photo
    # 링 색(동작 강조색)이 같아도 배경이 바뀌면 다시 그려야 함
    dlg._dial.set_tokens({**dlg._tokens, "surface": "#123456"})
    assert dlg._dial._photo is not photo


def test_repaint_keeps_state(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg._set_action(ACTION_RESTART)
    dlg._set_unit(UNIT_HOUR)
    dlg._total_seconds = 5400
    dlg._sync_value_text()
    dlg._on_chip(5)
    dlg._refresh_monitor_row()
    dlg.top.update()
    height = dlg.top.winfo_height()
    value = dlg.var_value.get()

    _switch(app, "light")
    dlg.top.update()

    tokens = theme.THEMES["light"]["tokens"]
    restart = tokens["actions"]["restart"]
    assert dlg._action == ACTION_RESTART
    assert dlg._unit == UNIT_HOUR
    assert dlg._total_seconds == 5400
    assert dlg.var_value.get() == value
    assert dlg.top.winfo_height() == height
    tab = dlg._action_btns[ACTION_RESTART]
    assert tab.cget("fg_color") == restart["tabFill"]
    assert tab.cget("text_color") == restart["onTab"]
    assert dlg._action_btns[ACTION_SHUTDOWN].cget("text_color") == tokens["textMuted"]
    assert dlg._unit_btns[UNIT_HOUR].cget("text_color") == (
        tokens["actions"]["monitor"]["onTab"]
    )
    assert dlg._unit_btns[UNIT_MINUTE].cget("fg_color") == "transparent"
    # 재시작 탭에서 칩은 비활성 표시를 유지해야 함
    chip = dlg._chip_btns[5]
    assert chip.cget("state") == "disabled"
    assert chip.cget("text_color_disabled") == tokens["textMuted"]
    assert dlg._btn_confirm.cget("fg_color") == restart["buttonFill"]
    assert dlg._btn_confirm.cget("text_color") == restart["onButton"]
    assert dlg._monitor_switch.cget("button_color") == theme.SWITCH_KNOB


def test_repaint_keeps_disabled_confirm_and_selected_chip(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg._total_seconds = 60
    dlg._sync_value_text()
    dlg._on_chip(5)
    dlg._refresh_monitor_row()
    assert dlg._btn_confirm.cget("state") == "disabled"

    _switch(app, "nord")

    tokens = theme.THEMES["nord"]["tokens"]
    monitor = tokens["actions"]["monitor"]
    assert dlg._btn_confirm.cget("state") == "disabled"
    assert dlg._btn_confirm.cget("fg_color") == tokens["surfaceAlt"]
    chip = dlg._chip_btns[5]
    assert chip.cget("border_color") == monitor["accent"]
    assert chip.cget("text_color") == monitor["accentText"]
    assert dlg._total_seconds == 60


def test_repaint_keeps_schedule(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    dlg._show_view("scheduled")
    before = (
        app.is_active,
        app.is_paused,
        app.scheduled_time,
        app.scheduled_action,
        app._monitor_lead_sec,
        app._linked_monitor.due,
    )
    height = dlg.top.winfo_height()
    timers = list(ss.threading.Timer.created)

    _switch(app, "light")
    dlg.top.update()

    assert before == (
        app.is_active,
        app.is_paused,
        app.scheduled_time,
        app.scheduled_action,
        app._monitor_lead_sec,
        app._linked_monitor.due,
    )
    assert dlg._view == "scheduled"
    assert dlg.top.winfo_height() == height
    assert ss.threading.Timer.created == timers


def test_badge_uses_accent_text(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    app.schedule_action(ACTION_RESTART, 1800, 300)
    dlg._show_view("scheduled")
    _switch(app, "light")
    restart = theme.THEMES["light"]["tokens"]["actions"]["restart"]
    # 강조색과 글자색이 다른 조합이라야 어느 토큰을 쓰는지 구분됨
    assert restart["accent"] != restart["accentText"]
    assert dlg._badge.cget("text_color") == restart["accentText"]


def test_immediate_confirm_is_not_repainted_and_not_bound(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    bound = len(dlg._bindings)
    before = set(dlg.top.winfo_children())
    dlg._show_immediate_confirm()
    confirm = next(w for w in dlg.top.winfo_children() if w not in before)
    confirm.update()
    assert confirm.cget("fg_color") == theme.THEMES["gh"]["tokens"]["background"]
    assert len(dlg._bindings) == bound
    assert _stray(confirm, app._theme) == {}

    _switch(app, "nord")

    assert confirm.winfo_exists()
    assert confirm.cget("fg_color") == theme.THEMES["gh"]["tokens"]["background"]
    confirm.destroy()
    # 닫힌 확인 창이 재칠을 깨뜨리면 안 됨
    _switch(app, "light")
    assert dlg._tokens is theme.THEMES["light"]["tokens"]


def test_header_gear_opens_settings(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    assert dlg._settings_btn.winfo_ismapped()
    dlg._settings_btn.invoke()
    assert isinstance(app._settings_dialog, ss.SettingsDialog)
    first = app._settings_dialog
    dlg._settings_btn.invoke()
    assert app._settings_dialog is first
    first._close()
    assert app._settings_dialog is None


def test_header_has_no_gear_without_scheduler(
    monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk  # noqa: F811
) -> None:
    monkeypatch.setattr(ss, "_bring_to_front", lambda window: None)
    monkeypatch.setattr(ss, "is_autostart_enabled", lambda: False)
    dlg = ss.ScheduleDialog(tk_root, lambda *args: None)
    assert not hasattr(dlg, "_settings_btn")
    assert dlg._theme is theme.THEMES["gh"]
    dlg.top.destroy()


@pytest.fixture
def settings_app(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> tuple[Any, Any, Any]:
    """예약 창과 설정 창을 함께 연다."""
    app, dlg = open_dialog()
    app._open_settings_dialog()
    settings = app._settings_dialog
    settings.top.update()
    return app, dlg, settings


def _switches(settings: Any) -> tuple[Any, Any]:
    """(따라가기 스위치, 보정 스위치) 를 만든 순서대로 찾는다."""
    found: list[Any] = []

    def _walk(widget: tk.Misc) -> None:
        if isinstance(widget, ctk.CTkSwitch):
            found.append(widget)
        for child in widget.winfo_children():
            _walk(child)

    _walk(settings.top)
    assert len(found) == 2
    return found[0], found[1]


def test_settings_window_layout(settings_app: tuple[Any, Any, Any]) -> None:
    app, _, settings = settings_app
    # 재칠 전 처음 연 상태 — 스와치 그림 라벨 배경까지 토큰이어야 함
    assert _stray(settings.top, app._theme) == {}
    assert list(settings._thumb_btns) == THEME_IDS
    assert settings.top.winfo_width() == ss.DIALOG_WIDTH
    assert settings.top.winfo_height() == settings._main.winfo_reqheight()
    infos = [btn.grid_info() for btn in settings._thumb_btns.values()]
    assert [(i["row"], i["column"]) for i in infos] == [
        (row, column) for row in range(5) for column in range(2)
    ]
    assert settings._follow_menus.winfo_manager() == ""
    assert settings._thumb_btns["gh"].cget("border_width") == 2
    assert settings._thumb_btns["nord"].cget("border_width") == 1


def test_settings_opens_right_of_schedule_window(
    settings_app: tuple[Any, Any, Any],
) -> None:
    _, dlg, settings = settings_app
    dlg.top.update()
    expected = dlg.top.winfo_x() + dlg.top.winfo_width() + config.SETTINGS_WINDOW_GAP
    assert settings.top.winfo_x() == expected
    assert settings.top.winfo_y() == dlg.top.winfo_y()


def test_settings_opens_left_when_right_overflows(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    x = dlg.top.winfo_screenwidth() - dlg.top.winfo_width() - 10
    dlg.top.geometry(f"+{x}+20")
    dlg.top.update()
    app._open_settings_dialog()
    settings = app._settings_dialog
    settings.top.update()
    expected = x - config.SETTINGS_WINDOW_GAP - ss.DIALOG_WIDTH
    assert settings.top.winfo_x() == expected


def test_settings_centered_without_schedule_window(
    monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk  # noqa: F811
) -> None:
    monkeypatch.setattr(ss, "_bring_to_front", lambda window: None)
    app = _sched_app(monkeypatch)
    app._active_dialog = None
    app._tk_root = tk_root
    app._open_settings_dialog()
    top = app._settings_dialog.top
    top.update()
    assert top.winfo_x() == (top.winfo_screenwidth() - ss.DIALOG_WIDTH) // 2
    assert top.winfo_y() == (top.winfo_screenheight() - top.winfo_height()) // 2


def test_settings_centered_when_schedule_window_hidden(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog()
    dlg.top.withdraw()
    dlg.top.update()
    app._open_settings_dialog()
    top = app._settings_dialog.top
    top.update()
    assert top.winfo_x() == (top.winfo_screenwidth() - ss.DIALOG_WIDTH) // 2


def test_off_track_repaints_with_theme(settings_app: tuple[Any, Any, Any]) -> None:
    app, dlg, settings = settings_app
    switches = (*_switches(settings), dlg._autostart_btn, dlg._monitor_switch)
    for theme_id in ("light", "nord", "light", "gh"):
        _switch(app, theme_id)
        settings.top.update()
        tokens = theme.THEMES[theme_id]["tokens"]
        track = tokens["textMuted"] if theme_id == "light" else tokens["border"]
        for widget in switches:
            assert widget.cget("fg_color") == track
            assert widget._canvas.itemcget("inner_parts", "fill") == track
            assert widget.cget("button_color") == theme.SWITCH_KNOB


def _all_switches(dlg: Any, settings: Any) -> tuple[Any, ...]:
    return (*_switches(settings), dlg._autostart_btn, dlg._monitor_switch)


def test_switch_border_follows_mode_and_restores(
    settings_app: tuple[Any, Any, Any],
) -> None:
    app, dlg, settings = settings_app
    default = ctk.ThemeManager.theme["CTkSwitch"]["border_width"]
    assert default > 0
    switches = _all_switches(dlg, settings)
    assert {w.cget("border_width") for w in switches} == {default}

    _switch(app, "light")
    assert {w.cget("border_width") for w in switches} == {0}

    for theme_id in THEME_IDS[:-1]:
        _switch(app, theme_id)
        assert {w.cget("border_width") for w in switches} == {default}, theme_id
        _switch(app, "light")
    _switch(app, "gh")
    assert {w.cget("border_width") for w in switches} == {default}


def test_switch_border_when_opened_in_light(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    app, dlg = open_dialog(prepare=lambda app: _switch(app, "light"))
    app._open_settings_dialog()
    switches = _all_switches(dlg, app._settings_dialog)
    assert {w.cget("border_width") for w in switches} == {0}


def test_switch_border_when_opened_in_dark(
    open_dialog: Callable[..., tuple[Any, Any]],  # noqa: F811
) -> None:
    default = ctk.ThemeManager.theme["CTkSwitch"]["border_width"]
    app, dlg = open_dialog(prepare=lambda app: _switch(app, "nord"))
    app._open_settings_dialog()
    switches = _all_switches(dlg, app._settings_dialog)
    assert {w.cget("border_width") for w in switches} == {default}


def test_switch_shape_keeps_sizes(settings_app: tuple[Any, Any, Any]) -> None:
    app, dlg, settings = settings_app
    dlg.top.update()
    settings.top.update()
    switches = _all_switches(dlg, settings)
    sizes = [(w.winfo_width(), w.winfo_height()) for w in switches]
    heights = (dlg.top.winfo_height(), settings.top.winfo_height())

    for theme_id in ("light", "gh"):
        _switch(app, theme_id)
        dlg.top.update()
        settings.top.update()
        assert [(w.winfo_width(), w.winfo_height()) for w in switches] == sizes
        assert (dlg.top.winfo_height(), settings.top.winfo_height()) == heights


def _grab_widget(widget: tk.Misc) -> Any:
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    box = (x, y, x + widget.winfo_width(), y + widget.winfo_height())
    return ImageGrab.grab(bbox=box, xdisplay=os.environ["DISPLAY"])


def test_dark_switch_pixels_restored_after_light(
    settings_app: tuple[Any, Any, Any],
) -> None:
    app, dlg, settings = settings_app

    def _pixels() -> list[bytes]:
        for _ in range(5):
            dlg.top.update()
            settings.top.update()
        return [_grab_widget(w).tobytes() for w in _all_switches(dlg, settings)]

    before = _pixels()
    _switch(app, "light")
    assert _pixels() != before
    _switch(app, "gh")
    assert _pixels() == before


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_dropdown_colors_follow_theme(
    settings_app: tuple[Any, Any, Any], theme_id: str
) -> None:
    app, _, settings = settings_app
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    settings.top.update()
    tokens = app._theme["tokens"]
    for option_menu in (settings._dark_menu, settings._light_menu):
        menu = option_menu._dropdown_menu
        assert str(tk.Menu.cget(menu, "bg")) == tokens["surface"]
        assert str(tk.Menu.cget(menu, "fg")) == tokens["text"]
        assert str(tk.Menu.cget(menu, "activebackground")) == tokens["surfaceAlt"]
        assert str(tk.Menu.cget(menu, "activeforeground")) == tokens["text"]
    assert _contrast(tokens["text"], tokens["surfaceAlt"]) >= 4.5


def test_system_switch_updates_dropdown_hover(
    monkeypatch: pytest.MonkeyPatch, settings_app: tuple[Any, Any, Any]
) -> None:
    app, _, settings = settings_app
    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: True})
    monkeypatch.setattr(ss, "_read_system_light", lambda: True)
    app._poll_system_theme()
    menu = settings._dark_menu._dropdown_menu
    expected = theme.THEMES["light"]["tokens"]["text"]
    assert str(tk.Menu.cget(menu, "activeforeground")) == expected


def test_build_error_leaves_no_half_window(
    monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk  # noqa: F811
) -> None:
    app = _sched_app(monkeypatch)
    app._active_dialog = None
    app._tk_root = tk_root
    monkeypatch.setattr(ss, "_bring_to_front", lambda window: None)
    build = ss.SettingsDialog._build_ui

    def _fail(self: Any) -> None:
        raise RuntimeError("구성 실패")

    monkeypatch.setattr(ss.SettingsDialog, "_build_ui", _fail)
    before = set(tk_root.winfo_children())
    with pytest.raises(RuntimeError):
        app._open_settings_dialog()
    assert set(tk_root.winfo_children()) == before
    assert app._settings_opening is False

    monkeypatch.setattr(ss.SettingsDialog, "_build_ui", build)
    app._open_settings_dialog()
    assert isinstance(app._settings_dialog, ss.SettingsDialog)
    app._settings_dialog._close()


def test_settings_has_no_autostart_toggle(settings_app: tuple[Any, Any, Any]) -> None:
    _, _, settings = settings_app
    assert len(_switches(settings)) == 2


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_settings_no_stray_color(
    settings_app: tuple[Any, Any, Any], theme_id: str
) -> None:
    app, _, settings = settings_app
    _switch(app, _other_mode(theme_id))
    _switch(app, theme_id)
    assert _stray(settings.top, app._theme) == {}
    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: True})
    assert _stray(settings.top, app._theme) == {}


def test_thumbnail_click_applies_and_saves(
    settings_app: tuple[Any, Any, Any], appdata: Any  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    settings._thumb_btns["light"].invoke()
    tokens = theme.THEMES["light"]["tokens"]
    assert app._theme["id"] == "light"
    assert dlg._tokens is tokens
    assert settings._tokens is tokens
    assert ctk.get_appearance_mode() == "Light"
    assert _read_settings(appdata)[SETTINGS_KEY_THEME] == "light"
    selected = settings._thumb_btns["light"]
    assert selected.cget("border_width") == 2
    assert selected.cget("border_color") == tokens["actions"]["monitor"]["accent"]
    other = settings._thumb_btns["gh"]
    assert other.cget("border_width") == 1
    assert other.cget("border_color") == tokens["border"]


def test_follow_toggle_shows_menus_and_locks_thumbnails(
    monkeypatch: pytest.MonkeyPatch,
    settings_app: tuple[Any, Any, Any],
    appdata: Any,  # noqa: F811
) -> None:
    app, _, settings = settings_app
    monkeypatch.setattr(ss, "_read_system_light", lambda: True)
    _switch(app, "nord")
    closed = settings.top.winfo_height()
    follow, _ = _switches(settings)

    follow.toggle()
    settings.top.update()

    assert settings._follow_menus.winfo_manager() == "grid"
    assert settings.top.winfo_height() > closed
    assert app._theme["id"] == "light"
    assert all(b.cget("state") == "disabled" for b in settings._thumb_btns.values())
    assert settings._thumb_btns["light"].cget("border_width") == 2
    assert settings._thumb_btns["nord"].cget("border_width") == 1
    assert settings._light_menu.get() == "GitHub Light"
    assert settings._dark_menu.get() == "GitHub Dark"
    saved = _read_settings(appdata)
    assert saved[SETTINGS_KEY_FOLLOW_SYSTEM] is True
    assert saved[SETTINGS_KEY_THEME] == "nord"
    assert app._settings[SETTINGS_KEY_THEME] == "nord"

    follow.toggle()
    settings.top.update()

    assert settings._follow_menus.winfo_manager() == ""
    assert settings.top.winfo_height() == closed
    assert app._theme["id"] == "nord"
    assert all(b.cget("state") == "normal" for b in settings._thumb_btns.values())


def test_follow_menu_saves_id_not_name(
    monkeypatch: pytest.MonkeyPatch,
    settings_app: tuple[Any, Any, Any],
    appdata: Any,  # noqa: F811
) -> None:
    app, _, settings = settings_app
    monkeypatch.setattr(ss, "_read_system_light", lambda: False)
    follow, _ = _switches(settings)
    follow.toggle()

    settings._dark_menu._dropdown_callback("Nord")

    assert _read_settings(appdata)[SETTINGS_KEY_DARK_THEME] == "nord"
    assert app._theme["id"] == "nord"
    assert settings._dark_menu.get() == "Nord"
    assert settings._dark_menu.cget("values") == [
        theme.THEMES[tid]["name"] for tid in THEME_IDS[:-1]
    ]
    assert settings._light_menu.cget("values") == ["GitHub Light"]


def test_contrast_toggle_uses_original_palette(
    settings_app: tuple[Any, Any, Any], appdata: Any  # noqa: F811
) -> None:
    app, dlg, settings = settings_app
    _, contrast = _switches(settings)
    assert settings._contrast_var.get() is True

    contrast.toggle()

    assert _read_settings(appdata)[SETTINGS_KEY_CONTRAST_FIX] is False
    shutdown = dlg._tokens["actions"]["shutdown"]
    assert shutdown["buttonFill"] == "#f85149"
    assert dlg._btn_confirm.cget("fg_color") == "#f85149"
    assert settings._contrast_var.get() is False

    contrast.toggle()
    assert dlg._tokens["actions"]["shutdown"]["buttonFill"] == "#d0443d"


def test_rose_contrast_toggle_updates_switch_without_repaint(
    settings_app: tuple[Any, Any, Any],
) -> None:
    app, dlg, settings = settings_app
    _switch(app, "rose")
    _, contrast = _switches(settings)
    with mock.patch.object(dlg, "apply_theme") as repaint:
        contrast.toggle()
    repaint.assert_not_called()
    assert settings._contrast_var.get() is False


def test_system_switches_to_light_while_settings_open(
    monkeypatch: pytest.MonkeyPatch, settings_app: tuple[Any, Any, Any]
) -> None:
    app, dlg, settings = settings_app
    app._update_theme_settings({SETTINGS_KEY_FOLLOW_SYSTEM: True})
    assert app._theme["id"] == "gh"
    monkeypatch.setattr(ss, "_read_system_light", lambda: True)

    app._poll_system_theme()

    light = theme.THEMES["light"]
    assert dlg._tokens is light["tokens"]
    assert settings._tokens is light["tokens"]
    assert settings._thumb_btns["light"].cget("border_width") == 2
    assert settings._thumb_btns["gh"].cget("border_width") == 1
    assert _stray(settings.top, light) == {}
    assert _stray(dlg.top, light) == {}


def test_settings_close_and_escape_binding(settings_app: tuple[Any, Any, Any]) -> None:
    app, _, settings = settings_app
    assert settings.top.bind("<Escape>")
    settings._close()
    assert app._settings_dialog is None


def test_apply_theme_after_destroy_is_silent(
    settings_app: tuple[Any, Any, Any],
) -> None:
    _, _, settings = settings_app
    settings.top.destroy()
    settings.apply_theme(theme.THEMES["light"])


@pytest.mark.skipif(
    os.environ.get(REAL_WINDOWS_ENV) != "1", reason="실제 창은 가상 화면에서만 만듦"
)
def test_saved_light_theme_before_tk_root(appdata: Any) -> None:  # noqa: F811
    """__init__ 은 tk 루트 전에 모드를 바꾼다 — 깨끗한 프로세스에서 실제로 확인함."""
    _write_settings(appdata, {SETTINGS_KEY_THEME: "light"})
    root = os.path.dirname(os.path.abspath(ss.__file__))
    script = textwrap.dedent(f"""
        import sys
        from unittest import mock
        sys.dont_write_bytecode = True
        sys.path.insert(0, {root!r})
        for name in ("pystray", "keyboard"):
            sys.modules[name] = mock.MagicMock(name=name)
        import customtkinter as ctk
        import shutdown_scheduler as ss
        ss._bring_to_front = lambda window: None
        ss.is_autostart_enabled = lambda: False
        app = ss.ShutdownScheduler()
        app._open_schedule_dialog()
        app._active_dialog.top.update()
        print(ctk.get_appearance_mode(), app._active_dialog._theme["id"])
        print(app._active_dialog.top.cget("fg_color"))
        app._tk_root.destroy()
        """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "APPDATA": str(appdata)},
    )
    assert result.returncode == 0, result.stderr
    background = theme.THEMES["light"]["tokens"]["background"]
    assert result.stdout.split("\n")[:2] == ["Light light", background]
