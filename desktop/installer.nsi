; ============================================================================
;  MiroFish - Windows x64 installer (NSIS 3 / Unicode)
;
;  Per-user install, no administrator rights required:
;      %LOCALAPPDATA%\Programs\MiroFish
;  Shortcuts: Start Menu + optional desktop shortcut, uninstaller entry under
;      HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\MiroFish
;
;  Command line:
;      /S          silent install
;      /D=<path>   install into another directory (must be the last argument)
;
;  Build (payload tree produced by desktop/build.py --mode setup):
;      makensis desktop/installer.nsi
; ============================================================================

Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinVer.nsh"
!include "x64.nsh"

!define APP_NAME      "MiroFish"
; build_installer.py passes the version from desktop/version.txt; 0.1.0 is the fallback
!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!define APP_VERSION_FULL "${APP_VERSION}.0"
!define APP_PUBLISHER "MiroFish"
!define APP_URL       "https://github.com/cfunkycreations-tech/MiroFish"
!define APP_EXE       "MiroFish.exe"

!define PAYLOAD "..\dist\MiroFish-${APP_VERSION}-windows-x64-setup"
!define ICON    "icon.ico"
!define OUT_EXE "..\dist\MiroFish-${APP_VERSION}-windows-x64-setup.exe"

!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiroFish"
!define MIN_WINVER "10.0"   ; Windows 10 / 11 x64

Name "${APP_NAME} ${APP_VERSION}"
BrandingText "${APP_NAME} ${APP_VERSION} - Windows x64"

OutFile "${OUT_EXE}"
; A per-user install needs no administrator rights; request the highest level
; that is available so the installer also works when it is launched elevated.
RequestExecutionLevel user

InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallDir"

VIProductVersion "${APP_VERSION_FULL}"
VIAddVersionKey /LANG=1033 "ProductName"     "${APP_NAME}"
VIAddVersionKey /LANG=1033 "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "${APP_NAME} ${APP_VERSION} installer"
VIAddVersionKey /LANG=1033 "FileVersion"     "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey /LANG=1033 "LegalCopyright"  "AGPL-3.0"

; ---------------------------------------------------------------- interface --
!define MUI_ABORTWARNING
!define MUI_ICON   "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_WELCOMEFINISHPAGE_BITMAP "installer-welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "installer-welcome.bmp"

!define MUI_WELCOMEPAGE_TITLE "Welcome to the ${APP_NAME} setup"
!define MUI_WELCOMEPAGE_TEXT  "This wizard installs ${APP_NAME} ${APP_VERSION} on your computer.$\r$\n$\r$\n${APP_NAME} is a desktop app for building knowledge graphs and running crowd simulations. The app itself is installed right away (about 34 MB); the Python runtime it needs (about 1.1 GB) is downloaded the first time you start it.$\r$\n$\r$\nNo administrator rights are required: everything goes into your own user profile and can be removed again from Settings > Apps."

!define MUI_DIRECTORYPAGE_TEXT_TOP "Setup installs ${APP_NAME} into the folder below. Keep the default, or pick another folder."

!define MUI_FINISHPAGE_TITLE "Setup is complete"
!define MUI_FINISHPAGE_TEXT "$\r$\n${APP_NAME} ${APP_VERSION} has been installed.$\r$\n$\r$\nStart it from the Start Menu shortcut, the desktop shortcut, or the ``Launch ${APP_NAME}'' checkbox below.$\r$\n$\r$\nThe first start downloads the Python runtime, so keep the machine online for a few minutes. The app then opens its settings page: enter your LLM API key and your Zep Cloud API key to begin."

!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME} now"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_LINK "Project home page"
!define MUI_FINISHPAGE_LINK_LOCATION "${APP_URL}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------------ helpers --
!macro StopRunningApp
  ; Ask a running instance to close so the payload can be replaced. The app is
  ; started from this folder, so a running copy would lock the executables.
  Push $0
  nsExec::ExecToStack 'taskkill /IM "${APP_EXE}" /T /F'
  Pop $0
  Pop $0
  Sleep 400
!macroend

