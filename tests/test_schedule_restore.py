"""재실행 시 예약 복원 테스트 — schedule.json 저장 시점·지우는 시점·복원 분기.

복원 중에는 어떤 경우에도 종료 명령(subprocess.run)·즉시 모니터 끄기가 0회여야 한다.
"""

import json
import os
import time
from datetime import datetime
from typing import Any, Optional
from unittest import mock

import pytest

from test_monitor_off import appdata, ss  # noqa: F401  (appdata 는 autouse 픽스처)
from test_monitor_schedule import _Timer, _due_timers, _sched_app
from cfg.config import (
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    MAX_SCHEDULE_SECONDS,
    RESTORE_PAUSE_UNDER_SEC,
    SCHEDULE_FILE_VERSION,
)


def _path(appdata: Any) -> str:
    return os.path.join(str(appdata), ss.APP_NAME, "schedule.json")


def _read(appdata: Any) -> Optional[dict]:
    path = _path(appdata)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(appdata: Any, data: Any, raw: Optional[bytes] = None) -> None:
    path = _path(appdata)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw if raw is not None else json.dumps(data).encode("utf-8"))


def _live(timers: list[_Timer]) -> list[_Timer]:
    return [t for t in timers if not t.cancelled]


def _exec_timers() -> list[_Timer]:
    return [
        t
        for t in _Timer.created
        if getattr(t.function, "__name__", "") == "<lambda>"
        and not t.function.__defaults__
        and not t.cancelled
    ]


def _shutdown_part(
    target_in: Optional[float],
    total: float,
    action: str = ACTION_SHUTDOWN,
    lead: Optional[int] = None,
    done: bool = False,
    paused: Optional[float] = None,
) -> dict:
    return {
        "action": action,
        "target": None if target_in is None else time.time() + target_in,
        "paused_remaining": paused,
        "total": total,
        "monitor_lead_sec": lead,
        "linked_done": done,
    }


def _file(shutdown: Optional[dict] = None, solo_in: Optional[float] = None) -> dict:
    return {
        "version": SCHEDULE_FILE_VERSION,
        "saved_at": time.time() - 5,
        "shutdown": shutdown,
        "solo": None if solo_in is None else {"target": time.time() + solo_in},
    }


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """종료 명령·즉시 끄기 호출을 세는 가짜를 깐 앱."""
    runs: list[Any] = []
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: runs.append(a))
    app = _sched_app(monkeypatch)
    app._turn_off_monitors_now = mock.MagicMock()
    app._execute_action = mock.MagicMock()
    return {"app": app, "runs": runs}


def _assert_nothing_fired(g: dict[str, Any]) -> None:
    app = g["app"]
    assert g["runs"] == []
    app._turn_off_monitors_now.assert_not_called()
    app._execute_action.assert_not_called()
    # 트레이 재확인용 after(250) 는 허용하고, 즉시 실행 경로의 after 만 막음
    for call in app._tk_root.after.call_args_list:
        assert call.args[0] == 250 and call.args[1] is not app._execute_action
    # arm(0 이하) 는 큐에 바로 끄기 이벤트를 넣으므로 큐가 비어 있어야 함
    assert app._queue.empty()


def _toast(app: Any) -> str:
    return app._show_confirm_toast.call_args.args[0]


# ------------------------------------------------------------------ #
# 저장                                                                 #
# ------------------------------------------------------------------ #


