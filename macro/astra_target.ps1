# 공통 일정의 당일 준비만 실행한다. 09시 예매 무장·발사는 사용자의 HUD 대기 시작으로 한다.
param([string]$Day = (Get-Date -Format 'yyyy-MM-dd'), [switch]$Preview, [switch]$Cold)
$ErrorActionPreference = 'Stop'
$astraRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$astraArgs = @('--day', $Day)
if ($Preview) { $astraArgs += '--preview' }
if ($Cold) { $astraArgs += '--cold' }
& (Join-Path $astraRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'prepare_day.py') @astraArgs
exit $LASTEXITCODE
