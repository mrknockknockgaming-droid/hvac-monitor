@echo off
:: HVAC Monitor cloud: ingest worker + Fullscope web app on http://localhost:8000
:: Needs the MQTT broker running (start-hvac.bat starts it). Sign in with the key in my-api-key.txt.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo The cloud's Python environment is missing. See hvac-cloud\README.md, "Development".
  pause
  exit /b 1
)
if not exist "dev.db" (
  echo No cloud account yet. Run the manage.py setup in hvac-cloud\README.md first.
  pause
  exit /b 1
)

echo Starting the cloud ingest worker...
powershell -NoProfile -Command "exit [int](-not (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*hvaccloud.ingest*' }))"
if %errorlevel% equ 0 (
  echo       Already running, leaving it alone.
) else (
  start "HVAC cloud ingest - leave this open" /d "%~dp0" .venv\Scripts\python.exe -m hvaccloud.ingest
)

echo Starting the web app...
netstat -ano | %SystemRoot%\System32\find.exe "127.0.0.1:8000 " | %SystemRoot%\System32\find.exe "LISTENING" >nul
if %errorlevel% equ 0 (
  echo       Already running, leaving it alone.
) else (
  start "HVAC web app - leave this open" /d "%~dp0" .venv\Scripts\python.exe -m uvicorn hvaccloud.api:app --port 8000
  timeout /t 3 /nobreak >nul
)

start "" http://localhost:8000
