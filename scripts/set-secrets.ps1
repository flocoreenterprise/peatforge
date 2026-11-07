[CmdletBinding()]
param([string[]]$Name)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-SettingShape {
    param([string]$SettingName, [string]$Value)
    if ($Value -match "[\r\n\x00']" -or $Value.Length -gt 4096) { return $false }
    switch ($SettingName) {
        'PUBLIC_DEMO' { return $Value -cmatch '^(true|false)$' }
        'PEATFORGE_ENGINE_MODE' { return $Value -cmatch '^(mock|real)$' }
        'PEATFORGE_DATA_SOURCE' { return $Value -cmatch '^(live|fixture)$' }
        'DEMO_PIN' { return $Value -cmatch '^\d{6}$' }
        'OPENAI_API_KEY' { return $Value -cmatch '^sk-[A-Za-z0-9_-]{16,}$' }
        'TWILIO_ACCOUNT_SID' { return $Value -cmatch '^AC[0-9a-fA-F]{32}$' }
        'TWILIO_AUTH_TOKEN' { return $Value -match '^[0-9a-fA-F]{32}$' }
        'TWILIO_FROM_NUMBER' { return $Value -match '^\+[1-9]\d{7,14}$' }
        'VOICE_ENGINE' { return $Value -cmatch '^(auto|twiml|realtime)$' }
        'OPENAI_REALTIME_MODEL' { return $Value -match '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$' }
        'OPENAI_REALTIME_VOICE' { return $Value -match '^[A-Za-z][A-Za-z0-9_-]{0,63}$' }
        'FIRMS_MAP_KEY' { return $Value -match '^[A-Za-z0-9_-]{8,128}$' }
        'TELEGRAM_BOT_TOKEN' { return $Value -match '^\d+:[A-Za-z0-9_-]{20,}$' }
        'TELEGRAM_WEBHOOK_SECRET' { return $Value -match '^[A-Za-z0-9_-]{32,256}$' }
        'CONTACT_ENCRYPTION_KEY' {
            if ($Value -notmatch '^[A-Za-z0-9_-]{43}=$') { return $false }
            try { return [Convert]::FromBase64String($Value.Replace('-','+').Replace('_','/')).Length -eq 32 }
            catch { return $false }
        }
        { $_ -in @('PUBLIC_BASE_URL', 'SMS_GATEWAY_URL') } {
            $parsed = $null
            if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$parsed)) { return $false }
            if ($parsed.Scheme -notin @('http','https') -or -not $parsed.Host -or $parsed.UserInfo -or $parsed.Query -or $parsed.Fragment) { return $false }
            if ($SettingName -eq 'PUBLIC_BASE_URL' -and ($parsed.Scheme -ne 'https' -or $parsed.AbsolutePath -ne '/')) { return $false }
            return $true
        }
        { $_ -in @('SESSION_SECRET','PHONE_HASH_SALT','SMS_WEBHOOK_SECRET','SMS_SIGNING_KEY') } { return $Value.Length -ge 32 }
        default { return -not [string]::IsNullOrWhiteSpace($Value) }
    }
}

function Update-EnvSettings {
    param([string]$Path, [System.Collections.IDictionary]$Updates)
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [IO.File]::ReadAllLines($Path)) {
            if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=' -and $Updates.Contains($Matches[1])) { continue }
            $lines.Add($line)
        }
    }
    foreach ($key in $Updates.Keys) { $lines.Add($key + "='" + $Updates[$key] + "'") }
    $temporary = Join-Path ([IO.Path]::GetDirectoryName($Path)) ('.env.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllLines($temporary, $lines, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}

function Invoke-SecretSetup {
    param([string[]]$SelectedNames)
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $allowed = @(Get-Content -LiteralPath (Join-Path $repoRoot '.env.example') | ForEach-Object {
        if ($_ -match '^([A-Z][A-Z0-9_]*)=$') { $Matches[1] }
    })
    if (-not $SelectedNames) { $SelectedNames = $allowed }
    foreach ($setting in $SelectedNames) {
        if ($setting -cnotin $allowed) { throw 'Nama konfigurasi tidak dikenal.' }
    }
    $updates = [ordered]@{}
    try {
        foreach ($setting in ($SelectedNames | Select-Object -Unique)) {
            do {
                $secure = Read-Host -Prompt ($setting + ' (kosong=lewati, !clear=hapus; ulang jika format salah)') -AsSecureString
                $pointer = [IntPtr]::Zero
                $value = $null
                try {
                    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                    $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
                }
                finally {
                    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
                    $secure.Dispose()
                }
                if ($value.Length -eq 0) { break }
                if ($value -ceq '!clear') { $updates[$setting] = ''; break }
                if (Test-SettingShape -SettingName $setting -Value $value) { $updates[$setting] = $value; break }
                $value = $null
            } while ($true)
        }
        if ($updates.Count -gt 0) { Update-EnvSettings -Path (Join-Path $repoRoot '.env') -Updates $updates }
        Write-Output 'tersimpan'
    }
    finally { $value = $null; $updates.Clear() }
}

# Dot sourcing exposes helpers for isolated tests, without asking for real secrets.
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-SecretSetup -SelectedNames $Name }
    catch { Write-Error 'Konfigurasi gagal disimpan. Nilai tidak ditampilkan.' -ErrorAction Continue; exit 1 }
}
