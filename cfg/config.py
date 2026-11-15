"""
설정 파일 - 상수 및 전역 설정값 관리.
"""

# --- 단축키 설정 ---
HOTKEY_SCHEDULE: str = "alt+q"  # 종료 예약 다이얼로그
HOTKEY_CANCEL: str = "alt+s"  # 예약 취소

# --- UI 설정 ---
DIALOG_TITLE: str = "종료 예약"
DIALOG_WIDTH: int = 340
# 최소 높이임 — 실제 높이는 화면 내용(reqheight)에 맞춤
DIALOG_HEIGHT: int = 360

# --- customtkinter 테마 ---
CTK_APPEARANCE: str = "dark"
CTK_COLOR_THEME: str = "blue"

# --- 폰트 ---
UI_FONT_FAMILY: str = "Segoe UI Variable"
UI_FONT_FAMILY_FALLBACK: str = "Segoe UI"
UI_FONT_SIZE: int = 13
UI_FONT_SIZE_LARGE: int = 15
# 예약 창 한글 글꼴 후보 (앞에서부터 설치된 것을 씀). Pretendard 는 동봉하지 않음
UI_FONT_FAMILIES_KO: tuple[str, ...] = ("Pretendard", "Segoe UI", "맑은 고딕")
# 다이얼 숫자 — Segoe UI 숫자는 폭이 같아 카운트다운 중 흔들리지 않음 (실측)
UI_FONT_NUMBER: str = "Segoe UI"
# 아이콘 글리프(⏻ ↻ ☾ ↺ ▶)가 들어 있는 글꼴
UI_FONT_SYMBOL: str = "Segoe UI Symbol"

# --- Glass 다크 팔레트 ---
# 레이어 구조: 배경(BG) → 카드(CARD) → 입력(CARD2) 순으로 밝아짐
UI_BG_COLOR: str = "#0D1117"  # 최심층 배경 (GitHub Dark 수준)
UI_CARD_COLOR: str = "#161B22"  # 1차 카드 (glass 레이어 1)
UI_CARD2_COLOR: str = "#1F2937"  # 2차 카드 (glass 레이어 2 / 입력)
UI_FG_COLOR: str = "#F0F6FC"  # 기본 텍스트 (밝은 흰색)
UI_SUB_FG_COLOR: str = "#8B949E"  # 2차 텍스트
UI_MUTED_FG: str = "#484F58"  # 3차 텍스트 (힌트)

# 액센트 — 선명한 블루
UI_ACCENT_COLOR: str = "#58A6FF"  # 블루 액센트
UI_ACCENT_HOVER: str = "#79B8FF"  # 블루 호버
UI_ACCENT_LIGHT: str = "#1C2D40"  # 블루 틴트 배경 (아이콘 뱃지)
UI_ACCENT_TEXT: str = "#FFFFFF"

# 버튼 — glass 레이어
UI_BTN_BG: str = "#21262D"  # glass 버튼 배경
UI_BTN_HOVER: str = "#30363D"  # glass 버튼 호버
UI_BTN_FG: str = "#C9D1D9"  # 버튼 텍스트

# 구분선
UI_BORDER_COLOR: str = "#30363D"  # glass 테두리

# 토스트 전용 퓨어 블랙 팔레트
UI_TOAST_BG: str = "#000000"  # 토스트 배경 (순수 블랙)
UI_TOAST_BORDER: str = "#333333"  # 토스트 외곽선
UI_TOAST_BADGE_BG: str = "#141414"  # 아이콘 뱃지 배경
UI_TOAST_FG: str = "#FFFFFF"  # 제목 텍스트 (순백)
UI_TOAST_SUB_FG: str = "#999999"  # 본문 텍스트
UI_TOAST_CLOSE: str = "#555555"  # 닫기 버튼
# confirm 토스트 액센트 — 화이트 계열
UI_TOAST_CONFIRM_ACCENT: str = "#FFFFFF"
UI_TOAST_CONFIRM_ICON: str = "#BBBBBB"
# warning 토스트 액센트 — 앰버(고급 주황)
UI_TOAST_WARN_ACCENT: str = "#F0A500"
UI_TOAST_WARN_ICON: str = "#F0A500"

