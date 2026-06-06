@echo off
setlocal enabledelayedexpansion

set WORKSPACE=C:\Users\Lordy\jobhuntrr
set TASKFILE=%WORKSPACE%\CURSOR_TASKS.md

echo Orchestrator loop started. Watching CURSOR_TASKS.md every 15 seconds...
echo Press Ctrl+C to stop.
echo.

:loop
if not exist "%TASKFILE%" (
    echo [%time%] CURSOR_TASKS.md not found.
    goto sleep
)

findstr /C:"status: PENDING" "%TASKFILE%" >nul 2>&1
if %errorlevel% == 0 (
    echo [%time%] PENDING task detected - calling Cursor agent...
    agent --trust --yolo -p "You are a senior Python developer on the JobHuntrr codebase at C:\Users\Lordy\jobhuntrr. Read CURSOR_TASKS.md. Find the task marked status: PENDING. Execute it exactly as described — including running any shell/terminal commands required. When done, update status from PENDING to DONE and append a brief result summary. Always read files before editing. Only modify files mentioned in the task." --workspace "%WORKSPACE%"
    echo [%time%] Cursor agent finished. Waiting for next task...
    echo.
) else (
    echo [%time%] No PENDING task. Sleeping...
)

:sleep
timeout /t 15 /nobreak >nul
goto loop
