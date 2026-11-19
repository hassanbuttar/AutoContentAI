# Start AutoContent AI (API + dashboard + workers + scheduler).
#   -Dev     also start the Vite dev server on http://localhost:5173 (hot reload)
#   -Hidden  run without a console window (used by the Task Scheduler entry)
param([switch]$Dev, [switch]$Hidden)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$vpy = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { throw "Not installed. Run scripts\install.ps1 first." }
$storage = Join-Path $root "storage"
New-Item -ItemType Directory -Force (Join-Path $storage "logs") | Out-Null
$pidFile = Join-Path $storage "autocontent.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { Write-Host "Already running (PID $old)."; exit 0 }
}
if ($Dev) {
    Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory (Join-Path $root "frontend")
    $env:AUTOCONTENT_ENV = "development"
}
$startArgs = @{ FilePath = $vpy; ArgumentList = @("-m", "app"); WorkingDirectory = (Join-Path $root "backend"); PassThru = $true }
if ($Hidden) {
    $startArgs.WindowStyle = "Hidden"
    $startArgs.RedirectStandardOutput = (Join-Path $storage "logs\stdout.log")
    $startArgs.RedirectStandardError = (Join-Path $storage "logs\stderr.log")
}
$p = Start-Process @startArgs
$p.Id | Set-Content $pidFile
Write-Host "AutoContent AI started (PID $($p.Id)) -> http://127.0.0.1:8000" -ForegroundColor Green
