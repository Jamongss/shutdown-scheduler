"""v1.2.1 왓슨 보강 — 진단 읽기 시간 초과 경계, 일시정지 복원 lead 낮춤 경계, 창 차단 장치.

가짜 DLL 만 쓰므로 실제 DDC·종료 명령은 나가지 않고, 창도 만들지 않는다.
"""

import os
import threading
import time
import tkinter as tk
from typing import Any, Optional

import pytest

from conftest import REAL_WINDOWS_ENV
from test_monitor_off import (  # noqa: F401  (appdata 는 autouse 픽스처)
    FakeDdc,
    _install_run,
    _make_app,
    _trace_text,
    appdata,
    ddc,
    ss,
)
from test_monitor_schedule import _sched_app
from test_schedule_restore import _read, _write
from cfg.config import (
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    MONITOR_PRE_READ_WAIT_SEC,
    SCHEDULE_FILE_VERSION,
)

NOT_RESPONDING = 0xC0262582
INVALID_HANDLE = 0xC026258C
WRITES = [("set", 1001, 5), ("set", 1002, 5), ("set", 1001, 4), ("set", 1002, 4)]
_NOW = 1_800_000_000.0


@pytest.fixture
def thread_errors(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """스레드 밖으로 샌 예외를 모은다."""
    errors: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", lambda a: errors.append(a.exc_value))
    return errors


def _join_workers(workers: list[threading.Thread]) -> None:
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()


# 1. 실제 상한(0.3초) 그대로 — 읽기가 멈추면 상한 뒤에 종료 명령이 나가고 쓰기도 그대로
def test_hang_runs_shutdown_after_real_limit(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    assert MONITOR_PRE_READ_WAIT_SEC == 0.3
    release = threading.Event()
    run_at: list[float] = []
    calls = _install_run(monkeypatch, ddc, returncode=0)
    real_run = ss.subprocess.run

    def _run(*args: Any, **kwargs: Any) -> Any:
        run_at.append(time.monotonic())
        return real_run(*args, **kwargs)

    monkeypatch.setattr(ss.subprocess, "run", _run)
    ddc.read_impl = lambda handle, code: release.wait(10)
    started = time.monotonic()
    try:
        _make_app(ACTION_SHUTDOWN, True)._execute_action()
    finally:
        release.set()
    waited = run_at[0] - started
    assert 0.29 <= waited < 1.5
    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert [e for e in ddc.events if e in WRITES] == WRITES
    assert "pre read timeout" in _trace_text(appdata)


# 2. 꺼진 모니터가 10ms 만에 실패하면 시간 초과 없이 바로 진행 (2대 모두 기록)
def test_fast_failure_has_no_timeout(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    ddc.last_error = NOT_RESPONDING

    def _read_impl(handle: int, code: int) -> bool:
        time.sleep(0.01)
        return False

    ddc.read_impl = _read_impl
    _make_app(ACTION_SHUTDOWN, True)._execute_action()
    trace = _trace_text(appdata)
    assert "pre read timeout" not in trace
    assert "pre monitor=0 read ok=False err=0xC0262582" in trace
    assert "pre monitor=1 read ok=False err=0xC0262582" in trace
    assert len(calls) == 1


# 3. 시간 초과 뒤 worker 가 쓰기·핸들 해제가 끝난 다음 예외로 깨어나도 밖으로 새지 않음
@pytest.mark.parametrize("wake", ["raise", "fail"])
def test_late_worker_after_destroy_does_not_leak(
    monkeypatch: pytest.MonkeyPatch,
    ddc: FakeDdc,
    appdata: Any,
    thread_errors: list[Any],
    wake: str,
) -> None:
    monkeypatch.setattr(ss, "MONITOR_PRE_READ_WAIT_SEC", 0.02)
    _install_run(monkeypatch, ddc, returncode=0)
    ddc.last_error = INVALID_HANDLE
    release = threading.Event()
    workers: list[threading.Thread] = []

    def _read_impl(handle: int, code: int) -> bool:
        workers.append(threading.current_thread())
        release.wait(10)
        if wake == "raise":
            raise OSError("handle destroyed")
        return False

    ddc.read_impl = _read_impl
    _make_app(ACTION_SHUTDOWN, True)._execute_action()
    assert ddc.destroys() == [("destroy", 1), ("destroy", 1)]
    release.set()
    _join_workers(workers)

    assert thread_errors == []
    assert len(workers) == 1 and workers[0].daemon is True
    trace = _trace_text(appdata)
    if wake == "raise":
        assert "pre monitor=0 read exception OSError('handle destroyed')" in trace
    else:
        assert "pre monitor=0 read ok=False err=0xC026258C" in trace
    assert "pre monitor=1" not in trace
    assert trace.index("end") < trace.index("pre monitor=0")


# 4. 진단 준비 단계 실패도 종료 명령을 막지 않음 (예외가 _execute_action 밖으로 안 나옴)
@pytest.mark.parametrize("target", ["_load_ddc_api", "_monitor_handles"])
def test_diagnostic_setup_failure_does_not_block_shutdown(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any, target: str
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    groups = ss._acquire_physical_monitors()

    def _boom(*args: Any) -> None:
        raise RuntimeError("setup")

    monkeypatch.setattr(ss, target, _boom)
    ss._trace_monitor_state(groups)
    assert "pre read error RuntimeError('setup')" in _trace_text(appdata)
    assert calls == []


# 5. 진단 실패 뒤에도 _execute_action 은 종료 명령을 낸다
def test_execute_continues_when_diagnostic_raises(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    real_thread = ss.threading.Thread

    class _Boom(real_thread):  # type: ignore[valid-type, misc]
        def join(self, timeout: Optional[float] = None) -> None:
            raise RuntimeError("join")

    monkeypatch.setattr(ss.threading, "Thread", _Boom)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()
    assert "pre read error RuntimeError('join')" in _trace_text(appdata)
    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert [e for e in ddc.events if e in WRITES] == WRITES


# ------------------------------------------------------------------ #
# B. 일시정지 복원의 lead 낮춤 경계 (시각 고정)                       #
# ------------------------------------------------------------------ #


def _file(sd: dict) -> dict:
    return {
        "version": SCHEDULE_FILE_VERSION,
        "saved_at": _NOW - 5,
        "shutdown": sd,
        "solo": None,
    }


def _sd(
    target: Optional[float] = None,
    paused: Optional[float] = None,
    lead: Optional[int] = 300,
    done: bool = False,
    action: str = ACTION_SHUTDOWN,
) -> dict:
    return {
        "action": action,
        "target": target,
        "paused_remaining": paused,
        "total": 1800,
        "monitor_lead_sec": lead,
        "linked_done": done,
    }


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = type("T", (), {"time": staticmethod(lambda: _NOW)})
    fake.monotonic = staticmethod(time.monotonic)  # type: ignore[attr-defined]
    monkeypatch.setattr(ss, "time", fake)


# 6. 남은 초 = lead 는 낮추고, lead + 1 은 유지 (곧 일시정지 복원·저장된 일시정지 모두)
@pytest.mark.parametrize(
    "sd, expected",
    [
        (_sd(target=_NOW + 300), 0),
        (_sd(target=_NOW + 301), 300),
        (_sd(paused=300.0), 0),
        (_sd(paused=301.0), 300),
        (_sd(paused=120.0, done=True), 300),
        (_sd(paused=120.0, lead=0), 0),
        (_sd(paused=120.0, lead=None), None),
        (_sd(paused=120.0, action=ACTION_RESTART), None),
    ],
)
def test_paused_restore_lead_boundary(
    monkeypatch: pytest.MonkeyPatch,
    appdata: Any,
    fixed_clock: None,
    sd: dict,
    expected: Optional[int],
) -> None:
    _write(appdata, _file(sd))
    app = _sched_app(monkeypatch)
    app._restore_schedule()
    assert app.is_paused is True
    assert app._monitor_lead_sec == expected
    assert _read(appdata)["shutdown"]["monitor_lead_sec"] == expected
    assert app._queue.empty()


# ------------------------------------------------------------------ #
# 창 차단 장치                                                        #
# ------------------------------------------------------------------ #


# 7. 기본 실행에서는 DISPLAY 가 지워지고 tk.Tk() 가 창을 만들지 못함
@pytest.mark.skipif(
    os.environ.get(REAL_WINDOWS_ENV) == "1", reason="실제 창 실행을 명시한 경우임"
)
def test_default_run_cannot_open_window() -> None:
    assert "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ
    with pytest.raises(tk.TclError):
        tk.Tk()
