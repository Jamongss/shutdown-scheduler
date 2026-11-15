"""예약 종료 시 모니터 끄기 기능 테스트.

ctypes(user32·dxva2)·subprocess.run 을 가짜로 바꿔 Windows 없이 실행한다.
"""

import ctypes
import importlib
import json
import os
import queue
import sys
import types
from typing import Any, Callable, Optional
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stub_if_missing(name: str) -> None:
    """Windows 전용 모듈이 없으면 MagicMock 으로 막는다."""
    try:
        importlib.import_module(name)
    except Exception:
        sys.modules[name] = mock.MagicMock(name=name)


for _name in ("pystray", "keyboard", "customtkinter"):
    _stub_if_missing(_name)

import shutdown_scheduler as ss  # noqa: E402
from cfg.config import (  # noqa: E402
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    MONITOR_OFF_BUDGET_SEC,
    SETTINGS_KEY_MONITOR_OFF,
    SHUTDOWN_TRACE_FILE_NAME,
    SHUTDOWN_TRACE_MAX_BYTES,
    VCP_BRIGHTNESS,
    VCP_POWER_MODE,
)

LOST = 0xC026258D
# ctypes.get_last_error 는 부호 있는 int 로 돌려줌 — 실제와 같은 형태로 흉내 냄
LOST_SIGNED = LOST - 2**32


class _Fn:
    """argtypes·restype 대입을 받아 주는 가짜 DLL 함수."""

    def __init__(self, impl: Callable[..., Any]) -> None:
        self.impl = impl

    def __call__(self, *args: Any) -> Any:
        return self.impl(*args)


class FakeDdc:
    """user32·dxva2 가짜 구현. 호출을 events 에 순서대로 남긴다.

    Attributes:
        layout: hmonitor → 물리 모니터 핸들 목록. 바꾸면 다음 열거에 반영된다.
        set_impl: (handle, value) → True/False 또는 예외. 실패 시 last_error 를 정한다.
        read_impl: (handle, code) → True/False 또는 예외. 기본은 성공.
    """

    def __init__(self, layout: dict[int, list[int]]) -> None:
        self.layout = layout
        self.events: list[Any] = []
        self.last_error = 0
        self.set_impl: Callable[[int, int], bool] = lambda handle, value: True
        self.read_impl: Callable[[int, int], bool] = lambda handle, code: True
        self.on_set: Optional[Callable[[], None]] = None
        self.user32 = types.SimpleNamespace(EnumDisplayMonitors=_Fn(self._enum))
        self.dxva2 = types.SimpleNamespace(
            GetNumberOfPhysicalMonitorsFromHMONITOR=_Fn(self._get_count),
            GetPhysicalMonitorsFromHMONITOR=_Fn(self._get_physical),
            SetVCPFeature=_Fn(self._set_vcp),
            GetVCPFeatureAndVCPFeatureReply=_Fn(self._read_vcp),
            DestroyPhysicalMonitors=_Fn(self._destroy),
        )

    def win_dll(self, name: str, use_last_error: bool = False) -> Any:
        assert use_last_error is True
        return {"user32": self.user32, "dxva2": self.dxva2}[name]

    def _enum(self, hdc: Any, clip: Any, callback: Any, lparam: int) -> int:
        self.events.append("enum")
        for hmonitor in list(self.layout):
            callback(hmonitor, None, None, 0)
        return 1

    def _get_count(self, hmonitor: int, count_ref: Any) -> int:
        count_ref._obj.value = len(self.layout.get(hmonitor, []))
        return 1

    def _get_physical(self, hmonitor: int, count: int, array: Any) -> int:
        for i, handle in enumerate(self.layout.get(hmonitor, [])[:count]):
            array[i].hPhysicalMonitor = handle
        return 1

    def _set_vcp(self, handle: int, code: int, value: int) -> int:
        assert code == VCP_POWER_MODE
        self.events.append(("set", handle, value))
        if self.on_set is not None:
            self.on_set()
        return 1 if self.set_impl(handle, value) else 0

    def _read_vcp(
        self, handle: int, code: int, kind: Any, current: Any, maximum: Any
    ) -> int:
        self.events.append(("read", handle, code))
        return 1 if self.read_impl(handle, code) else 0

    def _destroy(self, count: int, array: Any) -> int:
        self.events.append(("destroy", count))
        return 1

    def sets(self) -> list[Any]:
        return [e for e in self.events if isinstance(e, tuple) and e[0] == "set"]

    def destroys(self) -> list[Any]:
        return [e for e in self.events if isinstance(e, tuple) and e[0] == "destroy"]


