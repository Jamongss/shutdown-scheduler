"""
설정 파일 - 상수 및 전역 설정값 관리.
"""

# --- 단축키 설정 ---
HOTKEY_SCHEDULE: str = "alt+q"  # 종료 예약 다이얼로그
HOTKEY_CANCEL: str = "alt+s"    # 예약 취소

# --- UI 설정 ---
DIALOG_TITLE: str = "종료 예약"
DIALOG_WIDTH: int = 420
DIALOG_HEIGHT: int = 580

# --- customtkinter 테마 ---
CTK_APPEARANCE: str = "dark"
CTK_COLOR_THEME: str = "blue"

# --- 폰트 ---
UI_FONT_FAMILY: str = "Segoe UI Variable"
UI_FONT_FAMILY_FALLBACK: str = "Segoe UI"
UI_FONT_SIZE: int = 13
UI_FONT_SIZE_LARGE: int = 15

# --- Glass 다크 팔레트 ---
# 레이어 구조: 배경(BG) → 카드(CARD) → 입력(CARD2) 순으로 밝아짐
UI_BG_COLOR: str = "#0D1117"         # 최심층 배경 (GitHub Dark 수준)
UI_CARD_COLOR: str = "#161B22"       # 1차 카드 (glass 레이어 1)
UI_CARD2_COLOR: str = "#1F2937"      # 2차 카드 (glass 레이어 2 / 입력)
UI_FG_COLOR: str = "#F0F6FC"         # 기본 텍스트 (밝은 흰색)
UI_SUB_FG_COLOR: str = "#8B949E"     # 2차 텍스트
UI_MUTED_FG: str = "#484F58"         # 3차 텍스트 (힌트)

# 액센트 — 선명한 블루
UI_ACCENT_COLOR: str = "#58A6FF"     # 블루 액센트
UI_ACCENT_HOVER: str = "#79B8FF"     # 블루 호버
UI_ACCENT_LIGHT: str = "#1C2D40"     # 블루 틴트 배경 (아이콘 뱃지)
UI_ACCENT_TEXT: str = "#FFFFFF"

# 버튼 — glass 레이어
UI_BTN_BG: str = "#21262D"           # glass 버튼 배경
UI_BTN_HOVER: str = "#30363D"        # glass 버튼 호버
UI_BTN_FG: str = "#C9D1D9"           # 버튼 텍스트

# 구분선
UI_BORDER_COLOR: str = "#30363D"     # glass 테두리

# 의미 색상
UI_DANGER_COLOR: str = "#F85149"     # 종료 (레드)
UI_DANGER_BG: str = "#2D1117"
UI_DANGER_HOVER: str = "#FF7B72"
UI_SUCCESS_COLOR: str = "#3FB950"    # 재시작 (그린)
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
APP_VERSION: str = "1.1.0"

# --- 동작 종류 ---
ACTION_SHUTDOWN: str = "shutdown"
ACTION_RESTART: str = "restart"
ACTION_LABEL_SHUTDOWN: str = "종료"
ACTION_LABEL_RESTART: str = "재시작"

# --- 설치 / 자동 시작 ---
APP_NAME: str = "ShutdownScheduler"
TASK_NAME: str = "ShutdownScheduler_AutoStart"
INSTALL_DIR_NAME: str = "ShutdownScheduler"
EXECUTABLE_NAME: str = "shutdown_scheduler.exe"
