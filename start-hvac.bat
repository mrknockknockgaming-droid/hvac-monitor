@echo off
:: HVAC Monitor one-click start. Lives in the project root (C:\Users\mrkno\hvac-monitor).
:: The Desktop "HVAC Monitor" shortcut points here.

:: Ask for administrator rights (needed to manage the Mosquitto service)
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo [1/5] Stopping the background Mosquitto service so it can't steal connections...
net stop mosquitto >nul 2>&1
sc config mosquitto start= demand >nul 2>&1

echo [2/5] Turning off QuickEdit so clicking a window can't freeze the broker...
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul

echo [3/5] Starting the HVAC broker in its own window...
if not exist "C:\Program Files\mosquitto\hvac.conf" (
  (echo listener 1883 0.0.0.0& echo allow_anonymous true)> "C:\Program Files\mosquitto\hvac.conf"
)
tasklist /fi "imagename eq mosquitto.exe" | %SystemRoot%\System32\find.exe /i "mosquitto.exe" >nul
if %errorlevel% equ 0 (
  echo       A broker is already running, leaving it alone.
) else (
  start "HVAC broker - leave this open" /d "C:\Program Files\mosquitto" mosquitto.exe -c hvac.conf -v
)

echo [4/5] Starting the PC dashboard (http://localhost:8080, no browser tab)...
netstat -ano | %SystemRoot%\System32\find.exe ":8080 " | %SystemRoot%\System32\find.exe "LISTENING" >nul
if %errorlevel% equ 0 (
  echo       A dashboard is already running on port 8080, leaving it alone.
) else if exist "%~dp0hvac-monitor-app\start.bat" (
  start "HVAC dashboard - leave this open" /d "%~dp0hvac-monitor-app" cmd /c start.bat --no-browser
) else (
  echo       hvac-monitor-app\start.bat not found next to this file.
)

echo [5/5] Starting the cloud and the Fullscope web app (http://localhost:8000)...
call "%~dp0hvac-cloud\start-cloud.bat"

echo.
echo Done. Leave the "HVAC broker", "HVAC dashboard", "HVAC cloud ingest" and "HVAC web app"
echo windows open. The web app opens in your browser at http://localhost:8000
echo.
pause
