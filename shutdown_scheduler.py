"""
shutdown_scheduler.py - 시스템 종료 예약 프로그램.

백그라운드 트레이 아이콘으로 실행되며, 글로벌 단축키로 종료 시간을 예약한다.
- Alt+Q: 종료 예약 다이얼로그 열기
- Alt+S: 예약 취소
"""

import os
import queue
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional

import customtkinter as ctk
import keyboard
import pystray
import tkinter as tk

try:
    from PIL import Image, ImageDraw

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from cfg.config import (
    ACTION_LABEL_RESTART,
    ACTION_LABEL_SHUTDOWN,
    ACTION_RESTART,
    ACTION_SHUTDOWN,
    CONFIRM_TOAST_DURATION_MS,
    CTK_APPEARANCE,
    CTK_COLOR_THEME,
    DIALOG_HEIGHT,
    DIALOG_TITLE,
    DIALOG_WIDTH,
    HOTKEY_CANCEL,
    HOTKEY_SCHEDULE,
    QUEUE_POLL_INTERVAL_MS,
    QUICK_ADD_BUTTONS,
    TASK_NAME,
    TOOLTIP_UPDATE_INTERVAL_MS,
    TRAY_ICON_COLOR_ACTIVE,
    TRAY_ICON_COLOR_IDLE,
    TRAY_ICON_SIZE,
    TRAY_TOOLTIP_IDLE,
    UI_ACCENT_COLOR,
    UI_ACCENT_HOVER,
    UI_ACCENT_LIGHT,
    UI_ACCENT_TEXT,
    UI_BG_COLOR,
    UI_BORDER_COLOR,
    UI_TOAST_BG,
    UI_TOAST_BADGE_BG,
    UI_TOAST_BORDER,
    UI_TOAST_CLOSE,
    UI_TOAST_CONFIRM_ACCENT,
    UI_TOAST_FG,
    UI_TOAST_SUB_FG,
    UI_TOAST_WARN_ACCENT,
    UI_BTN_BG,
    UI_BTN_FG,
    UI_BTN_HOVER,
    UI_CARD_COLOR,
    UI_CARD2_COLOR,
    UI_DANGER_BG,
    UI_DANGER_COLOR,
    UI_DANGER_HOVER,
    UI_FG_COLOR,
    UI_FONT_FAMILY,
    UI_FONT_FAMILY_FALLBACK,
    UI_FONT_SIZE,
    UI_FONT_SIZE_LARGE,
    UI_MUTED_FG,
    UI_SUB_FG_COLOR,
    UI_SUCCESS_BG,
    UI_SUCCESS_COLOR,
    UI_SUCCESS_HOVER,
    UNIT_HOUR,
    UNIT_LABEL_HOUR,
    UNIT_LABEL_MINUTE,
    UNIT_LABEL_SECOND,
    UNIT_MINUTE,
    UNIT_SECOND,
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
        draw.arc([im, im, size - im, size - im], start=300, end=240, fill=sym, width=arc_w)
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


class ScheduleDialog:
    """종료/재시작 시간 입력 팝업 다이얼로그 (customtkinter 다크 UI).

    내부 상태를 _total_seconds(총 초)로 단일 관리하여 단위 전환/퀵추가
    시 정보 손실 없이 정확한 시간 환산이 가능하다.

    Attributes:
        top (ctk.CTkToplevel): 팝업 윈도우
        _action (str): 동작 종류 (ACTION_SHUTDOWN/ACTION_RESTART)
        _unit (str): 현재 선택된 시간 단위
        _total_seconds (int): 진실 소스 — 총 예약 시간(초)
        var_value (tk.StringVar): 숫자 입력 필드 변수
        callback (Callable[[str, int, int, int], None]): 확인 시 콜백
    """

    _UNIT_TO_SEC: dict[str, int] = {
        UNIT_HOUR: 3600,
        UNIT_MINUTE: 60,
        UNIT_SECOND: 1,
    }

    def __init__(
        self,
        parent: tk.Tk,
        callback: Callable[[str, int, int, int], None],
        on_close: Optional[Callable[[], None]] = None,
        scheduler: Optional["ShutdownScheduler"] = None,
    ) -> None:
        """다이얼로그 초기화.

        Args:
            parent: tkinter 루트 윈도우
            callback: 확인 시 호출할 함수 (action, hours, minutes, seconds)
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

        self._action_btns: dict[str, ctk.CTkButton] = {}
        self._unit_btns: dict[str, ctk.CTkButton] = {}

        self.top = ctk.CTkToplevel(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI 구성                                                              #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        """customtkinter 다크 모던 UI 구성."""
        top = self.top
        top.title(DIALOG_TITLE)
        top.resizable(False, False)

        ff = _resolve_ui_font(top)

        top.update_idletasks()
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        x = (sw - DIALOG_WIDTH) // 2
        y = (sh - DIALOG_HEIGHT) // 2
        top.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}+{x}+{y}")
        top.grab_set()
        # 창 열릴 때만 잠깐 앞으로 가져온 뒤 topmost 해제
        # (항상 최상단 고정 방지)
        top.attributes("-topmost", True)
        top.after(100, lambda: top.attributes("-topmost", False))

        # ── 메인 스크롤 없는 단일 프레임
        main = ctk.CTkFrame(top, fg_color=UI_BG_COLOR, corner_radius=0)
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1)

        # ━━━ 헤더 영역 ━━━
        header = ctk.CTkFrame(main, fg_color=UI_BG_COLOR, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        header.grid_columnconfigure(1, weight=1)

        # 아이콘 뱃지 (glass 레이어)
        icon_badge = ctk.CTkFrame(
            header,
            width=44, height=44,
            fg_color=UI_ACCENT_LIGHT,
            corner_radius=12,
        )
        icon_badge.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        icon_badge.grid_propagate(False)
        ctk.CTkLabel(
            icon_badge,
            text="⏻",
            font=(ff, 20, "bold"),
            text_color=UI_ACCENT_COLOR,
        ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        ctk.CTkLabel(
            header,
            text="종료 예약",
            font=(ff, UI_FONT_SIZE_LARGE, "bold"),
            text_color=UI_FG_COLOR,
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            header,
            text="시스템 종료 또는 재시작을 예약합니다.",
            font=(ff, 11),
            text_color=UI_SUB_FG_COLOR,
            anchor="w",
        ).grid(row=1, column=1, sticky="w")

        # ━━━ 예약 카운트다운 배너 (예약 중일 때만 표시) ━━━
        self._countdown_var = tk.StringVar(value="")
        self._countdown_frame = ctk.CTkFrame(
            main,
            fg_color=UI_DANGER_BG,
            corner_radius=10,
            border_width=1,
            border_color=UI_DANGER_COLOR,
        )
        self._countdown_frame.grid_columnconfigure(0, weight=1)

        self._countdown_label = ctk.CTkLabel(
            self._countdown_frame,
            textvariable=self._countdown_var,
            font=(ff, 12, "bold"),
            text_color=UI_DANGER_COLOR,
            anchor="w",
        )
        self._countdown_label.grid(row=0, column=0, padx=(12, 4), pady=6, sticky="ew")

        # ■ 취소 버튼
        self._btn_cancel_sched = ctk.CTkButton(
            self._countdown_frame,
            text="■",
            font=(ff, 11, "bold"),
            width=28, height=28,
            corner_radius=6,
            fg_color=UI_DANGER_BG,
            hover_color=UI_DANGER_COLOR,
            border_width=1,
            border_color=UI_DANGER_COLOR,
            text_color=UI_FG_COLOR,
            command=self._on_cancel_schedule,
        )
        self._btn_cancel_sched.grid(row=0, column=1, padx=(0, 4), pady=6)

        # II/▶ 일시정지·재개 버튼 (토글)
        # "⏸"는 일부 폰트에서 뭉개지므로 ASCII 기반 문자 사용
        is_paused = bool(self._scheduler and self._scheduler.is_paused)
        self._btn_pause_resume = ctk.CTkButton(
            self._countdown_frame,
            text="▶" if is_paused else "II",
            font=(ff, 10, "bold"),
            width=28, height=28,
            corner_radius=6,
            fg_color=UI_DANGER_BG,
            hover_color=UI_DANGER_COLOR,
            border_width=1,
            border_color=UI_DANGER_COLOR,
            text_color=UI_FG_COLOR,
            command=self._on_pause_resume,
        )
        self._btn_pause_resume.grid(row=0, column=2, padx=(0, 8), pady=6)

        # 예약 중이면 즉시 표시, 아니면 숨김
        if self._scheduler and self._scheduler.is_active:
            self._countdown_frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 0))
            self._tick_countdown()
        # 예약 중 아닐 땐 grid하지 않아 공간 차지 안 함

        # ━━━ 동작 선택 세그먼트 ━━━
        ctk.CTkLabel(
            main, text="동작",
            font=(ff, 11),
            text_color=UI_MUTED_FG,
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=24, pady=(14, 3))

        action_seg = ctk.CTkFrame(main, fg_color=UI_BTN_BG, corner_radius=12)
        action_seg.grid(row=3, column=0, sticky="ew", padx=24)
        action_seg.grid_columnconfigure((0, 1), weight=1)

        for col, (action_val, icon, label) in enumerate([
            (ACTION_SHUTDOWN, "⏻", "종료"),
            (ACTION_RESTART,  "↻", "재시작"),
        ]):
            btn = ctk.CTkButton(
                action_seg,
                text=f"{icon}  {label}",
                font=(ff, 13, "bold"),
                height=38,
                corner_radius=10,
                fg_color="transparent",
                hover_color=UI_BTN_HOVER,
                text_color=UI_SUB_FG_COLOR,
                command=lambda v=action_val: self._set_action(v),
            )
            btn.grid(row=0, column=col, sticky="ew", padx=4, pady=4)
            self._action_btns[action_val] = btn

        # ━━━ 입력 카드 (glass 레이어 1) ━━━
        card = ctk.CTkFrame(
            main,
            fg_color=UI_CARD_COLOR,
            corner_radius=16,
            border_width=1,
            border_color=UI_BORDER_COLOR,
        )
        card.grid(row=4, column=0, sticky="ew", padx=24, pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)

        # 숫자 Entry (glass 레이어 2)
        self.var_value = tk.StringVar(value="0")
        vcmd = (top.register(self._validate_int_input), "%P")
        self._entry = ctk.CTkEntry(
            card,
            textvariable=self.var_value,
            font=(ff, 44, "bold"),
            justify=tk.CENTER,
            height=68,
            fg_color=UI_CARD2_COLOR,
            border_width=0,
            corner_radius=10,
            text_color=UI_FG_COLOR,
            validate="key",
            validatecommand=vcmd,
        )
        self._entry.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))

        # 얇은 구분선
        ctk.CTkFrame(card, height=1, fg_color=UI_BORDER_COLOR, corner_radius=0).grid(
            row=1, column=0, sticky="ew", padx=12, pady=(10, 0)
        )

        # 단위 세그먼트 (카드 내부 glass)
        unit_seg = ctk.CTkFrame(card, fg_color=UI_BTN_BG, corner_radius=10)
        unit_seg.grid(row=2, column=0, pady=(8, 0))

        for unit_val, unit_label in [
            (UNIT_HOUR,   UNIT_LABEL_HOUR),
            (UNIT_MINUTE, UNIT_LABEL_MINUTE),
            (UNIT_SECOND, UNIT_LABEL_SECOND),
        ]:
            btn = ctk.CTkButton(
                unit_seg,
                text=unit_label,
                font=(ff, 12, "bold"),
                width=76,
                height=30,
                corner_radius=8,
                fg_color="transparent",
                hover_color=UI_BTN_HOVER,
                text_color=UI_SUB_FG_COLOR,
                command=lambda v=unit_val: self._set_unit(v),
            )
            btn.pack(side=tk.LEFT, padx=3, pady=3)
            self._unit_btns[unit_val] = btn

        # 환산 표시
        self._preview_var = tk.StringVar(value="즉시 실행")
        ctk.CTkLabel(
            card,
            textvariable=self._preview_var,
            font=(ff, 11),
            text_color=UI_SUB_FG_COLOR,
            anchor=tk.CENTER,
        ).grid(row=3, column=0, pady=(6, 10))

        # ━━━ 퀵 추가 + 초기화 버튼 ━━━
        quick_frame = ctk.CTkFrame(main, fg_color=UI_BG_COLOR, corner_radius=0)
        quick_frame.grid(row=5, column=0, sticky="ew", padx=24, pady=(10, 0))
        quick_frame.grid_columnconfigure((0, 1, 2), weight=1)

        for col, (btn_text, add_sec) in enumerate(QUICK_ADD_BUTTONS):
            b = ctk.CTkButton(
                quick_frame,
                text=btn_text,
                font=(ff, 12, "bold"),
                height=40,
                corner_radius=12,
                fg_color=UI_CARD_COLOR,
                hover_color=UI_BTN_HOVER,
                border_width=1,
                border_color=UI_BORDER_COLOR,
                text_color=UI_ACCENT_COLOR,
                command=lambda s=add_sec: self._quick_add(s),
            )
            b.grid(row=0, column=col, sticky="ew", padx=(0, 6) if col < 2 else 0)

        # 초기화 버튼
        reset_btn = ctk.CTkButton(
            quick_frame,
            text="↺",
            font=(ff, 15, "bold"),
            width=40,
            height=40,
            corner_radius=12,
            fg_color=UI_CARD_COLOR,
            hover_color=UI_DANGER_COLOR,
            border_width=1,
            border_color=UI_BORDER_COLOR,
            text_color=UI_MUTED_FG,
            command=self._reset_input,
        )
        reset_btn.grid(row=0, column=3, padx=(6, 0))

        # ━━━ 하단 버튼 ━━━
        footer = ctk.CTkFrame(main, fg_color=UI_BG_COLOR, corner_radius=0)
        footer.grid(row=6, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            footer,
            text="닫기",
            font=(ff, 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=UI_BTN_BG,
            hover_color=UI_BTN_HOVER,
            border_width=1,
            border_color=UI_BORDER_COLOR,
            text_color=UI_BTN_FG,
            command=self._on_cancel,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            footer,
            text="예약하기",
            font=(ff, 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=UI_DANGER_COLOR,
            hover_color=UI_DANGER_HOVER,
            text_color=UI_ACCENT_TEXT,
            command=self._on_confirm,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # ━━━ 자동 시작 토글 버튼 ━━━
        autostart_on = is_autostart_enabled()
        self._autostart_btn = ctk.CTkButton(
            footer,
            text=("✔  Windows 시작 시 자동 실행  (활성화됨)"
                  if autostart_on else
                  "Windows 시작 시 자동 실행  (비활성화됨)"),
            font=(ff, 12),
            height=36,
            corner_radius=10,
            fg_color=UI_SUCCESS_BG if autostart_on else UI_BTN_BG,
            hover_color=UI_SUCCESS_HOVER if autostart_on else UI_BTN_HOVER,
            border_width=1,
            border_color=UI_SUCCESS_COLOR if autostart_on else UI_BORDER_COLOR,
            text_color=UI_FG_COLOR if autostart_on else UI_MUTED_FG,
            command=self._on_toggle_autostart,
        )
        self._autostart_btn.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        top.bind("<Return>", lambda e: self._on_confirm())
        top.bind("<Escape>", lambda e: self._on_cancel())
        # 다른 창 클릭 시 topmost 해제 → 자연스럽게 뒤로 내려감
        top.bind("<FocusOut>", lambda e: top.attributes("-topmost", False))

        self._refresh_action_btns()
        self._refresh_unit_btns()
        self.var_value.trace_add("write", self._on_value_change)
        self._entry.focus_set()

    # ------------------------------------------------------------------ #
    # 상태 변경                                                            #
    # ------------------------------------------------------------------ #

    def _tick_countdown(self) -> None:
        """예약 중인 경우 남은 시간을 1초마다 카운트다운 배너에 갱신."""
        try:
            if self._scheduler and self._scheduler.is_active:
                label = (
                    ACTION_LABEL_RESTART
                    if self._scheduler.scheduled_action == ACTION_RESTART
                    else ACTION_LABEL_SHUTDOWN
                )
                # 일시정지 중이면 남은 초를 고정 표시
                if self._scheduler.is_paused:
                    total_sec = int(self._scheduler._pause_remaining)
                    h, rem = divmod(total_sec, 3600)
                    m, s = divmod(rem, 60)
                    self._countdown_var.set(
                        f"⏸  {label}까지  {h:02d}:{m:02d}:{s:02d}  (일시정지)"
                    )
                    self._countdown_after_id = self.top.after(1000, self._tick_countdown)
                    return
                if self._scheduler.scheduled_time:
                    remaining = self._scheduler.scheduled_time - datetime.now()
                    total_sec = int(remaining.total_seconds())
                    if total_sec > 0:
                        h, rem = divmod(total_sec, 3600)
                        m, s = divmod(rem, 60)
                        self._countdown_var.set(
                            f"⏱  {label}까지  {h:02d}:{m:02d}:{s:02d}  남았습니다"
                        )
                        self._countdown_after_id = self.top.after(1000, self._tick_countdown)
                        return
            # 예약이 없거나 시간 초과: 배너 숨김
            self._countdown_frame.grid_remove()
            self._countdown_var.set("")
        except tk.TclError:
            pass

    def _set_action(self, action: str) -> None:
        """동작(종료/재시작) 변경.

        Args:
            action: ACTION_SHUTDOWN 또는 ACTION_RESTART
        """
        self._action = action
        self._refresh_action_btns()

    def _set_unit(self, unit: str) -> None:
        """단위 변경 — 입력값을 0으로 초기화하고 _total_seconds를 0으로 리셋.

        Args:
            unit: UNIT_HOUR / UNIT_MINUTE / UNIT_SECOND
        """
        self._unit = unit
        self._refresh_unit_btns()
        self._updating = True
        self.var_value.set("0")
        self._updating = False
        self._total_seconds = 0
        self._update_preview()

    def _reset_input(self) -> None:
        """입력값과 _total_seconds를 0으로 초기화."""
        self._updating = True
        self.var_value.set("0")
        self._updating = False
        self._total_seconds = 0
        self._update_preview()

    def _refresh_action_btns(self) -> None:
        """동작 버튼 선택 상태 갱신."""
        for val, btn in self._action_btns.items():
            if val == self._action:
                accent = UI_SUCCESS_COLOR if val == ACTION_RESTART else UI_DANGER_COLOR
                hover = UI_SUCCESS_HOVER if val == ACTION_RESTART else UI_DANGER_HOVER
                btn.configure(fg_color=accent, hover_color=hover, text_color="#FFFFFF")
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=UI_BTN_HOVER,
                    text_color=UI_SUB_FG_COLOR,
                )

    def _refresh_unit_btns(self) -> None:
        """단위 버튼 선택 상태 갱신."""
        for val, btn in self._unit_btns.items():
            if val == self._unit:
                btn.configure(
                    fg_color=UI_ACCENT_COLOR,
                    hover_color=UI_ACCENT_HOVER,
                    text_color="#FFFFFF",
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=UI_BTN_HOVER,
                    text_color=UI_SUB_FG_COLOR,
                )

    # ------------------------------------------------------------------ #
    # 입력값 처리                                                          #
    # ------------------------------------------------------------------ #

    def _on_value_change(self, *_: object) -> None:
        """var_value 변경 시 _total_seconds 동기화 및 환산 표시 갱신."""
        if self._updating:
            return
        val = self._parse_int_or_zero(self.var_value.get()) or 0
        self._total_seconds = val * self._UNIT_TO_SEC[self._unit]
        self._update_preview()

    def _update_preview(self) -> None:
        """_total_seconds를 'X시간 Y분 Z초' 형태로 환산해 레이블 갱신."""
        t = self._total_seconds
        if t <= 0:
            self._preview_var.set("즉시 실행")
            return
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        parts: list[str] = []
        if h:
            parts.append(f"{h}시간")
        if m:
            parts.append(f"{m}분")
        if s:
            parts.append(f"{s}초")
        self._preview_var.set(" ".join(parts))

    def _quick_add(self, add_seconds: int) -> None:
        """총 초에 add_seconds를 더하고 현재 단위로 입력 필드 표시 갱신.

        Args:
            add_seconds: 추가할 초
        """
        self._total_seconds += add_seconds
        self._updating = True
        display_val = self._total_seconds // self._UNIT_TO_SEC[self._unit]
        self.var_value.set(str(display_val))
        self._updating = False
        self._update_preview()

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

    def _on_confirm(self) -> None:
        """확인 처리 — 0초 입력 시 즉시 실행 여부를 사용자에게 확인 후 callback 호출."""
        total = self._total_seconds
        if total == 0:
            self._show_immediate_confirm()
            return
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        action = self._action
        self._close(destroy=True)
        self.callback(action, h, m, s)

    def _show_immediate_confirm(self) -> None:
        """즉시 실행 확인 다이얼로그 (glass 다크 스타일)."""
        ff = _resolve_ui_font(self.top)
        action_label = ACTION_LABEL_RESTART if self._action == ACTION_RESTART else ACTION_LABEL_SHUTDOWN

        dlg = ctk.CTkToplevel(self.top)
        dlg.title("")
        dlg.resizable(False, False)
        dlg.transient(self.top)
        dlg.grab_set()

        # 중앙 배치
        dlg.update_idletasks()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        w, h = 320, 180
        dlg.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # topmost 잠깐 켜고 끄기
        dlg.attributes("-topmost", True)
        dlg.after(100, lambda: dlg.attributes("-topmost", False))

        main = ctk.CTkFrame(dlg, fg_color=UI_BG_COLOR, corner_radius=0)
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1)

        # 경고 아이콘 + 메시지
        top_row = ctk.CTkFrame(main, fg_color=UI_BG_COLOR, corner_radius=0)
        top_row.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        top_row.grid_columnconfigure(1, weight=1)

        badge = ctk.CTkFrame(top_row, width=40, height=40, fg_color=UI_DANGER_BG, corner_radius=10)
        badge.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        badge.grid_propagate(False)
        ctk.CTkLabel(
            badge, text="⚠",
            font=(ff, 18, "bold"),
            text_color=UI_DANGER_COLOR,
        ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        ctk.CTkLabel(
            top_row,
            text=f"즉시 {action_label}",
            font=(ff, 14, "bold"),
            text_color=UI_FG_COLOR,
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            top_row,
            text=f"지금 바로 시스템을 {action_label}합니다.\n계속하시겠습니까?",
            font=(ff, 11),
            text_color=UI_SUB_FG_COLOR,
            anchor="w",
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        # 버튼
        footer = ctk.CTkFrame(main, fg_color=UI_BG_COLOR, corner_radius=0)
        footer.grid(row=1, column=0, sticky="ew", padx=24, pady=(18, 20))
        footer.grid_columnconfigure((0, 1), weight=1)

        def _cancel() -> None:
            dlg.destroy()

        def _execute() -> None:
            dlg.destroy()
            action = self._action
            self._close(destroy=True)
            self.callback(action, 0, 0, 0)

        ctk.CTkButton(
            footer,
            text="취소",
            font=(ff, 12, "bold"),
            height=40,
            corner_radius=10,
            fg_color=UI_BTN_BG,
            hover_color=UI_BTN_HOVER,
            border_width=1,
            border_color=UI_BORDER_COLOR,
            text_color=UI_BTN_FG,
            command=_cancel,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            footer,
            text=f"즉시 {action_label}",
            font=(ff, 12, "bold"),
            height=40,
            corner_radius=10,
            fg_color=UI_DANGER_COLOR,
            hover_color=UI_DANGER_HOVER,
            text_color="#FFFFFF",
            command=_execute,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        dlg.bind("<Return>", lambda e: _execute())
        dlg.bind("<Escape>", lambda e: _cancel())

    def _on_cancel_schedule(self) -> None:
        """■ 예약 취소 버튼 처리 — 예약 취소 후 배너를 숨기고 창은 유지."""
        if self._scheduler is not None:
            self._scheduler.cancel_shutdown()
        try:
            self._countdown_frame.grid_remove()
            self._countdown_var.set("")
            if self._countdown_after_id is not None:
                self.top.after_cancel(self._countdown_after_id)
                self._countdown_after_id = None
        except tk.TclError:
            pass

    def _on_pause_resume(self) -> None:
        """⏸/▶ 일시정지·재개 버튼 처리 — 상태 토글 후 버튼 아이콘 갱신."""
        if self._scheduler is None:
            return
        if self._scheduler.is_paused:
            self._scheduler.resume_shutdown()
            self._btn_pause_resume.configure(text="II")
        else:
            self._scheduler.pause_shutdown()
            self._btn_pause_resume.configure(text="▶")

    def _on_toggle_autostart(self) -> None:
        """자동 시작 토글 버튼 처리 — 상태 전환 후 버튼 외형 갱신 및 토스트 표시."""
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
        """자동 시작 버튼 색상·텍스트 갱신."""
        autostart_on = is_autostart_enabled()
        self._autostart_btn.configure(
            text=("✔  Windows 시작 시 자동 실행  (활성화됨)"
                  if autostart_on else
                  "Windows 시작 시 자동 실행  (비활성화됨)"),
            fg_color=UI_SUCCESS_BG if autostart_on else UI_BTN_BG,
            hover_color=UI_SUCCESS_HOVER if autostart_on else UI_BTN_HOVER,
            border_color=UI_SUCCESS_COLOR if autostart_on else UI_BORDER_COLOR,
            text_color=UI_FG_COLOR if autostart_on else UI_MUTED_FG,
        )

    def _on_cancel(self) -> None:
        """닫기 / X버튼 / Esc 처리."""
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
        self._toast_type: str = ""  # "schedule"|"cancel"|"autostart_on"|"autostart_off"|"info"
        self._warning_timers: list[threading.Timer] = []
        self.is_paused: bool = False
        self._pause_remaining: float = 0.0  # 일시정지 시점의 남은 초

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
            pystray.MenuItem("종료 예약", lambda icon, item: self._queue.put("open_dialog")),
            pystray.MenuItem("예약 취소", lambda icon, item: self._queue.put("cancel")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Windows 시작 시 자동 실행",
                lambda icon, item: self._queue.put("toggle_autostart"),
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("프로그램 종료", lambda icon, item: self._queue.put("exit")),
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
            left_down  = bool(ctypes.windll.user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
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
        """
        open_dialog_requested = False
        try:
            while True:
                event = self._queue.get_nowait()
                if event == "open_dialog":
                    open_dialog_requested = True
                elif event == "cancel":
                    self.cancel_shutdown()
                elif event == "toggle_autostart":
                    self._toggle_autostart()
                elif event == "exit":
                    self._exit_app()
                elif isinstance(event, str) and event.startswith("warn:"):
                    warn_label = event[len("warn:"):]
                    label = (
                        ACTION_LABEL_RESTART
                        if self.scheduled_action == ACTION_RESTART
                        else ACTION_LABEL_SHUTDOWN
                    )
                    self._show_warning_toast(f"{label} {warn_label} 전입니다.")
        except queue.Empty:
            pass
        if open_dialog_requested:
            self._open_schedule_dialog()
        self._tk_root.after(QUEUE_POLL_INTERVAL_MS, self._process_queue)

    def _update_tooltip_loop(self) -> None:
        """트레이 툴팁에 남은 시간 표시 (1초 주기)."""
        if self.is_active and self.scheduled_time and self.tray_icon:
            remaining = self.scheduled_time - datetime.now()
            total_sec = int(remaining.total_seconds())
            if total_sec > 0:
                hours, rem = divmod(total_sec, 3600)
                minutes, seconds = divmod(rem, 60)
                label = (
                    ACTION_LABEL_RESTART
                    if self.scheduled_action == ACTION_RESTART
                    else ACTION_LABEL_SHUTDOWN
                )
                self.tray_icon.title = (
                    f"{label} 예약 중 - 남은 시간: "
                    f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                )
                self._tooltip_timer = threading.Timer(
                    TOOLTIP_UPDATE_INTERVAL_MS / 1000,
                    self._update_tooltip_loop,
                )
                self._tooltip_timer.daemon = True
                self._tooltip_timer.start()
            else:
                self._set_active_state(False)

    def _open_schedule_dialog(self) -> None:
        """종료 예약 다이얼로그 열기 (메인 스레드에서 실행).

        이미 열려 있는 다이얼로그가 있으면 포커스만 가져오고
        새 창을 만들지 않는다.
        """
        if self._active_dialog is not None:
            try:
                top = self._active_dialog.top
                top.deiconify()
                # 다른 앱 위로 잠깐 올라온 뒤 topmost 해제
                # → 포커스 잃으면 FocusOut 바인딩에 의해 다시 뒤로 내려감
                top.attributes("-topmost", True)
                top.lift()
                top.focus_force()
                top.after(100, lambda: top.attributes("-topmost", False))
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

    def _on_dialog_confirm(
        self, action: str, hours: int, minutes: int, seconds: int
    ) -> None:
        """다이얼로그 확인 콜백.

        Args:
            action: ACTION_SHUTDOWN 또는 ACTION_RESTART
            hours: 시
            minutes: 분
            seconds: 초
        """
        total = hours * 3600 + minutes * 60 + seconds
        self.schedule_action(action, total)

    def schedule_action(self, action: str, total_seconds: int) -> None:
        """시스템 종료 또는 재시작 예약.

        Args:
            action: ACTION_SHUTDOWN 또는 ACTION_RESTART
            total_seconds: 실행까지 대기 시간 (초). 0이면 즉시 실행.
        """
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None

        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()

        self.scheduled_action = action
        label = ACTION_LABEL_RESTART if action == ACTION_RESTART else ACTION_LABEL_SHUTDOWN

        if total_seconds == 0:
            self._show_confirm_toast(f"시스템을 즉시 {label}합니다.", toast_type="schedule")
            self._tk_root.after(CONFIRM_TOAST_DURATION_MS, self._execute_action)
            return

        self.scheduled_time = datetime.now() + timedelta(seconds=total_seconds)
        self._set_active_state(True)
        self._update_tooltip_loop()

        self._shutdown_timer = threading.Timer(total_seconds, self._execute_action)
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()

        # 종료 10분/5분/1분 전 토스트 알림 등록 (예약 시간보다 짧은 경우만)
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

        hours, rem = divmod(total_seconds, 3600)
        minutes, secs = divmod(rem, 60)

        parts: list[str] = []
        if hours > 0:
            parts.append(f"{hours}시간")
        if minutes > 0:
            parts.append(f"{minutes}분")
        if secs > 0:
            parts.append(f"{secs}초")
        time_str = " ".join(parts) if parts else "0초"

        self._show_confirm_toast(f"{time_str} 후 시스템이 {label}됩니다.", toast_type="schedule")

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
        user32  = ctypes.windll.user32
        gdi32   = ctypes.windll.gdi32
        RADIUS  = 12  # 모서리 반경

        # ── 1. SetWindowRgn — 둥근 클리핑 마스크 (Win10/11 공통, 즉시 적용)
        rgn = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, RADIUS * 2, RADIUS * 2)
        user32.SetWindowRgn(hwnd, rgn, True)

        # ── 2. Windows 11 네이티브 둥근 모서리 (DWMWCP_ROUND = 2)
        try:
            DWMWA_CORNER = 33
            pref = ctypes.c_int(2)
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CORNER, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

        # ── 3. Acrylic blur (ACCENT_ENABLE_ACRYLICBLURBEHIND = 4)
        #    GradientColor: AABBGGRR 형식
        try:
            class ACCENTPOLICY(ctypes.Structure):
                _fields_ = [
                    ("AccentState",   ctypes.c_int),
                    ("AccentFlags",   ctypes.c_int),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId",   ctypes.c_int),
                ]

            class WINCOMPATTRDATA(ctypes.Structure):
                _fields_ = [
                    ("Attribute",  ctypes.c_int),
                    ("Data",       ctypes.c_void_p),
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
            alpha = 1.0 - (progress ** 2)
            window.attributes("-alpha", max(0.0, alpha))
            next_progress = progress + (1.0 / _TOTAL_STEPS)
            window.after(
                _INTERVAL_MS,
                lambda: ShutdownScheduler._fade_out_toast(window, on_done, next_progress),
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
            rgn = gdi32.CreateRoundRectRgn(0, 0, _TOAST_W + 1, th + 1, RADIUS * 2, RADIUS * 2)
            user32.SetWindowRgn(hwnd, rgn, True)
            # Windows 11 네이티브 둥근 모서리
            dwmapi = ctypes.windll.dwmapi
            pref = ctypes.c_int(2)
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
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
                        "autostart_on"(초록) | "autostart_off"(회색) | "info"
        """
        # 타입별 색상·제목·아이콘 결정
        action_label = (
            ACTION_LABEL_RESTART
            if self.scheduled_action == ACTION_RESTART
            else ACTION_LABEL_SHUTDOWN
        )
        _TYPE_META: dict[str, tuple[str, str, str]] = {
            "schedule":      (f"{action_label} 예약",    "⏻", UI_DANGER_COLOR),
            "cancel":        (f"{action_label} 예약 취소", "⏻", UI_ACCENT_COLOR),
            "autostart_on":  ("자동 시작 활성화",          "✔", UI_SUCCESS_COLOR),
            "autostart_off": ("자동 시작 비활성화",         "✕", UI_SUB_FG_COLOR),
            "info":          ("알림",                     "ℹ", UI_ACCENT_COLOR),
        }
        title, icon, accent = _TYPE_META.get(
            toast_type, _TYPE_META["info"]
        )

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
        """실제 종료/재시작 명령 실행 (타이머 콜백)."""
        flag = "/r" if self.scheduled_action == ACTION_RESTART else "/s"
        try:
            subprocess.run(
                ["shutdown", flag, "/t", "0", "/f"],
                check=False,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            print(f"{self.scheduled_action} 명령 실행 실패: {e}", file=sys.stderr)

    def cancel_shutdown(self) -> None:
        """종료/재시작 예약 취소."""
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

        self.is_paused = False
        self._pause_remaining = 0.0
        self._set_active_state(False)
        self._show_confirm_toast(f"{label} 예약이 취소되었습니다.", toast_type="cancel")

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
        if self._tooltip_timer:
            self._tooltip_timer.cancel()
            self._tooltip_timer = None

    def resume_shutdown(self) -> None:
        """일시정지된 종료 타이머 재개."""
        if not self.is_active or not self.is_paused:
            return
        self.is_paused = False
        total_seconds = int(self._pause_remaining)
        self._pause_remaining = 0.0
        # 남은 시간으로 재예약 (scheduled_time 갱신)
        self.scheduled_time = datetime.now() + timedelta(seconds=total_seconds)
        self._update_tooltip_loop()
        self._shutdown_timer = threading.Timer(total_seconds, self._execute_action)
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()
        # 경고 타이머 재등록
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
            self.tray_icon.title = TRAY_TOOLTIP_IDLE if not active else self.tray_icon.title

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

    def _exit_app(self) -> None:
        """프로그램 종료."""
        if self._tooltip_timer:
            self._tooltip_timer.cancel()
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
        for t in self._warning_timers:
            t.cancel()
        self._warning_timers.clear()
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

    app = ShutdownScheduler()
    app.run()


if __name__ == "__main__":
    main()
