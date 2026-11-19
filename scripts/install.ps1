# AutoContent AI - Windows installer (idempotent).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Python {
    foreach ($v in @("3.13", "3.12", "3.11")) {
        try {
            $p = & py "-$v" -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
        } catch {}
    }
    try {
        $ver = & python -c "import sys;print('%d.%d' % sys.version_info[:2])"
        if ([version]$ver -ge [version]"3.11") { return (Get-Command python).Source }
    } catch {}
    throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')."
}

Write-Host "==> Python" -ForegroundColor Cyan
$py = Find-Python
Write-Host "    using $py"
if (-not (Test-Path "backend\.venv\Scripts\python.exe")) { & $py -m venv backend\.venv }
$vpy = "backend\.venv\Scripts\python.exe"
# --use-feature=truststore uses the Windows certificate store (works behind corporate TLS inspection)
& $vpy -m pip install --quiet --use-feature=truststore --upgrade pip
& $vpy -m pip install --quiet --use-feature=truststore -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Dashboard" -ForegroundColor Cyan
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js 20+ not found. Install from https://nodejs.org/" }
Push-Location frontend
$env:NODE_USE_SYSTEM_CA = "1"
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install failed" }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "dashboard build failed" }
Pop-Location

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "==> Created .env from .env.example" }

Write-Host "==> Bundled FFmpeg" -ForegroundColor Cyan
& $vpy -c "import imageio_ffmpeg,subprocess;print(subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-version'],capture_output=True,text=True).stdout.splitlines()[0])"

Write-Host ""
Write-Host "Installed. Start with:  powershell -File scripts\start.ps1   then open http://127.0.0.1:8000" -ForegroundColor Green
