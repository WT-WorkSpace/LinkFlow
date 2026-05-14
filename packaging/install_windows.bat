@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem =============================================================================
rem Windows counterpart to install_linux.sh: PyInstaller onefile + desktop shortcut (LinkFlow.lnk)
rem Run from anywhere: packaging\install_windows.bat (double-click OK)
rem Windows --add-data separator is semicolon: icon;icon (matches main.py _MEIPASS\icon)
rem =============================================================================

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

rem Default Python; override with LINKFLOW_BUILD_PYTHON=full\path\python.exe
set "DEFAULT_PY=D:\APPS\Miniconda\envs\py39\python.exe"
if defined LINKFLOW_BUILD_PYTHON (
  set "PY=%LINKFLOW_BUILD_PYTHON%"
) else (
  set "PY=%DEFAULT_PY%"
)

echo [LinkFlow] ROOT=%ROOT%
echo [LinkFlow] PY=%PY%

if not exist "%PY%" (
  echo ERROR: Python not found: %PY%
  echo Edit DEFAULT_PY in this script, or set LINKFLOW_BUILD_PYTHON.
  exit /b 1
)

"%PY%" -c "import sys; print(sys.executable)" 2>nul
if errorlevel 1 (
  echo ERROR: Python failed to run.
  exit /b 1
)

rem Keep generated .spec under build\ so PyInstaller does NOT overwrite repo-root LinkFlow.spec (breaks packaging\build-app.bat).
set "SPEC_DIR=%ROOT%\build\install_windows_onefile_spec"
set "WORK_DIR=%ROOT%\build\install_windows_onefile_work"
if not exist "%SPEC_DIR%" mkdir "%SPEC_DIR%"
if not exist "%WORK_DIR%" mkdir "%WORK_DIR%"

echo [LinkFlow] PyInstaller onefile...
"%PY%" -m PyInstaller --noconfirm --specpath "%SPEC_DIR%" --workpath "%WORK_DIR%" --distpath "%ROOT%\dist" --onefile --windowed --name "LinkFlow" --icon "%ROOT%\icon\link.ico" --add-data "%ROOT%\icon;icon" "%ROOT%\main.py"
if errorlevel 1 (
  echo ERROR: PyInstaller failed.
  exit /b 1
)

set "EXE=%ROOT%\dist\LinkFlow.exe"
if not exist "%EXE%" (
  echo ERROR: Missing output: %EXE%
  exit /b 1
)

if exist "%ROOT%\hosts.json" (
  copy /Y "%ROOT%\hosts.json" "%ROOT%\dist\hosts.json" >nul
  echo [LinkFlow] Copied hosts.json to dist\
)

rem Desktop shortcut script expects dist\icon\link.ico (same as set-desktop-shortcut.ps1)
if exist "%ROOT%\icon" (
  if exist "%ROOT%\dist\icon" rd /s /q "%ROOT%\dist\icon"
  mkdir "%ROOT%\dist\icon" 2>nul
  xcopy "%ROOT%\icon\*" "%ROOT%\dist\icon\" /E /H /Y /Q >nul
)

set "PS1=%SCRIPT_DIR%set-desktop-shortcut.ps1"
if not exist "%PS1%" (
  echo WARNING: Missing script: %PS1%
  echo         Desktop shortcut was not created. Add packaging\set-desktop-shortcut.ps1 from the repo.
  goto :after_shortcut
)
echo [LinkFlow] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -AppDir "%ROOT%\dist"
if errorlevel 1 (
  echo WARNING: Shortcut creation failed. Try manually:
  echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -AppDir "%ROOT%\dist"
) else (
  echo [LinkFlow] Desktop shortcut step finished OK.
)
:after_shortcut

echo.
echo Done. Executable: %EXE%
echo If the shortcut was created, open LinkFlow.lnk on the desktop.
echo.
pause
endlocal
exit /b 0
