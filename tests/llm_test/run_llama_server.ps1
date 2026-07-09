$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$ConfigPath = Join-Path $RepoRoot "config\app_config.json"

if (-not (Test-Path $ConfigPath)) {
    throw "Config not found: $ConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$Backend = $Config.llm_backend.local

if (-not $Backend) {
    throw "Missing llm_backend.local config in $ConfigPath"
}

$ServerExe = [string]$Backend.server_executable
$ModelPath = [string]$Backend.model_path
$ServerHost = [string]$Backend.host
$Port = [int]$Backend.port
$ContextSize = [int]$Backend.context_size
$PredictTokens = [int]($Backend.predict_tokens | ForEach-Object { $_ })
$GpuLayers = [int]$Backend.gpu_layers
$Parallel = [int]$Backend.parallel
$ContBatching = [bool]$Backend.cont_batching
$BatchSize = [int]$Backend.batch_size
$UBatchSize = [int]$Backend.ubatch_size

if (-not (Test-Path $ServerExe)) {
    throw "llama-server.exe not found: $ServerExe"
}

if (-not (Test-Path $ModelPath)) {
    throw "Model not found: $ModelPath"
}

$Existing = Get-Process llama-server -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Stopping existing llama-server process(es): $($Existing.Id -join ', ')"
    $Existing | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

$Arguments = @(
    "--host", $ServerHost,
    "--port", $Port,
    "--model", $ModelPath,
    "--ctx-size", $ContextSize,
    "--predict", $PredictTokens,
    "--gpu-layers", $GpuLayers,
    "--parallel", $Parallel,
    "--batch-size", $BatchSize,
    "--ubatch-size", $UBatchSize
)

if ($ContBatching) {
    $Arguments += "--cont-batching"
}

Write-Host "Starting llama-server on http://$ServerHost`:$Port with ctx=$ContextSize, predict=$PredictTokens, gpu-layers=$GpuLayers, parallel=$Parallel, cont-batching=$ContBatching"

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
    Start-Sleep -Milliseconds 750
    if (-not $process.HasExited) {
        Write-Host "Started llama-server PID $($process.Id)"
    } else {
        Write-Host "llama-server exited immediately with code $($process.ExitCode)"
    }
} catch {
    Write-Error "Failed to start llama-server: $($_.Exception.Message)"
    throw
}