@pytest.fixture(autouse=True)
def appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """설정·추적 로그가 임시 폴더에만 쓰이게 한다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def ddc(monkeypatch: pytest.MonkeyPatch) -> FakeDdc:
    """Windows ctypes 환경을 가짜 DLL 로 흉내 낸다 (주 모니터 1001, 세로 1002)."""
    fake = FakeDdc({101: [1001], 102: [1002]})
    monkeypatch.setattr(ctypes, "windll", object(), raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", fake.win_dll, raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(
        ctypes, "get_last_error", lambda: fake.last_error, raising=False
    )
    return fake


def _install_run(
    monkeypatch: pytest.MonkeyPatch,
    fake: Optional[FakeDdc],
    returncode: int = 0,
    raises: Optional[Exception] = None,
) -> list[list[str]]:
    """subprocess.run 을 가짜로 바꾸고 받은 명령 목록을 돌려준다."""
    calls: list[list[str]] = []

    def _run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        if fake is not None:
            fake.events.append("run")
        if raises is not None:
            raise raises
        return types.SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(ss.subprocess, "run", _run)
    return calls


def _make_app(action: str, enabled: bool) -> Any:
    """tk 루트 없이 ShutdownScheduler 인스턴스를 만든다."""
    app = object.__new__(ss.ShutdownScheduler)
    app.scheduled_action = action
    app._settings = {SETTINGS_KEY_MONITOR_OFF: enabled}
    app._queue = queue.Queue()
    app.tray_icon = None
    app._monitor_lead_sec = 0 if enabled else None
    app._linked_monitor = ss._MonitorOffTimer("linked", app._queue)
    app._solo_monitor = ss._MonitorOffTimer("solo", app._queue)
    app._linked_done = False
    return app


def _trace_text(appdata: Any) -> str:
    path = os.path.join(str(appdata), ss.APP_NAME, SHUTDOWN_TRACE_FILE_NAME)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


# 1. 종료 + 설정 켬 + 반환값 0
def test_shutdown_enabled_order(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert ddc.events == [
        "enum",
        ("read", 1001, VCP_BRIGHTNESS),
        ("read", 1002, VCP_BRIGHTNESS),
        "run",
        ("set", 1001, 5),
        ("set", 1002, 5),
        ("set", 1001, 4),
        ("set", 1002, 4),
        ("destroy", 1),
        ("destroy", 1),
    ]
    trace = _trace_text(appdata)
    assert "shutdown returncode=0" in trace
    assert "first round=1 monitor=0 value=5 ok=True err=0x00000000" in trace
    assert trace.rstrip().endswith("end")


# 2. 반환값이 0 이 아니면 쓰기 0회, 핸들은 해제
def test_shutdown_rejected_no_write(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _install_run(monkeypatch, ddc, returncode=1190)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert ddc.sets() == []
    assert len(ddc.destroys()) == 2
    assert "shutdown returncode=1190" in _trace_text(appdata)


# 3. subprocess.run 예외 → 쓰기 0회, 예외 전파 없음
def test_shutdown_exception_no_write(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _install_run(monkeypatch, ddc, raises=OSError("boom"))
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert ddc.sets() == []
    assert len(ddc.destroys()) == 2
    assert "shutdown error" in _trace_text(appdata)


# 4. 재시작이면 열거·쓰기 0회
def test_restart_untouched(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    _make_app(ACTION_RESTART, True)._execute_action()

    assert calls == [["shutdown", "/r", "/t", "0", "/f"]]
    assert ddc.events == ["run"]
    assert _trace_text(appdata) == ""


# 5. 설정 꺼짐이면 열거·쓰기 0회
def test_disabled_untouched(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _install_run(monkeypatch, ddc, returncode=0)
    _make_app(ACTION_SHUTDOWN, False)._execute_action()

    assert ddc.events == ["run"]
    assert _trace_text(appdata) == ""


# 6. 한 모니터 실패(False·예외)는 삼키고 나머지는 씀
@pytest.mark.parametrize("mode", ["false", "raise"])
def test_one_monitor_failure_swallowed(ddc: FakeDdc, mode: str) -> None:
    def _impl(handle: int, value: int) -> bool:
        if handle == 1001:
            if mode == "raise":
                raise RuntimeError("i2c")
            ddc.last_error = 0xC0262584 - 2**32
            return False
        return True

    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    written = ss._power_off_monitors(groups)
    ss._release_physical_monitors(groups)

    assert written == 2
    assert ("set", 1002, 5) in ddc.sets()
    assert ("set", 1002, 4) in ddc.sets()
    assert ddc.events.count("enum") == 1


# 7-1. 2라운드 핸들 소실 → 재열거 정확히 1회, 잡힌 모니터에만 5 → 4
def test_handle_lost_retry_once(ddc: FakeDdc, appdata: Any) -> None:
    def _impl(handle: int, value: int) -> bool:
        if handle == 1001 and value == 5:
            # 주 모니터가 꺼지며 OS 가 핸들을 지움 — 재열거엔 세로 모니터만 남음
            ddc.layout = {102: [2002]}
            return True
        if handle in (1001, 1002):
            ddc.last_error = LOST_SIGNED
            return False
        return True

    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    written = ss._power_off_monitors(groups)
    ss._release_physical_monitors(groups)

    assert ddc.events.count("enum") == 2
    assert ddc.sets() == [
        ("set", 1001, 5),
        ("set", 1002, 5),
        ("set", 1001, 4),
        ("set", 1002, 4),
        ("set", 2002, 5),
        ("set", 2002, 4),
    ]
    assert written == 3
    assert len(ddc.destroys()) == 3
    assert f"err=0x{LOST:08X}" in _trace_text(appdata)
    assert "retry re-enumerated monitors=1" in _trace_text(appdata)


# 7-2. 재시도에서도 소실 오류가 나도 재열거는 1회로 끝남
def test_handle_lost_retry_limited(ddc: FakeDdc) -> None:
    def _impl(handle: int, value: int) -> bool:
        ddc.last_error = LOST_SIGNED
        return False

    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    assert ss._power_off_monitors(groups) == 0
    ss._release_physical_monitors(groups)

    assert ddc.events.count("enum") == 2
    assert len(ddc.sets()) == 8


# 7-3. 1라운드 소실 오류만으로는 재시도하지 않음 (2라운드 기준)
def test_handle_lost_first_round_only_no_retry(ddc: FakeDdc) -> None:
    def _impl(handle: int, value: int) -> bool:
        if value == 5 and handle == 1002:
            ddc.last_error = LOST_SIGNED
            return False
        return True

    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    ss._power_off_monitors(groups)

    assert ddc.events.count("enum") == 1


# 7-4. 가짜 시계로 예산을 넘기면 멈춤
def test_budget_stops_writes(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    now = [100.0]
    monkeypatch.setattr(ss, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    step = MONITOR_OFF_BUDGET_SEC / 1.5  # 쓰기 1회에 예산의 2/3 가 흐름

    def _tick() -> None:
        now[0] += step

    ddc.on_set = _tick
    groups = ss._acquire_physical_monitors()
    written = ss._power_off_monitors(groups)

    assert written == 2
    assert ddc.sets() == [("set", 1001, 5), ("set", 1002, 5)]
    assert "budget exceeded round=2 monitor=0" in _trace_text(appdata)


# 7-5. 예산을 넘긴 뒤엔 소실 오류가 있어도 재열거하지 않음
def test_budget_blocks_retry(monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc) -> None:
    now = [0.0]
    monkeypatch.setattr(ss, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    step = MONITOR_OFF_BUDGET_SEC / 4

    def _tick() -> None:
        now[0] += step

    def _impl(handle: int, value: int) -> bool:
        ddc.last_error = LOST_SIGNED
        return False

    ddc.on_set = _tick
    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    ss._power_off_monitors(groups)

    assert len(ddc.sets()) == 4
    assert ddc.events.count("enum") == 1


# 8. 설정 파일 없음·깨짐 → 기본값 False, 저장 뒤 다시 읽으면 유지
def test_settings_default_and_roundtrip(appdata: Any) -> None:
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}

    path = ss._settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken")
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}

    with open(path, "w", encoding="utf-8") as f:
        json.dump({SETTINGS_KEY_MONITOR_OFF: "yes"}, f)
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}

    assert ss.save_settings({SETTINGS_KEY_MONITOR_OFF: True}) is True
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: True}
    assert not os.path.exists(path + ".tmp")
    assert path.startswith(os.path.join(str(appdata), ss.APP_NAME))


def test_toggle_monitor_off_saves_and_toasts(appdata: Any) -> None:
    app = _make_app(ACTION_SHUTDOWN, False)
    app.tray_icon = mock.MagicMock()
    app._show_confirm_toast = mock.MagicMock()

    app._toggle_monitor_off()

    assert app._settings[SETTINGS_KEY_MONITOR_OFF] is True
    assert ss.load_settings()[SETTINGS_KEY_MONITOR_OFF] is True
    app.tray_icon.update_menu.assert_called_once()
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_off_on"


# 9. Timer 경로가 "execute" 를 큐에 넣고, 큐 처리에서 _execute_action 을 부름
class _FakeTimer:
    created: list["_FakeTimer"] = []

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        _FakeTimer.created.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _timer_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    _FakeTimer.created = []
    monkeypatch.setattr(ss.threading, "Timer", _FakeTimer)
    app = _make_app(ACTION_SHUTDOWN, True)
    app._shutdown_timer = None
    app._warning_timers = []
    app._tooltip_timer = None
    app._tk_root = mock.MagicMock()
    app._set_active_state = mock.MagicMock()
    app._update_tooltip_loop = mock.MagicMock()
    app._show_confirm_toast = mock.MagicMock()
    app._execute_action = mock.MagicMock()
    return app


def test_schedule_timer_puts_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _timer_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 120)

    main_timer = [t for t in _FakeTimer.created if t.interval == 120]
    assert len(main_timer) == 1
    main_timer[0].function()
    assert app._queue.get_nowait() == "execute"
    app._execute_action.assert_not_called()


def test_resume_timer_puts_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _timer_app(monkeypatch)
    app.is_active = True
    app.is_paused = True
    app._pause_remaining = 45.0
    app.resume_shutdown()

    assert len(_FakeTimer.created) == 1
    assert _FakeTimer.created[0].interval == 45
    _FakeTimer.created[0].function()
    assert app._queue.get_nowait() == "execute"


def test_process_queue_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _timer_app(monkeypatch)
    app._toggle_monitor_off = mock.MagicMock()
    app._queue.put("execute")
    app._queue.put("toggle_monitor_off")

    app._process_queue()

    app._execute_action.assert_called_once_with()
    app._toggle_monitor_off.assert_called_once_with()


# 10. windll 이 없는 환경에서 헬퍼가 예외를 내지 않음
def test_helpers_without_windll(monkeypatch: pytest.MonkeyPatch, appdata: Any) -> None:
    monkeypatch.delattr(ctypes, "windll", raising=False)
    fake_groups = [(1, object())]

    assert ss._acquire_physical_monitors() == []
    assert ss._power_off_monitors(fake_groups) == 0
    ss._release_physical_monitors(fake_groups)
    ss._set_shutdown_level_last()

    calls = _install_run(monkeypatch, None, returncode=0)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()
    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]


def test_trace_log_rotates(appdata: Any) -> None:
    path = os.path.join(str(appdata), ss.APP_NAME, SHUTDOWN_TRACE_FILE_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * (SHUTDOWN_TRACE_MAX_BYTES + 1))

    ss._trace_log("fresh")

    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert content.endswith(" fresh\n")
    assert len(content) < 100
