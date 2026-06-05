@echo off
cd /d C:\Users\Lordy\jobhuntrr
echo ============================================================
echo   Jobhuntrr Bot Starting
echo ============================================================
echo.
echo Step 1: Setting up LinkedIn session...
python setup_linkedin.py
echo.
echo Step 2: Applying to Notion jobs (LIVE)...
python apply_from_notion.py --live
echo.
echo Step 3: Discovering and applying to new jobs...
python orchestrator.py --run-once --apply --live
echo.
echo ============================================================
echo   Done!
echo ============================================================
pause
