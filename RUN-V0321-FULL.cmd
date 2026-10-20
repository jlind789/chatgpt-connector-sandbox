@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_research\run-v0321-full.ps1" %*
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
 echo Full-development research stopped with code %EXITCODE%.
 echo Completed checkpoints remain saved for resuming.
)
pause
exit /b %EXITCODE%