# W-1. 예약·일시정지·재개·연장 때 쓰고, 내용은 확정안 형식
def test_save_points_and_format(monkeypatch: pytest.MonkeyPatch, appdata: Any) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800, 300)
    data = _read(appdata)
    assert set(data) == {"version", "saved_at", "shutdown", "solo"}
    assert data["version"] == SCHEDULE_FILE_VERSION and data["solo"] is None
    sd = data["shutdown"]
    assert sd["action"] == ACTION_SHUTDOWN and sd["paused_remaining"] is None
    assert sd["total"] == 1800 and sd["monitor_lead_sec"] == 300
    assert sd["linked_done"] is False
    assert abs(sd["target"] - app.scheduled_time.timestamp()) < 0.01

    app.pause_shutdown()
    sd = _read(appdata)["shutdown"]
    assert sd["target"] is None and sd["paused_remaining"] == pytest.approx(1800, abs=2)

    app.extend_schedule(600)
    assert _read(appdata)["shutdown"]["paused_remaining"] == pytest.approx(2400, abs=2)

    app.resume_shutdown()
    sd = _read(appdata)["shutdown"]
    assert sd["paused_remaining"] is None and sd["total"] == pytest.approx(2400, abs=2)

    app.extend_schedule(600)
    sd = _read(appdata)["shutdown"]
    assert abs(sd["target"] - app.scheduled_time.timestamp()) < 0.01
    assert sd["total"] == pytest.approx(3000, abs=2)


# W-2. 단독 예약·linked 꺼짐도 저장, 즉시 실행(0초)은 저장하지 않음
def test_save_solo_linked_done_and_skip_immediate(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 0, 0)
    assert _read(appdata) is None
    app.schedule_monitor_only(0)
    assert _read(appdata) is None

    app.schedule_monitor_only(900)
    solo = _read(appdata)["solo"]
    assert abs(solo["target"] - app._solo_monitor.due.timestamp()) < 0.01

    app.schedule_action(ACTION_SHUTDOWN, 1800, 600)
    app._turn_off_monitors_now = mock.MagicMock()
    _due_timers("linked")[0].fire()
    app._process_queue()
    data = _read(appdata)
    assert data["shutdown"]["linked_done"] is True and data["solo"] is not None


# W-3. 취소 계열은 해당 부분만 지우고, 비면 파일을 지움
def test_cancel_clears_parts(monkeypatch: pytest.MonkeyPatch, appdata: Any) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.schedule_monitor_only(900)
    app.cancel_shutdown()
    data = _read(appdata)
    assert data["shutdown"] is None and data["solo"] is not None
    app.cancel_monitor_only()
    assert _read(appdata) is None

    app.schedule_action(ACTION_RESTART, 1800)
    app.schedule_monitor_only(900)
    app._cancel_all()
    assert _read(appdata) is None


