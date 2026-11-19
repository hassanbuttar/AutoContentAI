# Stop AutoContent AI. Jobs interrupted mid-run are recovered on the next start (DB leases); a post
# interrupted mid-upload is verified with the platform before anything is retried, so stopping never
# causes duplicate posts.
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "storage\autocontent.pid"
if (-not (Test-Path $pidFile)) { Write-Host "Not running (no PID file)."; exit 0 }
$procId = Get-Content $pidFile
if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
    Stop-Process -Id $procId
    Write-Host "Stopped PID $procId"
} else {
    Write-Host "Process $procId is not running."
}
Remove-Item $pidFile -ErrorAction SilentlyContinue
