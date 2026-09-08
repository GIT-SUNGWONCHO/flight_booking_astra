# Compatibility entry: every Astra Windows launcher uses the same ownership and label checks.
param([switch]$Restart, [int]$Port = 0)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'astra_browsers.ps1') -Restart:$Restart -Port $Port