; ------------------------------------------------------------------ installer --
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "${APP_NAME} requires 64-bit Windows 10 or 11."
    Abort
  ${EndIf}
  SetRegView 64

  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP|MB_OK "${APP_NAME} needs Windows 10 or Windows 11, 64-bit."
    Abort
  ${EndIf}

  ; Upgrade in place: reuse the previous directory unless /D= overrides it.
  ReadRegStr $0 HKCU "${UNINST_KEY}" "InstallLocation"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0\${APP_EXE}"
    StrCpy $INSTDIR $0
  ${EndIf}

  ; The previous version must be closed before its files can be overwritten.
  !insertmacro StopRunningApp
FunctionEnd

Section "Install" SecMain
  SetShellVarContext current
  SetOutPath "$INSTDIR"

  ; Uninstaller first, so a failure later still leaves a way to remove the app.
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; ---- payload ------------------------------------------------------------
  ; The app itself (MiroFish.exe, Setup.bat, the Flask back end and the built
  ; frontend, uv.exe) plus documentation.
  SetOutPath "$INSTDIR"
  File "${PAYLOAD}\${APP_EXE}"
  File "${PAYLOAD}\Setup.bat"
  File "${PAYLOAD}\Setup-Slim.bat"
  File "${PAYLOAD}\MiroFish-Console.bat"
  File "${PAYLOAD}\MiroFish-Doctor.bat"
  File "${PAYLOAD}\README.txt"
  File "${PAYLOAD}\.env.example"
  File "${PAYLOAD}\LICENSE"

  SetOutPath "$INSTDIR\app"
  File /r "${PAYLOAD}\app\*.*"

  SetOutPath "$INSTDIR\tools"
  File "${PAYLOAD}\tools\uv.exe"
  File "${PAYLOAD}\tools\patch_oasis_recsys.py"

  ; ---- shortcuts ----------------------------------------------------------
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Run ${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Setup (download runtime).lnk" "$INSTDIR\Setup.bat" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Create the Python runtime and install the dependencies"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Console (troubleshooting).lnk" "$INSTDIR\MiroFish-Console.bat" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Start with a console window to see errors"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Diagnostics (report a problem).lnk" "$INSTDIR\MiroFish-Doctor.bat" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Collect a diagnostic report into doctor-report.txt"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0 SW_SHOWNORMAL "" "Remove ${APP_NAME}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0 SW_SHOWNORMAL "" "Run ${APP_NAME}"

  ; ---- registry -----------------------------------------------------------
  ; HKCU is enough for a per-user install, and no elevation is involved.
  WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"

  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME} ${APP_VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE},0"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
  WriteRegStr   HKCU "${UNINST_KEY}" "URLInfoAbout"    "${APP_URL}"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ; Size on disk, as shown in Settings > Apps.
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"

  ; ---- record the installed version --------------------------------------
  WriteRegStr HKCU "Software\${APP_NAME}" "Version" "${APP_VERSION}"
SectionEnd

; ---------------------------------------------------------------- uninstaller --
Section "Uninstall"
  SetShellVarContext current
  ; The installer wrote its keys in the 64-bit view; the uninstaller is a 32-bit
  ; process, so it has to be told the same thing or the entry would be left behind.
  SetRegView 64
  !insertmacro StopRunningApp

  ; User data lives in %APPDATA%\MiroFish and is deliberately left alone:
  ; documents, graphs and API keys survive a reinstall. The question below
  ; only removes it when the user explicitly asks for it.
  ${IfNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "Also delete your MiroFish data?$\r$\n$\r$\nThis removes %APPDATA%\MiroFish: your API keys, uploaded documents, graphs, simulations and reports.$\r$\n$\r$\nChoose No to keep the data for a later reinstall." \
      IDYES remove_data IDNO keep_data
    remove_data:
      RMDir /r "$APPDATA\${APP_NAME}"
    keep_data:
  ${EndIf}

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Setup (download runtime).lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Console (troubleshooting).lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Diagnostics (report a problem).lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\tools"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Setup.bat"
  Delete "$INSTDIR\Setup-Slim.bat"
  Delete "$INSTDIR\MiroFish-Console.bat"
  Delete "$INSTDIR\MiroFish-Doctor.bat"
  Delete "$INSTDIR\doctor-report.txt"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP_NAME}"
SectionEnd
