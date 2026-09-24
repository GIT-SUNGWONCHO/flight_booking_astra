# 포트 소유권을 확인하고 이 worktree의 정확한 프로필 경로만 종료한다.
param([switch]$Restart, [int]$Port = 0)
$ErrorActionPreference = 'Stop'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$root = Split-Path -Parent $PSScriptRoot
# 9242(.api-profile, 본인)·9243(.debug-profile3, 와이프)은 두 번째 예매용이다.
# 인자 없이 부르면 운영 2개(9232·9233)만 다루고, 나머지는 -Port 로 지정해야 열린다
# (2026-09-23 사용자 결정: 다계정 구성).
$all = @(@{Port=9232; Profile='.debug-profile'}, @{Port=9233; Profile='.debug-profile2'},
         @{Port=9242; Profile='.api-profile'}, @{Port=9243; Profile='.debug-profile3'})
$targets = if ($Port) { @($all | Where-Object { $_.Port -eq $Port }) }
           else { @($all | Where-Object { $_.Port -lt 9242 }) }
if (-not $targets.Count) { throw 'Only Astra ports 9232, 9233, 9242 and 9243 are allowed' }
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
    if ($listeners.Count -and $owned.Count) {
      & (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'close_astra_browser.py') --port $t.Port
      Start-Sleep -Seconds 2
    }
    # 포트가 없는 GPU/utility 자식도 프로필 잠금을 유지할 수 있다.
    $profileOwned = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -match $profilePattern })
    foreach ($p in $profileOwned) {
      # 정상 종료 후 사라진 PID는 다시 종료하지 않는다.
      $remainingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)"
      if (-not $remainingProcess -or $remainingProcess.CommandLine -notmatch $profilePattern) { continue }
      Write-Output "Restarting owned Chrome PID $($p.ProcessId), port $($t.Port), profile $profile"
      # 확인 직후 자식 프로세스가 정상 종료될 수 있다. 최종 성공 여부는
      # 아래 포트 해제 검사로 판정한다.
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      Wait-Process -Id $p.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }
    # 프로세스 종료와 포트 해제 시점은 다를 수 있다. 2초 뒤 한 번만
    # 검사하면 실제로 종료되는 중인 브라우저를 실패로 판정한다.
    $closeDeadline = [DateTimeOffset]::Now.AddSeconds(30)
    do {
      $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $t.Port -ErrorAction SilentlyContinue)
      $profileRemaining = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -match $profilePattern })
      if (-not $listeners.Count -and -not $profileRemaining.Count) { break }
      Start-Sleep -Milliseconds 500
    } while ([DateTimeOffset]::Now -lt $closeDeadline)
    if ($listeners.Count) { throw "Port $($t.Port) did not close" }
    if ($profileRemaining.Count) { throw "Profile $profile still has live Chrome processes" }
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
  # 이름표 주입을 유지한다. Chrome 종료 시 함께 종료하고, 포트별 mutex로 중복 실행을 막는다.
  Start-Process -FilePath (Join-Path $root '.venv/Scripts/python.exe') -WindowStyle Hidden -ArgumentList @(
    ('"' + (Join-Path $PSScriptRoot 'browser_identity.py') + '"'), '--port', [string]$t.Port, '--keep') | Out-Null
  Write-Output "Astra Chrome port $($t.Port) ready"
}
$global:LASTEXITCODE = 0
