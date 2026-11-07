[CmdletBinding()]
param(
    [ValidateSet('Check','Prepare','Call','Diagnostic','Sms','RegisterSms')][string]$Action = 'Check',
    [ValidatePattern('^[A-Za-z0-9_-]{1,80}$')][string]$Responder = 'uat1',
    [switch]$Consent,
    [string]$Text
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/start.ps1 -Install first.' }
$arguments = @((Join-Path $PSScriptRoot 'uat.py'))
switch ($Action) {
    'Check' { $arguments += @('voice','check') }
    'Prepare' {
        if (-not $Consent) { throw 'The chosen test responder must consent; then use -Consent.' }
        $arguments += @('voice','provision-uat','--consent')
    }
    'Call' { $arguments += @('voice','call',$Responder,'--uat') }
    'Diagnostic' { $arguments += @('voice','call',$Responder,'--diag') }
    'RegisterSms' { $arguments += @('sms-register') }
    'Sms' {
        if ([string]::IsNullOrWhiteSpace($Text) -or $Text.Length -gt 300) { throw 'Supply an SMS of 1-300 characters.' }
        $arguments += @('sms','send',$Responder,$Text)
    }
}
Push-Location $repoRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'UAT command did not complete. Configuration values are not displayed.' }
}
finally { Pop-Location }
