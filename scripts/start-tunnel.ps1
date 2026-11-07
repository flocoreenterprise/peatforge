[CmdletBinding()]
param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) { throw 'Install cloudflared before using this script.' }
# Run only while sharing the demo. Ctrl+C closes this tunnel. Access logging is disabled in start.ps1.
& cloudflared tunnel --url ("http://127.0.0.1:" + $Port) --no-autoupdate
if ($LASTEXITCODE -ne 0) { throw 'Tunnel exited with an error.' }
