@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_research\run-v0322-shadow.ps1" %*
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
 echo Shadow research stopped with error code %EXITCODE%.
 echo Completed decisions remain in the same extracted repository for resuming.
)
pause
exit /b %EXITCODE%
