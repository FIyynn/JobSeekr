@echo off
REM JobHuntrr — launcher (local SQLite + GUI)
cd /d "%~dp0"

echo ============================================================
echo  JobHuntrr — UAE Autonomous Job Agent
echo ============================================================
echo.
echo  Storage: local (data/jobs.db)
echo  Default: open GUI - use "Search + apply now (LIVE)"
echo           or "Start repeating search + apply (LIVE)"
echo.
echo  CLI alternatives:
echo    python gui/jobhunter_gui.py
echo    python orchestrator.py --run-once
echo    python orchestrator.py --run-once --apply --live
echo ============================================================
echo.

python gui\jobhunter_gui.py
pause
