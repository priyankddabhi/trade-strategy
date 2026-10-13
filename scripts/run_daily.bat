@echo off
rem Daily simulation run.
rem   Double-click: asks which market.   run_daily.bat india / us / both: no questions (for Task Scheduler).
cd /d "%~dp0.."
if not exist runs mkdir runs
set MARKET=%1
if "%MARKET%"=="" (
  echo Which market?  1 = India   2 = US   3 = Both
  choice /c 123 /n /m "Press 1, 2 or 3: "
  if errorlevel 3 (set MARKET=both) else if errorlevel 2 (set MARKET=us) else set MARKET=india
)
rem The scheduled run always works on the main profile (Profile 1). Change p1 to p2 or p3 for another profile.
set PROFILE=p1
set ARGS=
if /i "%MARKET%"=="india" set ARGS=--market india
if /i "%MARKET%"=="us" set ARGS=--market us
echo Running the bot for %MARKET%, profile %PROFILE%... (up to a minute)
".venv\Scripts\python.exe" run.py --profile %PROFILE% daily %ARGS% >> "runs\daily_log.txt" 2>&1
echo Done. Report: runs\daily\latest.html   Log: runs\daily_log.txt
if "%1"=="" pause
