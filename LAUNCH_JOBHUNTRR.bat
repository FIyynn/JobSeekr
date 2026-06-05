@echo off
title JobHuntrr Launcher
cd /d "C:\Users\Lordy\jobhuntrr"

echo.
echo  ============================================================
echo   JobHuntrr - UAE Autonomous Job Agent
echo  ============================================================
echo.

echo  [STEP 1] LinkedIn login setup (browser will open - just log in)...
echo.
python setup_linkedin.py
echo.

echo.
echo  ============================================================
echo   [STEP 2] Applying to pending Notion jobs (live)...
echo  ============================================================
echo.
python apply_from_notion.py --live
echo.

echo.
echo  ============================================================
echo   [STEP 3] Discovering new jobs + applying (run-once, live)...
echo  ============================================================
echo.
python orchestrator.py --run-once --apply --live

echo.
echo  ============================================================
echo   Done! Check the Notion database for results.
echo  ============================================================
pause
