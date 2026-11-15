"""
shutdown_scheduler.py - 시스템 종료 예약 프로그램.

백그라운드 트레이 아이콘으로 실행되며, 글로벌 단축키로 종료 시간을 예약한다.
- Alt+Q: 종료 예약 다이얼로그 열기
- Alt+S: 예약 취소
"""

import ctypes
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from typing import Any, Callable, NamedTuple, Optional

import customtkinter as ctk
import keyboard
import pystray
import tkinter as tk
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from cfg.config import (
    ACTION_LABEL_MONITOR_OFF,
    ACTION_LABEL_RESTART,
    ACTION_LABEL_SHUTDOWN,
    ACTION_MONITOR_OFF,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    APP_NAME,
    CONFIRM_TOAST_DURATION_MS,
    CONTRAST_FIX_DEFAULT,
    CTK_APPEARANCE,
    CTK_COLOR_THEME,
    DIALOG_HEIGHT,
    DIALOG_TITLE,
    DIALOG_WIDTH,
    EXTEND_SECONDS,
    FOLLOW_SYSTEM_DEFAULT,
    MAX_SCHEDULE_SECONDS,
    MONITOR_LEAD_CHIPS,
    MONITOR_LEAD_MAX_MIN,
    MONITOR_OFF_BUDGET_SEC,
    MONITOR_OFF_DEFAULT,
    MONITOR_OFF_VCP_SEQUENCE,
    MONITOR_PRE_READ_WAIT_SEC,
    MONITOR_OFF_WARN_SEC,
    QUEUE_POLL_INTERVAL_MS,
    QUICK_ADD_BUTTONS,
    RESTORE_CLOCK_SKEW_SEC,
    RESTORE_MAX_SPAN_SEC,
    RESTORE_PAUSE_UNDER_SEC,
    RING_MAX_MINUTES,
    SCHEDULE_FILE_NAME,
    SCHEDULE_FILE_VERSION,
    SETTINGS_DIALOG_TITLE,
    SETTINGS_FILE_NAME,
    SETTINGS_KEY_CONTRAST_FIX,
    SETTINGS_KEY_DARK_THEME,
    SETTINGS_KEY_FOLLOW_SYSTEM,
    SETTINGS_KEY_LIGHT_THEME,
    SETTINGS_KEY_MONITOR_OFF,
    SETTINGS_KEY_THEME,
    SETTINGS_WINDOW_GAP,
    SHUTDOWN_LEVEL_LAST,
    SHUTDOWN_TRACE_FILE_NAME,
    SHUTDOWN_TRACE_MAX_BYTES,
    SYSTEM_THEME_POLL_MS,
    SYSTEM_THEME_REG_KEY,
    SYSTEM_THEME_REG_VALUE,
    TASK_NAME,
    TOOLTIP_UPDATE_INTERVAL_MS,
    TRAY_ICON_COLOR_ACTIVE,
    TRAY_ICON_COLOR_IDLE,
    TRAY_ICON_SIZE,
    TRAY_TOOLTIP_IDLE,
    UI_ACCENT_COLOR,
    UI_DANGER_COLOR,
    UI_FONT_FAMILIES_KO,
    UI_FONT_FAMILY,
    UI_FONT_FAMILY_FALLBACK,
    UI_FONT_NUMBER,
    UI_FONT_SYMBOL,
    UI_SUB_FG_COLOR,
    UI_SUCCESS_COLOR,
    UI_TOAST_BADGE_BG,
    UI_TOAST_BG,
    UI_TOAST_BORDER,
    UI_TOAST_CLOSE,
    UI_TOAST_FG,
    UI_TOAST_SUB_FG,
    UI_TOAST_WARN_ACCENT,
    UNIT_HOUR,
    UNIT_LABEL_HOUR,
    UNIT_LABEL_MINUTE,
    UNIT_LABEL_SECOND,
    UNIT_MINUTE,
    UNIT_SECOND,
    VCP_BRIGHTNESS,
    VCP_POWER_MODE,
    WHEEL_DELTA_UNIT,
)
from cfg.theme import (
    DEFAULT_DARK_THEME_ID,
    DEFAULT_LIGHT_THEME_ID,
    SHAPE,
    SWITCH_KNOB,
    THEMES,
    action_tokens,
    clean_theme_settings,
    get_theme,
    mix,
    resolve_theme_id,
    swatch_colors,
    theme_ids,
)

# customtkinter 전역 테마 설정
ctk.set_appearance_mode(CTK_APPEARANCE)
ctk.set_default_color_theme(CTK_COLOR_THEME)

# subprocess 호출 시 콘솔 창 숨김용 플래그 (Windows 전용)
_CREATE_NO_WINDOW = 0x08000000


def _resolve_ui_font(widget: tk.Misc) -> str:
    """사용 가능한 UI 폰트 패밀리 결정.

    Args:
        widget: tkinter 위젯

    Returns:
        사용할 폰트 패밀리명
    """
    try:
        from tkinter import font as tkfont

        families = set(tkfont.families(widget))
        if UI_FONT_FAMILY in families:
            return UI_FONT_FAMILY
        if UI_FONT_FAMILY_FALLBACK in families:
            return UI_FONT_FAMILY_FALLBACK
    except Exception:
        pass
    return UI_FONT_FAMILY_FALLBACK


def _bring_to_front(window: tk.Toplevel) -> None:
    """topmost 없이 창을 최상단으로 가져온다.

    Windows는 백그라운드 프로세스의 SetForegroundWindow를 차단한다.
    AttachThreadInput으로 현재 포그라운드 스레드에 입력 큐를 붙인 뒤
    SetForegroundWindow를 호출하면 이 제한을 우회할 수 있다.

    Args:
        window: 앞으로 가져올 Toplevel 창
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # hwnd 획득: wm_frame() → GetAncestor(GA_ROOT=2)
        try:
            frame_str = window.wm_frame()
            hwnd = int(frame_str, 16) if frame_str else window.winfo_id()
        except Exception:
            hwnd = window.winfo_id()
        root_hwnd = user32.GetAncestor(hwnd, 2)
        hwnd = root_hwnd if root_hwnd else hwnd

        if not hwnd:
            return

        # 현재 포그라운드 창의 스레드 ID
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        my_tid = kernel32.GetCurrentThreadId()

        # 포그라운드 스레드에 입력 큐 부착 → SetForegroundWindow 차단 우회
        attached = False
        if fg_tid and fg_tid != my_tid:
            attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE=9
        user32.SetForegroundWindow(hwnd)

        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)

    except Exception:
        pass
    try:
        window.focus_force()
    except Exception:
        pass


def _make_power_icon_photo(root: tk.Misc) -> Optional["ImageTk.PhotoImage"]:
    """전원 버튼 모양 32×32 PhotoImage 생성 (타이틀바 아이콘용).

    Args:
        root: tkinter 위젯

    Returns:
        PhotoImage 객체. Pillow 미설치 시 None.
    """
    if not PIL_AVAILABLE:
        return None
    try:
        from PIL import ImageTk, Image as PILImage, ImageDraw as PILDraw

        size = 32
        img = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = PILDraw.Draw(img)
        m = 2
        r, g, b = 0x4A, 0x90, 0xD9
        draw.ellipse([m, m, size - m, size - m], fill=(r, g, b, 255))
        sym = (255, 255, 255, 255)
        im = m + max(5, size // 5)
        arc_w = max(2, size // 10)
        draw.arc(
            [im, im, size - im, size - im], start=300, end=240, fill=sym, width=arc_w
        )
        cx = size // 2
        lt = im - max(1, size // 16)
        lb = (im + size - im) // 2 - max(1, size // 12)
        draw.line([(cx, lt), (cx, lb)], fill=sym, width=arc_w)
        return ImageTk.PhotoImage(img, master=root)
    except Exception:
        return None


def _get_executable_path() -> str:
    """현재 실행 파일의 절대 경로를 반환.

    Returns:
        실행 파일 절대 경로
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def is_autostart_enabled() -> bool:
    """작업 스케줄러에 자동 시작 작업이 등록되어 있는지 확인.

    Returns:
        등록되어 있으면 True
    """
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


