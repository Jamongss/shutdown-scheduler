@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  ShutdownScheduler Build Script (PyInstaller + Inno Setup)
echo ============================================================
echo.

REM ---------- [0/5] Detect Python 3.13 ----------
echo [0/5] Detecting Python 3.13...
set "PY_CMD="

where py >nul 2>&1
if not errorlevel 1 (
    py -3.13 --version >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=py -3.13"
        goto :py_found
    )
)

where python3.13 >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python3.13"
    goto :py_found
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :py_found
)

echo [FAIL] Python launcher not found. Install Python 3.13 or fix PATH.
goto :fail

:py_found
echo      Using: !PY_CMD!
!PY_CMD! --version
if errorlevel 1 (
    echo [FAIL] Python execution failed
    goto :fail
)
echo.

REM ---------- [1/5] Detect Inno Setup (ISCC.exe) ----------
echo [1/5] Detecting Inno Setup (ISCC.exe)...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    where ISCC.exe >nul 2>&1
    if not errorlevel 1 set "ISCC=ISCC.exe"
)
if not defined ISCC (
    echo [FAIL] Inno Setup ^(ISCC.exe^) not found.
    echo        Install from https://jrsoftware.org/isdl.php
    goto :fail
)
echo      Using: !ISCC!
echo.

REM ---------- [2/5] Ensure dependencies ----------
echo [2/5] Checking dependencies...
!PY_CMD! -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo      Installing PyInstaller...
    !PY_CMD! -m pip install pyinstaller
    if errorlevel 1 (
        echo [FAIL] PyInstaller install failed
        goto :fail
    )
) else (
    echo      PyInstaller: already installed
)

!PY_CMD! -m pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo      Installing customtkinter...
    !PY_CMD! -m pip install customtkinter>=5.2.0
    if errorlevel 1 (
        echo [FAIL] customtkinter install failed
        goto :fail
    )
) else (
    echo      customtkinter: already installed
)

!PY_CMD! -m pip show pillow >nul 2>&1
if errorlevel 1 (
    echo      Installing Pillow...
    !PY_CMD! -m pip install Pillow>=10.0.0
    if errorlevel 1 (
        echo [FAIL] Pillow install failed
        goto :fail
    )
) else (
    echo      Pillow: already installed
)

!PY_CMD! -m pip show pystray >nul 2>&1
if errorlevel 1 (
    echo      Installing pystray...
    !PY_CMD! -m pip install pystray>=0.19.5
    if errorlevel 1 (
        echo [FAIL] pystray install failed
        goto :fail
    )
) else (
    echo      pystray: already installed
)

!PY_CMD! -m pip show keyboard >nul 2>&1
if errorlevel 1 (
    echo      Installing keyboard...
    !PY_CMD! -m pip install keyboard>=0.13.5
    if errorlevel 1 (
        echo [FAIL] keyboard install failed
        goto :fail
    )
) else (
    echo      keyboard: already installed
)
echo.

REM ---------- [3/6] Clean previous artifacts ----------
echo [3/6] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dist_package rmdir /s /q dist_package
if exist shutdown_scheduler.spec del /q shutdown_scheduler.spec
if exist app_icon.ico del /q app_icon.ico
echo      Done
echo.

REM ---------- [4/6] Generate app_icon.ico ----------
echo [4/6] Generating app_icon.ico...
!PY_CMD! generate_icon.py
if errorlevel 1 (
    echo [FAIL] Icon generation failed
    goto :fail
)
if not exist "app_icon.ico" (
    echo [FAIL] app_icon.ico was not created
    goto :fail
)
echo.

REM ---------- [5/6] PyInstaller build ----------
echo [5/6] Running PyInstaller...
echo      (this may take 30s ~ 2min)
!PY_CMD! -m PyInstaller --onefile --noconsole --name shutdown_scheduler --add-data "cfg;cfg" --collect-data customtkinter --hidden-import customtkinter --icon app_icon.ico --uac-admin shutdown_scheduler.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed
    goto :fail
)
if not exist "dist\shutdown_scheduler.exe" (
    echo [FAIL] dist\shutdown_scheduler.exe was not created
    goto :fail
)
echo.

REM ---------- [6/6] Inno Setup compile ----------
echo [6/6] Compiling installer with Inno Setup...
"!ISCC!" installer.iss
if errorlevel 1 (
    echo [FAIL] Inno Setup compile failed
    goto :fail
)
echo.

echo ============================================================
echo  [OK] Build complete
echo  Installer: %~dp0dist_package\ShutdownScheduler_Setup_1.1.0.exe
echo.
echo  Distribute this single .exe file. Users just double-click
echo  it to install (UAC prompt will appear).
echo ============================================================
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo ============================================================
echo  [FAIL] Build aborted. See messages above.
echo ============================================================
echo.
pause
endlocal
exit /b 1
