; setup.iss — Inno Setup 6 script for FlutterAIStudio
; Produces a single FlutterAIStudio_Setup.exe installer (~4-5 GB)

#define AppName      "FlutterAIStudio"
#define AppVersion   "1.0.0"
#define AppPublisher "Your Company"
#define AppURL       "http://localhost:8000"
#define AppExe       "FlutterAIStudio.exe"
#define StageDir     "build_tmp\FlutterAIStudio"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=FlutterAIStudio_Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
DisableProgramGroupPage=yes
; Run as admin to write to Program Files
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenu";   Description: "Create Start Menu shortcut"; GroupDescription: "Shortcuts:"

[Files]
; Main application (everything in the staging dir)
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Launch app after install (optional)
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop services on uninstall
Filename: "taskkill"; Parameters: "/F /IM redis-server.exe";  Flags: runhidden; RunOnceId: "KillRedis"
Filename: "taskkill"; Parameters: "/F /IM postgres.exe";      Flags: runhidden; RunOnceId: "KillPostgres"
Filename: "taskkill"; Parameters: "/F /IM FlutterAIStudio.exe"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
// Show a warning if less than 8 GB free on the target drive
function NextButtonClick(CurPageID: Integer): Boolean;
var
  FreeBytes: Int64;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    if GetSpaceFreeOnDisk(WizardDirValue, FreeBytes) then begin
      if FreeBytes < 8589934592 then begin  // 8 GB
        if MsgBox('Warning: Less than 8 GB free on the selected drive. The application requires approximately 5 GB. Continue anyway?',
                  mbConfirmation, MB_YESNO) = IDNO then
          Result := False;
      end;
    end;
  end;
end;
