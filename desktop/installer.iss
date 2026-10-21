; Per-user, versioned program payload. User data is never an uninstall target.
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef Version
  #define Version "5.2.0"
#endif
#ifndef Build
  #define Build "R3.10-desktop-candidate"
#endif
[Setup]
AppId={{8F130024-CF24-4E11-8185-D460C6B2E7B8}
AppName=Seedance Studio
AppVersion={#Version}-{#Build}
AppVerName=Seedance Studio {#Version} {#Build}
AppPublisher=Seedance Studio
SetupIconFile=..\assets\branding\seedance-studio.ico
VersionInfoDescription=Seedance Studio 视频创作工作台
VersionInfoProductName=Seedance Studio
VersionInfoVersion={#Version}
DefaultDirName={localappdata}\Programs\SeedanceStudio
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#OutputDir}
OutputBaseFilename=SeedanceStudio_Setup_{#Version}_{#Build}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\versions\{#Version}-{#Build}\SeedanceStudio.exe
UninstallDisplayName=Seedance Studio 视频创作工作台
ShowLanguageDialog=no
UsePreviousLanguage=no
CloseApplications=no
AppMutex=Local\SeedanceStudioDesktopDefault
RestartApplications=no
Uninstallable=yes
SetupLogging=yes
DiskSpanning=no
ExtraDiskSpaceRequired=1200000000
[Languages]
Name: "chinesesimplified"; MessagesFile: "locale\ChineseSimplified.isl"
[LangOptions]
DialogFontName=Microsoft YaHei UI
WelcomeFontName=Microsoft YaHei UI
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce
[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}\versions\{#Version}-{#Build}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Seedance Studio"; Filename: "{app}\versions\{#Version}-{#Build}\SeedanceStudio.exe"; WorkingDir: "{app}\versions\{#Version}-{#Build}"; AppUserModelID: "SeedanceStudio.Desktop"
Name: "{autodesktop}\Seedance Studio"; Filename: "{app}\versions\{#Version}-{#Build}\SeedanceStudio.exe"; WorkingDir: "{app}\versions\{#Version}-{#Build}"; Tasks: desktopicon; AppUserModelID: "SeedanceStudio.Desktop"
[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then begin
    MsgBox('Seedance Studio 需要 Windows 10/11 64 位系统。', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then begin
    WizardForm.StatusLabel.Caption := '正在验证内置 Microsoft WebView2 界面组件，请稍候...';
    if not Exec(ExpandConstant('{app}\versions\{#Version}-{#Build}\SeedanceStudio.exe'),
      '--ensure-webview', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      RaiseException('无法验证 WebView2 界面组件。您的设置、密钥、素材和历史记录已保留。');
    if ResultCode <> 0 then
      RaiseException('内置 WebView2 组件验证失败，请重新运行此完整安装包修复。用户数据已保留。');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    // Deliberately do not traverse user data, credentials, assets or databases.
    // Retention is explicit, including silent uninstallation.
    Log('User data is retained in %LOCALAPPDATA%\SeedanceStudio. No user files are deleted.');
    if not UninstallSilent then
      MsgBox('卸载仅移除程序文件。您的设置、API 密钥、素材和历史记录将保留在 %LOCALAPPDATA%\SeedanceStudio。', mbInformation, MB_OK);
  end;
end;
