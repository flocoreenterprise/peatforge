[CmdletBinding()]
param([switch]$Install, [switch]$Build, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ($Install) {
        if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
            python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        }
        & .\.venv\Scripts\python.exe -m pip install -r agents/requirements.txt -r engine/requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        Push-Location web
        try {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run this script with -Install first.' }
    if ($Build -or -not (Test-Path -LiteralPath 'web\dist\index.html')) {
        Push-Location web
        try {
            npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
        } finally { Pop-Location }
    }
    $serverArguments = @('-m','uvicorn','agents.app:app','--host','127.0.0.1','--port',"$Port",'--no-access-log')
    if (Test-Path -LiteralPath '.env') { $serverArguments += @('--env-file', (Join-Path $repoRoot '.env')) }
    & .\.venv\Scripts\python.exe @serverArguments
    if ($LASTEXITCODE -ne 0) { throw 'Server exited with an error.' }
} finally { Pop-Location }