# W-4. _execute_action 은 subprocess.run 전에 종료 부분을 지움 (단독은 남김)
def test_execute_clears_shutdown_before_run(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.schedule_monitor_only(900)
    seen: list[Any] = []

    def _run(args: list[str], **kwargs: Any) -> Any:
        seen.append(_read(appdata))
        return mock.MagicMock(returncode=0)

    monkeypatch.setattr(ss.subprocess, "run", _run)
    app._execute_action()
    assert len(seen) == 1
    assert seen[0]["shutdown"] is None and seen[0]["solo"] is not None

    app.cancel_monitor_only()
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    seen.clear()
    app._execute_action()
    assert seen == [None]


# W-5. 단독 실행 뒤엔 단독 부분을 지우고, 프로그램 종료 때는 파일을 지움
def test_solo_fire_and_exit_clear(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    app = _sched_app(monkeypatch)
    app._turn_off_monitors_now = mock.MagicMock()
    app.schedule_action(ACTION_SHUTDOWN, 1800)
    app.schedule_monitor_only(900)
    _due_timers("solo")[0].fire()
    app._process_queue()
    app._turn_off_monitors_now.assert_called_once_with("solo")
    data = _read(appdata)
    assert data["solo"] is None and data["shutdown"] is not None

    app._exit_app()
    assert _read(appdata) is None


# ------------------------------------------------------------------ #
# 복원                                                                 #
# ------------------------------------------------------------------ #


# R-1. 파일 없음 → 아무것도 안 함
def test_restore_no_file(guard: dict[str, Any]) -> None:
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active is False
    app._show_confirm_toast.assert_not_called()
    _assert_nothing_fired(guard)


# R-2. 깨진 파일·버전 다름·형식 틀림 → 버리고 지움
@pytest.mark.parametrize(
    "data, raw",
    [
        (None, b"{not json"),
        (None, b"[1, 2]"),
        ({**_file(), "version": SCHEDULE_FILE_VERSION + 1}, None),
        (_file(shutdown={**_shutdown_part(900, 900), "paused_remaining": 30.0}), None),
        (_file(shutdown={**_shutdown_part(900, 900), "action": "monitor_off"}), None),
        (_file(shutdown={**_shutdown_part(900, 900), "linked_done": "no"}), None),
        (_file(shutdown={**_shutdown_part(900, 900), "total": True}), None),
        ({**_file(), "solo": {"target": "x"}}, None),
    ],
)
def test_restore_broken_file(
    guard: dict[str, Any], appdata: Any, data: Any, raw: Optional[bytes]
) -> None:
    _write(appdata, data, raw)
    guard["app"]._restore_schedule()
    assert _read(appdata) is None if raw is None else not os.path.exists(_path(appdata))
    assert guard["app"].is_active is False
    _assert_nothing_fired(guard)


# R-3. 시계 이상(저장 시각보다 과거, 남은 초 > total + 60) → 버리고 알림
@pytest.mark.parametrize(
    "data",
    [
        {**_file(shutdown=_shutdown_part(1800, 1800)), "saved_at": time.time() + 3600},
        _file(shutdown=_shutdown_part(4000, 1800), solo_in=900),
    ],
)
def test_restore_clock_changed(guard: dict[str, Any], appdata: Any, data: dict) -> None:
    _write(appdata, data)
    app = guard["app"]
    app._restore_schedule()
    assert _toast(app) == "시계가 바뀌어 복원하지 않았습니다."
    assert app.is_active is False and app._solo_monitor.armed is False
    assert _read(appdata) is None
    _assert_nothing_fired(guard)


# R-4. 남은 초 0 이하 → 버리고 "앱이 꺼진 사이 HH:MM 예약 시각이 지나 취소했습니다"
def test_restore_time_passed(guard: dict[str, Any], appdata: Any) -> None:
    data = _file(shutdown=_shutdown_part(-30, 1800, lead=300))
    _write(appdata, data)
    app = guard["app"]
    app._restore_schedule()
    at = f"{datetime.fromtimestamp(data['shutdown']['target']):%H:%M}"
    assert _toast(app) == f"앱이 꺼진 사이 {at} 예약 시각이 지나 취소했습니다."
    assert app.is_active is False
    assert _read(appdata) is None
    _assert_nothing_fired(guard)


# R-5. 0 < 남은 초 < 600 → 일시정지 상태로 복원하고 "▶로 재개하세요"
@pytest.mark.parametrize(
    "remaining, expected_lead",
    [(1, 0), (120, 0), (RESTORE_PAUSE_UNDER_SEC - 1, 300)],
)
def test_restore_short_paused(
    guard: dict[str, Any], appdata: Any, remaining: int, expected_lead: int
) -> None:
    """일시정지 복원 시 끄는 시각이 지났으면(남은 초 ≤ lead) lead 를 0 으로 낮춘다.

    PM 확정안 B: ▶ 를 눌렀을 때 바로 모니터가 꺼지지 않게 일시정지 복원에도 낮춤을 적용한다.
    (예전 기대값은 남은 초와 상관없이 300 이었음)
    """
    _write(appdata, _file(shutdown=_shutdown_part(remaining, 1800, lead=300)))
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active is True and app.is_paused is True
    assert app._pause_remaining == pytest.approx(remaining, abs=2)
    assert app._monitor_lead_sec == expected_lead
    assert "▶로 재개하세요" in _toast(app)
    assert _exec_timers() == [] and _live(_due_timers("linked")) == []
    assert _read(appdata)["shutdown"]["paused_remaining"] == pytest.approx(
        remaining, abs=2
    )
    _assert_nothing_fired(guard)
    vm = ss._view_model(app)
    assert vm.paused is True and vm.label == "일시정지"


# R-6. 정상 복원 — 실행·경고·linked Timer 를 남은 시간 기준으로 다시 걺
def test_restore_running(guard: dict[str, Any], appdata: Any) -> None:
    data = _file(shutdown=_shutdown_part(1800, 3600, lead=300))
    _write(appdata, data)
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active is True and app.is_paused is False
    assert abs(app.scheduled_time.timestamp() - data["shutdown"]["target"]) < 2
    execs = _exec_timers()
    assert len(execs) == 1 and abs(execs[0].interval - 1800) < 2
    linked = _live(_due_timers("linked"))
    assert len(linked) == 1 and abs(linked[0].interval - 1500) < 2
    assert app._monitor_lead_sec == 300
    assert "예약을 복원했습니다" in _toast(app)
    assert _read(appdata)["shutdown"]["total"] == pytest.approx(1800, abs=2)
    _assert_nothing_fired(guard)


# R-7. linked 끄는 시각이 지났으면 lead 를 0 으로, linked_done 이면 그대로
@pytest.mark.parametrize(
    "lead, done, expected_lead, linked_count",
    [
        (2000, False, 0, 0),
        (1800, False, 0, 0),
        (300, True, 300, 0),
        (None, False, None, 0),
    ],
)
def test_restore_linked_variants(
    guard: dict[str, Any],
    appdata: Any,
    lead: Optional[int],
    done: bool,
    expected_lead: Optional[int],
    linked_count: int,
) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(1800, 3600, lead=lead, done=done)))
    app = guard["app"]
    app._restore_schedule()
    assert app._monitor_lead_sec == expected_lead
    assert app._linked_done is done
    assert len(_live(_due_timers("linked"))) == linked_count
    _assert_nothing_fired(guard)


