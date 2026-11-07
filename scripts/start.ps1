[CmdletBinding()]
param([switch]$Install, [switch]$Build, [switch]$Uat, [switch]$PublicReporting, [switch]$PublicTunnel, [switch]$PhoneUat, [switch]$DemoTunnel, [switch]$ProtectedTunnel, [string]$PublicBase = '', [int]$Port = 8000, [int]$PublicPort = 8001)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ($Install) {
        if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
            python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        }
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
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
    $serverArguments = @('scripts/serve.py','--port',"$Port")
    if ($Uat) { $serverArguments += '--uat' }
    if ($PublicReporting) { $serverArguments += @('--public-reporting','--public-port',"$PublicPort") }
    if ($PublicTunnel) { $serverArguments += '--public-tunnel' }
    if ($PhoneUat) { $serverArguments += '--phone-uat' }
    if ($DemoTunnel) { $serverArguments += '--demo-tunnel' }
    if ($ProtectedTunnel) { $serverArguments += '--protected-tunnel' }
    if ($PublicBase) { $serverArguments += @('--public-base',$PublicBase) }
    & .\.venv\Scripts\python.exe @serverArguments
    if ($LASTEXITCODE -ne 0) { throw 'Server exited with an error.' }
} finally { Pop-Location }
