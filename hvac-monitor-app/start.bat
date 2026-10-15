@echo off
REM HVAC Monitor bench dashboard on http://localhost:8080
REM Runs the C# version (..\dotnet) when the .NET 10 SDK is installed, otherwise the Python one
REM (python server.py; the first run creates a Python environment and installs packages).
cd /d "%~dp0"
where dotnet >nul 2>nul
if not errorlevel 1 if exist ..\dotnet\src\Fullscope.Cloud\Fullscope.Cloud.csproj (
  dotnet run --project ..\dotnet\src\Fullscope.Cloud -c Release -- bench --dir "%~dp0." %*
  pause
  goto :eof
)
if not exist .venv (
  echo Creating Python environment...
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python server.py %*
pause