# R-8. 일시정지 상태로 저장됐으면 그대로, 재시작은 lead 를 쓰지 않음
def test_restore_saved_paused_and_restart(guard: dict[str, Any], appdata: Any) -> None:
    _write(
        appdata,
        _file(shutdown=_shutdown_part(None, 50, ACTION_RESTART, lead=300, paused=50.0)),
    )
    app = guard["app"]
    app._restore_schedule()
    assert app.scheduled_action == ACTION_RESTART
    assert app.is_paused is True and app._pause_remaining == 50.0
    assert app._monitor_lead_sec is None
    assert _toast(app) == "일시정지된 재시작 예약을 복원했습니다."
    _assert_nothing_fired(guard)
    app.resume_shutdown()
    assert abs(_exec_timers()[0].interval - 50) < 1


# R-9. 단독 예약 — 600초 이상이면 복원, 짧으면·지났으면 버리고 알림
@pytest.mark.parametrize(
    "remaining, armed, note",
    [
        (900, True, "모니터 끄기 예약을 복원했습니다."),
        (RESTORE_PAUSE_UNDER_SEC - 1, False, "남은 시간이 짧아 복원하지 않았습니다."),
        (-10, False, "모니터 끄기 시각이 지나 취소했습니다."),
    ],
)
def test_restore_solo(
    guard: dict[str, Any], appdata: Any, remaining: int, armed: bool, note: str
) -> None:
    _write(appdata, _file(solo_in=remaining))
    app = guard["app"]
    app._restore_schedule()
    assert app._solo_monitor.armed is armed
    assert note in _toast(app)
    if armed:
        live = _live(_due_timers("solo"))
        assert len(live) == 1 and abs(live[0].interval - remaining) < 2
        assert _read(appdata)["solo"] is not None
    else:
        assert _read(appdata) is None
    _assert_nothing_fired(guard)


# R-10. 종료 + 단독 함께 복원, 알림은 한 토스트에 줄로 이어 붙임
def test_restore_both(guard: dict[str, Any], appdata: Any) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(1800, 1800), solo_in=900))
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active and app._solo_monitor.armed
    assert len(_toast(app).split("\n")) == 2
    assert ss._view_model(app).solo_extra is True
    _assert_nothing_fired(guard)


# R-11. 저장 → 새 앱에서 복원하면 같은 예약 시각으로 돌아옴
def test_round_trip(monkeypatch: pytest.MonkeyPatch, appdata: Any) -> None:
    app = _sched_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 3600, 600)
    target = app.scheduled_time
    app2 = _sched_app(monkeypatch)
    app2._restore_schedule()
    assert abs((app2.scheduled_time - target).total_seconds()) < 2
    assert app2._monitor_lead_sec == 600 and app2.is_active is True