# 의미 색상
UI_DANGER_COLOR: str = "#F85149"  # 종료 (레드)
UI_DANGER_BG: str = "#2D1117"
UI_DANGER_HOVER: str = "#FF7B72"
UI_SUCCESS_COLOR: str = "#3FB950"  # 재시작 (그린)
UI_SUCCESS_BG: str = "#0D2A15"
UI_SUCCESS_HOVER: str = "#56D364"

# --- 호환용 ---
UI_GLASS_LIGHT: str = "#21262D"
UI_GLASS_DARK: str = "#161B22"
UI_GLASS_HOVER: str = "#30363D"
UI_GLASS_ACTIVE: str = "#58A6FF"
UI_GLASS_ACTIVE_TEXT: str = "#FFFFFF"
UI_SEG_CONTAINER: str = "#21262D"
UI_SEG_ACTIVE: str = "#58A6FF"
UI_SEG_ACTIVE_TEXT: str = "#FFFFFF"
UI_SEG_INACTIVE_TEXT: str = "#8B949E"
UI_SEL_BG_COLOR: str = "#30363D"
UI_INPUT_BG: str = "#1F2937"

# --- 입력 범위 ---
INPUT_VALUE_MAX: int = 9999

# --- 시간 단위 ---
UNIT_HOUR: str = "hour"
UNIT_MINUTE: str = "minute"
UNIT_SECOND: str = "second"
UNIT_LABEL_HOUR: str = "시간"
UNIT_LABEL_MINUTE: str = "분"
UNIT_LABEL_SECOND: str = "초"

# --- 퀵 추가 버튼 (표시 텍스트, 추가할 초) ---
QUICK_ADD_BUTTONS: list[tuple[str, int]] = [
    ("+5분", 5 * 60),
    ("+30분", 30 * 60),
    ("+1시간", 60 * 60),
]

# --- 다이얼 링 ---
# 링 한 바퀴 = 120분, 드래그는 1분 단위
RING_MAX_MINUTES: int = 120
# Windows 휠 한 칸의 delta 값
WHEEL_DELTA_UNIT: int = 120

# --- 모니터 끄는 시간 칩 (분 전, 0 = 종료 직전) ---
MONITOR_LEAD_CHIPS: tuple[int, ...] = (0, 1, 5, 10)

# --- 예약 시간 상한 ---
# Windows threading.TIMEOUT_MAX(4294967초, 약 49.7일)를 넘는 Timer 는 스레드 안에서
# OverflowError 로 죽어 종료가 실행되지 않음 — 시간 입력 3자리에 맞춘 999시간으로 막음
MAX_SCHEDULE_SECONDS: int = 999 * 3600

# --- 예약됨 화면 ---
EXTEND_SECONDS: int = 600

# --- 재실행 시 예약 복원 (%APPDATA%\<APP_NAME>\schedule.json) ---
SCHEDULE_FILE_NAME: str = "schedule.json"
SCHEDULE_FILE_VERSION: int = 1
# 남은 시간이 이보다 짧으면 바로 실행되지 않게 일시정지 상태로 복원함
RESTORE_PAUSE_UNDER_SEC: int = 600
# 저장 시각보다 이만큼 넘게 과거면 시계가 바뀐 것으로 봄
RESTORE_CLOCK_SKEW_SEC: int = 60
# 복원 파일의 시각·초가 지금에서 이보다 멀면 깨진 값으로 봄 (예약 상한과 같음)
RESTORE_MAX_SPAN_SEC: int = MAX_SCHEDULE_SECONDS

# --- 확인 토스트 ---
CONFIRM_TOAST_DURATION_MS: int = 3000

