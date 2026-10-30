$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== SkyrimNet Kokoro PT-BR adapter setup ==" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.12 virtual environment..." -ForegroundColor Yellow
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 not found. Install it with: winget install Python.Python.3.12"
    }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

New-Item -ItemType Directory -Force -Path "models" | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

$files = @(
    @{ Path = "models\kokoro-v1.0.onnx"; Url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"; Min = 300000000 },
    @{ Path = "models\voices-v1.0.bin"; Url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"; Min = 20000000 }
)

foreach ($f in $files) {
    if ((Test-Path $f.Path) -and ((Get-Item $f.Path).Length -ge $f.Min)) {
        Write-Host "Already present: $($f.Path)" -ForegroundColor DarkGray
        continue
    }
    Write-Host "Downloading $($f.Path) ..." -ForegroundColor Yellow
    curl.exe -L --fail --retry 3 --retry-delay 2 --silent --show-error -o $f.Path $f.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($f.Url)" }
    if ((Get-Item $f.Path).Length -lt $f.Min) { throw "Downloaded file looks incomplete: $($f.Path)" }
}

Write-Host "Verifying model and pt-BR voices..." -ForegroundColor Yellow
& $python tools\check_install.py
if ($LASTEXITCODE -ne 0) { throw "Model/voice verification failed" }

Write-Host ""
Write-Host "Setup complete. Start the adapter with: start.bat" -ForegroundColor Green
Write-Host "Then in SkyrimNet: Settings -> Voice/TTS -> engine XTTS -> http://127.0.0.1:8020" -ForegroundColor Green
