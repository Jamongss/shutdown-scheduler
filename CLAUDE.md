# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

Windows 시스템 트레이 백그라운드 앱. `Alt+Q`로 시/분/초 단위 종료·재시작 예약, `Alt+S`로 취소.
Python 단일 파일(`shutdown_scheduler.py`) + 설정 모듈(`cfg/config.py`) 구조.

## 개발 환경 명령어

```bash
# 의존성 설치 (Python 3.13 필요)
python3.13 -m pip install -r requirements.txt

# 실행 (Windows 환경에서)
python3.13 shutdown_scheduler.py

# 포맷팅
black .

# 테스트
pytest
```

> 빌드(`build.bat`) 및 배포는 Windows 환경에서만 실행. Claude Code에서 직접 실행 금지.

## 소스 이중화 구조

- **실제 실행 위치 (Windows):** 개발자 로컬 작업 폴더 (WSL 에서는 `/mnt/c/...` 마운트 경로로 접근)
- **백업/레포 경로 (WSL):** WSL 홈 하위의 레포 클론 경로

**수정 절차:**
1. 실제 실행 위치(WSL 마운트 경로)의 파일을 백업 후 수정
2. 수정 후 백업 경로(WSL 레포 클론)로 동기화
3. `git add . && git commit -m "..." && git push origin main`

**백업 경로:** `back/yyyymmdd/파일명.back_yyyymmddHHMMSS`

## 아키텍처

### 스레드 모델

- **메인 스레드:** tkinter mainloop + 50ms 큐 폴링(`_process_queue`) + 1초 툴팁 갱신
- **daemon 스레드:** pystray `tray_icon.run()` (블로킹)
- **keyboard 내부 스레드:** 단축키 감지 → `queue.put()`만 수행

**원칙:** tkinter UI 조작은 반드시 메인 스레드에서만. `_process_queue`에서 `open_dialog_requested` 플래그로 중복 다이얼로그 방지.

### ScheduleDialog 상태 관리

`_total_seconds: int`가 단일 진실 소스.
- 입력 변경 → `_on_value_change` → `_total_seconds` 업데이트 → `_update_preview()`
- 단위 변경(`_set_unit`) → 입력 초기화 (`var_value = "0"`, `_total_seconds = 0`)
- 퀵 추가(`_quick_add`) → `_total_seconds += add_seconds` → 현재 단위로 재표시
- `_updating: bool` 플래그로 `var_value` trace 재진입 방지
- `_total_seconds == 0` 확인 시 → `_show_immediate_confirm()` 호출

### 토스트 창

`tk.Toplevel + overrideredirect(True)` 사용 (CTkToplevel 불가).
Windows API로 둥근 모서리 + Acrylic blur 적용:
- `_get_hwnd()`: `wm_frame()` + `GetAncestor(GA_ROOT=2)`로 실제 Win32 핸들 획득
- `_apply_rounded_glass()`: `SetWindowRgn` + `DwmSetWindowAttribute` + `SetWindowCompositionAttribute`

단일 토스트 관리: 예약/취소 전환 시 새 창 생성 없이 `_toast_label.configure(text=...)`로 즉시 교체.

### 단일 인스턴스

`_acquire_single_instance_mutex()`: Windows Named Mutex(`Global\ShutdownScheduler_SingleInstance`).
`GetLastError() == 183` → `sys.exit(0)`.

## 절대 금지 사항

1. **`shutdown /t <초>` 방식 사용 금지** — 600초 미만 시 Windows 토스트 알림 강제 표시됨
   - 종료 실행: `threading.Timer` + `shutdown /s /t 0 /f` 방식만 허용
2. **ttk 위젯 사용 금지** — Windows 시스템 테마가 배경색을 오버라이드함 → `tk` + `customtkinter`만 사용
3. **`build.bat` 직접 실행 금지** (실제 배포 명령)
4. **개인 로컬 경로(`C:\Users\<사용자>\...`) 포함 파일 커밋 금지**

## UI 패턴

### topmost 처리

- **다이얼로그:** 창 열릴 때만 100ms 동안 topmost → 이후 False로 해제
- **토스트:** topmost=True 유지

### 하단 고정 버튼 (tk.pack 사용 시)

footer 프레임을 **콘텐츠보다 먼저 `side=BOTTOM`으로 pack**해야 버튼이 잘리지 않음.
(현재는 customtkinter grid 방식 사용 중 — `tk.Toplevel` 직접 사용 시 적용)

## 버전 관리

수정 완료 시 3곳 동시 업데이트:
1. `cfg/config.py` → `APP_VERSION`
2. `installer.iss` → `#define MyAppVersion`
3. `build.bat` → 출력 메시지 버전 문자열

## 빌드 파이프라인 (Windows에서만 실행)

```
build.bat
  → generate_icon.py → app_icon.ico
  → PyInstaller --onefile --collect-data customtkinter --hidden-import customtkinter
                --add-data "cfg;cfg" --uac-admin
  → Inno Setup → dist_package\ShutdownScheduler_Setup_X.X.X.exe
```

PyInstaller 필수 옵션: `--collect-data customtkinter`, `--add-data "cfg;cfg"`, `cfg/__init__.py` 존재 필수.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
