; Seedance Studio candidate EXE. User data and old version folders are preserved.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!ifndef PRODUCT_VERSION
  !error "Set PRODUCT_VERSION"
!endif
!ifndef RELEASE_ID
  !error "Set RELEASE_ID"
!endif
!ifndef PAYLOAD_DIR
  !error "Set PAYLOAD_DIR"
!endif
!ifndef OUTPUT_EXE
  !error "Set OUTPUT_EXE"
!endif

Name "Seedance Studio ${PRODUCT_VERSION}"
OutFile "${OUTPUT_EXE}"
InstallDir "$LOCALAPPDATA\Programs\SeedanceStudio"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
BrandingText "Seedance Studio"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\versions${RELEASE_ID}\Studio\00_START.cmd"
!define MUI_FINISHPAGE_RUN_TEXT "启动 Seedance Studio"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Var PriorRoot
Var NewRoot
Var InstallRC

Section "安装完整应用" SEC_INSTALL
  SetShellVarContext current
  StrCpy $NewRoot "$INSTDIR\versions${RELEASE_ID}\Studio"
  ReadRegStr $PriorRoot HKCU "Software\SeedanceStudio" "CurrentAppRoot"
  ${If} $PriorRoot == $NewRoot
    MessageBox MB_ICONSTOP "当前安装版本已存在。为保护运行中的任务，不允许直接覆盖相同版本。"
    Abort
  ${EndIf}
  SetOutPath "$NewRoot"
  File /r "${PAYLOAD_DIR}\Studio\*.*"
  DetailPrint "准备 Python 运行环境与必要依赖..."
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$NewRoot\tools\install_new_pc.ps1"'
  Pop $InstallRC
  ${If} $InstallRC != 0
    MessageBox MB_ICONSTOP "安装依赖未通过 (代码 $InstallRC)。旧版本及其数据保持原样。请查看新版本 install_logs。"
    Abort
  ${EndIf}
  ${If} $PriorRoot != ""
    ${If} $PriorRoot != $NewRoot
      ${If} ${FileExists} "$PriorRoot\tools\safe_migrate.py"
        DetailPrint "检测旧版本任务状态并迁移数据库及素材..."
        nsExec::ExecToLog '"$NewRoot\.venv\Scripts\python.exe" "$NewRoot\tools\safe_migrate.py" --from "$PriorRoot"'
        Pop $InstallRC
        ${If} $InstallRC != 0
          MessageBox MB_ICONSTOP "旧版本正在运行、任务未结束或数据迁移失败。已保留原安装和数据；请停止任务后重试。"
          Abort
        ${EndIf}
      ${Else}
        MessageBox MB_ICONSTOP "无法验证旧版本安全迁移工具；拒绝自动覆盖数据。原版本未更改。"
        Abort
      ${EndIf}
    ${EndIf}
  ${EndIf}
  WriteRegStr HKCU "Software\SeedanceStudio" "CurrentAppRoot" "$NewRoot"
  WriteRegStr HKCU "Software\SeedanceStudio" "CurrentReleaseId" "${RELEASE_ID}"
  CreateDirectory "$SMPROGRAMS\Seedance Studio"
  CreateShortCut "$SMPROGRAMS\Seedance Studio\启动 Seedance Studio.lnk" "$NewRoot\00_START.cmd"
  CreateShortCut "$DESKTOP\Seedance Studio.lnk" "$NewRoot\00_START.cmd"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall-SeedanceStudio.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio" "DisplayName" "Seedance Studio ${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio" "UninstallString" '"$INSTDIR\Uninstall-SeedanceStudio.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Seedance Studio.lnk"
  Delete "$SMPROGRAMS\Seedance Studio\启动 Seedance Studio.lnk"
  RMDir "$SMPROGRAMS\Seedance Studio"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedanceStudio"
  DeleteRegKey HKCU "Software\SeedanceStudio"
  Delete "$INSTDIR\Uninstall-SeedanceStudio.exe"
  MessageBox MB_ICONINFORMATION "已移除客户端入口和安装登记。为防止误删素材、数据库和密钥，历史版本目录及用户数据仍保留在 $INSTDIR。"
SectionEnd
