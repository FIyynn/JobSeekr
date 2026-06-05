# Kill orphaned Playwright / JobHuntrr browser processes
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
$patterns = @('ms-playwright', 'linkedin_session', 'playwright', 'remote-debugging', 'jobhuntrr')
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return }
    foreach ($p in $patterns) {
        if ($cmd -like "*$p*") {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $killed++
            break
        }
    }
}
Write-Host "Stopped $killed Playwright/JobHuntrr Chrome process(es)."
