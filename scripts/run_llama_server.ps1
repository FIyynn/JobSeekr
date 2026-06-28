$ServerExe = "E:\llama.cpp\llama-server.exe"
$ModelPath = "E:\HF_cache\external_models\qewn3.5\Qwen3.5-9B.Q4_K_M.gguf"
$ServerHost = "127.0.0.1"
$Port = 8080
$ContextSize = 20000
$GpuLayers = 999
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StdoutLog = Join-Path $ScriptDir "llama-server.stdout.log"
$StderrLog = Join-Path $ScriptDir "llama-server.stderr.log"

if (-not (Test-Path $ServerExe)) {
    throw "llama-server.exe not found: $ServerExe"
}

if (-not (Test-Path $ModelPath)) {
    throw "Model not found: $ModelPath"
}

$Arguments = @(
    "--host", $ServerHost,
    "--port", $Port,
    "--model", $ModelPath,
    "--ctx-size", $ContextSize,
    "--gpu-layers", $GpuLayers
)

Write-Host "Starting llama-server on http://$ServerHost`:$Port with ctx=$ContextSize and gpu-layers=$GpuLayers"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ServerExe
$psi.Arguments = ($Arguments | ForEach-Object {
    if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
}) -join ' '
$psi.WorkingDirectory = Split-Path $ServerExe
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true

try {
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $process.StandardOutput.ReadToEndAsync() | Out-Null
    $process.StandardError.ReadToEndAsync() | Out-Null
    Start-Sleep -Milliseconds 500
    if (-not $process.HasExited) {
        Write-Host "Started llama-server PID $($process.Id)"
        Write-Host "stdout: $StdoutLog"
        Write-Host "stderr: $StderrLog"
    } else {
        Write-Host "llama-server exited immediately with code $($process.ExitCode)"
    }
} catch {
    Write-Error "Failed to start llama-server: $($_.Exception.Message)"
    throw
}
