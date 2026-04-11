; ============================================================
;  ShutdownScheduler - Inno Setup Script
;  빌드 전에 dist\shutdown_scheduler.exe 가 존재해야 함
;  (build.bat 이 PyInstaller 실행 후 ISCC 호출)
; ============================================================

#define MyAppName "ShutdownScheduler"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Jamong"
#define MyAppExeName "shutdown_scheduler.exe"
#define MyTaskName "ShutdownScheduler_AutoStart"

[Setup]
AppId={{B7F4A9C2-3D6E-4F1A-8B2D-ShutdownSched01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=dist_package
OutputBaseFilename=ShutdownScheduler_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=app_icon.ico
ShowLanguageDialog=no
; 이미 설치된 버전이 있으면 자동으로 제거 후 재설치
CloseApplications=yes
CloseApplicationsFilter=*{#MyAppExeName}*

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 아이콘:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; runascurrentuser: 설치 마법사의 관리자 권한을 그대로 사용
; (exe가 requireAdministrator manifest를 포함하므로 필수)
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} 실행"; Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
; 제거 시 실행 중인 프로세스 종료 및 자동 시작 작업 삭제
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""{#MyTaskName}"" /f"; Flags: runhidden; RunOnceId: "RemoveTask"

[Code]
// 설치 시작 전: 이미 설치된 버전이 있으면 자동으로 Uninstaller 실행
function GetUninstallerPath(): String;
var
  RegKey: String;
  UninstPath: String;
begin
  Result := '';
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7F4A9C2-3D6E-4F1A-8B2D-ShutdownSched01}_is1';
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey, 'UninstallString', UninstPath) then
    Result := UninstPath
  else if RegQueryStringValue(HKEY_CURRENT_USER, RegKey, 'UninstallString', UninstPath) then
    Result := UninstPath;
end;

function InitializeSetup(): Boolean;
var
  UninstPath: String;
  ResultCode: Integer;
begin
  Result := True;
  UninstPath := GetUninstallerPath();
  if UninstPath <> '' then begin
    // 기존 버전 발견 → 프로세스 먼저 종료
    Exec('taskkill.exe', '/f /im {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // Uninstaller 실행 (/SILENT: 조용히, /NORESTART: 재시작 없음)
    if not Exec(RemoveQuotes(UninstPath), '/SILENT /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
      MsgBox('이전 버전 제거에 실패했습니다. 수동으로 제거 후 다시 설치해 주세요.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;
