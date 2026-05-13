@echo off
setlocal EnableExtensions

rem Repo root (this script lives in packaging\)
cd /d "%~dp0.."
set "ROOT=%CD%"
set "SPEC=%ROOT%\LinkFlow.spec"
set "APP=%ROOT%\dist\LinkFlow"

rem Default Python; override with env var LINKFLOW_BUILD_PYTHON=full\path\python.exe
set "DEFAULT_PY=D:\APPS\Miniconda\envs\py39\python.exe"
if defined LINKFLOW_BUILD_PYTHON (
  set "PY=%LINKFLOW_BUILD_PYTHON%"
) else (
  set "PY=%DEFAULT_PY%"
)

echo [LinkFlow] ROOT=%ROOT%
echo [LinkFlow] PY=%PY%
"%PY%" -c "import sys; print(sys.executable)" 2>nul
if errorlevel 1 (
  echo ERROR: Python failed to run. Set LINKFLOW_BUILD_PYTHON to a valid python.exe
  exit /b 1
)

echo [LinkFlow] pip install -r requirements.txt
"%PY%" -m pip uninstall -y PySide6 PySide6_Addons 2>nul
"%PY%" -m pip install -r "%ROOT%\requirements.txt" "pyinstaller>=6.0"
if errorlevel 1 (
  echo ERROR: pip install failed.
  exit /b 1
)

echo [LinkFlow] PyInstaller LinkFlow.spec
"%PY%" -m PyInstaller --noconfirm "%SPEC%"
if errorlevel 1 (
  echo ERROR: PyInstaller failed.
  exit /b 1
)

if not exist "%APP%\LinkFlow.exe" (
  echo ERROR: Missing output: %APP%\LinkFlow.exe
  exit /b 1
)

if exist "%ROOT%\hosts.json" (
  copy /Y "%ROOT%\hosts.json" "%APP%\hosts.json" >nul
  echo [LinkFlow] Copied hosts.json to dist\LinkFlow\
)

if exist "%APP%\_internal\icon" (
  if exist "%APP%\icon" rd /s /q "%APP%\icon"
  mkdir "%APP%\icon" 2>nul
  xcopy "%APP%\_internal\icon\*" "%APP%\icon\" /E /H /Y /Q >nul
  echo [LinkFlow] Copied _internal\icon to icon\
)

echo [LinkFlow] Desktop shortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-desktop-shortcut.ps1" -AppDir "%APP%"
if errorlevel 1 (
  echo ERROR: desktop shortcut script failed.
  exit /b 1
)

set "TGZ=%ROOT%\dist\LinkFlow-windows-amd64.tar.gz"
if exist "%TGZ%" del /f /q "%TGZ%"
tar -czf "%TGZ%" -C "%ROOT%\dist" LinkFlow
if errorlevel 1 (
  echo WARNING: tar compress failed; ship dist\LinkFlow folder as-is.
) else (
  echo [LinkFlow] Archive: %TGZ%
)

echo.
echo Done. App folder: %APP%
echo Run: %APP%\LinkFlow.exe
echo Ship dist\LinkFlow folder, or the .tar.gz if created ^(often under 100MB^).
echo.
pause
endlocal
exit /b 0