# R-12. 트레이가 아직 없으면 잠깐 뒤 다시 보고, 있으면 색·툴팁을 맞춤
def test_sync_tray_after_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _sched_app(monkeypatch)
    app._sync_tray_after_restore()
    assert app._tk_root.after.call_args.args[0] == 250
    app._tk_root.after.reset_mock()
    app._sync_tray_after_restore(tries=0)
    app._tk_root.after.assert_not_called()

    app.tray_icon = mock.MagicMock()
    app._update_tooltip_loop = mock.MagicMock()
    app.is_active = True
    app._sync_tray_after_restore()
    assert app.tray_icon.icon is not None
    app._update_tooltip_loop.assert_called_once_with()


# R-13. main() 은 객체를 만든 뒤 after(0) 으로 복원을 건다
def test_main_schedules_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = mock.MagicMock()
    monkeypatch.setattr(ss, "ShutdownScheduler", lambda: fake)
    monkeypatch.setattr(ss, "_acquire_single_instance_mutex", lambda: object())
    monkeypatch.setattr(ss, "_set_shutdown_level_last", lambda: None)
    ss.main()
    fake._tk_root.after.assert_called_once_with(0, fake._restore_schedule)
    fake.run.assert_called_once_with()


# R-14. 상한(999시간)보다 멀리 떨어진 시각·초는 깨진 파일로 버림
@pytest.mark.parametrize(
    "data",
    [
        _file(shutdown=_shutdown_part(400 * 86400, 400 * 86400)),
        _file(shutdown=_shutdown_part(1800, 1e12)),
        _file(shutdown=_shutdown_part(1800, 1800, lead=10**12)),
        _file(solo_in=400 * 86400),
        {**_file(solo_in=900), "saved_at": 0},
    ],
)
def test_restore_out_of_span(guard: dict[str, Any], appdata: Any, data: dict) -> None:
    """PM 결정: Windows TIMEOUT_MAX 4294967s 대응으로 상한 999시간.

    값(400일, 1e12 등)은 1년 상한 때와 같고 999시간 상한에서도 범위 밖이다.
    """
    _write(appdata, data)
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active is False and app._solo_monitor.armed is False
    assert _read(appdata) is None
    _assert_nothing_fired(guard)


# R-15. 검증을 빠져나간 값으로 복원 중 예외가 나면 되살린 것을 되돌리고 파일을 지움
@pytest.mark.parametrize("exc", [ValueError("x"), OverflowError("x"), OSError("x")])
def test_restore_exception_rolls_back(
    guard: dict[str, Any], appdata: Any, exc: Exception
) -> None:
    _write(appdata, _file(shutdown=_shutdown_part(1800, 1800), solo_in=900))
    app = guard["app"]
    real = app._restore_shutdown

    def _boom(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        app._solo_monitor.arm(900)
        raise exc

    app._restore_shutdown = _boom
    app._restore_schedule()
    assert app.is_active is False and app._solo_monitor.armed is False
    assert _exec_timers() == []
    assert _read(appdata) is None
    _assert_nothing_fired(guard)


# R-16. PM 결정: Windows TIMEOUT_MAX 4294967s 대응으로 상한 999시간 — 복원 경계
@pytest.mark.parametrize(
    "target_in, active",
    [(MAX_SCHEDULE_SECONDS - 5, True), (MAX_SCHEDULE_SECONDS + 5, False)],
)
def test_restore_max_schedule_boundary(
    guard: dict[str, Any], appdata: Any, target_in: int, active: bool
) -> None:
    assert ss.RESTORE_MAX_SPAN_SEC == MAX_SCHEDULE_SECONDS
    _write(appdata, _file(shutdown=_shutdown_part(target_in, MAX_SCHEDULE_SECONDS)))
    app = guard["app"]
    app._restore_schedule()
    assert app.is_active is active
    if active:
        assert _exec_timers()[0].interval <= MAX_SCHEDULE_SECONDS
    else:
        assert _read(appdata) is None
    _assert_nothing_fired(guard)
