$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Get-ChildItem -LiteralPath $Root -Filter "*.ps1" |
    Where-Object { $_.Name -ne "start.ps1" } |
    Sort-Object Name |
    Select-Object -First 1

if (-not $Script) {
    throw "Startup PowerShell script not found."
}

& $Script.FullName @args
