"""종료 전 모니터 진단 읽기와 일시정지 복원 lead 낮춤 테스트.

test_monitor_off.py 의 가짜 DLL 을 쓰므로 실제 DDC 호출은 나가지 않는다.
"""

import threading
import time
from typing import Any

import pytest

from test_monitor_off import (  # noqa: F401  (appdata 는 autouse 픽스처)
    FakeDdc,
    _install_run,
    _make_app,
    _trace_text,
    appdata,
    ddc,
    ss,
)
from test_monitor_schedule import _due_timers, _sched_app
from test_schedule_restore import _file, _live, _read, _shutdown_part, _write
from cfg.config import ACTION_RESTART, ACTION_SHUTDOWN, VCP_BRIGHTNESS

NOT_RESPONDING = 0xC0262582
WRITES = [
    ("set", 1001, 5),
    ("set", 1002, 5),
    ("set", 1001, 4),
    ("set", 1002, 4),
]
READS = [("read", 1001, VCP_BRIGHTNESS), ("read", 1002, VCP_BRIGHTNESS)]
DESTROYS = [("destroy", 1), ("destroy", 1)]


def _reads(fake: FakeDdc) -> list[Any]:
    return [e for e in fake.events if isinstance(e, tuple) and e[0] == "read"]


# 1. 읽기 성공 — 결과가 종료 명령 전에 찍히고 쓰기 순서는 그대로
def test_read_success_logged_before_shutdown(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _install_run(monkeypatch, ddc, returncode=0)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert ddc.events == ["enum", *READS, "run", *WRITES, *DESTROYS]
    trace = _trace_text(appdata)
    returncode_at = trace.index("shutdown returncode=0")
    assert trace.index("pre monitor=0 read ok=True err=0x00000000") < returncode_at
    assert trace.index("pre monitor=1 read ok=True err=0x00000000") < returncode_at
    assert trace.index("acquired monitors=2") < trace.index("pre monitor=0")


# 2. 읽기 실패 — 부호 있는 값도 부호 없이 찍히고 run·쓰기는 그대로 진행
@pytest.mark.parametrize("error", [NOT_RESPONDING, NOT_RESPONDING - 2**32])
def test_read_failure_logged_and_continues(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any, error: int
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    ddc.last_error = error
    ddc.read_impl = lambda handle, code: False
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert "pre monitor=0 read ok=False err=0xC0262582" in _trace_text(appdata)
    assert "pre monitor=1 read ok=False err=0xC0262582" in _trace_text(appdata)
    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert ddc.events == ["enum", *READS, "run", *WRITES, *DESTROYS]


# 3. 읽기 예외 — 다음 모니터 읽기와 run 이 진행되고 예외가 밖으로 나오지 않음
def test_read_exception_continues(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _install_run(monkeypatch, ddc, returncode=0)

    def _read_impl(handle: int, code: int) -> bool:
        if handle == 1001:
            raise OSError("driver")
        return True

    ddc.read_impl = _read_impl
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    trace = _trace_text(appdata)
    assert "pre monitor=0 read exception OSError('driver')" in trace
    assert "pre monitor=1 read ok=True err=0x00000000" in trace
    assert ddc.events == ["enum", *READS, "run", *WRITES, *DESTROYS]


# 4. 읽기 멈춤 — 최대 대기 뒤 곧바로 run, 풀어 준 뒤 다음 모니터를 읽지 않음
def test_read_hang_times_out(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    monkeypatch.setattr(ss, "MONITOR_PRE_READ_WAIT_SEC", 0.05)
    _install_run(monkeypatch, ddc, returncode=0)
    release = threading.Event()
    workers: list[threading.Thread] = []

    def _read_impl(handle: int, code: int) -> bool:
        workers.append(threading.current_thread())
        release.wait(10)
        return True

    ddc.read_impl = _read_impl
    try:
        started = time.monotonic()
        _make_app(ACTION_SHUTDOWN, True)._execute_action()
        elapsed = time.monotonic() - started

        assert elapsed < 2
        trace = _trace_text(appdata)
        assert trace.index("pre read timeout") < trace.index("shutdown returncode=0")
        assert ddc.events.index(READS[0]) < ddc.events.index("run")
        assert [e for e in ddc.events if e in WRITES] == WRITES
    finally:
        release.set()
    assert len(workers) == 1
    workers[0].join(5)
    assert not workers[0].is_alive()
    assert _reads(ddc) == [READS[0]]
    assert "pre monitor=1" not in _trace_text(appdata)


# 5. 스레드 시작 실패 — pre read error 만 남기고 종료는 진행
def test_thread_start_failure_continues(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    class _NoThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    calls = _install_run(monkeypatch, ddc, returncode=0)
    monkeypatch.setattr(ss.threading, "Thread", _NoThread)
    _make_app(ACTION_SHUTDOWN, True)._execute_action()

    assert "pre read error RuntimeError" in _trace_text(appdata)
    assert calls == [["shutdown", "/s", "/t", "0", "/f"]]
    assert ddc.events == ["enum", "run", *WRITES, *DESTROYS]


# 6-1. 재시작·설정 꺼짐·모니터 0대는 읽지 않음
@pytest.mark.parametrize(
    "action, enabled, layout",
    [
        (ACTION_RESTART, True, None),
        (ACTION_SHUTDOWN, False, None),
        (ACTION_SHUTDOWN, True, {}),
    ],
)
def test_no_read_without_target(
    monkeypatch: pytest.MonkeyPatch,
    ddc: FakeDdc,
    action: str,
    enabled: bool,
    layout: Any,
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    if layout is not None:
        ddc.layout = layout
    _make_app(action, enabled)._execute_action()

    assert len(calls) == 1
    assert _reads(ddc) == []


# 6-2. N분 전·단독 끄기 경로는 읽지 않음
@pytest.mark.parametrize("kind", ["linked", "solo"])
def test_no_read_on_monitor_only_paths(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, kind: str
) -> None:
    app = _sched_app(monkeypatch)
    if kind == "linked":
        app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    else:
        app.schedule_monitor_only(900)
    _due_timers(kind)[0].fire()
    app._process_queue()

    assert ddc.sets()
    assert _reads(ddc) == []


# 7. 끄는 시각이 지난 일시정지 복원 — ▶ 뒤에도 끄지 않고, 종료 직전 끄기는 유지
@pytest.mark.parametrize("remaining", [120, 299])
def test_paused_restore_past_lead_resume_does_not_fire(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any, remaining: int
) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(remaining, 1800, lead=300)))
    app = _sched_app(monkeypatch)
    app._restore_schedule()
    assert app.is_paused is True and app._monitor_lead_sec == 0

    app.resume_shutdown()
    app._process_queue()

    assert ddc.events == []
    assert _live(_due_timers("linked")) == []
    assert app._linked_monitor.armed is False
    assert _read(appdata)["shutdown"]["monitor_lead_sec"] == 0

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()
    assert ddc.events == ["enum", *READS, "run", *WRITES, *DESTROYS]


# 8-1. 이미 끈 뒤(done=True)면 lead 를 유지하고 ▶ 뒤에도 다시 걸지 않음
def test_paused_restore_done_keeps_lead(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(120, 1800, lead=300, done=True)))
    app = _sched_app(monkeypatch)
    app._restore_schedule()
    assert app._monitor_lead_sec == 300 and app._linked_done is True
    assert _read(appdata)["shutdown"]["monitor_lead_sec"] == 300

    app.resume_shutdown()
    app._process_queue()
    assert ddc.events == []
    assert _live(_due_timers("linked")) == []
    assert app._monitor_lead_sec == 300


# 8-2. 끄는 시각이 남았으면(remaining > lead) ▶ 뒤 linked 가 remaining - lead 로 걸림
def test_paused_restore_before_lead_arms_on_resume(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(500, 1800, lead=300)))
    app = _sched_app(monkeypatch)
    app._restore_schedule()
    assert app.is_paused is True and app._monitor_lead_sec == 300
    assert _live(_due_timers("linked")) == []

    app.resume_shutdown()
    app._process_queue()

    linked = _live(_due_timers("linked"))
    assert len(linked) == 1
    assert linked[0].interval == pytest.approx(200, abs=2)
    assert ddc.events == []
