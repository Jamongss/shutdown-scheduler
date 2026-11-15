"""모니터 끄기(v1.2.0) 보강 테스트 — 큐 경유 실행 경계, 저장 실패, 실기 관찰 반영.

test_monitor_off.py 의 가짜 DLL·픽스처를 그대로 쓴다.
"""

import ctypes
import os
import re
from datetime import datetime, timedelta
from typing import Any, Callable
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
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    CONFIRM_TOAST_DURATION_MS,
    QUEUE_POLL_INTERVAL_MS,
    SETTINGS_KEY_MONITOR_OFF,
)


class _TrackTimer:
    """cancel 여부를 기록하는 가짜 Timer. 실제로 시간을 재지 않는다."""

    created: list["_TrackTimer"] = []

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        self.cancelled = False
        _TrackTimer.created.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        """threading.Timer 처럼 취소되지 않았을 때만 콜백을 부른다."""
        if not self.cancelled:
            self.function()


def _race_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Timer·토스트·트레이만 가짜로 두고 예약 상태 변수는 실제 코드를 쓴다."""
    _TrackTimer.created = []
    monkeypatch.setattr(ss.threading, "Timer", _TrackTimer)
    app = _make_app(ACTION_SHUTDOWN, True)
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
    app._update_tooltip_loop = mock.MagicMock()
    app._open_schedule_dialog = mock.MagicMock()
    app._execute_action = mock.MagicMock()
    return app


def _exec_timers() -> list[_TrackTimer]:
    """실행용 Timer 만 고른다 (경고 Timer 람다는 wl 기본 인자를 가짐)."""
    return [t for t in _TrackTimer.created if not t.function.__defaults__]


# A-1. 발화 전 취소 → 실행 Timer 가 취소되어 "execute" 가 영영 안 들어감
def test_cancel_before_fire_blocks_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 30)
    timer = _exec_timers()[0]

    app.cancel_shutdown()
    timer.fire()
    app._process_queue()

    assert timer.cancelled is True
    assert app._queue.empty()
    app._execute_action.assert_not_called()


# A-2. 발화 전 일시정지 → 재개: 옛 Timer 는 취소, 살아 있는 실행 Timer 는 1개
def test_pause_resume_before_fire_single_live_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 30)
    old = _exec_timers()[0]

    app.pause_shutdown()
    assert app.is_paused is True and old.cancelled is True
    app.resume_shutdown()

    live = [t for t in _exec_timers() if not t.cancelled]
    assert len(live) == 1 and live[0] is not old
    for t in _exec_timers():
        t.fire()
    app._process_queue()
    app._execute_action.assert_called_once_with()


# A-3. 발화 뒤 UI 취소 — 바꾸기 전에도 Timer 스레드가 이미 shutdown 을 불렀으므로 결과가 같음
def test_cancel_after_fire_still_executes_like_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 30)
    _exec_timers()[0].fire()  # 큐에 "execute" 가 들어간 상태

    app.cancel_shutdown()  # 다음 폴링 전 다이얼로그 ■ 버튼 (메인 스레드)
    app._process_queue()

    app._execute_action.assert_called_once_with()


# A-4. 발화 뒤 일시정지는 남은 시간 <= 0 이라 무시됨 → 재개로 새 Timer 가 생기지 않음
def test_pause_after_fire_is_noop_no_second_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 30)
    _exec_timers()[0].fire()
    app.scheduled_time = datetime.now() - timedelta(
        milliseconds=5
    )  # 발화 시점은 예약 시각 이후

    app.pause_shutdown()
    app.resume_shutdown()

    assert app.is_paused is False
    assert len(_exec_timers()) == 1
    app._process_queue()
    app._execute_action.assert_called_once_with()


# A-5. 단축키 취소가 같은 폴링에서 "execute" 보다 먼저 꺼내져도 실행됨 (Timer 는 이미 발화)
def test_cancel_queued_before_execute_same_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 30)
    app._queue.put("cancel")
    _exec_timers()[0].fire()

    app._process_queue()

    assert app.is_active is False
    app._execute_action.assert_called_once_with()


# A-6. 큐 처리 중 다른 이벤트에서 예외가 나도 폴링이 다시 걸려야 함
#      바꾸기 전엔 Timer 스레드가 직접 종료해 폴링과 무관했음 — 큐 경유로 바뀌며 생긴 의존
def test_process_queue_rearms_after_handler_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app._show_warning_toast.side_effect = ss.tk.TclError("toast failed")
    app._queue.put("warn:1분")

    try:
        app._process_queue()
    except Exception:
        pass

    app._tk_root.after.assert_called_with(QUEUE_POLL_INTERVAL_MS, app._process_queue)


# A-7. 같은 폴링에서 앞 이벤트가 예외를 내도 뒤의 "execute" 는 실행되어야 함
def test_execute_survives_earlier_handler_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _race_app(monkeypatch)
    app._show_warning_toast.side_effect = ss.tk.TclError("toast failed")
    app._queue.put("warn:1분")
    app._queue.put("execute")

    try:
        app._process_queue()
    except Exception:
        pass

    app._execute_action.assert_called_once_with()


# A-8. 즉시 실행(0초)은 Timer 없이 메인 스레드 after 로 실행
def test_immediate_uses_after_not_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _race_app(monkeypatch)
    app.schedule_action(ACTION_SHUTDOWN, 0)

    assert _TrackTimer.created == []
    app._tk_root.after.assert_called_once_with(
        CONFIRM_TOAST_DURATION_MS, app._execute_action
    )


def _block_appdata(appdata: Any) -> None:
    """앱 폴더 자리에 파일을 둬 makedirs 가 실제 OSError 를 내게 한다."""
    with open(os.path.join(str(appdata), ss.APP_NAME), "w", encoding="utf-8") as f:
        f.write("not a dir")


# B-1. 저장 실패 → False 반환, 메모리 값은 바뀌고 토스트에 실패 문구가 붙음
@pytest.mark.parametrize("start", [False, True])
def test_toggle_save_failure_keeps_memory_and_warns(appdata: Any, start: bool) -> None:
    _block_appdata(appdata)
    app = _make_app(ACTION_SHUTDOWN, start)
    app.tray_icon = mock.MagicMock()
    app._show_confirm_toast = mock.MagicMock()

    app._toggle_monitor_off()

    assert app._settings[SETTINGS_KEY_MONITOR_OFF] is (not start)
    msg = app._show_confirm_toast.call_args.args[0]
    assert msg.endswith("\n설정 저장에 실패해 이번 실행에만 적용됩니다.")
    expected_type = "monitor_off_off" if start else "monitor_off_on"
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == expected_type
    app.tray_icon.update_menu.assert_called_once()
    assert ss.load_settings()[SETTINGS_KEY_MONITOR_OFF] is False


# B-2. 저장 실패 — 메모리·토스트·메뉴는 바뀌지만 진행 중 예약(lead=None)엔 영향 없음
def test_save_failure_still_applies_in_session(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    """저장 실패해도 메모리 값은 바뀌고, 진행 중 예약(lead=None)의 실행은 쓰기 0회.

    바꾼 이유: 실행 판단이 설정값에서 예약 때 복사한 _monitor_lead_sec 으로 바뀜.
    """
    _block_appdata(appdata)
    app = _make_app(ACTION_SHUTDOWN, False)
    app.tray_icon = mock.MagicMock()
    app._show_confirm_toast = mock.MagicMock()
    assert app._monitor_lead_sec is None

    app._toggle_monitor_off()

    assert app._settings[SETTINGS_KEY_MONITOR_OFF] is True
    msg = app._show_confirm_toast.call_args.args[0]
    assert msg.endswith("\n설정 저장에 실패해 이번 실행에만 적용됩니다.")
    assert app._show_confirm_toast.call_args.kwargs["toast_type"] == "monitor_off_on"
    app.tray_icon.update_menu.assert_called_once()
    assert app._monitor_lead_sec is None
    assert app._linked_monitor.armed is False

    _install_run(monkeypatch, ddc, returncode=0)
    app._execute_action()

    assert ddc.events == ["run"]


# B-3. os.replace 실패 → False, 기존 파일은 그대로 (tmp 는 남음)
def test_replace_failure_keeps_old_file(
    monkeypatch: pytest.MonkeyPatch, appdata: Any
) -> None:
    assert ss.save_settings({SETTINGS_KEY_MONITOR_OFF: True}) is True

    def _deny(src: str, dst: str) -> None:
        raise PermissionError(13, "locked")

    monkeypatch.setattr(ss.os, "replace", _deny)
    assert ss.save_settings({SETTINGS_KEY_MONITOR_OFF: False}) is False
    assert ss.load_settings()[SETTINGS_KEY_MONITOR_OFF] is True
    assert os.path.exists(ss._settings_path() + ".tmp")


# B-4. JSON 이지만 dict 가 아니거나 UTF-8 이 아니면 기본값 False
@pytest.mark.parametrize("raw", [b"[true]", b"null", b"true", b"\xff\xfe\x00{", b""])
def test_load_settings_odd_content(appdata: Any, raw: bytes) -> None:
    path = ss._settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}


# B-5. 설정 키가 없으면 load_settings 는 False, lead=None 이면 실행 시 끄지 않음
def test_missing_key_means_off(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc, appdata: Any
) -> None:
    """키 없는 설정은 False 로 읽고, lead=None 실행은 열거·쓰기 0회.

    바꾼 이유: 실행 판단이 설정값에서 예약 때 복사한 _monitor_lead_sec 으로 바뀜.
    """
    path = ss._settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    assert ss.load_settings() == {SETTINGS_KEY_MONITOR_OFF: False}

    _install_run(monkeypatch, ddc, returncode=0)
    app = _make_app(ACTION_SHUTDOWN, False)
    app._settings = {}
    app._execute_action()
    assert ddc.events == ["run"]


# C-1. 실기에서 물리 모니터 핸들이 0(ctypes 로는 None)으로 나옴 — 걸러지지 않고 써야 함
def test_zero_handle_is_still_written(ddc: FakeDdc) -> None:
    ddc.layout = {101: [0]}
    groups = ss._acquire_physical_monitors()
    handles = ss._monitor_handles(groups)

    assert handles == [None]
    assert ss._power_off_monitors(groups) == 2
    assert ddc.sets() == [("set", None, 5), ("set", None, 4)]
    ss._release_physical_monitors(groups)
    assert ddc.destroys() == [("destroy", 1)]


# C-2. 실측 구조체 크기(64비트 264) 와 같은 레이아웃
def test_physical_monitor_struct_size() -> None:
    expected = ctypes.sizeof(ctypes.c_void_p) + 128 * ctypes.sizeof(ctypes.c_wchar)
    if ctypes.sizeof(ctypes.c_wchar) == 2 and ctypes.sizeof(ctypes.c_void_p) == 8:
        assert expected == 264
    assert ctypes.sizeof(ss._PHYSICAL_MONITOR) == expected


# 열거 일부 실패: 개수 조회 실패·0개·배열 조회 실패 모니터는 건너뜀
def test_acquire_skips_failed_hmonitors(ddc: FakeDdc) -> None:
    ddc.layout = {101: [1001], 102: [], 103: [1003], 104: [1004]}
    orig_count, orig_phys = ddc._get_count, ddc._get_physical
    ddc.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.impl = lambda h, ref: (
        0 if h == 103 else orig_count(h, ref)
    )
    ddc.dxva2.GetPhysicalMonitorsFromHMONITOR.impl = lambda h, n, arr: (
        0 if h == 104 else orig_phys(h, n, arr)
    )

    groups = ss._acquire_physical_monitors()

    assert ss._monitor_handles(groups) == [1001]


# 재열거에서 모니터가 하나도 안 잡히면 추가 쓰기 없이 끝남
def test_retry_with_no_monitors(ddc: FakeDdc) -> None:
    def _impl(handle: int, value: int) -> bool:
        ddc.layout = {}
        ddc.last_error = ss._ERROR_INVALID_PHYSICAL_MONITOR_HANDLE - 2**32
        return value == 5

    ddc.set_impl = _impl
    groups = ss._acquire_physical_monitors()
    assert ss._power_off_monitors(groups) == 2
    assert ddc.events.count("enum") == 2
    assert len(ddc.sets()) == 4


# _power_off_monitors 가 예기치 않게 예외를 내도 핸들은 1회 해제되고 밖으로 안 샘
def test_execute_releases_when_power_off_raises(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    _install_run(monkeypatch, ddc, returncode=0)
    monkeypatch.setattr(
        ss, "_power_off_monitors", mock.MagicMock(side_effect=RuntimeError("x"))
    )
    _make_app(ACTION_SHUTDOWN, True)._execute_action()
    assert len(ddc.destroys()) == 2


# 재시작 + 설정 꺼짐 조합도 모니터를 건드리지 않음
def test_restart_disabled_untouched(
    monkeypatch: pytest.MonkeyPatch, ddc: FakeDdc
) -> None:
    calls = _install_run(monkeypatch, ddc, returncode=0)
    _make_app(ACTION_RESTART, False)._execute_action()
    assert calls == [["shutdown", "/r", "/t", "0", "/f"]]
    assert ddc.events == ["run"]


# 확정안 6: shutdown 의 /t 값은 "0" 만, ttk 미사용
def test_source_guards() -> None:
    with open(ss.__file__, encoding="utf-8") as f:
        src = f.read()
    t_values = re.findall(r'"/t",\s*([^\],]+)', src)
    assert t_values and all(v.strip() == '"0"' for v in t_values)
    assert not re.search(r"\bttk\b", src)


def _poll_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """큐 처리기만 가짜로 둔 인스턴스 (폴링 재예약은 _tk_root.after 로 확인)."""
    app = _race_app(monkeypatch)
    app._exit_app = mock.MagicMock()
    app._toggle_autostart = mock.MagicMock()
    app._toggle_monitor_off = mock.MagicMock()
    app.cancel_shutdown = mock.MagicMock()
    return app


def _rearmed(app: Any) -> bool:
    return mock.call(QUEUE_POLL_INTERVAL_MS, app._process_queue) in (
        app._tk_root.after.call_args_list
    )


# Q-1. exit 성공 뒤엔 남은 이벤트를 꺼내지 않고 재예약도 안 함 (destroy 된 루트)
def test_exit_stops_batch_without_rearm(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    for e in ("cancel", "exit", "execute", "open_dialog"):
        app._queue.put(e)

    app._process_queue()

    app.cancel_shutdown.assert_called_once_with()
    app._exit_app.assert_called_once_with()
    app._execute_action.assert_not_called()
    app._open_schedule_dialog.assert_not_called()
    assert not _rearmed(app)
    assert app._queue.qsize() == 2


# Q-2. exit 가 예외를 내면 앱이 살아 있으므로 남은 이벤트 처리 + 재예약
def test_exit_failure_continues_and_rearms(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app._exit_app.side_effect = RuntimeError("tray stop failed")
    app._queue.put("exit")
    app._queue.put("execute")

    app._process_queue()

    app._execute_action.assert_called_once_with()
    assert _rearmed(app)


# Q-3. open_dialog 처리 중 예외 → 같은 배치의 다른 이벤트는 처리되고 재예약됨
def test_open_dialog_error_rearms(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app._open_schedule_dialog.side_effect = ss.tk.TclError("dialog failed")
    app._queue.put("open_dialog")
    app._queue.put("execute")

    app._process_queue()

    app._execute_action.assert_called_once_with()
    app._open_schedule_dialog.assert_called_once_with()
    assert _rearmed(app)


# Q-4. open_dialog 가 여러 번 쌓여도 1회, 다른 이벤트 처리 뒤에 열림
def test_open_dialog_once_after_others(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    order: list[str] = []
    app._open_schedule_dialog.side_effect = lambda: order.append("dialog")
    app.cancel_shutdown.side_effect = lambda: order.append("cancel")
    for e in ("open_dialog", "open_dialog", "cancel", "open_dialog"):
        app._queue.put(e)

    app._process_queue()

    assert order == ["cancel", "dialog"]
    assert _rearmed(app)


# Q-5. 같은 배치의 execute 2개는 둘 다 실행 — 수정 전 코드와 같은 동작 (중복 제거 없음)
def test_double_execute_same_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app._queue.put("execute")
    app._queue.put("execute")

    app._process_queue()

    assert app._execute_action.call_count == 2
    assert _rearmed(app)


# Q-6. execute 자체가 예외를 내도 뒤 이벤트 처리 + 재예약
def test_execute_error_does_not_stop_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app._execute_action.side_effect = OSError("spawn failed")
    app._queue.put("execute")
    app._queue.put("toggle_monitor_off")

    app._process_queue()

    app._toggle_monitor_off.assert_called_once_with()
    assert _rearmed(app)


# Q-7. 분기 매핑 — 각 이벤트가 기존과 같은 처리기로 가고 warn 은 재시작 라벨을 따름
def test_dispatch_mapping_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app.scheduled_action = ACTION_RESTART
    for e in ("cancel", "toggle_autostart", "toggle_monitor_off", "warn:5분"):
        app._queue.put(e)
    app._queue.put("unknown")
    app._queue.put(None)

    app._process_queue()

    app.cancel_shutdown.assert_called_once_with()
    app._toggle_autostart.assert_called_once_with()
    app._toggle_monitor_off.assert_called_once_with()
    app._show_warning_toast.assert_called_once_with(
        f"{ss.ACTION_LABEL_RESTART} 5분 전입니다."
    )
    app._execute_action.assert_not_called()
    app._exit_app.assert_not_called()
    assert _rearmed(app) and app._queue.empty()


# Q-8. 빈 큐에서도 재예약
def test_empty_queue_rearms(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _poll_app(monkeypatch)
    app._process_queue()
    app._tk_root.after.assert_called_once_with(
        QUEUE_POLL_INTERVAL_MS, app._process_queue
    )
