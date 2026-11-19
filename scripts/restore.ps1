# Restore the database from a backup in storage\backups. Stop the app first (scripts\stop.ps1).
# Usage: powershell -File scripts\restore.ps1 -Backup autocontent_20261119_031500.db
param([Parameter(Mandatory = $true)][string]$Backup)
$root = Split-Path -Parent $PSScriptRoot
$src = if (Test-Path $Backup) { $Backup } else { Join-Path $root "storage\backups\$Backup" }
if (-not (Test-Path $src)) { throw "Backup not found: $Backup" }
$pidFile = Join-Path $root "storage\autocontent.pid"
if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
    throw "Stop the app first (scripts\stop.ps1)."
}
$db = Join-Path $root "storage\autocontent.db"
if (Test-Path $db) { Copy-Item $db "$db.before-restore" -Force }
Remove-Item "$db-wal", "$db-shm" -ErrorAction SilentlyContinue
Copy-Item $src $db -Force
Write-Host "Restored $src -> $db (previous copy kept as autocontent.db.before-restore)"
