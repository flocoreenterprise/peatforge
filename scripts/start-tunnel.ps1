[CmdletBinding()]
param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
if ($Port -ne 8001) { throw 'Only the reviewed public-reporting gateway on port 8001 may be tunneled. Operator port 8000 stays local.' }
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) { throw 'Install cloudflared before using this script.' }
# Run only while sharing the demo. Ctrl+C closes this tunnel. Access logging is disabled in start.ps1.
function Protect-TunnelLog {
    param([string]$Line)
    $Line = $Line -replace '/api/field/[A-Za-z0-9_-]{43}', '/api/field/[redacted]'
    $Line = $Line -replace '/f/[A-Za-z0-9_-]{43}', '/f/[redacted]'
    $Line = $Line -replace '/api/voice/stream/[A-Za-z0-9_.-]+', '/api/voice/stream/[redacted]'
    return ($Line -replace '\?[^\s"]+', '?[redacted]')
}
# cloudflared connection errors can contain full callback URLs even when ASGI
# access logs are off. Sanitize both native streams before displaying them.
$previousErrorPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & cloudflared tunnel --url ("http://127.0.0.1:" + $Port) --no-autoupdate 2>&1 |
        ForEach-Object { Protect-TunnelLog -Line ([string]$_) }
    $tunnelExitCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousErrorPreference }
if ($tunnelExitCode -ne 0) { throw 'Tunnel exited with an error.' }
