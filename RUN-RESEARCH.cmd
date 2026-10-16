@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_research\run-v0320.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
  echo Research runner exited with code %EXITCODE%.
) else (
  echo Research runner completed successfully.
)
pause
exit /b %EXITCODE%
