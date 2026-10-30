$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.11+ is required. Install it explicitly before setup.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js 22+ is required. Install it explicitly before setup.' }
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create project virtual environment.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location frontend
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Ready. Run .\start.cmd and open the local URL printed by the launcher.'