# --- 트레이 아이콘 ---
TRAY_ICON_SIZE: tuple[int, int] = (64, 64)
TRAY_ICON_COLOR_IDLE: str = "#4A90D9"
TRAY_ICON_COLOR_ACTIVE: str = "#E74C3C"
TRAY_TOOLTIP_IDLE: str = "종료 예약기 - 대기 중"

# --- 갱신 주기 ---
TOOLTIP_UPDATE_INTERVAL_MS: int = 1000
QUEUE_POLL_INTERVAL_MS: int = 50

# --- 앱 버전 ---
APP_VERSION: str = "1.3.0"

# --- 동작 종류 ---
ACTION_SHUTDOWN: str = "shutdown"
ACTION_RESTART: str = "restart"
ACTION_LABEL_SHUTDOWN: str = "종료"
ACTION_LABEL_RESTART: str = "재시작"
# PC 는 켜 둔 채 모니터만 끄는 단독 예약
ACTION_MONITOR_OFF: str = "monitor_off"
ACTION_LABEL_MONITOR_OFF: str = "모니터 끄기"

# --- 설치 / 자동 시작 ---
APP_NAME: str = "ShutdownScheduler"
TASK_NAME: str = "ShutdownScheduler_AutoStart"
INSTALL_DIR_NAME: str = "ShutdownScheduler"
EXECUTABLE_NAME: str = "shutdown_scheduler.exe"

# --- 사용자 설정 / 추적 로그 (%APPDATA%\<APP_NAME>\ 아래, cfg 는 exe 에 묶여 저장 불가) ---
SETTINGS_FILE_NAME: str = "settings.json"
SETTINGS_KEY_MONITOR_OFF: str = "monitor_off_on_shutdown"
SETTINGS_KEY_THEME: str = "theme"
SETTINGS_KEY_FOLLOW_SYSTEM: str = "followSystem"
SETTINGS_KEY_DARK_THEME: str = "darkTheme"
SETTINGS_KEY_LIGHT_THEME: str = "lightTheme"
SETTINGS_KEY_CONTRAST_FIX: str = "contrastFix"
FOLLOW_SYSTEM_DEFAULT: bool = False
CONTRAST_FIX_DEFAULT: bool = True
SYSTEM_THEME_REG_KEY: str = (
    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
)
SYSTEM_THEME_REG_VALUE: str = "AppsUseLightTheme"
# 레지스트리 읽기만 하므로 부담이 작고, 2초면 전환이 늦게 느껴지지 않음
SYSTEM_THEME_POLL_MS: int = 2000
SETTINGS_DIALOG_TITLE: str = "테마 설정"
SETTINGS_WINDOW_GAP: int = 8
SHUTDOWN_TRACE_FILE_NAME: str = "shutdown_trace.log"
SHUTDOWN_TRACE_MAX_BYTES: int = 1024 * 1024

# --- 종료 시 모니터 끄기 (DDC/CI VCP 0xD6) ---
MONITOR_OFF_DEFAULT: bool = False
# 주 모니터는 5, 세로 모니터는 4 로 꺼짐 — 실측값임
MONITOR_OFF_VCP_SEQUENCE: tuple[int, ...] = (5, 4)
# 종료 접수 뒤 로그오프 알림까지 약 2초라 그 안에 끝냄
MONITOR_OFF_BUDGET_SEC: float = 1.5
VCP_POWER_MODE: int = 0xD6
# 두 모니터 모두 읽기에 응답함 — D6 은 주 모니터가 읽기 미지원임
VCP_BRIGHTNESS: int = 0x10
# 읽기 2회 약 130ms — 응답 없는 모니터가 종료를 늦추지 않게 자름
MONITOR_PRE_READ_WAIT_SEC: float = 0.3
# SetProcessShutdownParameters 의 앱용 최후 구간(0x100~0x1FF) 시작값임
SHUTDOWN_LEVEL_LAST: int = 0x100
# "종료 N분 전 끄기" 입력란 3자리에 맞춘 N 상한 (분)
MONITOR_LEAD_MAX_MIN: int = 999
MONITOR_OFF_WARN_SEC: int = 60
