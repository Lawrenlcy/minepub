param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
if (-not $env:MINERU_MODEL_SOURCE) { $env:MINERU_MODEL_SOURCE = 'modelscope' }
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv .venv --python 3.11 }
    else { py -3.11 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed. Install Python 3.11 or uv first.' }
}
& $pythonExe -c "import sys; from importlib.metadata import distributions; p={d.metadata['Name'].lower(): d.version for d in distributions()}; sys.exit(0 if all(n in p for n in ('fastapi','uvicorn','pypdf')) and p.get('mineru','').startswith('4.') else 1)"
if ($LASTEXITCODE -ne 0) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv pip install --python $pythonExe -r requirements.txt }
    else { & $pythonExe -m pip install -r requirements.txt }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
}
$localUrl = "http://127.0.0.1:$Port"
try {
    $status = Invoke-RestMethod "$localUrl/api/health" -TimeoutSec 2
    if ($status.engine) {
        Write-Host "Minepub is already running at $localUrl"
        if (-not $NoBrowser) { Start-Process $localUrl }
        exit 0
    }
} catch {}
Write-Host "Minepub: $localUrl"
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
if ($NoBrowser) { & $pythonExe run.py --port $Port --no-browser }
else { & $pythonExe run.py --port $Port }
