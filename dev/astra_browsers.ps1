# Only processes with both this worktree profile and its assigned port are owned.
param([switch]$Restart, [int]$Port = 0)
$ErrorActionPreference = 'Stop'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$root = Split-Path -Parent $PSScriptRoot
$targets = @(@{Port=9232; Profile='.debug-profile'}, @{Port=9233; Profile='.debug-profile2'})
if ($Port) { $targets = @($targets | Where-Object { $_.Port -eq $Port }) }
if (-not $targets.Count) { throw 'Only Astra ports 9232 and 9233 are allowed' }
foreach ($t in $targets) {
  $profile = [IO.Path]::GetFullPath((Join-Path $root $t.Profile))
  if (-not $profile.StartsWith([IO.Path]::GetFullPath($root) + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Profile outside this worktree'
  }
  $portPattern = '(?:^|\s)--remote-debugging-port=' + $t.Port + '(?:\s|$)'
  $profilePattern = '(?:^|\s)--user-data-dir=(?:"' + [regex]::Escape($profile) + '"|' + [regex]::Escape($profile) + ')(?:\s|$)'
  $owned = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
    $_.CommandLine -match $portPattern -and $_.CommandLine -match $profilePattern
  })
  $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $t.Port -ErrorAction SilentlyContinue)
  if ($listeners.Count -and -not $owned.Count) { throw "Port $($t.Port) belongs to an unrecognized process" }
  if ($Restart) {
    foreach ($p in $owned) {
      Write-Output "Restarting owned Chrome PID $($p.ProcessId), port $($t.Port), profile $profile"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
      Wait-Process -Id $p.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $t.Port -ErrorAction SilentlyContinue)
    if ($listeners.Count) { throw "Port $($t.Port) did not close" }
  }
  if (-not $listeners.Count) {
    Start-Process -FilePath $chrome -WindowStyle Hidden -ArgumentList @(
      "--remote-debugging-port=$($t.Port)", "--user-data-dir=`"$profile`"",
      '--no-first-run', '--no-default-browser-check', '--window-size=1600,1000',
      '--disable-popup-blocking',
      '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding', 'https://www.koreanair.com/kr/ko') | Out-Null
  }
  $ready = $false
  for ($attempt=0; $attempt -lt 20; $attempt++) {
    try {
      $v = Invoke-RestMethod -Uri "http://127.0.0.1:$($t.Port)/json/version" -TimeoutSec 2
      if ($v.webSocketDebuggerUrl) { $ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) { throw "Chrome endpoint $($t.Port) did not become ready" }
  & (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'browser_identity.py') --port $t.Port
  if ($LASTEXITCODE -ne 0) { Write-Warning "Chrome is ready, but its cosmetic profile label needs retry on $($t.Port)" }
  Write-Output "Astra Chrome port $($t.Port) ready"
}
$global:LASTEXITCODE = 0
