@echo off
cd /d C:\Users\Lordy\jobhuntrr
echo ============================================================
echo   JobHuntrr — GCC-Only Discovery + Dry-Run Apply Test
echo ============================================================
echo.
echo   This run will:
echo     1. Discover fresh GCC jobs (UAE, Qatar, Saudi, Bahrain)
echo     2. Score each job against your profile
echo     3. Log results to data/jobs.db
echo     4. DRY-RUN apply to auto_apply jobs (no actual submit)
echo.
echo   To run LIVE (actually apply), use:
echo     python orchestrator.py --run-once --apply --live
echo ============================================================
echo.

REM Step 1: Discover + score + log (no apply yet)
echo [Step 1] Discovering and scoring GCC jobs...
python orchestrator.py --run-once --no-auto-enrich
echo.

REM Step 2: Check what's in the GCC queue
echo [Step 2] GCC apply queue:
python check_gcc_queue.py
echo.

REM Step 3: Dry-run apply to GCC auto_apply jobs
echo [Step 3] Dry-run applying to GCC auto_apply jobs...
python apply_jobs.py --gcc-only
echo.

echo ============================================================
echo   Test complete. Check data/jobs.db or GUI for results.
echo ============================================================
pause
