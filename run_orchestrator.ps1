# JobHuntrr Orchestrator Loop
# Run: powershell -ExecutionPolicy Bypass -File run_orchestrator.ps1

$workspacePath = "C:\Users\Lordy\jobhuntrr"
$taskFile = Join-Path $workspacePath "CURSOR_TASKS.md"
$pollInterval = 15

$prompt = "You are a senior Python developer working on the JobHuntrr codebase at C:\Users\Lordy\jobhuntrr. Read CURSOR_TASKS.md carefully. Find the task marked with status: PENDING. Execute it exactly as described. When done, update its status from PENDING to DONE and append a brief result summary. Rules: always read files before editing, follow existing code style, only modify files mentioned in the task, run terminal commands if the task requires it."

Write-Host "Orchestrator loop started. Watching $taskFile every $pollInterval seconds..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Yellow

while ($true) {
    if (Test-Path $taskFile) {
        $content = Get-Content $taskFile -Raw

        if ($content -match "status: PENDING") {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] PENDING task detected - calling Cursor agent..." -ForegroundColor Green

            cursor agent -p $prompt --workspace $workspacePath

            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Cursor agent finished. Waiting for next task...`n" -ForegroundColor Cyan
        } else {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] No PENDING task. Sleeping..." -ForegroundColor Gray
        }
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] CURSOR_TASKS.md not found." -ForegroundColor Red
    }

    Start-Sleep -Seconds $pollInterval
}
