@echo off
rem Contractor demo: creates the demo account the first time, then keeps its six homes live.
rem Uses the same database as the local cloud (dev.db); the demo is its own account and never emails.
rem Remove it with: .venv\Scripts\python.exe demo.py reset
cd /d "%~dp0"
if not exist demo-login.txt (
  echo Setting up the demo (about a minute)...
  .venv\Scripts\python.exe demo.py setup
  if errorlevel 1 goto :eof
)
start "HVAC demo - leave this open" .venv\Scripts\python.exe demo.py run
echo.
type demo-login.txt
echo.
echo Sign in at http://localhost:8000 with the contractor or homeowner sign-in above.
start "" http://localhost:8000/app/
pause
