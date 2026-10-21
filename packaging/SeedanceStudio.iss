; Seedance Studio executable installer candidate.
; The existing repository builder prepares ALL app sources and pinned runtimes.
; This installer never deletes user data, old ZIP installs, logs or DPAPI keys.
#ifndef AppVersion
  #error AppVersion must be specified by the verified build pipeline
#endif
#define AppName "Seedance Studio"
#define AppPublisher "Seedance Studio"
[Setup]
AppId={{83DA1452-41AB-4DA6-BE21-FB8707C61A22}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\SeedanceStudio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
OutputDir=..\build\installer
OutputBaseFilename=SeedanceStudio_Setup_{#AppVersion}_Candidate
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}.0
; A signed stable build must be configured separately; unsigned EXEs are candidates only.
[Languages]
Name: "zh"; MessagesFile: "ChineseSimplified.isl"
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项"; Flags: unchecked
[Files]
Source: "..\build\SeedanceStudio_Candidate\Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Seedance Studio"; Filename: "{cmd}"; Parameters: "/c ""{app}\00_START.cmd"""; WorkingDir: "{app}"
Name: "{autodesktop}\Seedance Studio"; Filename: "{cmd}"; Parameters: "/c ""{app}\00_START.cmd"""; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
; Dependency setup is the existing first-run installer. No paid provider calls.
Filename: "{cmd}"; Parameters: "/c ""{app}\01_INSTALL.cmd"""; WorkingDir: "{app}"; Description: "安装运行依赖（首次使用必需）"; Flags: postinstall skipifsilent runascurrentuser
[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
