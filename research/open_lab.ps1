# 안정 예매/계측과 별도인 API 연구용 Chrome만 연다. 기존 프로필을 복사하지 않는다.
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
$labProfile = [IO.Path]::GetFullPath((Join-Path $labRoot '.api-profile'))
if (-not $labProfile.StartsWith([IO.Path]::GetFullPath($labRoot) + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw 'API profile outside Astra workspace'
}
$labPort = 9242
$labPattern = '(?:^|\s)--user-data-dir=(?:"' + [regex]::Escape($labProfile) + '"|' + [regex]::Escape($labProfile) + ')(?:\s|$)'
$labOwned = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
  $_.CommandLine -match '(?:^|\s)--remote-debugging-port=9242(?:\s|$)' -and $_.CommandLine -match $labPattern
})
$labListeners = @(Get-NetTCPConnection -State Listen -LocalPort $labPort -ErrorAction SilentlyContinue)
if ($labListeners.Count -and -not $labOwned.Count) { throw '9242 is occupied by an unrecognized process' }
if (-not $labListeners.Count) {
  Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -WindowStyle Hidden -ArgumentList @(
    '--remote-debugging-port=9242', ('--user-data-dir="' + $labProfile + '"'), '--no-first-run',
    '--no-default-browser-check', '--window-size=1600,1000', '--disable-popup-blocking',
    '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding', 'https://www.koreanair.com/kr/ko') | Out-Null
}
for ($labAttempt=0; $labAttempt -lt 20; $labAttempt++) {
  try {
    $labVersion = Invoke-RestMethod -Uri 'http://127.0.0.1:9242/json/version' -TimeoutSec 2
    if ($labVersion.webSocketDebuggerUrl) { Write-Output 'Astra API research Chrome 9242 ready'; exit 0 }
  } catch {}
  Start-Sleep -Milliseconds 500
}
throw 'API research Chrome did not become ready'
