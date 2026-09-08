# Explicit single-date release candidate. No scheduler is installed by this file.
# Opens the payment window; never approves payment inside it.
param([switch]$PartialDry)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$fire = [DateTimeOffset]::Parse('2026-09-09T09:00:00+09:00')
$now = [DateTimeOffset]::Now
if ($now -lt $fire.AddMinutes(-30) -or $now -ge $fire.AddMinutes(-15)) {
  throw 'This candidate must start on 2026-09-09 between 08:30 and 08:45 KST (recommended 08:35). It never rolls over to another day.'
}
& (Join-Path $PSScriptRoot 'astra_browsers.ps1') -Port 9232 -Restart
if ($LASTEXITCODE -ne 0) { throw 'Astra Chrome launch failed' }
$python = Join-Path $root '.venv/Scripts/python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$mode = if ($PartialDry) { 'dry' } else { 'payment-window' }
& $python (Join-Path $PSScriptRoot 'daily.py') --at $fire.ToString('o') `
  --from FCO --route ICN --date 09-04 --cabin '프레스티지' --no-watch `
  --ready-by 08:50 --mode $mode
exit $LASTEXITCODE
