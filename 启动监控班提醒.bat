@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Start failed. Error code: %EXITCODE%
  echo Please check the error above.
)

echo.
echo Window will stay open. Press Ctrl+C to stop the service.
pause
exit /b %EXITCODE%
