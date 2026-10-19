@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_research\run-v0321-shadow.ps1" %*
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
  echo Pilot stopped with exit code %EXITCODE%. Logs and completed decisions are preserved.
) else (
  echo Shadow pilot completed.
)
pause
exit /b %EXITCODE%
