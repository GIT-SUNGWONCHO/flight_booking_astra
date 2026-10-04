# 아침 한 방. 08:20 에 스케줄러가 이것 하나를 실행한다.
#
#   08:20  리허설 - 크롬을 죽이고 새로 띄운 뒤, 9시에 도는 것과 같은 진입점으로
#          이미 열린 날짜에 dry 발사까지 해본다. 실패하면 팝업 (40분 남는다).
#   ~08:32 실전 셋팅 - 두 크롬을 마일리지 달력에 세운다.
#   08:50  준비 확인 - 정말로 보너스 달력에 서 있는지 눈으로 본다. 아니면 팝업.
#   09:00  발사 (08:59:57.5 선발사)
#
# 왜 아침에 리허설을 하나
#   09-04 에 09:00 을 통째로 잃었다. 원인은 '로그인 직후 위젯이 현금 모드로 남는 것'
#   이었는데, KE 세션 쿠키는 크롬을 끄면 사라지므로 **PC 를 껐다 켠 아침에만**
#   로그인이 필요하고 그때만 터졌다. 전날 16:00 리허설은 크롬이 켜져 있어
#   로그인 단계를 안 타고 늘 통과했다.
#   그래서 시험은 반드시 아침에, 차가운 크롬에서, 로그인부터 해야 한다.
param(
  # 시험용. 리허설만 하고 실전 셋팅/발사는 건너뛴다.
  [switch]$NoDaily
)
$ErrorActionPreference = 'SilentlyContinue'

# 파이썬 자식 프로세스의 한글이 로그에서 깨지지 않게. PowerShell 은 자식 출력을
# 콘솔 OEM 코드페이지(949)로 디코드하는데 파이썬은 UTF-8 로 쓴다. (09-04 실측)
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "day.ps1")      # $DailyArgs
$py = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "dev-shots"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Say([string]$m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
  Write-Output $line
  Add-Content -Path (Join-Path $logDir "morning.log") -Value $line
}

function Popup([string]$title, [string]$body) {
  # 스케줄러는 창을 숨긴 채 돌리므로, 문제가 생기면 이렇게라도 눈에 띄게 한다.
  #
  # **반드시 별도 프로세스로 띄운다.** MessageBox.Show 는 누가 확인을 누를 때까지
  # 부르는 쪽을 멈춘다. 09-07 에 리허설이 실패하자 이 팝업이 08:26 부터 09:20 까지
  # morning.ps1 을 붙잡았고, 그 아래 있던 **09:00 실전 코드가 실행조차 되지 않았다.**
  # 알리려고 만든 것이 지키려던 것을 죽였다. 다시는 이 함수가 흐름을 막지 않는다.
  $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(@"
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(@'
$body
'@, @'
$title
'@, 'OK', 'Warning')
"@))
  try {
    Start-Process -FilePath (Get-Process -Id $PID).Path `
      -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $enc | Out-Null
  } catch {
    try { Start-Process msg.exe -ArgumentList "*", $body | Out-Null } catch {}
  }
}

Say "=== 아침 시작 ==="

# --- 1) 리허설: 차가운 크롬 -> 로그인 -> 발사까지 ---
# 여기서 크롬을 죽이고 새로 띄우므로 browsers.ps1 을 따로 부르지 않는다.
#
# 리허설은 '남는 시간에 하는 것' 이다. PC 를 늦게 켜서 스케줄러가 밀린 실행을
# 뒤늦게 쏘면(StartWhenAvailable) 리허설이 실전 셋팅 시간을 통째로 먹는다.
# 08:40 이 지났으면 리허설을 통째로 건너뛰고 바로 실전 셋팅으로 간다.
# 실전 셋팅은 어차피 스스로 로그인·셋업을 하므로 리허설 없이도 굴러간다.
$deadline = (Get-Date).Date.AddHours(8).AddMinutes(40)
$leftSec  = [int]($deadline - (Get-Date)).TotalSeconds
$rOk = $true
$skipped = $false

if ($leftSec -lt 300) {
  $skipped = $true
  Say ("리허설 건너뜀 - 08:40 이 지났거나 남은 시간이 부족하다 (남은 {0}초). 실전 셋팅으로 바로 간다." -f [Math]::Max($leftSec, 0))
} else {
  Say ("리허설 (" + ($DailyArgs -join ' ') + ") - 크롬 죽이고 새로, 이미 열린 날짜로 dry 발사")
  $rOut = Join-Path $logDir "rehearse.out"
  $proc = Start-Process -FilePath $py -PassThru -NoNewWindow `
          -ArgumentList (@((Join-Path $PSScriptRoot "rehearse.py"), "--minutes", "8") + $DailyArgs) `
          -RedirectStandardOutput $rOut -RedirectStandardError (Join-Path $logDir "rehearse.err")
  # 08:40 까지만 준다. 넘기면 잘라낸다 - 실전이 리허설보다 중요하다.
  if ($proc.WaitForExit($leftSec * 1000)) {
    $rOk = ($proc.ExitCode -eq 0)
  } else {
    & taskkill /T /F /PID $proc.Id 2>&1 | Out-Null
    $rOk = $false
    Say "리허설이 08:40 을 넘겨 잘라냈다 - 실전 셋팅으로 넘어간다"
  }
  Get-Content $rOut -ErrorAction SilentlyContinue | ForEach-Object { Say "  $_" }
}

if ($skipped) {
  # 아무것도 안 알린다. 늦게 켠 것은 사람이 이미 아는 사실이고,
  # 지금 필요한 것은 알림이 아니라 09:00 에 서 있는 것이다.
} elseif ($rOk) {
  Say "리허설 통과 - 실전 셋팅으로 넘어간다"
} else {
  $why = ($rOut | Get-Content -ErrorAction SilentlyContinue |
          Select-String '^\s+X ' | ForEach-Object { $_.ToString().Trim() }) -join "`n"
  if (-not $why) { $why = "리허설이 끝나지 않았거나 로그를 읽지 못했습니다" }
  Say "!!! 리허설 실패 - 팝업"
  Popup "9시 리허설 실패 (지금 손봐야 합니다)" `
        ("09:00 까지 시간이 있습니다. 아래가 안 됐습니다:`n`n$why`n`n" +
         "실전 셋팅은 그대로 진행합니다. 08:50 에 한 번 더 확인합니다.`n" +
         "자세한 로그: dev-shots\rehearse.out / setup_failures.log")
}

# --- 2) 실전 셋팅 + 08:50 준비 확인 + 09:00 발사 ---
# 리허설이 실패했어도 돌린다. 사람이 그 사이에 고칠 수 있고, 셋업은 스스로 재시도한다.
if ($NoDaily) {
  Say "(-NoDaily) 실전 셋팅은 건너뜀 - 리허설만 확인"
} else {
  Say ("실전 셋팅 시작 (" + ($DailyArgs -join ' ') + ") - 08:50 준비 확인, 08:59:57.5 발사")
  & $py (Join-Path $PSScriptRoot "daily.py") @DailyArgs --ready-by 08:50 2>&1 |
    ForEach-Object { Say "  $_" }
}
Say "=== 아침 끝 (결과는 dev-shots 참고) ==="