def enable_autostart() -> tuple[bool, str]:
    """작업 스케줄러에 로그온 트리거 + 최고 권한 작업을 등록.

    Returns:
        (성공 여부, 메시지)
    """
    exe_path = _get_executable_path()
    if not os.path.exists(exe_path):
        return False, f"실행 파일을 찾을 수 없습니다:\n{exe_path}"

    try:
        result = subprocess.run(
            [
                "schtasks",
                "/create",
                "/tn",
                TASK_NAME,
                "/tr",
                f'"{exe_path}"',
                "/sc",
                "onlogon",
                "/rl",
                "highest",
                "/f",
            ],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, "자동 시작이 활성화되었습니다."
        return False, f"작업 등록 실패:\n{result.stderr or result.stdout}"
    except Exception as e:
        return False, f"작업 등록 중 오류 발생:\n{e}"


def disable_autostart() -> tuple[bool, str]:
    """작업 스케줄러에서 자동 시작 작업을 삭제.

    Returns:
        (성공 여부, 메시지)
    """
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, "자동 시작이 비활성화되었습니다."
        return False, f"작업 삭제 실패:\n{result.stderr or result.stdout}"
    except Exception as e:
        return False, f"작업 삭제 중 오류 발생:\n{e}"


def _app_data_dir() -> str:
    """앱 데이터 폴더(%APPDATA%\\<APP_NAME>) 경로를 반환.

    Returns:
        폴더 절대 경로. APPDATA 환경 변수가 없으면 홈 폴더 아래 경로.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def _settings_path() -> str:
    """사용자 설정 파일 경로를 반환.

    Returns:
        settings.json 절대 경로
    """
    return os.path.join(_app_data_dir(), SETTINGS_FILE_NAME)


def load_settings() -> dict[str, Any]:
    """사용자 설정을 읽는다.

    Returns:
        설정 dict. 파일이 없거나 JSON 이 깨졌으면 기본값.
    """
    settings: dict[str, Any] = {SETTINGS_KEY_MONITOR_OFF: MONITOR_OFF_DEFAULT}
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        value = data.get(SETTINGS_KEY_MONITOR_OFF)
        if isinstance(value, bool):
            settings[SETTINGS_KEY_MONITOR_OFF] = value
        settings.update(clean_theme_settings(data))
    return settings


def save_settings(settings: dict[str, Any]) -> bool:
    """사용자 설정을 저장한다.

    Args:
        settings: 저장할 설정 dict

    Returns:
        저장 성공 여부
    """
    path = _settings_path()
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        # 쓰는 도중 끊겨도 기존 파일이 깨지지 않게 교체 방식으로 저장함
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def _schedule_path() -> str:
    """예약 복원 파일 경로를 반환.

    Returns:
        schedule.json 절대 경로
    """
    return os.path.join(_app_data_dir(), SCHEDULE_FILE_NAME)


def _write_schedule_file(data: dict[str, Any]) -> bool:
    """예약 복원 파일을 tmp 에 쓴 뒤 os.replace 로 바꾼다.

    Args:
        data: 저장할 내용

    Returns:
        저장 성공 여부
    """
    path = _schedule_path()
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def _delete_schedule_file() -> None:
    """예약 복원 파일을 지운다. 없거나 실패해도 무시한다."""
    try:
        os.remove(_schedule_path())
    except OSError:
        pass


def _read_schedule_file() -> tuple[bool, Optional[dict[str, Any]]]:
    """예약 복원 파일을 읽는다.

    Returns:
        (파일 있음, 내용). JSON 이 깨졌으면 (True, None), 없으면 (False, None).
    """
    try:
        with open(_schedule_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, None
    except (OSError, ValueError):
        return True, None
    return True, data if isinstance(data, dict) else None


def _clear_schedule_part(part: str) -> None:
    """복원 파일에서 "shutdown" 또는 "solo" 부분만 지우고, 비면 파일을 지운다.

    Args:
        part: "shutdown" 또는 "solo"
    """
    exists, data = _read_schedule_file()
    if not exists:
        return
    if data is None:
        _delete_schedule_file()
        return
    data[part] = None
    if data.get("shutdown") is None and data.get("solo") is None:
        _delete_schedule_file()
        return
    data["saved_at"] = time.time()
    _write_schedule_file(data)


def _is_number(value: object) -> bool:
    """bool 을 뺀 유한한 int·float 인지.

    json 은 NaN·Infinity 를 읽어 들이므로 유한한지도 본다.

    Args:
        value: 검사할 값

    Returns:
        유한한 숫자면 True
    """
    return type(value) in (int, float) and math.isfinite(value)


def _in_span(value: object, low: float, high: float) -> bool:
    """유한한 숫자이고 low ~ high 안인지.

    Args:
        value: 검사할 값
        low: 하한
        high: 상한

    Returns:
        범위 안이면 True
    """
    return _is_number(value) and low <= value <= high


# 본체가 [] 로 읽는 키 — 하나라도 없으면 깨진 파일로 봄
_SCHEDULE_KEYS: frozenset[str] = frozenset({"version", "saved_at", "shutdown", "solo"})
_SHUTDOWN_KEYS: frozenset[str] = frozenset(
    {"action", "target", "paused_remaining", "total", "monitor_lead_sec", "linked_done"}
)


def _valid_schedule(data: object) -> bool:
    """복원 파일 내용의 형식과 버전을 확인한다.

    시각은 지금 ± RESTORE_MAX_SPAN_SEC(999시간), 초는 0 ~ RESTORE_MAX_SPAN_SEC 안이어야 한다.
    그 밖의 값은 fromtimestamp·timedelta 에서 예외가 나므로 깨진 파일로 본다.

    Args:
        data: json.load 결과

    Returns:
        복원에 쓸 수 있으면 True
    """
    now = time.time()
    t_low, t_high = now - RESTORE_MAX_SPAN_SEC, now + RESTORE_MAX_SPAN_SEC
    if not isinstance(data, dict) or not _SCHEDULE_KEYS <= set(data):
        return False
    if data.get("version") != SCHEDULE_FILE_VERSION:
        return False
    if not _in_span(data.get("saved_at"), t_low, t_high):
        return False
    sd = data.get("shutdown")
    if sd is not None:
        if not isinstance(sd, dict) or not _SHUTDOWN_KEYS <= set(sd):
            return False
        if sd.get("action") not in (ACTION_SHUTDOWN, ACTION_RESTART):
            return False
        target, paused = sd.get("target"), sd.get("paused_remaining")
        # 진행 중이면 target, 일시정지면 paused_remaining 하나만 있어야 함
        if (target is None) == (paused is None):
            return False
        if target is not None and not _in_span(target, t_low, t_high):
            return False
        if paused is not None and not _in_span(paused, 0, RESTORE_MAX_SPAN_SEC):
            return False
        if not _in_span(sd.get("total"), 0, RESTORE_MAX_SPAN_SEC):
            return False
        lead = sd.get("monitor_lead_sec")
        if lead is not None and (
            type(lead) is not int or not 0 <= lead <= RESTORE_MAX_SPAN_SEC
        ):
            return False
        if not isinstance(sd.get("linked_done"), bool):
            return False
    solo = data.get("solo")
    if solo is not None:
        if not isinstance(solo, dict) or not _in_span(
            solo.get("target"), t_low, t_high
        ):
            return False
    return True


def _trace_log(message: str) -> None:
    """종료 추적 로그에 밀리초 시각과 함께 한 줄을 덧붙인다. 실패는 무시한다.

    Args:
        message: 기록할 내용
    """
    try:
        path = os.path.join(_app_data_dir(), SHUTDOWN_TRACE_FILE_NAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "a"
        if os.path.exists(path) and os.path.getsize(path) > SHUTDOWN_TRACE_MAX_BYTES:
            mode = "w"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(path, mode, encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
            f.flush()
            # 종료 도중 프로세스가 끊겨도 남도록 줄마다 디스크에 씀
            os.fsync(f.fileno())
    except Exception:
        pass


# 모니터가 꺼지며 OS 가 물리 모니터 핸들을 지울 때 나는 오류임 (다른 모니터 핸들에도 남)
_ERROR_INVALID_PHYSICAL_MONITOR_HANDLE = 0xC026258D

# (물리 모니터 개수, PHYSICAL_MONITOR 배열) — DestroyPhysicalMonitors 호출 단위
_PhysicalMonitorGroup = tuple[int, Any]


class _PHYSICAL_MONITOR(ctypes.Structure):
    """dxva2 PHYSICAL_MONITOR 구조체."""

    _fields_ = [
        ("hPhysicalMonitor", wintypes.HANDLE),
        ("szPhysicalMonitorDescription", wintypes.WCHAR * 128),
    ]


class _DdcApi(NamedTuple):
    """argtypes·restype 을 지정한 user32·dxva2 함수 묶음."""

    user32: Any
    dxva2: Any
    enum_proc_type: Any


def _load_ddc_api() -> Optional[_DdcApi]:
    """user32·dxva2 를 불러 함수 시그니처를 지정한다.

    Returns:
        _DdcApi. Windows 가 아니거나 로드에 실패하면 None.
    """
    if not hasattr(ctypes, "windll"):
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )
        lp_monitor = ctypes.POINTER(_PHYSICAL_MONITOR)

        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            enum_proc_type,
            wintypes.LPARAM,
        ]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            lp_monitor,
        ]
        dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        dxva2.SetVCPFeature.argtypes = [
            wintypes.HANDLE,
            wintypes.BYTE,
            wintypes.DWORD,
        ]
        dxva2.SetVCPFeature.restype = wintypes.BOOL
        dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            wintypes.HANDLE,
            wintypes.BYTE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
        dxva2.DestroyPhysicalMonitors.argtypes = [wintypes.DWORD, lp_monitor]
        dxva2.DestroyPhysicalMonitors.restype = wintypes.BOOL
        return _DdcApi(user32, dxva2, enum_proc_type)
    except Exception:
        return None


def _last_error() -> int:
    """ctypes 에 저장된 마지막 Win32 오류 코드를 부호 없는 32비트로 반환.

    Returns:
        오류 코드. 조회할 수 없으면 0.
    """
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    # ctypes 는 오류 코드를 부호 있는 int 로 줘서 0xC026258D 가 음수로 나옴
    return getter() & 0xFFFFFFFF


def _acquire_physical_monitors() -> list[_PhysicalMonitorGroup]:
    """연결된 모든 물리 모니터 핸들을 얻는다.

    Returns:
        물리 모니터 그룹 목록. 다 쓴 뒤 _release_physical_monitors 로 해제한다.
        Windows 가 아니면 빈 목록, 도중에 실패하면 그때까지 얻은 만큼.
    """
    api = _load_ddc_api()
    if api is None:
        return []
    groups: list[_PhysicalMonitorGroup] = []
    try:
        hmonitors: list[int] = []

        def _on_monitor(
            hmonitor: Optional[int], _hdc: Any, _rect: Any, _lp: Any
        ) -> bool:
            if hmonitor:
                hmonitors.append(hmonitor)
            return True

        # 콜백 객체를 변수로 잡아 둬야 열거 도중 GC 로 사라지지 않음
        callback = api.enum_proc_type(_on_monitor)
        api.user32.EnumDisplayMonitors(None, None, callback, 0)
        for hmonitor in hmonitors:
            count = wintypes.DWORD(0)
            if not api.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                hmonitor, ctypes.byref(count)
            ):
                continue
            if count.value == 0:
                continue
            array = (_PHYSICAL_MONITOR * count.value)()
            if api.dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, array):
                groups.append((count.value, array))
    except Exception:
        pass
    return groups


def _monitor_handles(groups: list[_PhysicalMonitorGroup]) -> list[Any]:
    """그룹 목록을 물리 모니터 핸들 목록으로 편다.

    Args:
        groups: _acquire_physical_monitors 반환값

    Returns:
        물리 모니터 핸들 목록
    """
    return [array[i].hPhysicalMonitor for count, array in groups for i in range(count)]


def _set_power_mode(api: _DdcApi, handle: Any, value: int) -> tuple[bool, int]:
    """한 모니터에 VCP 0xD6 값을 쓴다. 예외는 삼킨다.

    Args:
        api: _load_ddc_api 반환값
        handle: 물리 모니터 핸들
        value: 쓸 값

    Returns:
        (성공 여부, 오류 코드). 성공이면 0, 예외면 -1.
    """
    try:
        if api.dxva2.SetVCPFeature(handle, VCP_POWER_MODE, value):
            return True, 0
        return False, _last_error()
    except Exception as e:
        _trace_log(f"SetVCPFeature exception {e!r}")
        return False, -1


def _read_monitors_for_trace(
    api: _DdcApi, handles: list[Any], stop: threading.Event
) -> None:
    """모니터별 VCP 0x10 읽기 결과를 추적 로그에 남긴다. 예외는 삼킨다.

    Args:
        api: _load_ddc_api 반환값
        handles: 물리 모니터 핸들 목록
        stop: 설정되면 다음 모니터를 읽지 않음
    """
    for index, handle in enumerate(handles):
        if stop.is_set():
            return
        try:
            kind, current, maximum = (
                wintypes.DWORD(),
                wintypes.DWORD(),
                wintypes.DWORD(),
            )
            ok = bool(
                api.dxva2.GetVCPFeatureAndVCPFeatureReply(
                    handle,
                    VCP_BRIGHTNESS,
                    ctypes.byref(kind),
                    ctypes.byref(current),
                    ctypes.byref(maximum),
                )
            )
            # get_last_error 는 스레드별 값이라 호출한 이 스레드에서 읽음
            code = 0 if ok else _last_error()
            _trace_log(
                f"pre monitor={index} read ok={ok} err=0x{code & 0xFFFFFFFF:08X}"
            )
        except Exception as e:
            _trace_log(f"pre monitor={index} read exception {e!r}")


def _trace_monitor_state(groups: list[_PhysicalMonitorGroup]) -> None:
    """종료 명령 전 모니터 응답을 기록한다. 최대 대기 뒤에는 기다리지 않는다.

    Args:
        groups: _acquire_physical_monitors 반환값
    """
    try:
        api = _load_ddc_api()
        if api is None:
            return
        stop = threading.Event()
        # 드라이버 호출은 중간에 끊을 수 없어 별도 스레드로 대기 시간을 제한함
        worker = threading.Thread(
            target=_read_monitors_for_trace,
            args=(api, _monitor_handles(groups), stop),
            daemon=True,
        )
        worker.start()
        worker.join(MONITOR_PRE_READ_WAIT_SEC)
        if worker.is_alive():
            stop.set()
            _trace_log("pre read timeout")
    except Exception as e:
        _trace_log(f"pre read error {e!r}")


def _write_power_rounds(
    api: _DdcApi, handles: list[Any], deadline: float, phase: str
) -> tuple[int, bool]:
    """값 순서대로 라운드를 돌며 모든 모니터에 전원 값을 쓴다.

    Args:
        api: _load_ddc_api 반환값
        handles: 물리 모니터 핸들 목록
        deadline: time.monotonic 기준 마감 시각
        phase: 추적 로그 구분용 이름

    Returns:
        (성공한 쓰기 수, 2라운드 이후 핸들 소실 오류가 났는지)
    """
    written = 0
    handle_lost = False
    for round_no, value in enumerate(MONITOR_OFF_VCP_SEQUENCE, start=1):
        for index, handle in enumerate(handles):
            if time.monotonic() >= deadline:
                _trace_log(f"{phase} budget exceeded round={round_no} monitor={index}")
                return written, handle_lost
            ok, code = _set_power_mode(api, handle, value)
            _trace_log(
                f"{phase} round={round_no} monitor={index} value={value} "
                f"ok={ok} err=0x{code & 0xFFFFFFFF:08X}"
            )
            if ok:
                written += 1
            elif round_no >= 2 and code == _ERROR_INVALID_PHYSICAL_MONITOR_HANDLE:
                handle_lost = True
    return written, handle_lost


def _power_off_monitors(groups: list[_PhysicalMonitorGroup]) -> int:
    """DDC/CI 로 모든 모니터 전원을 끈다. 예외를 밖으로 내지 않는다.

    MONITOR_OFF_VCP_SEQUENCE 순서로 라운드마다 모든 모니터에 쓴다.
    2라운드 이후 핸들 소실 오류가 나면 다시 열거해 1회만 재시도한다.
    MONITOR_OFF_BUDGET_SEC 를 넘기면 남은 쓰기를 멈춘다.

    Args:
        groups: _acquire_physical_monitors 반환값

    Returns:
        성공한 쓰기 수. Windows 가 아니거나 대상이 없으면 0.
    """
    if not groups:
        return 0
    api = _load_ddc_api()
    if api is None:
        return 0
    deadline = time.monotonic() + MONITOR_OFF_BUDGET_SEC
    written = 0
    try:
        written, handle_lost = _write_power_rounds(
            api, _monitor_handles(groups), deadline, "first"
        )
        if not handle_lost:
            return written
        if time.monotonic() >= deadline:
            _trace_log("retry skipped: budget exceeded")
            return written
        retry_groups = _acquire_physical_monitors()
        try:
            retry_handles = _monitor_handles(retry_groups)
            _trace_log(f"retry re-enumerated monitors={len(retry_handles)}")
            retry_written, _ = _write_power_rounds(
                api, retry_handles, deadline, "retry"
            )
            written += retry_written
        finally:
            _release_physical_monitors(retry_groups)
    except Exception as e:
        _trace_log(f"power off error {e!r}")
    return written


def _release_physical_monitors(groups: list[_PhysicalMonitorGroup]) -> None:
    """_acquire_physical_monitors 로 얻은 핸들을 해제한다. 예외는 삼킨다.

    Args:
        groups: _acquire_physical_monitors 반환값
    """
    if not groups:
        return
    api = _load_ddc_api()
    if api is None:
        return
    for count, array in groups:
        try:
            api.dxva2.DestroyPhysicalMonitors(count, array)
        except Exception:
            pass


def _set_shutdown_level_last() -> None:
    """종료 순서에서 앱용 최후 구간에 서도록 요청한다. 실패는 무시한다."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetProcessShutdownParameters(SHUTDOWN_LEVEL_LAST, 0)
    except Exception:
        pass


def _format_duration(total_seconds: int) -> str:
    """초를 'X시간 Y분 Z초' 문자열로 바꾼다.

    Args:
        total_seconds: 초

    Returns:
        환산 문자열. 0 이하면 "0초".
    """
    hours, rem = divmod(max(int(total_seconds), 0), 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    if secs > 0:
        parts.append(f"{secs}초")
    return " ".join(parts) if parts else "0초"


def _format_hms(total_seconds: float) -> str:
    """초를 HH:MM:SS 로 바꾼다. 음수는 0 으로 본다.

    Args:
        total_seconds: 초

    Returns:
        HH:MM:SS 문자열
    """
    hours, rem = divmod(max(int(total_seconds), 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class _MonitorOffTimer:
    """모니터 끄기 Timer 와 1분 전 경고 Timer 묶음. UI 를 다루지 않는다.

    발화하면 큐에 "monitor_due:<kind>:<gen>" / "monitor_warn:<kind>:<gen>" 만 넣는다.
    arm·cancel 마다 gen 을 올려, 이미 큐에 들어간 옛 이벤트를 처리 단계에서 버린다.

    Attributes:
        kind: "linked"(종료 예약에 딸림) 또는 "solo"(모니터 단독 예약)
        gen: 세대 번호
        due: 끄기 예정 시각. 걸려 있지 않으면 None.
    """

    def __init__(self, kind: str, event_queue: queue.Queue) -> None:
        """초기화.

        Args:
            kind: "linked" 또는 "solo"
            event_queue: 발화 이벤트를 넣을 큐
        """
        self.kind = kind
        self.gen = 0
        self.due: Optional[datetime] = None
        self._queue = event_queue
        self._timers: list[threading.Timer] = []

    @property
    def armed(self) -> bool:
        """끄기 예약이 걸려 있는지."""
        return self.due is not None

    def is_current(self, gen: int) -> bool:
        """이벤트의 세대가 지금 세대인지.

        Args:
            gen: 이벤트에 붙은 세대 번호

        Returns:
            지금 세대면 True
        """
        return gen == self.gen

    def arm(self, delay_sec: float) -> None:
        """delay_sec 뒤 끄기를 건다. 기존 예약은 대체한다.

        Args:
            delay_sec: 끌 때까지 초. 0 이하면 바로 끄기 이벤트를 넣는다.
        """
        self.cancel()
        self.due = datetime.now() + timedelta(seconds=max(delay_sec, 0))
        due_event = f"monitor_due:{self.kind}:{self.gen}"
        if delay_sec <= 0:
            self._queue.put(due_event)
            return
        self._start(delay_sec, due_event)
        if delay_sec > MONITOR_OFF_WARN_SEC:
            self._start(
                delay_sec - MONITOR_OFF_WARN_SEC,
                f"monitor_warn:{self.kind}:{self.gen}",
            )

    def cancel(self) -> None:
        """걸린 Timer 를 모두 취소하고 세대를 올린다."""
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        self.gen += 1
        self.due = None

    def _start(self, delay_sec: float, event: str) -> None:
        """이벤트 하나를 넣는 Timer 를 시작한다.

        Args:
            delay_sec: 지연 초
            event: 발화 때 큐에 넣을 이벤트
        """
        # 기본 인자로 묶어 종료 실행 Timer(기본 인자 없는 람다)와 구분되게 함
        timer = threading.Timer(delay_sec, lambda e=event: self._queue.put(e))
        timer.daemon = True
        timer.start()
        self._timers.append(timer)


_ACTION_LABELS: dict[str, str] = {
    ACTION_SHUTDOWN: ACTION_LABEL_SHUTDOWN,
    ACTION_RESTART: ACTION_LABEL_RESTART,
    ACTION_MONITOR_OFF: ACTION_LABEL_MONITOR_OFF,
}
_RING_FULL_SEC: int = RING_MAX_MINUTES * 60


def _capped_add(remaining: float, add_seconds: int) -> int:
    """남은 초에 더할 초를 MAX_SCHEDULE_SECONDS 를 넘지 않게 줄인다.

    Args:
        remaining: 지금 남은 초
        add_seconds: 더하려는 초

    Returns:
        실제로 더할 초 (상한에 걸리면 줄인 값, 소수는 버림)
    """
    room = MAX_SCHEDULE_SECONDS - remaining
    return add_seconds if add_seconds <= room else max(int(room), 0)


def _action_label(action: str) -> str:
    """동작 상수를 화면 표시 이름으로 바꾼다.

    Args:
        action: ACTION_SHUTDOWN / ACTION_RESTART / ACTION_MONITOR_OFF

    Returns:
        "종료" / "재시작" / "모니터 끄기". 모르는 값이면 "종료".
    """
    return _ACTION_LABELS.get(action, ACTION_LABEL_SHUTDOWN)


def _dial_text(seconds: int, action: str) -> tuple[str, str, str]:
    """다이얼 가운데 글자(라벨, 큰 숫자, 단위)를 만든다 (설계안 p05 표시 규칙).

    Args:
        seconds: 설정 시간 또는 남은 시간(초). 음수는 0 으로 본다.
        action: 동작 상수

    Returns:
        (라벨, 큰 숫자, 단위). 0초면 단위 자리에 "누르면 바로 {동작}".
    """
    seconds = max(int(seconds), 0)
    label = f"{_action_label(action)}까지"
    if seconds == 0:
        return label, "즉시", f"누르면 바로 {_action_label(action)}"
    if seconds < 60:
        return label, str(seconds), UNIT_LABEL_SECOND
    if seconds < 3600:
        minutes, secs = divmod(seconds, 60)
        if secs == 0:
            return label, str(minutes), UNIT_LABEL_MINUTE
        return label, f"{minutes}:{secs:02d}", "분:초"
    hours, rem = divmod(seconds, 3600)
    return label, f"{hours}:{rem // 60:02d}", "시간:분"


def _confirm_label(seconds: int, action: str, now: datetime) -> str:
    """예약 버튼 글자 "HH:MM {동작} 예약" 을 만든다.

    Args:
        seconds: 설정 시간(초)
        action: 동작 상수
        now: 기준 시각

    Returns:
        버튼 글자. 0초면 시각 없이 "{동작} 예약".
    """
    if seconds <= 0:
        return f"{_action_label(action)} 예약"
    at = now + timedelta(seconds=seconds)
    return f"{at:%H:%M} {_action_label(action)} 예약"


def _ring_progress(seconds: float) -> float:
    """링 진행률 min(s, 7200) / 7200.

    Args:
        seconds: 설정 시간 또는 남은 시간(초)

    Returns:
        0.0 ~ 1.0
    """
    return min(max(seconds, 0), _RING_FULL_SEC) / _RING_FULL_SEC


def _monitor_segment(
    seconds: float, lead_sec: Optional[int]
) -> Optional[tuple[float, float]]:
    """링 위 모니터 구간 [max(s - lead, 0), s] 을 0~7200 으로 자른 비율을 구한다.

    Args:
        seconds: 종료까지 초
        lead_sec: 종료 몇 초 전에 끌지. None·0 이면 구간 없음.

    Returns:
        (시작 비율, 끝 비율). 그릴 구간이 없으면 None.
    """
    if lead_sec is None or lead_sec <= 0 or seconds <= 0:
        return None
    start = min(max(seconds - lead_sec, 0), _RING_FULL_SEC)
    end = min(seconds, _RING_FULL_SEC)
    if end <= start:
        return None
    return start / _RING_FULL_SEC, end / _RING_FULL_SEC


def _angle_to_minutes(dx: float, dy: float) -> int:
    """링 중심 기준 좌표를 분으로 바꾼다. 12시가 0, 시계 방향으로 늘어난다.

    Args:
        dx: 중심에서 오른쪽으로 떨어진 거리
        dy: 중심에서 아래쪽으로 떨어진 거리 (화면 좌표)

    Returns:
        0 ~ RING_MAX_MINUTES 분 (1분 단위)
    """
    theta = math.atan2(dx, -dy)
    if theta < 0:
        theta += 2 * math.pi
    return round(theta / (2 * math.pi) * RING_MAX_MINUTES)


def _clamp_wrap(prev_min: int, new_min: int) -> int:
    """드래그가 12시를 넘어갈 때 값이 0↔120 으로 튀지 않게 고정한다.

    Args:
        prev_min: 직전 분
        new_min: 이번 각도로 구한 분

    Returns:
        고정을 적용한 분
    """
    high = RING_MAX_MINUTES * 3 // 4
    low = RING_MAX_MINUTES // 4
    if prev_min > high and new_min < low:
        return RING_MAX_MINUTES
    if prev_min < low and new_min > high:
        return 0
    return new_min


def _wheel_steps(acc: int, delta: int) -> tuple[int, int]:
    """휠 delta 를 120 단위로 모아 칸 수로 바꾼다.

    Args:
        acc: 지금까지 남은 delta
        delta: 이번 이벤트 delta (위로 굴리면 양수)

    Returns:
        (움직일 칸 수, 남은 delta)
    """
    acc += delta
    steps = int(acc / WHEEL_DELTA_UNIT)
    return steps, acc - steps * WHEEL_DELTA_UNIT


def _in_ring_band(dx: float, dy: float, radius: float, stroke: float) -> bool:
    """좌표가 링 띠 안쪽인지 확인한다 (잡기 쉽게 띠 두께만큼 여유를 둠).

    Args:
        dx: 중심 기준 x
        dy: 중심 기준 y
        radius: 링 반지름
        stroke: 링 두께

    Returns:
        띠 안이면 True
    """
    distance = math.hypot(dx, dy)
    return radius - stroke <= distance <= radius + stroke


def _pick_font_family(widget: tk.Misc, candidates: tuple[str, ...]) -> str:
    """설치된 글꼴 중 후보 순서대로 첫 번째를 고른다.

    Args:
        widget: tkinter 위젯
        candidates: 글꼴 이름 후보

    Returns:
        고른 글꼴 이름. 하나도 없으면 첫 후보 (tk 가 대체 글꼴로 그림).
    """
    try:
        families = set(tkfont.families(widget))
    except Exception:
        return candidates[0]
    for name in candidates:
        if name in families:
            return name
    return candidates[0]


def _read_system_light() -> Optional[bool]:
    """Windows 앱 테마가 라이트인지 읽는다.

    Returns:
        라이트면 True, 다크면 False, 읽을 수 없으면 None
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SYSTEM_THEME_REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, SYSTEM_THEME_REG_VALUE)
    except (ImportError, OSError):
        return None
    return bool(value)


def _make_icon_image(kind: str, color: str, px: int) -> Optional["Image.Image"]:
    """헤더·카드 아이콘(전원/재시작/모니터)을 PIL 로 그린다.

    글리프 글꼴 유무와 상관없이 같은 모양이 나오게 4배로 그린 뒤 줄인다.

    Args:
        kind: "power" / "restart" / "monitor"
        color: '#rrggbb' 선 색
        px: 결과 크기 (정사각형)

    Returns:
        RGBA 이미지. PIL 이 없으면 None.
    """
    if not PIL_AVAILABLE:
        return None
    ss = 4
    size = px * ss
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    fill = rgb + (255,)
    width = max(2, round(size * 0.11))
    pad = round(size * 0.14)
    box = [pad, pad, size - pad, size - pad]
    if kind == "monitor":
        top, bottom = round(size * 0.18), round(size * 0.66)
        draw.rounded_rectangle(
            [pad - ss, top, size - pad + ss, bottom],
            radius=round(size * 0.08),
            outline=fill,
            width=width,
        )
        cx = size // 2
        draw.line([(cx, bottom), (cx, round(size * 0.80))], fill=fill, width=width)
        draw.line(
            [
                (round(size * 0.32), round(size * 0.82)),
                (round(size * 0.68), round(size * 0.82)),
            ],
            fill=fill,
            width=width,
        )
    elif kind == "restart":
        draw.arc(box, start=-30, end=270, fill=fill, width=width)
        # 화살촉은 호가 끝나는 12시 쪽에 붙임
        cx, top = size // 2, pad + width // 2
        head = round(size * 0.16)
        draw.polygon(
            [
                (cx + head, top),
                (cx - head // 3, top - head),
                (cx - head // 3, top + head),
            ],
            fill=fill,
        )
    else:
        draw.arc(box, start=300, end=240, fill=fill, width=width)
        cx = size // 2
        draw.line([(cx, pad - ss), (cx, size // 2)], fill=fill, width=width)
    return img.resize((px, px), Image.LANCZOS)


def _beside_position(
    anchor_x: int,
    anchor_y: int,
    anchor_width: int,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    gap: int = SETTINGS_WINDOW_GAP,
) -> Optional[tuple[int, int]]:
    """기준 창 옆에 붙일 좌표를 구한다. 오른쪽을 먼저 보고 안 되면 왼쪽을 본다.

    판단은 주 모니터 너비 기준임. 왼쪽 보조 모니터의 음수 좌표는 그대로 통과함.

    Args:
        anchor_x: 기준 창 왼쪽 x
        anchor_y: 기준 창 위쪽 y
        anchor_width: 기준 창 너비
        width: 놓을 창 너비
        height: 놓을 창 높이
        screen_width: 화면 너비
        screen_height: 화면 높이
        gap: 두 창 사이 간격

    Returns:
        (x, y). 양쪽 다 화면을 벗어나면 None.
    """
    y = anchor_y
    if y + height > screen_height:
        y = max(0, screen_height - height)
    right = anchor_x + anchor_width + gap
    if right + width <= screen_width:
        return right, y
    left = anchor_x - gap - width
    if left >= 0:
        return left, y
    return None


def _make_swatch_image(
    colors: tuple[str, str, str, str], border: str, width: int, height: int
) -> Optional["Image.Image"]:
    """테마 썸네일 스와치(4칸 + 테두리)를 그린다.

    Args:
        colors: 왼쪽부터 칠할 4색
        border: 테두리 색
        width: 너비 px
        height: 높이 px

    Returns:
        PIL 이미지. PIL 이 없으면 None.
    """
    if not PIL_AVAILABLE:
        return None
    img = Image.new("RGB", (width, height), colors[0])
    draw = ImageDraw.Draw(img)
    for index, color in enumerate(colors):
        draw.rectangle(
            (index * width // 4, 0, (index + 1) * width // 4, height), fill=color
        )
    # 2배로 그려 CTkImage 가 줄이므로 2px 가 화면의 1px 임
    draw.rectangle((0, 0, width - 1, height - 1), outline=border, width=2)
    return img


def _apply_caption_color(window: tk.Misc, color: str) -> None:
    """Windows 11 타이틀바 배경을 color 로 칠한다 (DWMWA_CAPTION_COLOR=35). 실패는 무시.

    Args:
        window: CTkToplevel
        color: '#rrggbb'
    """
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
        value = ctypes.c_int(r | (g << 8) | (b << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


class _ViewModel(NamedTuple):
    """예약됨 화면에 그릴 내용.

    Attributes:
        target: 버튼(취소·연장)이 적용될 예약 — "shutdown" / "solo" / None(예약 없음)
        accent_action: 강조색을 가져올 동작
        label: 링 가운데 라벨
        remaining: 남은 초 (올림)
        paused: 일시정지 중인지
        show_pause: 일시정지 버튼을 보일지
        solo_extra: 종료 예약과 단독 모니터 예약이 함께 있는지
        fixed_at: 실행 시각. 일시정지 중이면 None.
    """

    target: Optional[str]
    accent_action: str
    label: str
    remaining: int
    paused: bool
    show_pause: bool
    solo_extra: bool
    fixed_at: Optional[datetime]


def _view_model(scheduler: Any, now: Optional[datetime] = None) -> _ViewModel:
    """예약 상태로 예약됨 화면의 표시 내용을 정한다 (UI 를 다루지 않음).

    종료 예약이 있으면 링·버튼은 종료 기준, 단독 모니터 예약만 있으면 모니터 기준이다.

    Args:
        scheduler: ShutdownScheduler (None 이면 예약 없음)
        now: 기준 시각. None 이면 지금.

    Returns:
        _ViewModel
    """
    now = now or datetime.now()
    empty = _ViewModel(None, ACTION_SHUTDOWN, "", 0, False, False, False, None)
    if scheduler is None:
        return empty
    solo_due = scheduler._solo_monitor.due
    if scheduler.is_active:
        action = scheduler.scheduled_action
        paused = bool(scheduler.is_paused)
        if paused:
            remaining = float(scheduler._pause_remaining)
            at = None
        elif scheduler.scheduled_time is not None:
            remaining = (scheduler.scheduled_time - now).total_seconds()
            at = scheduler.scheduled_time
        else:
            remaining, at = 0.0, None
        if remaining > 0:
            label = "일시정지" if paused else f"{_action_label(action)}까지 남음"
            return _ViewModel(
                "shutdown",
                action,
                label,
                math.ceil(remaining),
                paused,
                True,
                solo_due is not None,
                at,
            )
    if solo_due is not None:
        remaining = max((solo_due - now).total_seconds(), 0.0)
        return _ViewModel(
            "solo",
            ACTION_MONITOR_OFF,
            f"{ACTION_LABEL_MONITOR_OFF}까지 남음",
            math.ceil(remaining),
            False,
            False,
            False,
            solo_due,
        )
    return empty


def _render_ring_image(
    px: int,
    scale: float,
    progress: float,
    segment: Optional[tuple[float, float]],
    colors: dict[str, str],
) -> "Image.Image":
    """링 이미지를 3배 크기로 그린 뒤 LANCZOS 로 줄인다.

    Args:
        px: 결과 크기 (정사각형, 배율 적용된 px)
        scale: 위젯 배율
        progress: 진행률 0.0 ~ 1.0
        segment: 모니터 구간 비율. 없으면 None.
        colors: bg·track·accent·monitor·handle·handle_ring 색

    Returns:
        RGB 이미지
    """
    ss = 3
    ring = SHAPE["ring"]
    size = px * ss
    c = size / 2
    r = ring["radius"] * scale * ss
    w = ring["stroke"] * scale * ss
    img = Image.new("RGB", (size, size), colors["bg"])
    draw = ImageDraw.Draw(img)
    # PIL 의 굵은 호는 상자 안쪽으로 두께가 쌓여 상자를 반 두께만큼 키움
    outer = r + w / 2
    box = [c - outer, c - outer, c + outer, c + outer]

    def _point(frac: float) -> tuple[float, float]:
        angle = math.radians(-90 + 360 * frac)
        return c + r * math.cos(angle), c + r * math.sin(angle)

    def _cap(frac: float, color: str, radius: float) -> None:
        x, y = _point(frac)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)

    def _arc(start: float, end: float, color: str) -> None:
        if end - start >= 1.0:
            draw.ellipse(box, outline=color, width=round(w))
            return
        draw.arc(
            box,
            start=-90 + 360 * start,
            end=-90 + 360 * end,
            fill=color,
            width=round(w),
        )
        _cap(start, color, w / 2)
        _cap(end, color, w / 2)

    draw.ellipse(box, outline=colors["track"], width=round(w))
    if progress <= 0:
        _cap(0.0, colors["accent"], w / 2)
    else:
        _arc(0.0, progress, colors["accent"])
    if segment is not None:
        _arc(segment[0], segment[1], colors["monitor"])
    if progress > 0:
        hr = ring["handle"] * scale * ss / 2
        _cap(progress, colors["handle_ring"], hr)
        _cap(progress, colors["handle"], hr - 2.5 * scale * ss)
    return img.resize((px, px), Image.LANCZOS)


class DialRing(ctk.CTkFrame):
    """원형 링 다이얼. 링은 PIL 이미지, 가운데 글자는 Canvas 글자 항목이다.

    값이 바뀔 때만 이미지를 다시 그리고, 글자는 itemconfig 로만 바꾼다.

    Attributes:
        canvas (tk.Canvas): 링을 그리는 캔버스 (키보드 포커스를 받음)
    """

    def __init__(
        self,
        master: Any,
        tokens: dict[str, Any],
        ko_family: str,
        num_family: str,
        on_drag: Optional[Callable[[int, bool], None]] = None,
        on_wheel: Optional[Callable[[int], None]] = None,
        on_center: Optional[Callable[[], None]] = None,
    ) -> None:
        """초기화.

        Args:
            master: 부모 위젯
            tokens: 테마 tokens 사전
            ko_family: 라벨 글꼴
            num_family: 큰 숫자 글꼴
            on_drag: 링 클릭·드래그 콜백 (분, 드래그 시작 여부). None 이면 입력을 받지 않음.
            on_wheel: 휠 콜백 (칸 수, 위로 굴리면 양수)
            on_center: 가운데 클릭 콜백
        """
        self._tokens = tokens
        size = SHAPE["ring"]["size"]
        super().__init__(
            master,
            width=size,
            height=size,
            fg_color=tokens["surface"],
            corner_radius=0,
        )
        self._ko_family = ko_family
        self._num_family = num_family
        self._on_drag = on_drag
        self._on_wheel = on_wheel
        self._on_center = on_center
        self._dragging = False
        self._wheel_acc = 0
        self._photo: Optional[Any] = None
        self._drawn_key: Optional[tuple] = None
        self._texts: dict[str, tuple] = {}
        self._state: dict[str, Any] = {
            "progress": 0.0,
            "segment": None,
            "color": tokens["actions"]["shutdown"]["accent"],
            "label": "",
            "big": "",
            "unit": "",
            "time": "",
            "time_color": tokens["actions"]["shutdown"]["accentText"],
        }
        self.canvas = tk.Canvas(
            self,
            bg=tokens["surface"],
            highlightthickness=0,
            bd=0,
            takefocus=1,
        )
        self.canvas.pack()
        self._img_item = self.canvas.create_image(0, 0, anchor=tk.CENTER)
        self._label_item = self.canvas.create_text(0, 0, anchor=tk.CENTER)
        self._big_item = self.canvas.create_text(0, 0, anchor=tk.CENTER)
        self._unit_item = self.canvas.create_text(0, 0, anchor=tk.W)
        self._time_item = self.canvas.create_text(0, 0, anchor=tk.W)
        self._fallback_items: list[int] = []
        self._layout()
        if on_drag is not None:
            self.canvas.bind("<Button-1>", self._on_press)
            self.canvas.bind("<B1-Motion>", self._on_motion)
            self.canvas.bind("<ButtonRelease-1>", self._on_release)
        if on_wheel is not None:
            self.canvas.bind("<MouseWheel>", lambda e: self._wheel(e.delta))
            # X11(WSLg 등)은 휠을 Button-4/5 로 보냄
            self.canvas.bind("<Button-4>", lambda e: self._wheel(WHEEL_DELTA_UNIT))
            self.canvas.bind("<Button-5>", lambda e: self._wheel(-WHEEL_DELTA_UNIT))

    def _scaled(self, value: float) -> float:
        """배율을 곱한 값을 돌려준다.

        Args:
            value: DPI 100% 기준 값

        Returns:
            배율 적용 값
        """
        return self._apply_widget_scaling(value)

    def _layout(self) -> None:
        """배율에 맞춰 캔버스 크기·글꼴·글자 위치를 다시 잡고 링을 다시 그린다."""
        px = round(self._scaled(SHAPE["ring"]["size"]))
        self._px = px
        self.canvas.configure(width=px, height=px)
        scale = self._get_widget_scaling()
        self._font_label = tkfont.Font(
            root=self.canvas, family=self._ko_family, size=-round(12 * scale)
        )
        self._font_big = tkfont.Font(
            root=self.canvas,
            family=self._num_family,
            size=-round(46 * scale),
            weight="bold",
        )
        self._font_sub = tkfont.Font(
            root=self.canvas, family=self._ko_family, size=-round(12 * scale)
        )
        c = px / 2
        self.canvas.coords(self._img_item, c, c)
        self.canvas.coords(self._label_item, c, c - self._scaled(32))
        self.canvas.coords(self._big_item, c, c)
        self.canvas.itemconfigure(self._label_item, font=self._font_label)
        self.canvas.itemconfigure(self._big_item, font=self._font_big)
        self.canvas.itemconfigure(self._unit_item, font=self._font_sub)
        self.canvas.itemconfigure(self._time_item, font=self._font_sub)
        self._drawn_key = None
        self._texts = {}
        self._apply()

    def _set_scaling(self, *args: Any, **kwargs: Any) -> None:
        """배율이 바뀌면 링과 글꼴을 다시 만든다."""
        super()._set_scaling(*args, **kwargs)
        self._layout()

    def set_tokens(self, tokens: dict[str, Any]) -> None:
        """테마가 바뀌면 배경·글자·링을 새 색으로 다시 그린다.

        Args:
            tokens: 새 테마의 tokens 사전
        """
        self._tokens = tokens
        self.configure(fg_color=tokens["surface"])
        # 캔버스는 tk 위젯이라 ctk 색 전파를 받지 못함
        self.canvas.configure(bg=tokens["surface"])
        # 링 캐시 키에 배경·트랙 색이 없어 직접 무효화함
        self._drawn_key = None
        self._apply()

    def set_state(self, **state: Any) -> None:
        """표시 상태를 바꾼다. 바뀐 부분만 다시 그린다.

        Args:
            **state: progress·segment·color·label·big·unit·time·time_color 중 바꿀 값
        """
        self._state.update(state)
        self._apply()

    def _apply(self) -> None:
        """현재 상태를 캔버스에 반영한다."""
        st = self._state
        tokens = self._tokens
        key = (self._px, round(st["progress"], 5), st["segment"], st["color"])
        if key != self._drawn_key:
            self._drawn_key = key
            self._draw_ring(st)
        self._set_item(self._label_item, st["label"], tokens["textMuted"])
        self._set_item(self._big_item, st["big"], tokens["text"])
        self._set_item(self._unit_item, st["unit"], tokens["textMuted"])
        self._set_item(self._time_item, st["time"], st["time_color"])
        # 단위와 실행 시각은 색이 달라 두 글자 항목을 가운데 맞춰 이어 붙임
        w_unit = self._font_sub.measure(st["unit"])
        w_time = self._font_sub.measure(st["time"])
        x0 = self._px / 2 - (w_unit + w_time) / 2
        y = self._px / 2 + self._scaled(34)
        self.canvas.coords(self._unit_item, x0, y)
        self.canvas.coords(self._time_item, x0 + w_unit, y)

    def _set_item(self, item: int, text: str, color: str) -> None:
        """글자 항목이 달라졌을 때만 itemconfig 한다.

        Args:
            item: 캔버스 항목 id
            text: 글자
            color: 글자 색
        """
        if self._texts.get(str(item)) == (text, color):
            return
        self._texts[str(item)] = (text, color)
        self.canvas.itemconfigure(item, text=text, fill=color)

    def _ring_colors(self, st: dict[str, Any]) -> dict[str, str]:
        """링 그리기에 쓸 색 사전을 만든다.

        Args:
            st: 표시 상태

        Returns:
            색 사전
        """
        tokens = self._tokens
        return {
            "bg": tokens["surface"],
            "track": tokens["surfaceAlt"],
            "accent": st["color"],
            "monitor": tokens["actions"]["monitor"]["accent"],
            "handle": tokens["text"],
            "handle_ring": tokens["background"],
        }

    def _draw_ring(self, st: dict[str, Any]) -> None:
        """링 이미지를 그린다. PIL 이 없으면 create_arc 로 대신 그린다.

        Args:
            st: 표시 상태
        """
        colors = self._ring_colors(st)
        if PIL_AVAILABLE:
            img = _render_ring_image(
                self._px,
                self._get_widget_scaling(),
                st["progress"],
                st["segment"],
                colors,
            )
            self._photo = ImageTk.PhotoImage(img, master=self.canvas)
            self.canvas.itemconfigure(self._img_item, image=self._photo)
            return
        for item in self._fallback_items:
            self.canvas.delete(item)
        c = self._px / 2
        r = self._scaled(SHAPE["ring"]["radius"])
        w = self._scaled(SHAPE["ring"]["stroke"])
        box = (c - r, c - r, c + r, c + r)
        items = [self.canvas.create_oval(*box, outline=colors["track"], width=w)]
        if st["progress"] > 0:
            items.append(
                self.canvas.create_arc(
                    *box,
                    start=90,
                    extent=-min(st["progress"], 0.9999) * 360,
                    style=tk.ARC,
                    outline=colors["accent"],
                    width=w,
                )
            )
        if st["segment"] is not None:
            a, b = st["segment"]
            items.append(
                self.canvas.create_arc(
                    *box,
                    start=90 - a * 360,
                    extent=-(b - a) * 360,
                    style=tk.ARC,
                    outline=colors["monitor"],
                    width=w,
                )
            )
        if st["progress"] > 0:
            angle = math.radians(-90 + 360 * min(st["progress"], 1.0))
            hx, hy = c + r * math.cos(angle), c + r * math.sin(angle)
            hr = self._scaled(SHAPE["ring"]["handle"]) / 2
            items.append(
                self.canvas.create_oval(
                    hx - hr,
                    hy - hr,
                    hx + hr,
                    hy + hr,
                    fill=colors["handle"],
                    outline=colors["handle_ring"],
                    width=self._scaled(2.5),
                )
            )
        for item in items:
            self.canvas.addtag_withtag("ring_fallback", item)
        # 태그로 한 번에 내려야 트랙 위에 진행 구간이 그려진 순서가 유지됨
        self.canvas.tag_lower("ring_fallback")
        self._fallback_items = items

    def _center_offset(self, event: Any) -> tuple[float, float]:
        """이벤트 좌표를 링 중심 기준으로 바꾼다.

        Args:
            event: 마우스 이벤트

        Returns:
            (dx, dy)
        """
        c = self._px / 2
        return event.x - c, event.y - c

    def _on_press(self, event: Any) -> None:
        """링 띠를 누르면 그 각도로 값을 정하고, 가운데를 누르면 숫자 입력을 연다.

        Args:
            event: 마우스 이벤트
        """
        self.canvas.focus_set()
        dx, dy = self._center_offset(event)
        radius = self._scaled(SHAPE["ring"]["radius"])
        stroke = self._scaled(SHAPE["ring"]["stroke"])
        if _in_ring_band(dx, dy, radius, stroke):
            self._dragging = True
            if self._on_drag is not None:
                self._on_drag(_angle_to_minutes(dx, dy), True)
        elif math.hypot(dx, dy) < radius - stroke and self._on_center is not None:
            self._on_center()

    def _on_motion(self, event: Any) -> None:
        """드래그 중 각도를 분으로 바꿔 넘긴다.

        Args:
            event: 마우스 이벤트
        """
        if not self._dragging or self._on_drag is None:
            return
        dx, dy = self._center_offset(event)
        if dx == 0 and dy == 0:
            return
        self._on_drag(_angle_to_minutes(dx, dy), False)

    def _on_release(self, _event: Any) -> None:
        """드래그를 끝낸다.

        Args:
            _event: 마우스 이벤트
        """
        self._dragging = False

    def _wheel(self, delta: int) -> None:
        """휠 delta 를 모아 칸 수만큼 콜백을 부른다.

        Args:
            delta: 휠 delta
        """
        steps, self._wheel_acc = _wheel_steps(self._wheel_acc, delta)
        if steps and self._on_wheel is not None:
            self._on_wheel(steps)


class _ThemedWindow:
    """토큰 색으로 위젯을 칠하는 창의 공통 기반.

    Attributes:
        _theme (dict): 현재 테마
        _tokens (dict): 현재 테마의 tokens
        _theme_action (str): "action." 경로가 가리키는 동작
        _bindings (list): (위젯, {옵션: 토큰 경로}) 목록
    """

    _theme: dict[str, Any]
    _tokens: dict[str, Any]
    _theme_action: str = ACTION_SHUTDOWN
    _bindings: list[tuple[Any, dict[str, Any]]]
    top: Any
    _ff: str

    def _tok(self, spec: Any) -> str:
        """토큰 경로를 색으로 바꾼다.

        Args:
            spec: "surface" 같은 기본 토큰, "action.accent"(현재 동작),
                "monitor.accent"(항상 모니터), ("mix", a, b, 비율), "knob"(토글 손잡이),
                "switchOff"(꺼진 토글 트랙)

        Returns:
            '#rrggbb' 색
        """
        if isinstance(spec, tuple):
            _, a, b, ratio = spec
            return mix(self._tok(a), self._tok(b), ratio)
        if spec == "knob":
            return SWITCH_KNOB
        if spec == "switchOff":
            # 라이트의 border 트랙은 흰 손잡이와 구분되지 않음
            return self._tokens[
                "textMuted" if self._theme["mode"] == "light" else "border"
            ]
        if spec.startswith("action."):
            return action_tokens(self._theme, self._theme_action)[spec[7:]]
        if spec.startswith("monitor."):
            return action_tokens(self._theme, ACTION_MONITOR_OFF)[spec[8:]]
        return self._tokens[spec]

    @staticmethod
    def _uses_action(spec: Any) -> bool:
        """토큰 경로가 현재 동작 색을 쓰는지.

        Args:
            spec: 토큰 경로

        Returns:
            동작 따라 바뀌면 True
        """
        if isinstance(spec, tuple):
            return _ThemedWindow._uses_action(spec[1]) or _ThemedWindow._uses_action(
                spec[2]
            )
        return isinstance(spec, str) and spec.startswith("action.")

    def _themed(
        self, cls: Any, master: Any, colors: dict[str, Any], **kwargs: Any
    ) -> Any:
        """토큰 색으로 위젯을 만들고 _bindings 에 등록한다.

        Args:
            cls: 위젯 클래스
            master: 부모 위젯
            colors: {옵션: 토큰 경로}
            **kwargs: 그 밖의 위젯 옵션

        Returns:
            만든 위젯
        """
        widget = cls(master, **{k: self._tok(v) for k, v in colors.items()}, **kwargs)
        self._bindings.append((widget, colors))
        self._apply_switch_shape(widget)
        return widget

    def _apply_switch_shape(self, widget: Any) -> None:
        """스위치 테두리 두께를 현재 모드에 맞춘다.

        Args:
            widget: 위젯 (스위치가 아니면 그대로 둠)
        """
        if not isinstance(widget, ctk.CTkSwitch):
            return
        # 손잡이가 트랙보다 커서 흰 배경에 묻히므로 라이트는 트랙을 손잡이 높이로 키움
        default = ctk.ThemeManager.theme["CTkSwitch"]["border_width"]
        width = 0 if self._theme["mode"] == "light" else default
        if widget.cget("border_width") != width:
            widget.configure(border_width=width)

    def _repaint_bindings(self) -> None:
        """_bindings 에 등록된 위젯을 현재 테마로 모두 다시 칠한다."""
        for widget, colors in self._bindings:
            widget.configure(**{k: self._tok(v) for k, v in colors.items()})
            self._apply_switch_shape(widget)

    def _font(self, size: int, bold: bool = False) -> tuple:
        """한글 글꼴 튜플을 만든다.

        Args:
            size: px (ctk 가 배율을 곱함)
            bold: 굵게 여부

        Returns:
            ctk 글꼴 튜플
        """
        return (self._ff, size, "bold") if bold else (self._ff, size)

    def _window_scaling(self) -> float:
        """창 배율을 돌려준다.

        Returns:
            배율. 구할 수 없으면 1.0.
        """
        try:
            return float(self.top._get_window_scaling())
        except Exception:
            return 1.0


class ScheduleDialog(_ThemedWindow):
    """종료·재시작·모니터 끄기 예약 창 (설정 화면).

    _total_seconds(총 초)가 유일한 기준값이다. 링·휠·±·빠른 추가·가운데 숫자 입력이
    모두 이 값을 바꾼다. 색은 cfg.theme 토큰으로만 칠한다.

    Attributes:
        top (ctk.CTkToplevel): 예약 창
        _action (str): 동작 종류 (ACTION_SHUTDOWN/ACTION_RESTART/ACTION_MONITOR_OFF)
        _unit (str): ±·휠의 증감 단위
        _total_seconds (int): 기준값 — 총 예약 시간(초). 모니터 동작이면 끌 때까지 시간.
        var_value (tk.StringVar): 가운데 숫자 입력 변수 (_total_seconds ÷ 단위)
        _monitor_on (tk.BooleanVar): "모니터 먼저 끄기" 스위치
        var_monitor_lead (tk.StringVar): 직접 입력 N(분 전) 변수
        _monitor_lead_min (int): N(분). 칩·직접 입력의 공통 기준값.
        _bindings (list): (위젯, {옵션: 토큰 경로}) 목록
        callback (Callable[[str, int, int, int, Optional[int]], None]): 확인 시 콜백
    """

    _UNIT_TO_SEC: dict[str, int] = {
        UNIT_HOUR: 3600,
        UNIT_MINUTE: 60,
        UNIT_SECOND: 1,
    }
    _HEADER: dict[str, tuple[str, str, str]] = {
        ACTION_SHUTDOWN: ("power", "종료 예약", "시스템 종료를 예약합니다"),
        ACTION_RESTART: ("restart", "재시작 예약", "시스템 재시작을 예약합니다"),
        ACTION_MONITOR_OFF: (
            "monitor",
            "모니터 끄기 예약",
            "지정 시간 후 화면만 끕니다",
        ),
    }
    _ICON_GLYPH: dict[str, str] = {"power": "⏻", "restart": "↻", "monitor": "☾"}
    _TAB_KEYS: dict[str, str] = {
        "1": ACTION_SHUTDOWN,
        "2": ACTION_RESTART,
        "3": ACTION_MONITOR_OFF,
        "KP_1": ACTION_SHUTDOWN,
        "KP_2": ACTION_RESTART,
        "KP_3": ACTION_MONITOR_OFF,
    }
    _MONITOR_WARN_TEXT: str = "예약 시간보다 길어요. 시간을 늘리거나 칩을 바꾸세요."
    _MAX_NOTICE: str = f"최대 {MAX_SCHEDULE_SECONDS // 3600}시간"

    # tk 없이 만든 인스턴스(테스트)에서도 메서드가 화면을 건드리지 않게 하는 기본값
    _views_built: bool = False
    _view: str = "setup"
    _lead_updating: bool = False
    # 마지막 입력이 상한에 걸려 잘렸는지 — 다이얼 라벨에 "최대 999시간" 을 띄움
    _clipped: bool = False
    _scheduler: Optional["ShutdownScheduler"] = None

    def __init__(
        self,
        parent: tk.Tk,
        callback: Callable[[str, int, int, int, Optional[int]], None],
        on_close: Optional[Callable[[], None]] = None,
        scheduler: Optional["ShutdownScheduler"] = None,
    ) -> None:
        """다이얼로그 초기화.

        Args:
            parent: tkinter 루트 윈도우
            callback: 확인 시 호출할 함수
                (action, hours, minutes, seconds, monitor_lead_sec)
            on_close: 창이 닫힐 때 호출될 콜백
            scheduler: 예약 상태 참조용 ShutdownScheduler 인스턴스
        """
        self.callback = callback
        self.on_close = on_close
        self._scheduler = scheduler
        self._action: str = ACTION_SHUTDOWN
        self._unit: str = UNIT_MINUTE
        self._total_seconds: int = 0
        self._updating: bool = False
        self._countdown_after_id: Optional[str] = None
        self._monitor_lead_min: int = 0

        self._theme: dict[str, Any] = (
            scheduler._theme if scheduler is not None else get_theme()
        )
        self._tokens: dict[str, Any] = self._theme["tokens"]
        self._theme_action: str = ACTION_SHUTDOWN
        self._bindings: list[tuple[Any, dict[str, Any]]] = []
        self._icon_cache: dict[tuple[str, str, int], Any] = {}
        self._layout_key: Optional[tuple] = None
        self._sched_layout_key: Optional[tuple] = None
        self._style_key: Optional[tuple] = None
        self._fitted_height: int = 0
        self._confirm_enabled: bool = True

        self._action_btns: dict[str, ctk.CTkButton] = {}
        self._unit_btns: dict[str, ctk.CTkButton] = {}
        self._chip_btns: dict[int, ctk.CTkButton] = {}

        self.top = ctk.CTkToplevel(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._build_ui()

    # ------------------------------------------------------------------ #
    # 테마                                                                 #
    # ------------------------------------------------------------------ #

    def _apply_action_theme(self, action: str) -> None:
        """동작 따라 바뀌는 강조색만 다시 칠하고 헤더 글자·아이콘을 바꾼다.

        Args:
            action: 강조색을 가져올 동작
        """
        if not self._views_built:
            return
        self._theme_action = action
        try:
            for widget, colors in self._bindings:
                if any(self._uses_action(v) for v in colors.values()):
                    widget.configure(**{k: self._tok(v) for k, v in colors.items()})
            icon, title, desc = self._HEADER.get(action, self._HEADER[ACTION_SHUTDOWN])
            self._set_icon(self._header_icon, icon, self._tok("action.accent"), 20)
            self._header_title.configure(text=title)
            self._header_desc.configure(text=desc)
            self._set_confirm_enabled(self._confirm_enabled)
        except tk.TclError:
            pass

    def apply_theme(self, theme: dict[str, Any]) -> None:
        """열린 창을 새 테마로 다시 칠한다 (메인 스레드 전용).

        Args:
            theme: 새 테마 사전
        """
        if not self._views_built:
            return
        self._theme, self._tokens = theme, theme["tokens"]
        try:
            self.top.configure(fg_color=self._tok("background"))
            self._repaint_bindings()
            self._dial.set_tokens(self._tokens)
            self._sched_dial.set_tokens(self._tokens)
            self._refresh_action_btns()
            self._refresh_unit_btns()
            for label in (self._monitor_icon, self._sched_monitor_icon):
                self._set_icon(label, "monitor", self._tok("monitor.accent"), 16)
            # 칩·제목 색은 모양이 바뀔 때만 칠하므로 캐시를 비움
            self._style_key = None
            self._apply_action_theme(self._theme_action)
            self._refresh_monitor_row()
            self._tick_countdown()
            _apply_caption_color(self.top, self._tok("background"))
            # 모드 전환 시 ctk 가 창을 숨겼다 보인 뒤 다시 칠함
            self.top.after(
                200, lambda: _apply_caption_color(self.top, self._tok("background"))
            )
        except tk.TclError:
            pass

    def _icon_image(self, kind: str, color: str, px: int) -> Optional[Any]:
        """아이콘 CTkImage 를 만든다 (같은 모양·색은 재사용).

        Args:
            kind: "power" / "restart" / "monitor"
            color: 선 색
            px: DPI 100% 기준 크기

        Returns:
            CTkImage. PIL 이 없으면 None.
        """
        key = (kind, color, px)
        if key not in self._icon_cache:
            # 배율이 커져도 선명하게 2배로 그려 CTkImage 가 줄이게 함
            img = _make_icon_image(kind, color, px * 2)
            self._icon_cache[key] = (
                None if img is None else ctk.CTkImage(img, img, size=(px, px))
            )
        return self._icon_cache[key]

    def _set_icon(self, label: Any, kind: str, color: str, px: int) -> None:
        """아이콘 라벨에 그림을 넣는다. PIL 이 없으면 글리프로 대신한다.

        Args:
            label: CTkLabel
            kind: 아이콘 종류
            color: 색
            px: 크기
        """
        image = self._icon_image(kind, color, px)
        if image is not None:
            label.configure(image=image, text="")
        else:
            label.configure(text=self._ICON_GLYPH[kind], text_color=color)

    @staticmethod
    def _hide(widget: Any) -> None:
        """grid 위젯을 숨긴다 (배치 옵션은 기억).

        ctk 는 grid_remove 를 감싸지 않아, 배율이 바뀌면 숨긴 위젯을 마지막 grid 호출로
        다시 붙인다. 그래서 그 기록을 따로 옮겨 두고 지운다.

        Args:
            widget: ctk 위젯
        """
        call = getattr(widget, "_last_geometry_manager_call", None)
        if call is not None:
            widget._hidden_grid_call = call
            widget._last_geometry_manager_call = None
        widget.grid_remove()

    @staticmethod
    def _show(widget: Any) -> None:
        """_hide 로 숨긴 위젯을 처음 배치 옵션 그대로 다시 붙인다.

        Args:
            widget: ctk 위젯
        """
        call = getattr(widget, "_hidden_grid_call", None)
        if call is not None and widget.winfo_manager() != "grid":
            widget.grid(**call["kwargs"])
        elif widget.winfo_manager() != "grid":
            widget.grid()

    # ------------------------------------------------------------------ #
    # UI 구성                                                              #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        """예약 창을 화면별 빌더로 구성한다."""
        top = self.top
        top.title(DIALOG_TITLE)
        top.resizable(False, False)
        top.configure(fg_color=self._tok("background"))
        self._ff = _pick_font_family(top, UI_FONT_FAMILIES_KO)
        self._nf = _pick_font_family(top, (UI_FONT_NUMBER,) + UI_FONT_FAMILIES_KO)

        self.var_value = tk.StringVar(value="0")
        self._preview_var = tk.StringVar(value="")
        default_on = MONITOR_OFF_DEFAULT
        if self._scheduler is not None:
            default_on = bool(
                self._scheduler._settings.get(
                    SETTINGS_KEY_MONITOR_OFF, MONITOR_OFF_DEFAULT
                )
            )
        self._monitor_on = tk.BooleanVar(value=default_on)
        self.var_monitor_lead = tk.StringVar(value="0")
        self._autostart_var = tk.BooleanVar(value=is_autostart_enabled())

        outer = SHAPE["spacing"]["outer"]
        gap = SHAPE["spacing"]["gap"]
        main = self._themed(
            ctk.CTkFrame, top, {"fg_color": "background"}, corner_radius=0
        )
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1)
        self._main = main

        self._build_header(main).grid(
            row=0, column=0, sticky="ew", padx=outer, pady=(outer, 0)
        )

        setup = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        setup.grid_columnconfigure(0, weight=1)
        self._setup_view = setup
        self._build_tabs(setup).grid(row=0, column=0, sticky="ew")
        self._build_dial_card(setup).grid(row=1, column=0, sticky="ew", pady=(gap, 0))
        self._options_label = self._themed(
            ctk.CTkLabel,
            setup,
            {"text_color": "textMuted"},
            text="추가 옵션",
            font=self._font(11),
            height=14,
            anchor="w",
        )
        self._options_label.grid(row=2, column=0, sticky="w", pady=(gap, 6))
        self._monitor_card = self._build_monitor_card(setup)
        self._monitor_card.grid(row=3, column=0, sticky="ew")
        self._build_footer(setup).grid(row=4, column=0, sticky="ew", pady=(gap, 0))
        setup.grid(row=1, column=0, sticky="ew", padx=outer, pady=(gap, 0))

        self._scheduled_view = self._build_scheduled_view(main)
        self._scheduled_view.grid(
            row=2, column=0, sticky="ew", padx=outer, pady=(gap, 0)
        )
        self._hide(self._scheduled_view)

        self._build_autostart_row(main).grid(
            row=3, column=0, sticky="ew", padx=outer, pady=(gap, gap)
        )

        top.bind("<Key>", self._on_key)
        self.var_value.trace_add("write", self._on_value_change)
        self.var_monitor_lead.trace_add("write", self._on_lead_change)

        self._views_built = True
        self._refresh_unit_btns()
        self._set_action(self._action)
        # 진행 중 예약(종료 또는 단독)이 있으면 예약됨 화면으로 엶
        self._show_view("scheduled" if self._has_schedule() else "setup")
        self._place_window()
        # topmost 없이 Windows API로 직접 포커스 이동 — 이후 일반 창처럼 동작
        top.after(50, lambda: _bring_to_front(top))
        # ctk 가 타이틀바를 다크로 바꾸며 창을 잠깐 숨겼다 보이므로 그 뒤에 칠함
        top.after(200, lambda: _apply_caption_color(top, self._tok("background")))

    def _build_header(self, parent: Any) -> Any:
        """헤더(아이콘 박스 + 제목·설명 + 예약됨 표시)를 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            헤더 프레임
        """
        header = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        header.grid_columnconfigure(1, weight=1)
        box = self._themed(
            ctk.CTkFrame,
            header,
            {"fg_color": ("mix", "action.accent", "surface", 0.16)},
            width=38,
            height=38,
            corner_radius=SHAPE["radius"]["control"],
        )
        box.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        box.grid_propagate(False)
        self._header_icon = self._themed(
            ctk.CTkLabel,
            box,
            {"text_color": "action.accent"},
            text="",
            font=(UI_FONT_SYMBOL, 18),
            width=20,
            height=20,
        )
        self._header_icon.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._header_title = self._themed(
            ctk.CTkLabel,
            header,
            {"text_color": "text"},
            text="",
            font=self._font(15, bold=True),
            height=20,
            anchor="w",
        )
        self._header_title.grid(row=0, column=1, sticky="sw")
        self._header_desc = self._themed(
            ctk.CTkLabel,
            header,
            {"text_color": "textMuted"},
            text="",
            font=self._font(12),
            height=16,
            anchor="w",
        )
        self._header_desc.grid(row=1, column=1, sticky="nw")
        self._badge = self._themed(
            ctk.CTkLabel,
            header,
            {
                "fg_color": ("mix", "action.accent", "surface", 0.16),
                "text_color": "action.accentText",
            },
            text="● 예약됨",
            font=self._font(11, bold=True),
            width=64,
            height=22,
            corner_radius=SHAPE["radius"]["control"],
        )
        self._badge.grid(row=0, column=2, rowspan=2, padx=(8, 0))
        self._hide(self._badge)
        if self._scheduler is not None:
            self._settings_btn = self._themed(
                ctk.CTkButton,
                header,
                {"hover_color": "surfaceAlt", "text_color": "textMuted"},
                text="⚙",
                font=(UI_FONT_SYMBOL, 14),
                width=26,
                height=26,
                corner_radius=SHAPE["radius"]["chip"],
                fg_color="transparent",
                command=self._scheduler._open_settings_dialog,
            )
            self._settings_btn.grid(row=0, column=3, rowspan=2, padx=(6, 0))
        return header

    def _build_tabs(self, parent: Any) -> Any:
        """동작 탭(3칸 세그먼트)을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            탭 컨테이너
        """
        bar = self._themed(
            ctk.CTkFrame,
            parent,
            {"fg_color": "surfaceAlt"},
            corner_radius=SHAPE["radius"]["control"],
        )
        bar.grid_columnconfigure((0, 1, 2), weight=1, uniform="tab")
        for col, (action_val, icon, label) in enumerate(
            [
                (ACTION_SHUTDOWN, "⏻", "종료"),
                (ACTION_RESTART, "↻", "재시작"),
                (ACTION_MONITOR_OFF, "☾", "모니터"),
            ]
        ):
            btn = ctk.CTkButton(
                bar,
                text=f"{icon}  {label}",
                font=self._font(13, bold=True),
                width=40,
                height=32,
                corner_radius=SHAPE["radius"]["chip"],
                fg_color="transparent",
                command=lambda v=action_val: self._set_action(v),
            )
            btn.grid(row=0, column=col, sticky="ew", padx=3, pady=3)
            self._action_btns[action_val] = btn
        return bar

    def _build_dial_card(self, parent: Any) -> Any:
        """다이얼 카드(링 + 단위 스테퍼 + 빠른 추가)를 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            카드 프레임
        """
        pad = SHAPE["spacing"]["card"]
        card = self._themed(
            ctk.CTkFrame,
            parent,
            {"fg_color": "surface", "border_color": "border"},
            border_width=1,
            corner_radius=SHAPE["radius"]["card"],
        )
        card.grid_columnconfigure(0, weight=1)
        self._dial = DialRing(
            card,
            self._tokens,
            self._ff,
            self._nf,
            on_drag=self._on_ring_drag,
            on_wheel=self._on_ring_wheel,
            on_center=self._open_value_entry,
        )
        self._dial.grid(row=0, column=0, pady=(pad, 0))
        vcmd = (self.top.register(self._validate_int_input), "%P")
        self._entry = self._themed(
            ctk.CTkEntry,
            self._dial,
            {"fg_color": "surfaceAlt", "text_color": "text", "border_color": "border"},
            textvariable=self.var_value,
            font=(self._nf, 34, "bold"),
            justify=tk.CENTER,
            width=120,
            height=52,
            border_width=1,
            corner_radius=SHAPE["radius"]["control"],
            validate="key",
            validatecommand=vcmd,
        )
        self._entry.bind("<FocusOut>", lambda e: self._close_value_entry())
        self._build_stepper(card).grid(row=1, column=0, pady=(12, 0))
        self._build_quick_add(card).grid(
            row=2, column=0, sticky="ew", padx=pad, pady=(10, pad)
        )
        return card

    def _build_stepper(self, parent: Any) -> Any:
        """단위 스테퍼(− / 시간·분·초 / +)를 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            스테퍼 프레임
        """
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        chip = SHAPE["radius"]["chip"]
        for col, (text, sign) in enumerate([("−", -1), ("+", 1)]):
            btn = self._themed(
                ctk.CTkButton,
                row,
                {
                    "fg_color": "surfaceAlt",
                    "hover_color": "border",
                    "text_color": "text",
                },
                text=text,
                font=(self._nf, 15, "bold"),
                width=32,
                height=28,
                corner_radius=chip,
                command=lambda s=sign: self._step(s),
            )
            btn.grid(
                row=0, column=0 if sign < 0 else 2, padx=(0, 8) if sign < 0 else (8, 0)
            )
        seg = self._themed(
            ctk.CTkFrame,
            row,
            {"fg_color": "surfaceAlt"},
            width=144,
            height=28,
            corner_radius=chip,
        )
        seg.grid(row=0, column=1)
        seg.grid_propagate(False)
        seg.grid_columnconfigure((0, 1, 2), weight=1, uniform="unit")
        seg.grid_rowconfigure(0, weight=1)
        for col, (unit_val, unit_label) in enumerate(
            [
                (UNIT_HOUR, UNIT_LABEL_HOUR),
                (UNIT_MINUTE, UNIT_LABEL_MINUTE),
                (UNIT_SECOND, UNIT_LABEL_SECOND),
            ]
        ):
            btn = ctk.CTkButton(
                seg,
                text=unit_label,
                font=self._font(12, bold=True),
                width=20,
                height=22,
                corner_radius=chip - 2,
                fg_color="transparent",
                command=lambda v=unit_val: self._set_unit(v),
            )
            btn.grid(row=0, column=col, sticky="ew", padx=2, pady=3)
            self._unit_btns[unit_val] = btn
        return row

    def _build_quick_add(self, parent: Any) -> Any:
        """빠른 추가(+5분/+30분/+1시간) + 초기화 줄을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            줄 프레임
        """
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        row.grid_columnconfigure((0, 1, 2), weight=1, uniform="quick")
        chip = SHAPE["radius"]["chip"]
        for col, (btn_text, add_sec) in enumerate(QUICK_ADD_BUTTONS):
            self._themed(
                ctk.CTkButton,
                row,
                {
                    "fg_color": "surfaceAlt",
                    "hover_color": "border",
                    "text_color": "monitor.accentText",
                },
                text=btn_text,
                font=self._font(12, bold=True),
                width=40,
                height=32,
                corner_radius=chip,
                command=lambda s=add_sec: self._quick_add(s),
            ).grid(row=0, column=col, sticky="ew", padx=(0, 6))
        self._themed(
            ctk.CTkButton,
            row,
            {
                "fg_color": "surfaceAlt",
                "hover_color": "border",
                "text_color": "textMuted",
            },
            text="↺",
            font=(UI_FONT_SYMBOL, 14, "bold"),
            width=34,
            height=32,
            corner_radius=chip,
            command=self._reset_input,
        ).grid(row=0, column=3)
        return row

    def _build_monitor_card(self, parent: Any) -> Any:
        """모니터 옵션 카드(헤더 줄 + 칩·직접 입력·경고 펼침 영역)를 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            카드 프레임
        """
        pad = SHAPE["spacing"]["card"]
        chip = SHAPE["radius"]["chip"]
        card = self._themed(
            ctk.CTkFrame,
            parent,
            {"fg_color": "surface", "border_color": "border"},
            border_width=1,
            corner_radius=SHAPE["radius"]["card"],
        )
        card.grid_columnconfigure(1, weight=1)
        box = self._themed(
            ctk.CTkFrame,
            card,
            {"fg_color": ("mix", "monitor.accent", "surface", 0.16)},
            width=30,
            height=30,
            corner_radius=chip,
        )
        box.grid(row=0, column=0, rowspan=2, padx=(pad, 10), pady=pad)
        box.grid_propagate(False)
        self._monitor_icon = ctk.CTkLabel(
            box, text="", font=(UI_FONT_SYMBOL, 14), width=16, height=16
        )
        self._monitor_icon.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._set_icon(self._monitor_icon, "monitor", self._tok("monitor.accent"), 16)
        self._monitor_title = self._themed(
            ctk.CTkLabel,
            card,
            {"text_color": "text"},
            text="모니터 먼저 끄기",
            font=self._font(13, bold=True),
            height=18,
            anchor="w",
        )
        self._monitor_title.grid(row=0, column=1, sticky="sw", pady=(pad, 0))
        desc = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        desc.grid(row=1, column=1, sticky="nw", pady=(0, pad))
        self._monitor_desc: list[Any] = []
        for key in ("textMuted", "monitor.accentText", "textMuted"):
            lbl = self._themed(
                ctk.CTkLabel,
                desc,
                {"text_color": key},
                text="",
                font=self._font(12),
                height=16,
                padx=0,
            )
            lbl.pack(side=tk.LEFT)
            self._monitor_desc.append(lbl)
        self._monitor_switch = self._themed(
            ctk.CTkSwitch,
            card,
            {
                "progress_color": "monitor.accent",
                "fg_color": "switchOff",
                "button_color": "knob",
                "button_hover_color": "knob",
            },
            text="",
            width=34,
            switch_width=34,
            switch_height=19,
            variable=self._monitor_on,
            onvalue=True,
            offvalue=False,
            command=self._refresh_monitor_row,
        )
        self._monitor_switch.grid(row=0, column=2, rowspan=2, padx=(6, pad))

        expand = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        expand.grid_columnconfigure(0, weight=1)
        self._monitor_expand = expand
        chips = ctk.CTkFrame(expand, fg_color="transparent", corner_radius=0)
        chips.grid(row=0, column=0, sticky="ew")
        chips.grid_columnconfigure(
            tuple(range(len(MONITOR_LEAD_CHIPS))), weight=1, uniform="chip"
        )
        gap = SHAPE["spacing"]["chip"]
        for col, minutes in enumerate(MONITOR_LEAD_CHIPS):
            btn = ctk.CTkButton(
                chips,
                text="직전" if minutes == 0 else f"{minutes}분 전",
                font=self._font(12, bold=True),
                width=40,
                height=30,
                corner_radius=chip,
                border_width=1,
                command=lambda m=minutes: self._on_chip(m),
            )
            last = col == len(MONITOR_LEAD_CHIPS) - 1
            btn.grid(row=0, column=col, sticky="ew", padx=(0, 0 if last else gap))
            self._chip_btns[minutes] = btn
        direct = ctk.CTkFrame(expand, fg_color="transparent", corner_radius=0)
        direct.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._themed(
            ctk.CTkLabel,
            direct,
            {"text_color": "textMuted"},
            text="직접 입력",
            font=self._font(12),
            height=26,
        ).pack(side=tk.LEFT, padx=(0, 8))
        lead_vcmd = (self.top.register(self._validate_lead_input), "%P")
        self._lead_entry = self._themed(
            ctk.CTkEntry,
            direct,
            {
                "fg_color": "surfaceAlt",
                "text_color": "text",
                "border_color": "border",
            },
            textvariable=self.var_monitor_lead,
            font=self._font(12, bold=True),
            width=56,
            height=26,
            justify=tk.CENTER,
            border_width=1,
            corner_radius=chip,
            validate="key",
            validatecommand=lead_vcmd,
        )
        self._lead_entry.pack(side=tk.LEFT)
        self._themed(
            ctk.CTkLabel,
            direct,
            {"text_color": "textMuted"},
            text="분 전",
            font=self._font(12),
            height=26,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._monitor_warn = self._themed(
            ctk.CTkLabel,
            expand,
            {"text_color": "warning"},
            text=self._MONITOR_WARN_TEXT,
            font=self._font(12),
            height=16,
            anchor="w",
        )
        self._monitor_warn.grid(row=2, column=0, sticky="w", pady=(8, 0))
        expand.grid(row=2, column=0, columnspan=3, sticky="ew", padx=pad, pady=(0, pad))
        return card

    def _build_footer(self, parent: Any) -> Any:
        """설정 화면 하단 버튼(닫기 1 : 예약 2)을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            버튼 줄 프레임
        """
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        row.grid_columnconfigure(0, weight=1, uniform="foot")
        row.grid_columnconfigure(1, weight=2, uniform="foot")
        radius = SHAPE["radius"]["control"]
        self._themed(
            ctk.CTkButton,
            row,
            {"fg_color": "surfaceAlt", "hover_color": "border", "text_color": "text"},
            text="닫기",
            font=self._font(13, bold=True),
            width=40,
            height=42,
            corner_radius=radius,
            command=self._on_cancel,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._btn_confirm = ctk.CTkButton(
            row,
            textvariable=self._preview_var,
            font=self._font(13, bold=True),
            width=40,
            height=42,
            corner_radius=radius,
            command=self._on_confirm,
        )
        self._btn_confirm.grid(row=0, column=1, sticky="ew")
        return row

    def _build_scheduled_view(self, parent: Any) -> Any:
        """예약됨 화면(카운트다운 링 + 일시정지 + 모니터 상태 + 취소/연장)을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            화면 프레임
        """
        pad = SHAPE["spacing"]["card"]
        gap = SHAPE["spacing"]["gap"]
        chip = SHAPE["radius"]["chip"]
        view = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        view.grid_columnconfigure(0, weight=1)

        card = self._themed(
            ctk.CTkFrame,
            view,
            {"fg_color": "surface", "border_color": "border"},
            border_width=1,
            corner_radius=SHAPE["radius"]["card"],
        )
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        self._sched_dial = DialRing(card, self._tokens, self._ff, self._nf)
        self._sched_dial.grid(row=0, column=0, pady=pad)
        self._btn_pause_resume = self._themed(
            ctk.CTkButton,
            card,
            {"fg_color": "surfaceAlt", "hover_color": "border", "text_color": "text"},
            text="II",
            font=(UI_FONT_SYMBOL, 11, "bold"),
            width=28,
            height=28,
            corner_radius=chip,
            command=self._on_pause_resume,
        )

        mon = self._themed(
            ctk.CTkFrame,
            view,
            {"fg_color": "surface", "border_color": "border"},
            border_width=1,
            corner_radius=SHAPE["radius"]["card"],
        )
        mon.grid(row=1, column=0, sticky="ew", pady=(gap, 0))
        mon.grid_columnconfigure(1, weight=1)
        self._sched_monitor_card = mon
        box = self._themed(
            ctk.CTkFrame,
            mon,
            {"fg_color": ("mix", "monitor.accent", "surface", 0.16)},
            width=30,
            height=30,
            corner_radius=chip,
        )
        box.grid(row=0, column=0, rowspan=2, padx=(pad, 10), pady=pad, sticky="n")
        box.grid_propagate(False)
        self._sched_monitor_icon = ctk.CTkLabel(
            box, text="", font=(UI_FONT_SYMBOL, 14), width=16, height=16
        )
        self._sched_monitor_icon.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._set_icon(
            self._sched_monitor_icon, "monitor", self._tok("monitor.accent"), 16
        )
        self._sched_monitor_title = self._themed(
            ctk.CTkLabel,
            mon,
            {"text_color": "text"},
            text="",
            font=self._font(13, bold=True),
            height=18,
            anchor="w",
        )
        self._sched_monitor_title.grid(row=0, column=1, sticky="sw", pady=(pad, 0))
        self._sched_monitor_sub = self._themed(
            ctk.CTkLabel,
            mon,
            {"text_color": "textMuted"},
            text="",
            font=self._font(12),
            height=16,
            anchor="w",
            justify="left",
        )
        self._sched_monitor_sub.grid(
            row=1, column=1, columnspan=2, sticky="nw", pady=(0, pad), padx=(0, pad)
        )
        solo = ctk.CTkFrame(mon, fg_color="transparent", corner_radius=0)
        solo.grid_columnconfigure(0, weight=1)
        self._sched_solo_row = solo
        self._sched_solo_label = self._themed(
            ctk.CTkLabel,
            solo,
            {"text_color": "monitor.accentText"},
            text="",
            font=self._font(12),
            height=24,
            anchor="w",
        )
        self._sched_solo_label.grid(row=0, column=0, sticky="w")
        self._themed(
            ctk.CTkButton,
            solo,
            {
                "fg_color": "surfaceAlt",
                "hover_color": "border",
                "text_color": "textMuted",
            },
            text="✕",
            font=(UI_FONT_SYMBOL, 11),
            width=24,
            height=24,
            corner_radius=chip,
            command=self._on_cancel_solo,
        ).grid(row=0, column=1, sticky="e")
        solo.grid(row=2, column=0, columnspan=3, sticky="ew", padx=pad, pady=(0, pad))

        self._themed(
            ctk.CTkButton,
            view,
            {
                "fg_color": "background",
                "hover_color": "surfaceAlt",
                "text_color": "textMuted",
            },
            text="다시 설정",
            font=self._font(12),
            width=70,
            height=24,
            corner_radius=chip,
            command=lambda: self._show_view("setup"),
        ).grid(row=2, column=0, sticky="e", pady=(8, 0))

        row = ctk.CTkFrame(view, fg_color="transparent", corner_radius=0)
        row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        row.grid_columnconfigure(0, weight=1, uniform="foot")
        row.grid_columnconfigure(1, weight=2, uniform="foot")
        radius = SHAPE["radius"]["control"]
        self._themed(
            ctk.CTkButton,
            row,
            {"fg_color": "surfaceAlt", "hover_color": "border", "text_color": "text"},
            text="예약 취소",
            font=self._font(13, bold=True),
            width=40,
            height=42,
            corner_radius=radius,
            command=self._on_cancel_schedule,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._btn_extend = self._themed(
            ctk.CTkButton,
            row,
            {
                "fg_color": "surfaceAlt",
                "hover_color": "border",
                "text_color": "action.accentText",
            },
            text=f"+{_format_duration(EXTEND_SECONDS)} 연장",
            font=self._font(13, bold=True),
            width=40,
            height=42,
            corner_radius=radius,
            command=self._on_extend,
        )
        self._btn_extend.grid(row=0, column=1, sticky="ew")
        return view

    def _build_autostart_row(self, parent: Any) -> Any:
        """창 맨 아래 "Windows 시작 시 자동 실행" 줄(구분선 + 스위치)을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            줄 프레임
        """
        wrap = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        wrap.grid_columnconfigure(0, weight=1)
        self._themed(
            ctk.CTkFrame,
            wrap,
            {"fg_color": "border"},
            height=1,
            corner_radius=0,
        ).grid(row=0, column=0, columnspan=2, sticky="ew")
        self._themed(
            ctk.CTkLabel,
            wrap,
            {"text_color": "textMuted"},
            text="Windows 시작 시 자동 실행",
            font=self._font(12),
            height=20,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self._autostart_btn = self._themed(
            ctk.CTkSwitch,
            wrap,
            {
                "progress_color": "success",
                "fg_color": "switchOff",
                "button_color": "knob",
                "button_hover_color": "knob",
            },
            text="",
            width=34,
            switch_width=34,
            switch_height=19,
            variable=self._autostart_var,
            onvalue=True,
            offvalue=False,
            command=self._on_toggle_autostart,
        )
        self._autostart_btn.grid(row=1, column=1, sticky="e", pady=(10, 0))
        return wrap

    def _place_window(self) -> None:
        """높이를 내용에 맞추고 화면 가운데에 놓는다."""
        self._fit_height()
        try:
            scale = self._window_scaling()
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            # geometry 의 위치는 배율을 곱하지 않으므로 실제 px 로 계산함
            x = (sw - round(DIALOG_WIDTH * scale)) // 2
            y = (sh - round(self._fitted_height * scale)) // 2
            self.top.geometry(f"{DIALOG_WIDTH}x{self._fitted_height}+{x}+{y}")
        except tk.TclError:
            pass

    def _fit_height(self) -> None:
        """창 높이를 내용 reqheight 에 맞춘다.

        CTkToplevel.geometry 가 배율을 곱하므로 reqheight ÷ 창 배율을 넘긴다.
        """
        if not self._views_built:
            return
        try:
            self.top.update_idletasks()
            req = self._main.winfo_reqheight()
            height = max(DIALOG_HEIGHT, math.ceil(req / self._window_scaling()))
            if height != self._fitted_height:
                self._fitted_height = height
                self.top.geometry(f"{DIALOG_WIDTH}x{height}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ #
    # 상태 변경                                                            #
    # ------------------------------------------------------------------ #

    def _has_schedule(self) -> bool:
        """종료 예약이나 모니터 단독 예약이 걸려 있는지.

        Returns:
            예약이 하나라도 있으면 True
        """
        sch = self._scheduler
        return bool(sch and (sch.is_active or sch._solo_monitor.armed))

    def _tick_countdown(self) -> None:
        """두 화면 공용 1초 갱신.

        설정 화면은 실행 시각(지금 + 설정 시간)을, 예약됨 화면은 남은 시간을 갱신한다.
        예약이 모두 사라지면 설정 화면으로 돌아간다.
        """
        try:
            # 여러 곳에서 다시 부르므로 걸린 갱신을 먼저 끊어 겹치지 않게 함
            if self._countdown_after_id is not None:
                self.top.after_cancel(self._countdown_after_id)
                self._countdown_after_id = None
            if self._view == "scheduled":
                vm = _view_model(self._scheduler)
                if vm.target is None:
                    self._show_view("setup")
                    return
                self._render_scheduled(vm)
            else:
                self._update_preview()
                self._refresh_monitor_row()
            self._countdown_after_id = self.top.after(1000, self._tick_countdown)
        except tk.TclError:
            pass

    def _show_view(self, view: str) -> None:
        """설정("setup")·예약됨("scheduled") 화면을 바꾼다.

        Args:
            view: "setup" 또는 "scheduled"
        """
        if not self._views_built:
            return
        self._view = view
        try:
            if view == "scheduled":
                self._close_value_entry()
                self._hide(self._setup_view)
                self._show(self._scheduled_view)
                self._show(self._badge)
                self._sched_layout_key = None
                focus = self._sched_dial.canvas
            else:
                self._hide(self._scheduled_view)
                self._hide(self._badge)
                self._show(self._setup_view)
                self._apply_action_theme(self._action)
                focus = self._dial.canvas
            self._tick_countdown()
            self._fit_height()
            focus.focus_set()
        except tk.TclError:
            pass

    def _render_scheduled(self, vm: _ViewModel) -> None:
        """예약됨 화면을 view model 대로 그린다.

        Args:
            vm: _view_model 결과
        """
        sch = self._scheduler
        if vm.accent_action != self._theme_action:
            self._apply_action_theme(vm.accent_action)
        tokens = action_tokens(self._theme, vm.accent_action)
        _, big, unit = _dial_text(vm.remaining, vm.accent_action)
        time_text = ""
        if vm.fixed_at is not None:
            time_text = f"{vm.fixed_at:%H:%M}"
            unit = f"{unit} · "
        lead = None
        if vm.target == "shutdown" and not sch._linked_done:
            lead = sch._monitor_lead_sec
        muted = self._tok("textMuted")
        self._sched_dial.set_state(
            progress=_ring_progress(vm.remaining),
            segment=None if vm.paused else _monitor_segment(vm.remaining, lead),
            color=muted if vm.paused else tokens["accent"],
            label=vm.label,
            big=big,
            unit=unit,
            time=time_text,
            time_color=tokens["accentText"],
        )
        pause_text = "▶" if vm.paused else "II"
        if self._btn_pause_resume.cget("text") != pause_text:
            self._btn_pause_resume.configure(text=pause_text)
        if vm.show_pause:
            self._btn_pause_resume.place(relx=1.0, x=-10, y=10, anchor="ne")
        else:
            self._btn_pause_resume.place_forget()

        now = datetime.now()
        lines = sch._monitor_status_lines(now) if sch is not None else []
        if vm.solo_extra and lines:
            # 단독 예약 줄은 ✕ 가 붙은 별도 줄로 보여 줌
            lines = lines[:-1]
        title = lines[0] if lines else ""
        sub = "\n".join(lines[1:])
        linked_due = sch._linked_monitor.due if vm.target == "shutdown" else None
        if not sub and linked_due is not None and sch._monitor_lead_sec:
            sub = (
                f"{_action_label(vm.accent_action)} "
                f"{_format_duration(sch._monitor_lead_sec)} 전 · {linked_due:%H:%M}에 끔"
            )
        if self._sched_monitor_title.cget("text") != title:
            self._sched_monitor_title.configure(text=title)
        if self._sched_monitor_sub.cget("text") != sub:
            self._sched_monitor_sub.configure(text=sub)
        if vm.solo_extra:
            solo_due = sch._solo_monitor.due
            solo_text = f"모니터만 {solo_due:%H:%M}에 끔 (PC 켜 둠)"
            if self._sched_solo_label.cget("text") != solo_text:
                self._sched_solo_label.configure(text=solo_text)
        layout_key = (bool(lines), bool(sub), vm.solo_extra)
        if layout_key != self._sched_layout_key:
            self._sched_layout_key = layout_key
            if lines or vm.solo_extra:
                self._show(self._sched_monitor_card)
            else:
                self._hide(self._sched_monitor_card)
            if title:
                self._show(self._sched_monitor_title)
            else:
                self._hide(self._sched_monitor_title)
            if sub:
                self._show(self._sched_monitor_sub)
            else:
                self._hide(self._sched_monitor_sub)
            if vm.solo_extra:
                self._show(self._sched_solo_row)
            else:
                self._hide(self._sched_solo_row)
            self._fit_height()

    def _set_action(self, action: str) -> None:
        """동작(종료/재시작/모니터) 변경 — 강조색·헤더·버튼 글자를 바꾼다.

        Args:
            action: ACTION_SHUTDOWN / ACTION_RESTART / ACTION_MONITOR_OFF
        """
        self._action = action
        self._refresh_action_btns()
        self._apply_action_theme(action)
        self._update_preview()
        self._refresh_monitor_row()

    def _set_unit(self, unit: str) -> None:
        """증감 단위 선택 — 값은 그대로 두고 가운데 입력 표시만 새 단위로 바꾼다.

        Args:
            unit: UNIT_HOUR / UNIT_MINUTE / UNIT_SECOND
        """
        self._unit = unit
        self._refresh_unit_btns()
        self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _reset_input(self) -> None:
        """입력값과 _total_seconds를 0으로 초기화."""
        self._set_total(0)
        self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _sync_value_text(self) -> None:
        """가운데 숫자 입력 글자를 _total_seconds ÷ 단위로 맞춘다 (감시 콜백은 막음)."""
        self._updating = True
        self.var_value.set(str(self._total_seconds // self._UNIT_TO_SEC[self._unit]))
        self._updating = False

    def _step(self, sign: int) -> None:
        """선택한 단위만큼 더하거나 뺀다. 최소 0.

        Args:
            sign: +1 또는 -1 (칸 수를 넘겨도 됨)
        """
        unit_sec = self._UNIT_TO_SEC[self._unit]
        self._set_total(self._total_seconds + sign * unit_sec)
        self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _refresh_action_btns(self) -> None:
        """동작 탭 선택 상태 갱신 (선택 칸 = 탭 배경 / 탭 글자)."""
        for val, btn in self._action_btns.items():
            if val == self._action:
                tokens = action_tokens(self._theme, val)
                btn.configure(
                    fg_color=tokens["tabFill"],
                    hover_color=tokens["tabFill"],
                    text_color=tokens["onTab"],
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=self._tok("border"),
                    text_color=self._tok("textMuted"),
                )

    def _refresh_unit_btns(self) -> None:
        """단위 선택 상태 갱신 (선택 칸 = 모니터 탭 배경 / 글자)."""
        for val, btn in self._unit_btns.items():
            if val == self._unit:
                btn.configure(
                    fg_color=self._tok("monitor.tabFill"),
                    hover_color=self._tok("monitor.tabFill"),
                    text_color=self._tok("monitor.onTab"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=self._tok("border"),
                    text_color=self._tok("textMuted"),
                )

    # ------------------------------------------------------------------ #
    # 입력값 처리                                                          #
    # ------------------------------------------------------------------ #

    def _on_value_change(self, *_: object) -> None:
        """var_value 변경 시 _total_seconds 동기화 및 표시 갱신."""
        if self._updating:
            return
        val = self._parse_int_or_zero(self.var_value.get()) or 0
        if self._set_total(val * self._UNIT_TO_SEC[self._unit]):
            # 넘긴 값은 상한으로 고쳐 보여 줌
            self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _set_total(self, seconds: int) -> bool:
        """_total_seconds 를 0 ~ MAX_SCHEDULE_SECONDS 로 잘라 넣는다.

        Args:
            seconds: 넣으려는 초

        Returns:
            상한에 걸려 잘렸으면 True
        """
        clipped = seconds > MAX_SCHEDULE_SECONDS
        self._total_seconds = min(max(seconds, 0), MAX_SCHEDULE_SECONDS)
        self._clipped = clipped
        return clipped

    def _update_preview(self) -> None:
        """예약 버튼 글자("HH:MM {동작} 예약")와 다이얼 표시를 갱신한다."""
        self._preview_var.set(
            _confirm_label(self._total_seconds, self._action, datetime.now())
        )
        self._render_setup_dial()

    def _render_setup_dial(self) -> None:
        """설정 화면 링·가운데 글자를 _total_seconds 기준으로 그린다."""
        if not self._views_built:
            return
        try:
            total = self._total_seconds
            label, big, unit = _dial_text(total, self._action)
            if self._clipped:
                label = self._MAX_NOTICE
            time_text = ""
            if total > 0:
                time_text = f"{datetime.now() + timedelta(seconds=total):%H:%M}"
                unit = f"{unit} · "
            lead = self._selected_monitor_lead_sec()
            tokens = action_tokens(self._theme, self._action)
            self._dial.set_state(
                progress=_ring_progress(total),
                segment=_monitor_segment(total, lead),
                color=tokens["accent"],
                label=label,
                big=big,
                unit=unit,
                time=time_text,
                time_color=tokens["accentText"],
            )
        except tk.TclError:
            pass

    def _quick_add(self, add_seconds: int) -> None:
        """총 초에 add_seconds를 더하고 현재 단위로 입력 필드 표시 갱신.

        Args:
            add_seconds: 추가할 초
        """
        self._set_total(self._total_seconds + add_seconds)
        self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _on_ring_drag(self, minutes: int, is_start: bool) -> None:
        """링 클릭·드래그 결과를 _total_seconds 에 반영한다 (0~120분, 1분 단위).

        Args:
            minutes: 각도로 구한 분
            is_start: 누른 순간이면 True (넘김 고정은 드래그 중에만 적용)
        """
        if not is_start:
            prev = min(self._total_seconds // 60, RING_MAX_MINUTES)
            minutes = _clamp_wrap(prev, minutes)
        self._set_total(minutes * 60)
        self._sync_value_text()
        self._update_preview()
        self._refresh_monitor_row()

    def _on_ring_wheel(self, steps: int) -> None:
        """링 위 휠 — 선택한 단위만큼 칸 수대로 더하고 뺀다.

        Args:
            steps: 칸 수 (위로 굴리면 양수)
        """
        self._step(steps)

    def _open_value_entry(self) -> None:
        """다이얼 가운데를 누르면 숫자 직접 입력란을 띄운다 (값 × 단위)."""
        try:
            self._sync_value_text()
            self._entry.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            self._entry.focus_set()
            self._entry.select_range(0, tk.END)
        except tk.TclError:
            pass

    def _close_value_entry(self) -> None:
        """숫자 직접 입력란을 숨긴다 (입력값은 이미 반영됨)."""
        try:
            self._entry.place_forget()
        except tk.TclError:
            pass

    def _on_lead_change(self, *_: object) -> None:
        """N(분 전) 입력 변경 시 _monitor_lead_min 동기화."""
        if self._lead_updating:
            return
        self._monitor_lead_min = (
            self._parse_int_or_zero(self.var_monitor_lead.get()) or 0
        )
        self._refresh_monitor_row()

    def _on_chip(self, minutes: int) -> None:
        """칩을 누르면 기준값과 직접 입력 글자를 함께 바꾼다.

        Args:
            minutes: 칩 값(분 전, 0 = 직전)
        """
        self._monitor_lead_min = minutes
        self._lead_updating = True
        self.var_monitor_lead.set(str(minutes))
        self._lead_updating = False
        self._refresh_monitor_row()

    def _bump_lead(self, sign: int) -> None:
        """직접 입력 N 을 ±1 한다 (0~MONITOR_LEAD_MAX_MIN).

        Args:
            sign: +1 또는 -1
        """
        value = min(max(self._monitor_lead_min + sign, 0), MONITOR_LEAD_MAX_MIN)
        self.var_monitor_lead.set(str(value))

    @staticmethod
    def _validate_lead_input(new_value: str) -> bool:
        """N 입력 검증: 빈 문자열 또는 0~MONITOR_LEAD_MAX_MIN 정수만 허용.

        Args:
            new_value: 변경 후 값 (%P)

        Returns:
            허용이면 True
        """
        if new_value == "":
            return True
        # int() 는 전각 숫자·부호·공백도 받아서 ASCII 숫자만 따로 거름
        if not (new_value.isascii() and new_value.isdigit()):
            return False
        return int(new_value) <= MONITOR_LEAD_MAX_MIN

    @staticmethod
    def _monitor_plan_error(
        action: str, monitor_on: bool, lead_min: int, total_seconds: int
    ) -> Optional[str]:
        """종료 N분 전 모니터 끄기 입력의 오류를 찾는다 (UI 를 다루지 않음).

        Args:
            action: 선택한 동작
            monitor_on: "모니터 끄기" 스위치 상태
            lead_min: N(분)
            total_seconds: 종료까지 초

        Returns:
            오류 문구. 문제가 없으면 None.
        """
        if action != ACTION_SHUTDOWN or not monitor_on:
            return None
        if lead_min < 0 or lead_min > MONITOR_LEAD_MAX_MIN:
            return f"0~{MONITOR_LEAD_MAX_MIN} 사이로 입력하세요."
        # 0초는 즉시 실행이라 막지 않고 종료 직전 끄기로 넘김 (설계안 p05 경고 조건)
        if lead_min > 0 and total_seconds > 0 and lead_min * 60 >= total_seconds:
            return "종료까지 남은 시간보다 짧게 입력하세요."
        return None

    def _selected_monitor_lead_sec(self) -> Optional[int]:
        """콜백에 넘길 monitor_lead_sec 를 구한다.

        Returns:
            스위치가 꺼졌거나 종료가 아니면 None, 켜져 있으면 N*60.
            0초(즉시 실행)면 N 과 상관없이 0(종료 직전).
        """
        if self._action != ACTION_SHUTDOWN or not self._monitor_on.get():
            return None
        if self._total_seconds <= 0:
            return 0
        return self._monitor_lead_min * 60

    def _monitor_desc_parts(self, now: datetime) -> tuple[str, str, str]:
        """모니터 카드 설명 줄을 (앞 글자, 강조 시각, 뒤 글자)로 만든다.

        Args:
            now: 기준 시각

        Returns:
            세 부분 글자
        """
        if self._action == ACTION_RESTART:
            return "재시작에는 적용하지 않습니다", "", ""
        if not self._monitor_on.get():
            return "종료 전에 화면을 미리 끕니다", "", ""
        if self._total_seconds <= 0:
            return "즉시 실행이라 종료 직전에 화면 끔", "", ""
        lead = self._monitor_lead_min
        if lead == 0:
            return "종료 직전에 화면 끔", "", ""
        off_at = now + timedelta(seconds=self._total_seconds - lead * 60)
        return (
            f"종료 {_format_duration(lead * 60)} 전 · ",
            f"{off_at:%H:%M}",
            "에 화면 끔",
        )

    def _refresh_monitor_row(self) -> None:
        """동작·입력에 맞춰 모니터 카드의 표시·활성·설명·경고와 예약 버튼을 갱신."""
        if not self._views_built:
            return
        try:
            action = self._action
            hidden = action == ACTION_MONITOR_OFF
            on = bool(self._monitor_on.get())
            restart = action == ACTION_RESTART
            lead = self._monitor_lead_min
            error = self._monitor_plan_error(action, on, lead, self._total_seconds)
            # 1초 갱신마다 위젯을 다시 그리지 않게 모양이 바뀔 때만 configure 함
            style_key = (action, on, lead, error)
            if style_key != self._style_key:
                self._style_key = style_key
                if hidden:
                    self._hide(self._options_label)
                    self._hide(self._monitor_card)
                else:
                    self._show(self._options_label)
                    self._show(self._monitor_card)
                if on:
                    self._show(self._monitor_expand)
                else:
                    self._hide(self._monitor_expand)
                if error:
                    in_range = 0 <= lead <= MONITOR_LEAD_MAX_MIN
                    self._monitor_warn.configure(
                        text=self._MONITOR_WARN_TEXT if in_range else error
                    )
                    self._show(self._monitor_warn)
                else:
                    self._hide(self._monitor_warn)
                self._monitor_switch.configure(
                    state="disabled" if restart else "normal"
                )
                self._lead_entry.configure(
                    state="normal" if on and not restart else "disabled"
                )
                self._monitor_title.configure(
                    text_color=self._tok("textMuted" if restart else "text")
                )
                self._refresh_chips(disabled=restart)
                self._set_confirm_enabled(error is None)
            parts = self._monitor_desc_parts(datetime.now())
            for lbl, text in zip(self._monitor_desc, parts):
                if lbl.cget("text") != text:
                    lbl.configure(text=text)
            layout_key = (hidden, on, bool(error))
            if layout_key != self._layout_key:
                self._layout_key = layout_key
                self._fit_height()
            self._render_setup_dial()
        except tk.TclError:
            pass

    def _refresh_chips(self, disabled: bool) -> None:
        """값이 칩 중 하나면 그 칩을 강조하고, 아니면 강조를 모두 끈다.

        Args:
            disabled: 재시작 탭처럼 카드를 비활성으로 보일지
        """
        for minutes, btn in self._chip_btns.items():
            selected = minutes == self._monitor_lead_min and not disabled
            if selected:
                btn.configure(
                    fg_color=self._tok(("mix", "monitor.accent", "surface", 0.16)),
                    hover_color=self._tok(("mix", "monitor.accent", "surface", 0.24)),
                    border_color=self._tok("monitor.accent"),
                    text_color=self._tok("monitor.accentText"),
                )
            else:
                btn.configure(
                    fg_color=self._tok("surfaceAlt"),
                    hover_color=self._tok("border"),
                    border_color=self._tok("surfaceAlt"),
                    text_color=self._tok("textMuted" if disabled else "text"),
                )
            btn.configure(
                state="disabled" if disabled else "normal",
                text_color_disabled=self._tok("textMuted"),
            )

    def _set_confirm_enabled(self, enabled: bool) -> None:
        """예약 버튼을 켜거나 끈다 (모니터 시간이 예약 시간 이상이면 끔).

        Args:
            enabled: 누를 수 있게 할지
        """
        self._confirm_enabled = enabled
        tokens = action_tokens(self._theme, self._action)
        fill = tokens["buttonFill"]
        self._btn_confirm.configure(
            state="normal" if enabled else "disabled",
            fg_color=fill if enabled else self._tok("surfaceAlt"),
            hover_color=mix(tokens["onButton"], fill, 0.12),
            text_color=tokens["onButton"],
            text_color_disabled=self._tok("textMuted"),
        )

    @staticmethod
    def _validate_int_input(new_value: str) -> bool:
        """입력 검증: 빈 문자열 또는 0 이상의 정수만 허용.

        Args:
            new_value: 변경 후 값 (%P)

        Returns:
            허용이면 True
        """
        if new_value == "":
            return True
        try:
            return int(new_value) >= 0
        except ValueError:
            return False

    @staticmethod
    def _parse_int_or_zero(value: str) -> Optional[int]:
        """입력 문자열을 정수로 변환. 빈 문자열은 0으로 간주.

        Args:
            value: 입력 문자열

        Returns:
            정수값, 숫자가 아니면 None
        """
        s = (value or "").strip()
        if s == "":
            return 0
        try:
            return int(s)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # 키보드                                                               #
    # ------------------------------------------------------------------ #

    def _focus_widget(self) -> Any:
        """지금 키보드 포커스를 가진 위젯.

        Returns:
            위젯. 구할 수 없으면 None.
        """
        try:
            return self.top.focus_get()
        except (KeyError, tk.TclError):
            return None

    def _on_key(self, event: Any) -> Optional[str]:
        """창 단축키 — Enter 예약 · Esc 닫기 · ↑/↓ 단위만큼 ± · 1/2/3 탭 · Space 일시정지.

        Args:
            event: 키 이벤트 (keysym 사용)

        Returns:
            처리했으면 "break"
        """
        key = getattr(event, "keysym", "")
        focus = self._focus_widget()
        in_entry = isinstance(focus, tk.Entry)
        if key in ("Return", "KP_Enter"):
            if self._view == "setup":
                self._on_confirm()
            return "break"
        if key == "Escape":
            self._on_cancel()
            return "break"
        if self._view != "setup":
            if key == "space" and not in_entry:
                if _view_model(self._scheduler).show_pause:
                    self._on_pause_resume()
                return "break"
            return None
        if key in ("Up", "Down"):
            sign = 1 if key == "Up" else -1
            if focus is not None and focus is getattr(self._lead_entry, "_entry", None):
                self._bump_lead(sign)
            else:
                self._step(sign)
            return "break"
        if key in self._TAB_KEYS and not in_entry:
            self._set_action(self._TAB_KEYS[key])
            return "break"
        return None

    def _on_confirm(self) -> None:
        """확인 처리 — 0초면 즉시 실행 확인창, 아니면 callback 뒤 예약됨 화면으로 바꾼다.

        모니터 끄기 입력에 오류가 있으면 예약하지 않는다.
        """
        total = self._total_seconds
        error = self._monitor_plan_error(
            self._action,
            bool(self._monitor_on.get()),
            self._monitor_lead_min,
            total,
        )
        if error:
            self._refresh_monitor_row()
            return
        if total == 0:
            self._show_immediate_confirm()
            return
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        action = self._action
        lead = self._selected_monitor_lead_sec()
        self.callback(action, h, m, s, lead)
        # 예약 뒤 창을 닫지 않고 예약됨 화면으로 바꿈 (모니터 단독 예약 포함)
        self._show_view("scheduled")

    def _show_immediate_confirm(self) -> None:
        """즉시 실행 확인 다이얼로그 (토큰 색)."""
        ff = self._ff
        is_monitor = self._action == ACTION_MONITOR_OFF
        action_label = _action_label(self._action)
        tokens = action_tokens(self._theme, self._action)
        if is_monitor:
            body = (
                "지금 바로 모니터를 끕니다.\n"
                "다시 켜려면 모니터 전원 버튼을\n눌러야 합니다."
            )
        else:
            body = f"지금 바로 시스템을 {action_label}합니다.\n계속하시겠습니까?"

        dlg = ctk.CTkToplevel(self.top)
        dlg.title("")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self._tok("background"))
        dlg.transient(self.top)
        dlg.grab_set()

        # 중앙 배치
        dlg.update_idletasks()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        # 모니터 안내는 3줄이라 높이를 늘림
        w, h = 320, 200 if is_monitor else 180
        dlg.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # topmost 잠깐 켜고 끄기
        dlg.attributes("-topmost", True)
        dlg.after(100, lambda: dlg.attributes("-topmost", False))
        dlg.after(200, lambda: _apply_caption_color(dlg, self._tok("background")))

        bg = self._tok("background")
        main = ctk.CTkFrame(dlg, fg_color=bg, corner_radius=0)
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1)

        # 경고 아이콘 + 메시지
        top_row = ctk.CTkFrame(main, fg_color=bg, corner_radius=0)
        top_row.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        top_row.grid_columnconfigure(1, weight=1)

        badge = ctk.CTkFrame(
            top_row,
            width=40,
            height=40,
            fg_color=mix(tokens["accent"], self._tok("surface"), 0.16),
            corner_radius=SHAPE["radius"]["control"],
        )
        badge.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        badge.grid_propagate(False)
        ctk.CTkLabel(
            badge,
            text="⚠",
            font=(UI_FONT_SYMBOL, 18, "bold"),
            text_color=tokens["accent"],
        ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        ctk.CTkLabel(
            top_row,
            text=f"즉시 {action_label}",
            font=(ff, 14, "bold"),
            text_color=self._tok("text"),
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            top_row,
            text=body,
            font=(ff, 12),
            text_color=self._tok("textMuted"),
            anchor="w",
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        # 버튼
        footer = ctk.CTkFrame(main, fg_color=bg, corner_radius=0)
        footer.grid(row=1, column=0, sticky="ew", padx=24, pady=(18, 20))
        footer.grid_columnconfigure((0, 1), weight=1)

        def _cancel() -> None:
            dlg.destroy()

        def _execute() -> None:
            dlg.destroy()
            action = self._action
            lead = self._selected_monitor_lead_sec()
            self._close(destroy=True)
            self.callback(action, 0, 0, 0, lead)

        ctk.CTkButton(
            footer,
            text="취소",
            font=(ff, 13, "bold"),
            height=40,
            corner_radius=SHAPE["radius"]["control"],
            fg_color=self._tok("surfaceAlt"),
            hover_color=self._tok("border"),
            text_color=self._tok("text"),
            command=_cancel,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            footer,
            text=f"즉시 {action_label}",
            font=(ff, 13, "bold"),
            height=40,
            corner_radius=SHAPE["radius"]["control"],
            fg_color=tokens["buttonFill"],
            hover_color=mix(tokens["onButton"], tokens["buttonFill"], 0.12),
            text_color=tokens["onButton"],
            command=_execute,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        dlg.bind("<Return>", lambda e: _execute())
        dlg.bind("<Escape>", lambda e: _cancel())

    def _on_cancel_schedule(self) -> None:
        """예약 취소 버튼 처리 — 예약 취소 후 화면을 갱신하고 창은 유지.

        종료 예약이 있으면 종료(딸린 모니터 끄기 포함)를, 단독 모니터 예약만 있으면
        그것을 취소한다. 남은 예약이 있으면 계속 보인다.
        """
        sch = self._scheduler
        if sch is not None:
            if sch.is_active:
                sch.cancel_shutdown()
            elif sch._solo_monitor.armed:
                sch.cancel_monitor_only()
        self._tick_countdown()

    def _on_extend(self) -> None:
        """+10분 연장 버튼 처리 — 버튼 대상 예약을 늘리고 화면을 갱신."""
        if self._scheduler is not None:
            self._scheduler.extend_schedule(EXTEND_SECONDS)
        self._tick_countdown()

    def _on_cancel_solo(self) -> None:
        """종료와 함께 있는 단독 모니터 예약만 ✕ 로 취소한다."""
        if self._scheduler is not None:
            self._scheduler.cancel_monitor_only()
        self._tick_countdown()

    def _on_pause_resume(self) -> None:
        """II/▶ 일시정지·재개 버튼 처리 — 상태를 바꾸고 화면을 갱신."""
        if self._scheduler is None:
            return
        if self._scheduler.is_paused:
            self._scheduler.resume_shutdown()
        else:
            self._scheduler.pause_shutdown()
        self._tick_countdown()

    def _on_toggle_autostart(self) -> None:
        """자동 시작 스위치 처리 — 상태 전환 후 스위치 갱신 및 토스트 표시."""
        if is_autostart_enabled():
            ok, msg = disable_autostart()
            toast_type = "autostart_off"
        else:
            ok, msg = enable_autostart()
            toast_type = "autostart_on"
        self._refresh_autostart_btn()
        if self._scheduler is not None:
            self._scheduler._show_confirm_toast(msg, toast_type=toast_type)

    def _refresh_autostart_btn(self) -> None:
        """자동 시작 스위치를 실제 등록 상태로 맞춘다."""
        self._autostart_var.set(is_autostart_enabled())

    def _on_cancel(self) -> None:
        """닫기 / X버튼 / Esc 처리 — 창만 닫고 예약은 유지."""
        self._close(destroy=True)

    def _close(self, destroy: bool = True) -> None:
        """창 종료 처리 및 on_close 콜백 호출.

        Args:
            destroy: True면 tk 창도 파괴
        """
        if self._countdown_after_id is not None:
            try:
                self.top.after_cancel(self._countdown_after_id)
            except Exception:
                pass
            self._countdown_after_id = None
        if destroy:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                pass


class SettingsDialog(_ThemedWindow):
    """테마 설정 창. 고르는 즉시 적용·저장한다.

    Attributes:
        top (ctk.CTkToplevel): 설정 창
        _thumb_btns (dict): 테마 id 별 썸네일 버튼
        _follow_var (tk.BooleanVar): "시스템 테마 따라가기" 스위치
        _contrast_var (tk.BooleanVar): "가독성 보정" 스위치
    """

    _SWATCH_SIZE: tuple[int, int] = (120, 22)

    # tk 없이 만든 인스턴스(테스트)에서도 메서드가 화면을 건드리지 않게 하는 기본값
    _views_built: bool = False

    def __init__(
        self,
        parent: tk.Tk,
        scheduler: "ShutdownScheduler",
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        """초기화.

        Args:
            parent: tkinter 루트 윈도우
            scheduler: 설정·현재 테마를 가진 ShutdownScheduler 인스턴스
            on_close: 창이 닫힐 때 호출될 콜백
        """
        self._scheduler = scheduler
        self.on_close = on_close
        self._theme: dict[str, Any] = scheduler._theme
        self._tokens: dict[str, Any] = self._theme["tokens"]
        self._bindings: list[tuple[Any, dict[str, Any]]] = []
        self._thumb_btns: dict[str, ctk.CTkButton] = {}
        self._swatch_cache: dict[tuple[str, str], Any] = {}
        self._fitted_height: int = 0

        self.top = ctk.CTkToplevel(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        try:
            self._build_ui()
        except Exception:
            # 추적되지 않는 반쪽 창을 남기지 않음
            self.top.destroy()
            raise

    def _build_ui(self) -> None:
        """위에서 아래로 테마 그리드·따라가기·보정·닫기를 구성한다."""
        top = self.top
        top.title(SETTINGS_DIALOG_TITLE)
        top.resizable(False, False)
        top.configure(fg_color=self._tok("background"))
        self._ff = _pick_font_family(top, UI_FONT_FAMILIES_KO)
        self._follow_var = tk.BooleanVar(master=top, value=False)
        self._contrast_var = tk.BooleanVar(master=top, value=CONTRAST_FIX_DEFAULT)

        outer = SHAPE["spacing"]["outer"]
        gap = SHAPE["spacing"]["gap"]
        main = self._themed(
            ctk.CTkFrame, top, {"fg_color": "background"}, corner_radius=0
        )
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1)
        self._main = main

        self._themed(
            ctk.CTkLabel,
            main,
            {"text_color": "textMuted"},
            text="테마",
            font=self._font(11),
            height=14,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=outer, pady=(outer, 6))
        self._build_thumbnails(main).grid(row=1, column=0, sticky="ew", padx=outer)
        self._build_switch_row(
            main,
            "시스템 테마 따라가기",
            self._follow_var,
            lambda: self._change(SETTINGS_KEY_FOLLOW_SYSTEM, self._follow_var.get()),
        ).grid(row=2, column=0, sticky="ew", padx=outer, pady=(gap, 0))
        self._follow_menus = self._build_follow_menus(main)
        self._follow_menus.grid(row=3, column=0, sticky="ew", padx=outer, pady=(gap, 0))
        ScheduleDialog._hide(self._follow_menus)
        self._build_switch_row(
            main,
            "가독성 보정",
            self._contrast_var,
            lambda: self._change(SETTINGS_KEY_CONTRAST_FIX, self._contrast_var.get()),
        ).grid(row=4, column=0, sticky="ew", padx=outer, pady=(gap, 0))
        self._themed(
            ctk.CTkButton,
            main,
            {
                "fg_color": "surfaceAlt",
                "hover_color": "border",
                "text_color": "text",
            },
            text="닫기",
            font=self._font(13, bold=True),
            height=36,
            corner_radius=SHAPE["radius"]["control"],
            command=self._close,
        ).grid(row=5, column=0, sticky="ew", padx=outer, pady=(gap, outer))

        top.bind("<Escape>", lambda _event: self._close())

        self._views_built = True
        self.refresh_state()
        self._place_window()
        top.after(50, lambda: _bring_to_front(top))
        # ctk 가 타이틀바를 다크로 바꾸며 창을 잠깐 숨겼다 보이므로 그 뒤에 칠함
        top.after(200, lambda: _apply_caption_color(top, self._tok("background")))

    def _build_thumbnails(self, parent: Any) -> Any:
        """테마 썸네일 2열 × 5행을 만든다.

        Args:
            parent: 부모 위젯

        Returns:
            그리드 프레임
        """
        grid = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="thumb")
        chip = SHAPE["spacing"]["chip"]
        for index, (theme_id, theme) in enumerate(THEMES.items()):
            btn = self._themed(
                ctk.CTkButton,
                grid,
                {
                    "fg_color": "surface",
                    "hover_color": "surfaceAlt",
                    "text_color": "text",
                    "text_color_disabled": "textMuted",
                },
                text=theme["name"],
                # 나중에 configure 로 넣으면 ctk 가 그림 라벨 배경을 칠하지 않음
                image=self._swatch(theme_id),
                compound="top",
                font=self._font(12),
                width=40,
                height=56,
                corner_radius=SHAPE["radius"]["chip"],
                command=lambda tid=theme_id: self._change(SETTINGS_KEY_THEME, tid),
            )
            row, column = divmod(index, 2)
            btn.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=(0, chip // 2) if column == 0 else (chip // 2, 0),
                pady=(0, 0) if row == 0 else (chip, 0),
            )
            self._thumb_btns[theme_id] = btn
        return grid

    def _build_switch_row(
        self, parent: Any, text: str, variable: tk.BooleanVar, command: Callable
    ) -> Any:
        """글자 + 스위치 한 줄을 만든다.

        Args:
            parent: 부모 위젯
            text: 글자
            variable: 스위치 변수
            command: 눌렀을 때 콜백

        Returns:
            줄 프레임
        """
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        row.grid_columnconfigure(0, weight=1)
        self._themed(
            ctk.CTkLabel,
            row,
            {"text_color": "text"},
            text=text,
            font=self._font(13),
            height=20,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._themed(
            ctk.CTkSwitch,
            row,
            {
                "progress_color": "success",
                "fg_color": "switchOff",
                "button_color": "knob",
                "button_hover_color": "knob",
            },
            text="",
            width=34,
            switch_width=34,
            switch_height=19,
            variable=variable,
            onvalue=True,
            offvalue=False,
            command=command,
        ).grid(row=0, column=1, sticky="e")
        return row

    def _build_follow_menus(self, parent: Any) -> Any:
        """다크/라이트 테마 드롭다운을 만든다 (따라가기를 켰을 때만 표시).

        Args:
            parent: 부모 위젯

        Returns:
            드롭다운 프레임
        """
        wrap = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        wrap.grid_columnconfigure(0, weight=1)
        self._dark_menu = self._build_theme_menu(
            wrap, 0, "다크 테마", SETTINGS_KEY_DARK_THEME, "dark"
        )
        self._light_menu = self._build_theme_menu(
            wrap, 1, "라이트 테마", SETTINGS_KEY_LIGHT_THEME, "light"
        )
        return wrap

    def _build_theme_menu(
        self, parent: Any, row: int, text: str, key: str, mode: str
    ) -> Any:
        """글자 + 테마 드롭다운 한 줄을 만든다. 화면에는 이름, 저장은 ID 로 한다.

        Args:
            parent: 부모 위젯
            row: grid 행
            text: 글자
            key: 저장할 설정 키
            mode: 목록에 넣을 테마 모드

        Returns:
            CTkOptionMenu
        """
        ids_by_name = {THEMES[tid]["name"]: tid for tid in theme_ids(mode)}
        pady = (0, 0) if row == 0 else (SHAPE["spacing"]["chip"], 0)
        self._themed(
            ctk.CTkLabel,
            parent,
            {"text_color": "textMuted"},
            text=text,
            font=self._font(12),
            height=28,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=pady)
        menu = self._themed(
            ctk.CTkOptionMenu,
            parent,
            {
                "fg_color": "surfaceAlt",
                "button_color": "border",
                "button_hover_color": "border",
                "text_color": "text",
                "text_color_disabled": "textMuted",
                "dropdown_fg_color": "surface",
                "dropdown_hover_color": "surfaceAlt",
                "dropdown_text_color": "text",
            },
            values=list(ids_by_name),
            font=self._font(12),
            dropdown_font=self._font(12),
            width=180,
            height=28,
            corner_radius=SHAPE["radius"]["chip"],
            dynamic_resizing=False,
            command=lambda name: self._change(key, ids_by_name[name]),
        )
        menu.grid(row=row, column=1, sticky="e", pady=pady)
        return menu

    def _swatch(self, theme_id: str) -> Optional[Any]:
        """테마의 스와치 CTkImage 를 돌려준다 (같은 테두리 색은 재사용).

        Args:
            theme_id: 테마 id

        Returns:
            CTkImage. PIL 이 없으면 None.
        """
        border = self._tok("border")
        key = (theme_id, border)
        if key not in self._swatch_cache:
            width, height = self._SWATCH_SIZE
            img = _make_swatch_image(
                swatch_colors(get_theme(theme_id)), border, width * 2, height * 2
            )
            self._swatch_cache[key] = (
                None if img is None else ctk.CTkImage(img, img, size=self._SWATCH_SIZE)
            )
        return self._swatch_cache[key]

    def _place_window(self) -> None:
        """높이를 내용에 맞추고 예약 창 옆에, 없거나 안 맞으면 화면 가운데에 놓는다."""
        self._fit_height()
        try:
            scale = self._window_scaling()
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            # geometry 의 위치는 배율을 곱하지 않으므로 실제 px 로 계산함
            width = round(DIALOG_WIDTH * scale)
            height = round(self._fitted_height * scale)
            position = None
            dialog = self._scheduler._active_dialog
            if dialog is not None and dialog.top.winfo_viewable():
                position = _beside_position(
                    dialog.top.winfo_x(),
                    dialog.top.winfo_y(),
                    dialog.top.winfo_width(),
                    width,
                    height,
                    sw,
                    sh,
                )
            x, y = position or ((sw - width) // 2, (sh - height) // 2)
            self.top.geometry(f"{DIALOG_WIDTH}x{self._fitted_height}+{x}+{y}")
        except tk.TclError:
            pass

    def _fit_height(self) -> None:
        """창 높이를 내용 reqheight 에 맞춘다."""
        try:
            self.top.update_idletasks()
            req = self._main.winfo_reqheight()
            height = math.ceil(req / self._window_scaling())
            if height != self._fitted_height:
                self._fitted_height = height
                self.top.geometry(f"{DIALOG_WIDTH}x{height}")
        except tk.TclError:
            pass

    def _change(self, key: str, value: Any) -> None:
        """설정 하나를 바꿔 달라고 ShutdownScheduler 에 넘긴다.

        Args:
            key: 설정 키
            value: 새 값
        """
        self._scheduler._update_theme_settings({key: value})

    def refresh_state(self) -> None:
        """스위치·썸네일·드롭다운을 현재 설정과 적용된 테마에 맞춘다."""
        if not self._views_built:
            return
        settings = self._scheduler._settings
        follow = settings.get(SETTINGS_KEY_FOLLOW_SYSTEM, FOLLOW_SYSTEM_DEFAULT)
        selected = self._scheduler._theme["id"]
        try:
            self._follow_var.set(follow)
            self._contrast_var.set(
                settings.get(SETTINGS_KEY_CONTRAST_FIX, CONTRAST_FIX_DEFAULT)
            )
            for theme_id, btn in self._thumb_btns.items():
                chosen = theme_id == selected
                # 따라가는 동안은 고를 수 없게 막되 지금 적용된 테마를 보여 줌
                btn.configure(
                    state="disabled" if follow else "normal",
                    border_width=2 if chosen else 1,
                    border_color=self._tok("monitor.accent" if chosen else "border"),
                )
                swatch = self._swatch(theme_id)
                if btn.cget("image") is not swatch:
                    btn.configure(image=swatch)
            dark = settings.get(SETTINGS_KEY_DARK_THEME, DEFAULT_DARK_THEME_ID)
            light = settings.get(SETTINGS_KEY_LIGHT_THEME, DEFAULT_LIGHT_THEME_ID)
            self._dark_menu.set(THEMES[dark]["name"])
            self._light_menu.set(THEMES[light]["name"])
            if follow:
                ScheduleDialog._show(self._follow_menus)
            else:
                ScheduleDialog._hide(self._follow_menus)
            self._fit_height()
        except tk.TclError:
            pass

    def apply_theme(self, theme: dict[str, Any]) -> None:
        """열린 창을 새 테마로 다시 칠한다 (메인 스레드 전용).

        Args:
            theme: 새 테마 사전
        """
        if not self._views_built:
            return
        self._theme, self._tokens = theme, theme["tokens"]
        try:
            self.top.configure(fg_color=self._tok("background"))
            self._repaint_bindings()
            self._sync_dropdown_colors()
            # 시스템 전환은 설정 변경 없이 오므로 선택 표시까지 여기서 맞춤
            self.refresh_state()
            _apply_caption_color(self.top, self._tok("background"))
            # 모드 전환 시 ctk 가 창을 숨겼다 보인 뒤 다시 칠함
            self.top.after(
                200, lambda: _apply_caption_color(self.top, self._tok("background"))
            )
        except tk.TclError:
            pass

    def _sync_dropdown_colors(self) -> None:
        """드롭다운 메뉴의 호버 글자까지 현재 토큰으로 맞춘다."""
        for option_menu in (self._dark_menu, self._light_menu):
            try:
                # ctk configure 는 activeforeground 를 바꾸지 않아 tk 메뉴에 직접 넣음
                tk.Menu.configure(
                    option_menu._dropdown_menu,
                    bg=self._tok("surface"),
                    fg=self._tok("text"),
                    activebackground=self._tok("surfaceAlt"),
                    activeforeground=self._tok("text"),
                )
            except (AttributeError, tk.TclError):
                # ctk 내부가 바뀌어도 호버 색만 포기하고 창은 살림
                pass

    def _close(self) -> None:
        """창을 닫고 on_close 를 부른다."""
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if self.on_close is not None:
            self.on_close()


class ShutdownScheduler:
    """시스템 종료 예약 애플리케이션 메인 클래스.

    트레이 아이콘, 글로벌 단축키, tkinter 다이얼로그를 통합 관리한다.
    pystray는 별도 daemon 스레드에서 실행되고, tkinter는 메인 스레드에서 실행된다.
    keyboard 라이브러리 콜백은 스레드 안전 큐를 통해 메인 스레드로 전달된다.

    Attributes:
        tray_icon (pystray.Icon): 트레이 아이콘 인스턴스
        scheduled_time (Optional[datetime]): 예약된 종료 시각
        is_active (bool): 예약 활성화 여부
        _queue (queue.Queue): 스레드간 이벤트 큐
        _tk_root (tk.Tk): tkinter 숨김 루트 윈도우
        _tooltip_timer (Optional[threading.Timer]): 툴팁 갱신 타이머
    """

    # 예약 상태 기본값 — 저장(_schedule_snapshot)이 __init__ 을 거치지 않은 인스턴스에서도 읽힘
    is_active: bool = False
    is_paused: bool = False
    scheduled_time: Optional[datetime] = None
    _pause_remaining: float = 0.0
    # 마지막으로 건 종료 Timer 의 초 — 복원 때 시계 이상 판정에 씀
    _armed_total: float = 0.0
    # 0초 즉시 실행 경로는 연장·저장 대상이 아님
    _shutdown_immediate: bool = False
    _solo_immediate: bool = False
    # 테스트가 object.__new__ 로 만드는 앱에서도 창 열기가 그대로 동작하게 하는 기본값
    _theme: dict[str, Any] = get_theme()
    _settings_dialog: Optional["SettingsDialog"] = None
    # ctk 생성자가 update() 를 돌려 만드는 중에 다시 불릴 수 있음
    _settings_opening: bool = False
    _system_light: Optional[bool] = None

    def __init__(self) -> None:
        """초기화."""
        self._queue: queue.Queue = queue.Queue()
        self.scheduled_time: Optional[datetime] = None
        self.is_active: bool = False
        self.scheduled_action: str = ACTION_SHUTDOWN
        self._tooltip_timer: Optional[threading.Timer] = None
        self._shutdown_timer: Optional[threading.Timer] = None
        self._active_dialog: Optional["ScheduleDialog"] = None
        self._toast_label: Optional[tk.Label] = None
        self._toast_window: Optional[tk.Toplevel] = None
        self._toast_after_id: Optional[str] = None
        # schedule|cancel|autostart_on|autostart_off|monitor_off_on|monitor_off_off|
        # monitor_schedule|monitor_cancel|info
        self._toast_type: str = ""
        self._warning_timers: list[threading.Timer] = []
        self.is_paused: bool = False
        self._pause_remaining: float = 0.0  # 일시정지 시점의 남은 초
        self._settings: dict[str, Any] = load_settings()
        self._system_light = _read_system_light()
        self._apply_theme_settings()
        # None 이면 끄지 않음, 0 이면 종료 직전, 0 보다 크면 종료 N초 전
        self._monitor_lead_sec: Optional[int] = None
        self._linked_monitor = _MonitorOffTimer("linked", self._queue)
        self._solo_monitor = _MonitorOffTimer("solo", self._queue)
        self._linked_done: bool = False

        # tkinter 루트 (숨김)
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.title("ShutdownScheduler")

        self.tray_icon: Optional[pystray.Icon] = None

    def _hex_to_rgb(self, hex_color: str) -> tuple[int, int, int]:
        """HEX 색상을 RGB 튜플로 변환.

        Args:
            hex_color: '#RRGGBB' 형식 문자열

        Returns:
            (R, G, B) 튜플
        """
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _create_tray_icon_image(self, color: str) -> "Image.Image":
        """트레이 아이콘 이미지 생성 (전원 버튼 모양).

        Args:
            color: '#RRGGBB' 형식 배경 색상

        Returns:
            PIL Image 객체
        """
        w, h = TRAY_ICON_SIZE
        if PIL_AVAILABLE:
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            r, g, b = self._hex_to_rgb(color)

            margin = 4
            draw.ellipse(
                [margin, margin, w - margin, h - margin],
                fill=(r, g, b, 255),
            )

            symbol_color = (255, 255, 255, 255)
            inner_margin = margin + max(8, w // 6)
            sx0, sy0 = inner_margin, inner_margin
            sx1, sy1 = w - inner_margin, h - inner_margin
            arc_width = max(3, w // 12)

            draw.arc(
                [sx0, sy0, sx1, sy1],
                start=300,
                end=240,
                fill=symbol_color,
                width=arc_width,
            )

            cx = w // 2
            line_top = sy0 - max(2, w // 24)
            line_bottom = (sy0 + sy1) // 2 - max(2, w // 16)
            draw.line(
                [(cx, line_top), (cx, line_bottom)],
                fill=symbol_color,
                width=arc_width,
            )

            return img
        else:
            import struct
            import zlib

            def create_minimal_png(r: int, g: int, b: int) -> bytes:
                def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
                    c = chunk_type + data
                    return (
                        struct.pack(">I", len(data))
                        + c
                        + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
                    )

                ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
                raw = b""
                for _ in range(16):
                    row = b"\x00" + bytes([r, g, b] * 16)
                    raw += row
                idat = zlib.compress(raw)
                return (
                    b"\x89PNG\r\n\x1a\n"
                    + png_chunk(b"IHDR", ihdr)
                    + png_chunk(b"IDAT", idat)
                    + png_chunk(b"IEND", b"")
                )

            import io

            rgb = self._hex_to_rgb(color)
            png_data = create_minimal_png(*rgb)
            from PIL import Image as PILImage

            return PILImage.open(io.BytesIO(png_data))

    def _build_tray_menu(self) -> pystray.Menu:
        """트레이 우클릭 메뉴 생성.

        Returns:
            pystray.Menu 인스턴스
        """
        return pystray.Menu(
            pystray.MenuItem(
                "종료 예약", lambda icon, item: self._queue.put("open_dialog")
            ),
            pystray.MenuItem("예약 취소", lambda icon, item: self._queue.put("cancel")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Windows 시작 시 자동 실행",
                lambda icon, item: self._queue.put("toggle_autostart"),
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.MenuItem(
                "종료 시 모니터 끄기",
                lambda icon, item: self._queue.put("toggle_monitor_off"),
                checked=lambda item: self._settings[SETTINGS_KEY_MONITOR_OFF],
            ),
            pystray.MenuItem(
                "테마 설정", lambda icon, item: self._queue.put("open_settings")
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "프로그램 종료", lambda icon, item: self._queue.put("exit")
            ),
        )

    def _start_tray(self) -> None:
        """트레이 아이콘 시작 (별도 스레드에서 실행)."""
        img = self._create_tray_icon_image(TRAY_ICON_COLOR_IDLE)
        self.tray_icon = pystray.Icon(
            "ShutdownScheduler",
            img,
            TRAY_TOOLTIP_IDLE,
            self._build_tray_menu(),
        )
        self.tray_icon.run()

    @staticmethod
    def _is_left_alt_only() -> bool:
        """Windows API로 left alt(VK_LMENU=0xA4)만 눌렸는지 확인.

        GetAsyncKeyState 최상위 비트가 세트되면 해당 키가 눌린 상태.
        right alt(한/영, VK_RMENU=0xA5)가 눌려 있으면 False를 반환한다.

        Returns:
            left alt만 눌린 상태면 True
        """
        try:
            import ctypes

            VK_LMENU = 0xA4
            VK_RMENU = 0xA5
            left_down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
            right_down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
            return left_down and not right_down
        except Exception:
            return False

    def _register_hotkeys(self) -> None:
        """글로벌 단축키 등록.

        keyboard.hook으로 raw 키 이벤트를 감지하고,
        GetAsyncKeyState로 left alt 여부를 확인해 우측 Alt(한/영 키)를 무시한다.
        """

        def _on_key_event(event: keyboard.KeyboardEvent) -> None:
            if event.event_type != keyboard.KEY_DOWN:
                return

            name = (event.name or "").lower()
            if name not in ("q", "s"):
                return

            if not self._is_left_alt_only():
                return

            if name == "q":
                self._queue.put("open_dialog")
            else:
                self._queue.put("cancel")

        try:
            keyboard.hook(_on_key_event)
        except Exception as e:
            print(f"단축키 등록 실패: {e}", file=sys.stderr)

    def _process_queue(self) -> None:
        """큐에서 이벤트를 꺼내 처리 (메인 스레드에서 50ms 주기 실행).

        같은 poll 주기에 "open_dialog"가 여러 번 쌓여도
        실제 처리는 최대 1회만 수행한다.
        처리기 하나가 예외를 내도 남은 이벤트를 계속 처리하고 다음 폴링을 예약한다.
        "exit" 처리가 끝나면 루트가 destroy 되므로 다음 폴링을 예약하지 않는다.
        """
        open_dialog_requested = False
        exited = False
        try:
            while True:
                try:
                    event = self._queue.get_nowait()
                except queue.Empty:
                    break
                if event == "open_dialog":
                    open_dialog_requested = True
                    continue
                try:
                    self._dispatch_queue_event(event)
                except Exception as e:
                    print(f"큐 이벤트 처리 실패 ({event}): {e!r}", file=sys.stderr)
                    continue
                if event == "exit":
                    exited = True
                    return
            if open_dialog_requested:
                try:
                    self._open_schedule_dialog()
                except Exception as e:
                    print(f"다이얼로그 열기 실패: {e!r}", file=sys.stderr)
        finally:
            # 폴링이 끊기면 뒤에 오는 "execute" 가 영영 실행되지 않음
            if not exited:
                self._tk_root.after(QUEUE_POLL_INTERVAL_MS, self._process_queue)

    def _dispatch_queue_event(self, event: object) -> None:
        """open_dialog 를 뺀 큐 이벤트 하나를 처리한다.

        Args:
            event: 큐에서 꺼낸 이벤트
        """
        if event == "cancel":
            self._cancel_all()
        elif event == "toggle_autostart":
            self._toggle_autostart()
        elif event == "toggle_monitor_off":
            self._toggle_monitor_off()
        elif event == "open_settings":
            self._open_settings_dialog()
        elif event == "execute":
            self._execute_action()
        elif event == "exit":
            self._exit_app()
        elif isinstance(event, str) and event.startswith("warn:"):
            warn_label = event[len("warn:") :]
            label = (
                ACTION_LABEL_RESTART
                if self.scheduled_action == ACTION_RESTART
                else ACTION_LABEL_SHUTDOWN
            )
            self._show_warning_toast(f"{label} {warn_label} 전입니다.")
        elif isinstance(event, str) and event.startswith(
            ("monitor_due:", "monitor_warn:")
        ):
            name, kind, gen = event.split(":", 2)
            if name == "monitor_due":
                self._on_monitor_due(kind, int(gen))
            else:
                self._on_monitor_warn(kind, int(gen))

    def _update_tooltip_loop(self) -> None:
        """트레이 툴팁에 종료·모니터 끄기 남은 시간 표시 (1초 주기)."""
        # 예약·재개·취소 여러 곳에서 다시 부르므로 이전 루프를 먼저 끊어 겹치지 않게 함
        if self._tooltip_timer is not None:
            self._tooltip_timer.cancel()
            self._tooltip_timer = None
        if not self.tray_icon:
            return
        now = datetime.now()
        if (
            self.is_active
            and not self.is_paused
            and self.scheduled_time is not None
            and int((self.scheduled_time - now).total_seconds()) <= 0
        ):
            self._set_active_state(False)
        if not (self.is_active or self._solo_monitor.armed):
            self.tray_icon.title = TRAY_TOOLTIP_IDLE
            return
        self.tray_icon.title = self._tooltip_text(now)
        self._tooltip_timer = threading.Timer(
            TOOLTIP_UPDATE_INTERVAL_MS / 1000,
            self._update_tooltip_loop,
        )
        self._tooltip_timer.daemon = True
        self._tooltip_timer.start()

    def _tooltip_text(self, now: datetime) -> str:
        """종료 줄과 모니터 줄을 이어 붙인 툴팁 문자열을 만든다.

        Args:
            now: 기준 시각

        Returns:
            툴팁 문자열. 표시할 예약이 없으면 대기 문구.
        """
        lines: list[str] = []
        if self.is_active and self.scheduled_time is not None:
            label = (
                ACTION_LABEL_RESTART
                if self.scheduled_action == ACTION_RESTART
                else ACTION_LABEL_SHUTDOWN
            )
            if self.is_paused:
                lines.append(
                    f"{label} 일시정지 - 남은 시간: "
                    f"{_format_hms(self._pause_remaining)}"
                )
            else:
                remaining = (self.scheduled_time - now).total_seconds()
                lines.append(f"{label} 예약 중 - 남은 시간: {_format_hms(remaining)}")
        lines.extend(self._monitor_status_lines(now))
        return "\n".join(lines) if lines else TRAY_TOOLTIP_IDLE

    def _monitor_status_lines(self, now: datetime) -> list[str]:
        """모니터 끄기 예약 상태 줄 목록을 만든다 (툴팁·예약 창 배너 공용).

        Args:
            now: 기준 시각

        Returns:
            상태 줄 목록. 모니터 예약이 없으면 빈 목록.
        """
        lines: list[str] = []
        lead = self._monitor_lead_sec
        if (
            self.is_active
            and self.scheduled_action == ACTION_SHUTDOWN
            and lead is not None
        ):
            linked_due = self._linked_monitor.due
            if self._linked_done:
                lines.append("모니터: 꺼짐")
            elif lead == 0:
                lines.append("모니터: 종료 직전")
            elif linked_due is not None:
                remaining = (linked_due - now).total_seconds()
                lines.append(f"모니터 끄기까지 {_format_hms(remaining)}")
            else:
                lines.append(f"모니터: 종료 {_format_duration(lead)} 전")
        solo_due = self._solo_monitor.due
        if solo_due is not None:
            remaining = (solo_due - now).total_seconds()
            lines.append(f"모니터 끄기까지 {_format_hms(remaining)} (PC 켜 둠)")
        return lines

    def _open_schedule_dialog(self) -> None:
        """종료 예약 다이얼로그 열기 (메인 스레드에서 실행).

        이미 열려 있는 다이얼로그가 있으면 포커스만 가져오고
        새 창을 만들지 않는다.
        """
        if self._active_dialog is not None:
            try:
                top = self._active_dialog.top
                top.deiconify()
                top.lift()
                _bring_to_front(top)
                return
            except tk.TclError:
                self._active_dialog = None

        self._active_dialog = ScheduleDialog(
            self._tk_root,
            self._on_dialog_confirm,
            on_close=self._on_dialog_closed,
            scheduler=self,
        )

    def _on_dialog_closed(self) -> None:
        """다이얼로그가 닫혔을 때 콜백."""
        self._active_dialog = None

    def _open_settings_dialog(self) -> None:
        """테마 설정 창 열기 (메인 스레드에서 실행).

        이미 열려 있거나 만드는 중이면 새 창을 만들지 않는다.
        """
        if self._settings_opening:
            return
        if self._settings_dialog is not None:
            try:
                top = self._settings_dialog.top
                top.deiconify()
                top.lift()
                _bring_to_front(top)
                return
            except tk.TclError:
                self._settings_dialog = None

        self._settings_opening = True
        try:
            self._settings_dialog = SettingsDialog(
                self._tk_root,
                self,
                on_close=self._on_settings_closed,
            )
        finally:
            self._settings_opening = False

    def _on_settings_closed(self) -> None:
        """테마 설정 창이 닫혔을 때 콜백."""
        self._settings_dialog = None

    def _apply_theme_settings(self) -> None:
        """설정대로 테마를 정해 열린 창에 칠한다 (메인 스레드 전용)."""
        theme = get_theme(
            resolve_theme_id(self._settings, self._system_light),
            self._settings.get(SETTINGS_KEY_CONTRAST_FIX, CONTRAST_FIX_DEFAULT),
        )
        if theme == self._theme:
            return
        self._theme = theme
        # ctk 가 안에서 update() 를 돌려 이벤트가 끼어들 수 있으니 값을 먼저 바꿈
        ctk.set_appearance_mode(theme["mode"])
        for window in (self._active_dialog, self._settings_dialog):
            if window is not None:
                window.apply_theme(self._theme)

    def _update_theme_settings(self, changes: dict[str, Any]) -> None:
        """설정 창의 변경을 저장하고 바로 적용한다.

        Args:
            changes: 바꿀 설정 키와 값
        """
        self._settings.update(changes)
        if changes.get(SETTINGS_KEY_FOLLOW_SYSTEM):
            self._system_light = _read_system_light()
        saved = save_settings(self._settings)
        self._apply_theme_settings()
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_state()
        if not saved:
            self._show_confirm_toast(
                "설정 저장에 실패해 이번 실행에만 적용됩니다.", toast_type="info"
            )

    def _poll_system_theme(self) -> None:
        """시스템 테마를 따라갈 때만 레지스트리를 읽어 바뀌면 적용한다."""
        try:
            if self._settings.get(SETTINGS_KEY_FOLLOW_SYSTEM, FOLLOW_SYSTEM_DEFAULT):
                light = _read_system_light()
                if light is not None and light != self._system_light:
                    self._system_light = light
                    self._apply_theme_settings()
        finally:
            self._tk_root.after(SYSTEM_THEME_POLL_MS, self._poll_system_theme)

    def _on_dialog_confirm(
        self,
        action: str,
        hours: int,
        minutes: int,
        seconds: int,
        monitor_lead_sec: Optional[int] = None,
    ) -> None:
        """다이얼로그 확인 콜백.

        Args:
            action: ACTION_SHUTDOWN / ACTION_RESTART / ACTION_MONITOR_OFF
            hours: 시
            minutes: 분
            seconds: 초
            monitor_lead_sec: 종료 몇 초 전에 모니터를 끌지. None 이면 끄지 않음.
        """
        total = hours * 3600 + minutes * 60 + seconds
        if action == ACTION_MONITOR_OFF:
            self.schedule_monitor_only(total)
            return
        self.schedule_action(action, total, monitor_lead_sec)

    def schedule_action(
        self,
        action: str,
        total_seconds: int,
        monitor_lead_sec: Optional[int] = None,
    ) -> None:
        """시스템 종료 또는 재시작 예약.

        Args:
            action: ACTION_SHUTDOWN 또는 ACTION_RESTART
            total_seconds: 실행까지 대기 시간 (초). 0이면 즉시 실행.
            monitor_lead_sec: 종료 몇 초 전에 모니터를 끌지. None 은 끄지 않음,
                0 은 종료 직전. 재시작이면 무시한다.
        """
        # Windows Timer 최대 대기값을 넘지 않게 방어로 자름
        total_seconds = min(total_seconds, MAX_SCHEDULE_SECONDS)
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None

        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()
        self._linked_monitor.cancel()
        self._linked_done = False
        # 일시정지 중 새 예약이면 남은 일시정지 상태가 linked 끄기·배너를 막음
        self.is_paused = False
        self._pause_remaining = 0.0

        self.scheduled_action = action
        lead = None if action == ACTION_RESTART else monitor_lead_sec
        if lead is not None and not 0 <= lead < total_seconds:
            # 예약 창 검증을 거치지 않은 값 방어 — 종료 직전 끄기로 낮춤
            lead = 0
        # 즉시 실행 경로도 _execute_action 이 이 값을 보므로 먼저 저장함
        self._monitor_lead_sec = lead
        label = (
            ACTION_LABEL_RESTART if action == ACTION_RESTART else ACTION_LABEL_SHUTDOWN
        )
        monitor_note = self._linked_monitor_note()

        if total_seconds == 0:
            self._shutdown_immediate = True
            self._show_confirm_toast(
                f"시스템을 즉시 {label}합니다.{monitor_note}", toast_type="schedule"
            )
            self._tk_root.after(CONFIRM_TOAST_DURATION_MS, self._execute_action)
            return

        self._shutdown_immediate = False
        self.scheduled_time = datetime.now() + timedelta(seconds=total_seconds)
        self._set_active_state(True)
        if lead:
            self._linked_monitor.arm(total_seconds - lead)
        self._update_tooltip_loop()
        self._arm_shutdown_timers(total_seconds)
        self._save_schedule()

        time_str = _format_duration(total_seconds)
        self._show_confirm_toast(
            f"{time_str} 후 시스템이 {label}됩니다.{monitor_note}",
            toast_type="schedule",
        )

    def _linked_monitor_note(self) -> str:
        """종료 예약 토스트에 덧붙일 모니터 끄기 안내를 만든다.

        Returns:
            줄바꿈으로 시작하는 안내 문구. 모니터를 끄지 않으면 빈 문자열.
        """
        lead = self._monitor_lead_sec
        if lead is None or self.scheduled_action != ACTION_SHUTDOWN:
            return ""
        if lead == 0:
            return "\n모니터는 종료 직전에 끕니다."
        return f"\n모니터는 종료 {_format_duration(lead)} 전에 끕니다."

    def schedule_monitor_only(self, total_seconds: int) -> None:
        """PC 는 켜 둔 채 모니터만 끄는 단독 예약. 이전 단독 예약은 대체한다.

        Args:
            total_seconds: 끌 때까지 초. 0 이면 확인 토스트가 사라질 즈음 끈다.
        """
        total_seconds = min(total_seconds, MAX_SCHEDULE_SECONDS)
        delay = total_seconds if total_seconds > 0 else CONFIRM_TOAST_DURATION_MS / 1000
        self._solo_immediate = total_seconds <= 0
        self._solo_monitor.arm(delay)
        self._save_schedule()
        self._update_tooltip_loop()
        self._show_confirm_toast(
            f"{_format_duration(int(delay))} 후 모니터를 끕니다.\n"
            "다시 켜려면 전원 버튼을 누르세요.",
            toast_type="monitor_schedule",
        )

    def cancel_monitor_only(self, show_toast: bool = True) -> None:
        """모니터 단독 예약 취소.

        Args:
            show_toast: 취소 토스트를 띄울지
        """
        if not self._solo_monitor.armed:
            return
        self._solo_monitor.cancel()
        self._save_schedule()
        self._update_tooltip_loop()
        if show_toast:
            self._show_confirm_toast(
                "모니터 끄기 예약이 취소되었습니다.", toast_type="monitor_cancel"
            )

    def _cancel_all(self) -> None:
        """Alt+S·트레이 "예약 취소" — 종료 예약과 모니터 단독 예약을 모두 취소."""
        if not self._solo_monitor.armed:
            self.cancel_shutdown()
            return
        shutdown_active = self.is_active
        label = (
            ACTION_LABEL_RESTART
            if self.scheduled_action == ACTION_RESTART
            else ACTION_LABEL_SHUTDOWN
        )
        self.cancel_shutdown(show_toast=False)
        self.cancel_monitor_only(show_toast=False)
        if shutdown_active:
            self._show_confirm_toast(
                f"{label} 예약과 모니터 끄기 예약이 모두 취소되었습니다.",
                toast_type="cancel",
            )
        else:
            self._show_confirm_toast(
                "모니터 끄기 예약이 취소되었습니다.", toast_type="monitor_cancel"
            )

    def _monitor_timer(self, kind: str) -> Optional[_MonitorOffTimer]:
        """kind 에 맞는 모니터 Timer 를 돌려준다.

        Args:
            kind: "linked" 또는 "solo"

        Returns:
            _MonitorOffTimer. 모르는 kind 면 None.
        """
        if kind == "linked":
            return self._linked_monitor
        if kind == "solo":
            return self._solo_monitor
        return None

    def _on_monitor_due(self, kind: str, gen: int) -> None:
        """모니터 끄기 시각 도달 처리 (메인 스레드).

        Args:
            kind: "linked" 또는 "solo"
            gen: 이벤트에 붙은 세대 번호
        """
        timer = self._monitor_timer(kind)
        if timer is None or not timer.is_current(gen):
            return
        timer.due = None
        if kind == "linked":
            if not self.is_active or self.is_paused:
                return
            self._linked_done = True
            self._save_schedule()
        self._turn_off_monitors_now(kind)
        if kind == "solo":
            _clear_schedule_part("solo")
            self._update_tooltip_loop()

    def _on_monitor_warn(self, kind: str, gen: int) -> None:
        """모니터 끄기 1분 전 경고 (메인 스레드).

        Args:
            kind: "linked" 또는 "solo"
            gen: 이벤트에 붙은 세대 번호
        """
        timer = self._monitor_timer(kind)
        if timer is None or not timer.is_current(gen) or not timer.armed:
            return
        self._show_warning_toast("1분 후 모니터를 끕니다. 취소: Alt+S")

    def _turn_off_monitors_now(self, phase: str) -> None:
        """종료와 상관없이 지금 모니터를 끈다.

        Args:
            phase: 추적 로그 구분용 이름 ("linked" / "solo")
        """
        _trace_log(f"monitor off start phase={phase}")
        groups = _acquire_physical_monitors()
        try:
            written = _power_off_monitors(groups)
            _trace_log(f"monitor off phase={phase} written={written}")
        finally:
            _release_physical_monitors(groups)
            _trace_log(f"monitor off end phase={phase}")

    @staticmethod
    def _get_hwnd(widget: tk.Toplevel) -> int:
        """tk.Toplevel의 실제 Win32 최상위 창 핸들을 반환.

        winfo_id()는 내부 자식 핸들을 반환할 수 있으므로
        wm_frame()으로 프레임 핸들을 구한 뒤 GetAncestor로 루트를 탐색한다.

        Args:
            widget: 핸들을 얻을 Toplevel 위젯

        Returns:
            Win32 창 핸들 (HWND)
        """
        import ctypes

        # wm_frame(): tkinter 내부 함수 — 실제 OS 창 핸들 반환
        hwnd = int(widget.wm_frame(), 16)
        # GA_ROOT = 2: 최상위 조상 창까지 거슬러 올라감
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)
        return root_hwnd if root_hwnd else hwnd

    @staticmethod
    def _apply_rounded_glass(
        hwnd: int,
        width: int,
        height: int,
        gradient_color: int = 0xD037291F,
    ) -> None:
        """Windows API로 둥근 모서리 + Acrylic glass 효과 적용.

        Windows 11: DwmSetWindowAttribute(DWMWCP_ROUND)로 네이티브 둥근 모서리.
        Windows 10/11 공통: SetWindowCompositionAttribute로 Acrylic blur.
        Fallback: SetWindowRgn으로 둥근 클리핑 마스크.

        Args:
            hwnd: Win32 창 핸들
            width: 창 너비 (px)
            height: 창 높이 (px)
            gradient_color: Acrylic GradientColor (AABBGGRR 형식, 기본값=다크 청회색)
        """
        import ctypes

        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        RADIUS = 12  # 모서리 반경

        # ── 1. SetWindowRgn — 둥근 클리핑 마스크 (Win10/11 공통, 즉시 적용)
        rgn = gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, RADIUS * 2, RADIUS * 2
        )
        user32.SetWindowRgn(hwnd, rgn, True)

        # ── 2. Windows 11 네이티브 둥근 모서리 (DWMWCP_ROUND = 2)
        try:
            DWMWA_CORNER = 33
            pref = ctypes.c_int(2)
            dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_CORNER, ctypes.byref(pref), ctypes.sizeof(pref)
            )
        except Exception:
            pass

        # ── 3. Acrylic blur (ACCENT_ENABLE_ACRYLICBLURBEHIND = 4)
        #    GradientColor: AABBGGRR 형식
        try:

            class ACCENTPOLICY(ctypes.Structure):
                _fields_ = [
                    ("AccentState", ctypes.c_int),
                    ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_int),
                ]

            class WINCOMPATTRDATA(ctypes.Structure):
                _fields_ = [
                    ("Attribute", ctypes.c_int),
                    ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t),
                ]

            accent = ACCENTPOLICY(4, 0, gradient_color, 0)
            data = WINCOMPATTRDATA(
                19,
                ctypes.cast(ctypes.byref(accent), ctypes.c_void_p),
                ctypes.sizeof(accent),
            )
            user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        except Exception:
            pass

    @staticmethod
    def _fade_out_toast(
        window: tk.Toplevel,
        on_done: Callable[[], None],
        progress: float = 0.0,
    ) -> None:
        """토스트 창을 ease-in 커브로 서서히 투명하게 만들며 닫는 애니메이션.

        30ms 간격, 약 0.8초(27스텝)에 걸쳐 사라진다.
        progress(0.0→1.0)의 제곱을 알파 감소에 적용해 처음엔 천천히,
        끝으로 갈수록 빠르게 사라지는 자연스러운 효과를 낸다.

        Args:
            window: 페이드아웃할 Toplevel 창
            on_done: 완전히 사라진 후 호출할 정리 콜백
            progress: 애니메이션 진행도 (0.0 ~ 1.0)
        """
        _TOTAL_STEPS = 27
        _INTERVAL_MS = 30
        try:
            if progress >= 1.0:
                window.destroy()
                on_done()
                return
            # ease-in: progress² 로 초반은 완만하게, 후반은 급격하게 투명해짐
            alpha = 1.0 - (progress**2)
            window.attributes("-alpha", max(0.0, alpha))
            next_progress = progress + (1.0 / _TOTAL_STEPS)
            window.after(
                _INTERVAL_MS,
                lambda: ShutdownScheduler._fade_out_toast(
                    window, on_done, next_progress
                ),
            )
        except tk.TclError:
            on_done()

    def _build_toast(
        self,
        title: str,
        message: str,
        icon: str,
        accent_color: str,
        badge_bg: str,
    ) -> tuple[tk.Toplevel, tk.Label]:
        """고급 토스트 창 생성 및 배치.

        좌측 컬러 바 + 아이콘 뱃지 + 제목/본문 2줄 구조.
        고정 너비 360px, 우상단 닫기(×) 버튼 포함.

        Args:
            title: 굵은 제목 텍스트
            message: 본문 텍스트
            icon: 아이콘 문자
            accent_color: 좌측 바·아이콘 색상
            badge_bg: 아이콘 뱃지 배경색

        Returns:
            생성된 tk.Toplevel 인스턴스
        """
        _TOAST_W = 360
        ff = _resolve_ui_font(self._tk_root)

        win = tk.Toplevel(self._tk_root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=UI_TOAST_BORDER)

        # ── 외곽 테두리 (1px)
        border = tk.Frame(win, bg=UI_TOAST_BORDER)
        border.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # ── 좌측 컬러 바 (3px)
        tk.Frame(border, bg=accent_color, width=3).pack(side=tk.LEFT, fill=tk.Y)

        # ── 본문 영역
        body = tk.Frame(border, bg=UI_TOAST_BG)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── 상단: 아이콘 뱃지 + 제목 + 닫기 버튼
        top_row = tk.Frame(body, bg=UI_TOAST_BG)
        top_row.pack(fill=tk.X, padx=14, pady=(13, 4))

        # 아이콘 뱃지 (원형 느낌, 배경 미세하게 구분)
        badge = tk.Frame(top_row, bg=UI_TOAST_BADGE_BG, width=34, height=34)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        badge.pack_propagate(False)
        tk.Label(
            badge,
            text=icon,
            font=(ff, 15),
            fg=accent_color,
            bg=UI_TOAST_BADGE_BG,
        ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # 제목
        tk.Label(
            top_row,
            text=title,
            font=(ff, 12, "bold"),
            fg=UI_TOAST_FG,
            bg=UI_TOAST_BG,
            anchor=tk.W,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 닫기 버튼
        close_btn = tk.Label(
            top_row,
            text="✕",
            font=(ff, 9),
            fg=UI_TOAST_CLOSE,
            bg=UI_TOAST_BG,
            cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # ── 본문 메시지
        tk.Label(
            body,
            text=message,
            font=(ff, 10),
            fg=UI_TOAST_SUB_FG,
            bg=UI_TOAST_BG,
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=_TOAST_W - 80,
        ).pack(fill=tk.X, padx=(58, 14), pady=(0, 13))

        # ── 위치 계산 및 배치
        win.update_idletasks()
        th = win.winfo_reqheight() or 80
        try:
            sw = self._tk_root.winfo_screenwidth()
            sh = self._tk_root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        x = (sw - _TOAST_W) // 2
        y = sh - th - 80
        win.geometry(f"{_TOAST_W}x{th}+{x}+{y}")

        # ── 둥근 모서리 (Acrylic 없이 순수 블랙 배경 유지)
        win.update()
        try:
            import ctypes

            hwnd = self._get_hwnd(win)
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            RADIUS = 12
            rgn = gdi32.CreateRoundRectRgn(
                0, 0, _TOAST_W + 1, th + 1, RADIUS * 2, RADIUS * 2
            )
            user32.SetWindowRgn(hwnd, rgn, True)
            # Windows 11 네이티브 둥근 모서리
            dwmapi = ctypes.windll.dwmapi
            pref = ctypes.c_int(2)
            dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
            )
        except Exception:
            pass

        return win, close_btn

    def _show_warning_toast(self, message: str) -> None:
        """종료 임박 경고 토스트 창 표시 (3초 표시, 주황색 액센트).

        Args:
            message: 표시할 경고 메시지
        """
        _WARNING_DURATION_MS = 3000

        label = (
            ACTION_LABEL_RESTART
            if self.scheduled_action == ACTION_RESTART
            else ACTION_LABEL_SHUTDOWN
        )

        win, close_btn = self._build_toast(
            title=f"{label} 예약 알림",
            message=message,
            icon="⏱",
            accent_color=UI_TOAST_WARN_ACCENT,
            badge_bg=UI_TOAST_BADGE_BG,
        )

        def _dismiss() -> None:
            self._fade_out_toast(win, lambda: None)

        close_btn.bind("<Button-1>", lambda e: _dismiss())
        win.after(_WARNING_DURATION_MS, _dismiss)

    def _show_confirm_toast(self, message: str, toast_type: str = "info") -> None:
        """3초 후 자동으로 사라지는 확인 토스트 창 표시.

        같은 toast_type이면 메시지만 교체하고 타이머를 풀 리셋한다.
        toast_type이 다르면 기존 창을 즉시 닫고 새 색상으로 다시 생성한다.

        Args:
            message: 표시할 메시지
            toast_type: "schedule"(빨강) | "cancel"(파랑) |
                        "autostart_on"(초록) | "autostart_off"(회색) |
                        "monitor_off_on"(초록) | "monitor_off_off"(회색) | "info"
        """
        # 타입별 색상·제목·아이콘 결정
        action_label = (
            ACTION_LABEL_RESTART
            if self.scheduled_action == ACTION_RESTART
            else ACTION_LABEL_SHUTDOWN
        )
        _TYPE_META: dict[str, tuple[str, str, str]] = {
            "schedule": (f"{action_label} 예약", "⏻", UI_DANGER_COLOR),
            "cancel": (f"{action_label} 예약 취소", "⏻", UI_ACCENT_COLOR),
            "autostart_on": ("자동 시작 활성화", "✔", UI_SUCCESS_COLOR),
            "autostart_off": ("자동 시작 비활성화", "✕", UI_SUB_FG_COLOR),
            "monitor_off_on": ("종료 시 모니터 끄기 켜짐", "✔", UI_SUCCESS_COLOR),
            "monitor_off_off": ("종료 시 모니터 끄기 꺼짐", "✕", UI_SUB_FG_COLOR),
            "monitor_schedule": ("모니터 끄기 예약", "☾", UI_ACCENT_COLOR),
            "monitor_cancel": ("모니터 끄기 예약 취소", "☾", UI_SUB_FG_COLOR),
            "info": ("알림", "ℹ", UI_ACCENT_COLOR),
        }
        title, icon, accent = _TYPE_META.get(toast_type, _TYPE_META["info"])

        # 기존 토스트가 살아있으면 같은 타입일 때만 재사용
        if self._toast_window is not None:
            if self._toast_type == toast_type:
                try:
                    existing_win = self._toast_window
                    if self._toast_label is not None:
                        self._toast_label.configure(text=message)
                    if self._toast_after_id is not None:
                        existing_win.after_cancel(self._toast_after_id)
                    existing_win.attributes("-alpha", 1.0)

                    def _on_reuse_done(target: tk.Toplevel = existing_win) -> None:
                        if self._toast_window is target:
                            self._close_toast()

                    self._toast_after_id = existing_win.after(
                        CONFIRM_TOAST_DURATION_MS,
                        lambda: self._fade_out_toast(existing_win, _on_reuse_done),
                    )
                    return
                except tk.TclError:
                    pass
            # 타입이 다르거나 TclError — 기존 토스트 즉시 닫고 새로 생성
            self._close_toast()

        win, close_btn = self._build_toast(
            title=title,
            message=message,
            icon=icon,
            accent_color=accent,
            badge_bg=UI_TOAST_BADGE_BG,
        )
        self._toast_window = win
        self._toast_type = toast_type

        # 본문 레이블 참조 저장 (메시지 교체용)
        body_widgets = win.winfo_children()[0].winfo_children()[1].winfo_children()
        self._toast_label = body_widgets[1] if len(body_widgets) > 1 else None

        # fade 완료 콜백: 이 창이 현재 toast_window와 동일할 때만 정리
        def _on_fade_done(target: tk.Toplevel = win) -> None:
            if self._toast_window is target:
                self._close_toast()

        def _dismiss() -> None:
            self._fade_out_toast(win, _on_fade_done)

        close_btn.bind("<Button-1>", lambda e: _dismiss())

        self._toast_after_id = win.after(
            CONFIRM_TOAST_DURATION_MS,
            lambda: self._fade_out_toast(win, _on_fade_done),
        )

    def _close_toast(self) -> None:
        """토스트 창 닫기 및 참조 초기화."""
        if self._toast_window is not None:
            try:
                self._toast_window.destroy()
            except tk.TclError:
                pass
        self._toast_window = None
        self._toast_label = None
        self._toast_after_id = None
        self._toast_type = ""

    def _execute_action(self) -> None:
        """실제 종료/재시작 명령 실행 (메인 스레드).

        종료이고 이 예약에 모니터 끄기가 걸려 있으면(미리 껐어도 다시)
        종료 명령이 접수된 뒤에만 DDC/CI 로 모니터를 끈다.
        """
        flag = "/r" if self.scheduled_action == ACTION_RESTART else "/s"
        want_off = (
            self.scheduled_action == ACTION_SHUTDOWN
            and self._monitor_lead_sec is not None
        )
        if want_off:
            _trace_log(f"start action={self.scheduled_action}")
        # 로그오프가 시작되면 열거가 실패할 수 있어 명령 전에 미리 잡음
        groups = _acquire_physical_monitors() if want_off else []
        if want_off:
            _trace_log(f"acquired monitors={sum(count for count, _ in groups)}")
        if groups:
            _trace_monitor_state(groups)
        # 종료가 접수되면 앱이 곧 꺼지므로 명령 전에 복원 대상에서 뺌
        _clear_schedule_part("shutdown")
        try:
            result = subprocess.run(
                ["shutdown", flag, "/t", "0", "/f"],
                check=False,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
            if want_off:
                _trace_log(f"shutdown returncode={result.returncode}")
            # 종료 접수 뒤에만 끔 — 꺼진 모니터는 DDC 로 다시 못 켬
            if groups and result.returncode == 0:
                written = _power_off_monitors(groups)
                _trace_log(f"power off written={written}")
        except Exception as e:
            print(f"{self.scheduled_action} 명령 실행 실패: {e}", file=sys.stderr)
            if want_off:
                _trace_log(f"shutdown error {e!r}")
        finally:
            _release_physical_monitors(groups)
            if want_off:
                _trace_log("end")

    def cancel_shutdown(self, show_toast: bool = True) -> None:
        """종료/재시작 예약 취소. 딸린 모니터 끄기도 함께 취소한다.

        Args:
            show_toast: 취소 토스트를 띄울지
        """
        if not self.is_active:
            return

        label = (
            ACTION_LABEL_RESTART
            if self.scheduled_action == ACTION_RESTART
            else ACTION_LABEL_SHUTDOWN
        )

        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None

        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()

        if self._tooltip_timer:
            self._tooltip_timer.cancel()
            self._tooltip_timer = None

        self._linked_monitor.cancel()
        had_monitor = self._monitor_lead_sec is not None
        self._monitor_lead_sec = None
        self._linked_done = False

        self.is_paused = False
        self._pause_remaining = 0.0
        self._set_active_state(False)
        self._save_schedule()
        if self._solo_monitor.armed:
            self._update_tooltip_loop()
        if not show_toast:
            return
        msg = f"{label} 예약이 취소되었습니다."
        if had_monitor:
            msg += "\n모니터 끄기도 함께 취소되었습니다."
        self._show_confirm_toast(msg, toast_type="cancel")

    def pause_shutdown(self) -> None:
        """종료 타이머 일시정지."""
        if not self.is_active or self.is_paused or self.scheduled_time is None:
            return
        remaining = (self.scheduled_time - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        self._pause_remaining = remaining
        self.is_paused = True
        # 타이머·경고 타이머 모두 취소 (재개 시 남은 시간으로 재등록)
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None
        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()
        # lead 는 남겨 재개 때 남은 시간 기준으로 다시 걺
        self._linked_monitor.cancel()
        if self._tooltip_timer:
            self._tooltip_timer.cancel()
            self._tooltip_timer = None
        # 단독 모니터 예약이 있으면 툴팁 갱신이 계속되어야 함
        self._update_tooltip_loop()
        self._save_schedule()

    def resume_shutdown(self) -> None:
        """일시정지된 종료 타이머 재개."""
        if not self.is_active or not self.is_paused:
            return
        self.is_paused = False
        total_seconds = int(self._pause_remaining)
        self._pause_remaining = 0.0
        # 남은 시간으로 재예약 (scheduled_time 갱신)
        self.scheduled_time = datetime.now() + timedelta(seconds=total_seconds)
        lead = self._monitor_lead_sec
        if lead is not None and lead > 0 and not self._linked_done:
            # 남은 시간이 lead 이하면 arm 이 바로 끄기 이벤트를 넣음
            self._linked_monitor.arm(total_seconds - lead)
        self._update_tooltip_loop()
        self._arm_shutdown_timers(total_seconds)
        self._save_schedule()

    def _arm_shutdown_timers(self, total_seconds: float) -> None:
        """종료 실행 Timer 와 10분/5분/1분 전 경고 Timer 를 (다시) 건다.

        schedule_action·resume_shutdown·extend_schedule 공용이다. 걸린 Timer 는 먼저 끊는다.

        Args:
            total_seconds: 실행까지 초
        """
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None
        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()
        self._armed_total = float(total_seconds)
        # 실행은 메인 스레드에서 함 — 큐 경유
        self._shutdown_timer = threading.Timer(
            total_seconds, lambda: self._queue.put("execute")
        )
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()
        # 예약 시간보다 짧은 경고만 등록함
        for warn_sec, warn_label in [(600, "10분"), (300, "5분"), (60, "1분")]:
            delay = total_seconds - warn_sec
            if delay >= 0 and total_seconds > warn_sec:
                t = threading.Timer(
                    delay,
                    lambda wl=warn_label: self._queue.put(f"warn:{wl}"),
                )
                t.daemon = True
                t.start()
                self._warning_timers.append(t)

    def extend_schedule(self, add_seconds: int = EXTEND_SECONDS) -> None:
        """진행 중인 예약을 add_seconds 만큼 늘린다 (+10분 연장).

        종료 예약이 있으면 종료에, 단독 모니터 예약만 있으면 그것에 적용한다.
        남은 시간이 0 이하이거나 예약이 없거나 즉시 실행 경로면 무시한다.
        결과가 MAX_SCHEDULE_SECONDS 를 넘으면 상한까지만 늘리고, 이미 상한이면 무시한다.

        Args:
            add_seconds: 늘릴 초
        """
        now = datetime.now()
        if self.is_active and not self._shutdown_immediate:
            if self.is_paused:
                if self._pause_remaining >= MAX_SCHEDULE_SECONDS:
                    return
                self._pause_remaining = min(
                    self._pause_remaining + add_seconds, MAX_SCHEDULE_SECONDS
                )
                self._save_schedule()
                return
            if self.scheduled_time is None:
                return
            remaining = (self.scheduled_time - now).total_seconds()
            if remaining <= 0 or remaining >= MAX_SCHEDULE_SECONDS:
                return
            added = _capped_add(remaining, add_seconds)
            new_total = remaining + added
            self._arm_shutdown_timers(new_total)
            lead = self._monitor_lead_sec
            if lead and not self._linked_done:
                self._linked_monitor.arm(new_total - lead)
            self.scheduled_time = now + timedelta(seconds=new_total)
            self._save_schedule()
            self._show_confirm_toast(
                f"{self.scheduled_time:%H:%M}으로 {_format_duration(added)} 연장",
                toast_type="schedule",
            )
            return
        due = self._solo_monitor.due
        if due is None or self._solo_immediate:
            return
        remaining = (due - now).total_seconds()
        if remaining <= 0 or remaining >= MAX_SCHEDULE_SECONDS:
            return
        added = _capped_add(remaining, add_seconds)
        self._solo_monitor.arm(remaining + added)
        self._save_schedule()
        new_due = self._solo_monitor.due
        self._show_confirm_toast(
            f"{new_due:%H:%M}으로 {_format_duration(added)} 연장",
            toast_type="monitor_schedule",
        )

    def _schedule_snapshot(self) -> dict[str, Any]:
        """지금 예약 상태를 복원 파일 형식으로 만든다. 즉시 실행(0초)은 넣지 않는다.

        Returns:
            {version, saved_at, shutdown, solo}
        """
        shutdown: Optional[dict[str, Any]] = None
        if self.is_active and not self._shutdown_immediate:
            if self.is_paused:
                target, paused = None, float(self._pause_remaining)
                total = paused
            elif self.scheduled_time is not None:
                target, paused = self.scheduled_time.timestamp(), None
                total = float(self._armed_total)
            else:
                target = paused = None
            if target is not None or paused is not None:
                shutdown = {
                    "action": self.scheduled_action,
                    "target": target,
                    "paused_remaining": paused,
                    "total": total,
                    "monitor_lead_sec": self._monitor_lead_sec,
                    "linked_done": bool(self._linked_done),
                }
        solo: Optional[dict[str, Any]] = None
        due = self._solo_monitor.due
        if due is not None and not self._solo_immediate:
            solo = {"target": due.timestamp()}
        return {
            "version": SCHEDULE_FILE_VERSION,
            "saved_at": time.time(),
            "shutdown": shutdown,
            "solo": solo,
        }

    def _save_schedule(self) -> None:
        """지금 예약 상태를 복원 파일에 쓴다. 예약이 없으면 파일을 지운다."""
        snapshot = self._schedule_snapshot()
        if snapshot["shutdown"] is None and snapshot["solo"] is None:
            _delete_schedule_file()
            return
        _write_schedule_file(snapshot)

    def _restore_schedule(self) -> None:
        """재실행 시 복원 파일로 예약을 되살린다 (main 에서 after(0) 으로 부름).

        복원 중에는 어떤 경우에도 종료 명령이나 즉시 모니터 끄기를 부르지 않는다.
        남은 시간이 RESTORE_PAUSE_UNDER_SEC 보다 짧은 종료는 일시정지 상태로 되살린다.
        검증을 빠져나간 값으로 예외가 나면 되살린 것을 되돌리고 파일을 지운다.
        """
        try:
            self._restore_schedule_from_file()
        except (ValueError, OverflowError, OSError, KeyError, TypeError) as e:
            print(f"예약 복원 실패: {e!r}", file=sys.stderr)
            # 파일을 남기면 실행할 때마다 같은 예외가 반복됨
            try:
                self.cancel_shutdown(show_toast=False)
                self.cancel_monitor_only(show_toast=False)
            except Exception:
                pass
            _delete_schedule_file()

    def _restore_schedule_from_file(self) -> None:
        """복원 파일을 읽어 예약 상태를 세운다 (_restore_schedule 본체)."""
        exists, data = _read_schedule_file()
        if not exists:
            return
        if data is None or not _valid_schedule(data):
            _delete_schedule_file()
            return
        now_ts = time.time()
        sd = data["shutdown"]
        solo = data["solo"]
        clock_changed = now_ts < data["saved_at"] - RESTORE_CLOCK_SKEW_SEC
        if sd is not None and sd["target"] is not None:
            if sd["target"] - now_ts > sd["total"] + RESTORE_CLOCK_SKEW_SEC:
                clock_changed = True
        if clock_changed:
            _delete_schedule_file()
            self._show_confirm_toast(
                "시계가 바뀌어 복원하지 않았습니다.", toast_type="info"
            )
            return

        notes: list[str] = []
        if sd is not None:
            label = _action_label(sd["action"])
            if sd["paused_remaining"] is not None:
                remaining = float(sd["paused_remaining"])
                if remaining > 0:
                    self._restore_shutdown(sd, remaining, paused=True)
                    notes.append(f"일시정지된 {label} 예약을 복원했습니다.")
            else:
                remaining = sd["target"] - now_ts
                at = f"{datetime.fromtimestamp(sd['target']):%H:%M}"
                if remaining <= 0:
                    notes.append(f"앱이 꺼진 사이 {at} 예약 시각이 지나 취소했습니다.")
                elif remaining < RESTORE_PAUSE_UNDER_SEC:
                    self._restore_shutdown(sd, remaining, paused=True)
                    notes.append(
                        f"{at} {label} 예약이 곧이라 일시정지 상태로 복원했습니다. "
                        "▶로 재개하세요."
                    )
                else:
                    self._restore_shutdown(sd, remaining, paused=False)
                    notes.append(f"{at} {label} 예약을 복원했습니다.")
        if solo is not None:
            remaining = solo["target"] - now_ts
            at = f"{datetime.fromtimestamp(solo['target']):%H:%M}"
            if remaining <= 0:
                notes.append(
                    f"앱이 꺼진 사이 {at} 모니터 끄기 시각이 지나 취소했습니다."
                )
            elif remaining < RESTORE_PAUSE_UNDER_SEC:
                notes.append(
                    f"{at} 모니터 끄기 예약은 남은 시간이 짧아 복원하지 않았습니다."
                )
            else:
                self._solo_immediate = False
                self._solo_monitor.arm(remaining)
                notes.append(f"{at} 모니터 끄기 예약을 복원했습니다.")
        self._save_schedule()
        self._sync_tray_after_restore()
        if notes:
            self._show_confirm_toast("\n".join(notes), toast_type="info")

    def _restore_shutdown(
        self, sd: dict[str, Any], remaining: float, paused: bool
    ) -> None:
        """복원 파일의 종료 부분으로 예약 상태를 세운다.

        Args:
            sd: 복원 파일의 shutdown 부분
            remaining: 남은 초 (0 보다 큼)
            paused: 일시정지 상태로 둘지
        """
        action = sd["action"]
        lead = None if action == ACTION_RESTART else sd["monitor_lead_sec"]
        done = bool(sd["linked_done"])
        self.scheduled_action = action
        self._shutdown_immediate = False
        self._linked_done = done
        self._linked_monitor.cancel()
        # 툴팁·창의 종료 줄은 scheduled_time 이 있어야 나오므로 일시정지여도 채움
        self.scheduled_time = datetime.now() + timedelta(seconds=remaining)
        if lead and not done and remaining - lead <= 0:
            # 끄는 시각이 지났으면 일시정지 복원도 종료 직전 끄기로 낮춤
            lead = 0
        if paused:
            self._monitor_lead_sec = lead
            self.is_paused = True
            self._pause_remaining = remaining
            self._armed_total = remaining
            self._set_active_state(True)
            return
        self._monitor_lead_sec = lead
        self.is_paused = False
        self._pause_remaining = 0.0
        self._set_active_state(True)
        if lead and not done:
            self._linked_monitor.arm(remaining - lead)
        self._arm_shutdown_timers(remaining)

    def _sync_tray_after_restore(self, tries: int = 20) -> None:
        """트레이 아이콘이 준비되면 색과 툴팁을 복원된 예약에 맞춘다.

        트레이는 다른 스레드에서 만들어져 복원 시점에 아직 없을 수 있어 잠깐씩 다시 본다.

        Args:
            tries: 남은 재시도 횟수
        """
        if self.tray_icon is None:
            if tries > 0:
                self._tk_root.after(
                    250, lambda: self._sync_tray_after_restore(tries - 1)
                )
            return
        if self.is_active:
            self._set_active_state(True)
        self._update_tooltip_loop()

    def _set_active_state(self, active: bool) -> None:
        """트레이 아이콘 상태 전환.

        Args:
            active: True면 예약 중(빨간색), False면 대기 중(파란색)
        """
        self.is_active = active
        if not active:
            self.scheduled_time = None

        if self.tray_icon:
            color = TRAY_ICON_COLOR_ACTIVE if active else TRAY_ICON_COLOR_IDLE
            self.tray_icon.icon = self._create_tray_icon_image(color)
            self.tray_icon.title = (
                TRAY_TOOLTIP_IDLE if not active else self.tray_icon.title
            )

    def _toggle_autostart(self) -> None:
        """자동 시작 상태 토글."""
        if is_autostart_enabled():
            ok, msg = disable_autostart()
            toast_type = "autostart_off"
        else:
            ok, msg = enable_autostart()
            toast_type = "autostart_on"

        if self.tray_icon:
            self.tray_icon.update_menu()

        self._show_confirm_toast(msg, toast_type=toast_type)

    def _toggle_monitor_off(self) -> None:
        """예약 창 모니터 스위치의 기본값을 토글한다 (저장 → 메뉴 갱신 → 토스트).

        진행 중인 예약에는 영향을 주지 않는다.
        """
        enabled = not self._settings.get(SETTINGS_KEY_MONITOR_OFF, MONITOR_OFF_DEFAULT)
        self._settings[SETTINGS_KEY_MONITOR_OFF] = enabled
        saved = save_settings(self._settings)

        if self.tray_icon:
            self.tray_icon.update_menu()

        if enabled:
            msg = "다음 예약부터 모니터 끄기를 기본으로 켭니다."
            toast_type = "monitor_off_on"
        else:
            msg = "다음 예약부터 모니터 끄기를 기본으로 끕니다."
            toast_type = "monitor_off_off"
        if not saved:
            msg += "\n설정 저장에 실패해 이번 실행에만 적용됩니다."
        self._show_confirm_toast(msg, toast_type=toast_type)

    def _exit_app(self) -> None:
        """프로그램 종료 (트레이 "프로그램 종료"). 예약 복원 파일도 지운다."""
        if self._tooltip_timer:
            self._tooltip_timer.cancel()
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()
        self._linked_monitor.cancel()
        self._solo_monitor.cancel()
        # 사용자가 앱을 끈 것이라 다음 실행 때 복원하지 않음
        _delete_schedule_file()
        keyboard.unhook_all()
        if self.tray_icon:
            self.tray_icon.stop()
        self._tk_root.destroy()

    def run(self) -> None:
        """애플리케이션 시작."""
        tray_thread = threading.Thread(target=self._start_tray, daemon=True)
        tray_thread.start()

        self._register_hotkeys()

        self._tk_root.after(QUEUE_POLL_INTERVAL_MS, self._process_queue)
        self._tk_root.after(SYSTEM_THEME_POLL_MS, self._poll_system_theme)

        self._tk_root.mainloop()


def _acquire_single_instance_mutex() -> Optional[object]:
    """Windows Named Mutex로 단일 인스턴스를 보장.

    Returns:
        Mutex 핸들. 중복 실행이면 None.
    """
    try:
        import ctypes

        _MUTEX_NAME = "Global\\ShutdownScheduler_SingleInstance"
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return object()


def main() -> None:
    """프로그램 진입점."""
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        sys.exit(0)

    _set_shutdown_level_last()

    app = ShutdownScheduler()
    app._tk_root.after(0, app._restore_schedule)
    app.run()


if __name__ == "__main__":
    main()
