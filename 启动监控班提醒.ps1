param(
    [int]$Port = 8080,
    [switch]$Reload,
    [switch]$NoBrowser,
    [switch]$NoScheduler,
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $Root ".venv")
    $Reinstall = $true
}

$NeedsInstall = $Reinstall
if (-not $NeedsInstall) {
    $UvicornExe = Join-Path $Root ".venv\Scripts\uvicorn.exe"
    if (-not (Test-Path $UvicornExe)) {
        $NeedsInstall = $true
    }
}

if (-not $NeedsInstall) {
    & $VenvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('rapidocr_onnxruntime') else 1)"
    if ($LASTEXITCODE -ne 0) {
        $NeedsInstall = $true
    }
}

if ($NeedsInstall) {
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip upgrade failed with exit code $LASTEXITCODE"
    }
    & $VenvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        throw "dependency install failed with exit code $LASTEXITCODE"
    }
}

$env:DATA_DIR = Join-Path $Root "data"
$env:UPLOAD_DIR = Join-Path $Root "uploads"
if ($NoScheduler) {
    $env:ENABLE_SCHEDULER = "false"
} else {
    $env:ENABLE_SCHEDULER = "true"
}
$env:TZ = "Asia/Shanghai"

New-Item -ItemType Directory -Force -Path $env:DATA_DIR, $env:UPLOAD_DIR | Out-Null

$Url = "http://127.0.0.1:$Port"
Write-Host "监控班提醒启动中: $Url"
Write-Host "停止服务: 在此窗口按 Ctrl+C"

if (-not $NoBrowser) {
    Start-Process $Url
}

$ArgsList = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) {
    $ArgsList += "--reload"
}

& $VenvPython @ArgsList
