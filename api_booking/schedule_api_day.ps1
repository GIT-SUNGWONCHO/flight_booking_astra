# api_day 를 Windows 작업 스케줄러에 한 번만 걸어 둔다(오늘 지정 시각, 로그온 세션, 콘솔 창 표시).
# 지속 설정이므로 사용자 승인 뒤에만 등록한다. 끝난 작업은 -Remove 로 지운다.
#
#   등록  powershell -File api_booking\schedule_api_day.ps1 -Name ColdStart -At 03:20 -ArgsLine "--mode rehearsal ..."
#   삭제  powershell -File api_booking\schedule_api_day.ps1 -Name ColdStart -Remove
#   확인  Get-ScheduledTask -TaskName Astra-* | Get-ScheduledTaskInfo
param(
  [Parameter(Mandatory = $true)][string]$Name,
  [string]$At = '',
  [string]$ArgsLine = '',
  [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$taskName = "Astra-$Name"
if ($Remove) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  Write-Output "removed $taskName"
  return
}
if (-not $At -or -not $ArgsLine) { throw '-At HH:mm and -ArgsLine are required' }
$when = Get-Date $At
if ($when -le (Get-Date).AddMinutes(1)) { throw "start time $At is not in the future (today)" }
if ($ArgsLine -match '[&|<>^"]') { throw 'ArgsLine must not contain shell metacharacters' }

$outDir = Join-Path $root 'dev-shots\api-day'
New-Item -ItemType Directory -Force $outDir | Out-Null
$log = Join-Path $outDir ("$Name-" + $when.ToString('yyyyMMdd-HHmm') + '-console.log')
$wrapper = Join-Path $outDir "$Name.cmd"
# 콘솔 표시·UTF-8 출력·로그 사본. 창은 끝나도 닫지 않는다(결과 확인·조사 모드 입력용).
$lines = @(
  '@echo off',
  'chcp 65001 >nul',
  "title ASTRA $Name",
  "cd /d `"$root`"",
  'set PYTHONUTF8=1',
  'set PYTHONIOENCODING=utf-8',
  ("powershell -NoProfile -ExecutionPolicy Bypass -Command `"[Console]::OutputEncoding=[Text.Encoding]::UTF8; " +
   "& '.\.venv\Scripts\python.exe' -u api_booking\api_day.py $ArgsLine 2>&1 | Tee-Object -FilePath '$log'`""),
  'echo.',
  'echo [ASTRA] finished. Check the result above. Do not close before checking.',
  'pause'
)
[IO.File]::WriteAllLines($wrapper, $lines, (New-Object Text.UTF8Encoding($false)))

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$wrapper`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At $when
# 배터리 조건 해제, 절전 해제 깨우기, 놓치면 실행하지 않음(StartWhenAvailable 기본 false), 중복 실행 금지.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ("$env:USERDOMAIN\$env:USERNAME") -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Force | Out-Null
Write-Output "registered $taskName at $($when.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Output "wrapper $wrapper"
Write-Output "log $log"
