# ScanGrade dev start script for Windows PowerShell
$ErrorActionPreference = "Stop"
$PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $PROJECT_DIR

Write-Host "=== ScanGrade Dev Server ===" -ForegroundColor Cyan

# 1. Detect Python 3.12+
$PYTHON = $null
$candidates = @(
    "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Python312\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $PYTHON = $c; break }
}
if (-not $PYTHON) {
    # Try PATH
    try { $PYTHON = (Get-Command python).Source } catch { }
}
if (-not $PYTHON) { Write-Host "Python 3.12 not found!" -ForegroundColor Red; exit 1 }

Write-Host "Using: $PYTHON" -ForegroundColor Green

# 2. Setup .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created — ISI SUPABASE KEY DAHULU" -ForegroundColor Yellow
    Write-Host "  Buka: https://supabase.com/dashboard/project/roshkbkbzgfedowozfo/settings/api" -ForegroundColor Yellow
    Write-Host "  Lalu isi SUPABASE_ANON_KEY dan SUPABASE_SERVICE_KEY di .env" -ForegroundColor Yellow
    Start-Sleep 2
}
$envContent = Get-Content ".env" -Raw
if ($envContent -match "ISI_|your-project") {
    Write-Host ".env masih berisi placeholder — isi key dulu di .env" -ForegroundColor Yellow
    Write-Host "Buka .env dengan notepad dan isi SUPABASE keys" -ForegroundColor Yellow
    notepad ".env"
    Write-Host "Setelah diisi, jalankan ulang script ini" -ForegroundColor Cyan
    exit 0
}

# 3. Setup venv
if (-not (Test-Path "venv\")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    & $PYTHON -m venv venv
}
$VENV_PY = ".\venv\Scripts\python.exe"
if (Test-Path $VENV_PY) { $PYTHON = $VENV_PY }

# 4. Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $PYTHON -m pip install -r requirements.txt -q

# 5. Build Tailwind CSS
Write-Host "Building Tailwind CSS..." -ForegroundColor Cyan
npm run css:build

# 6. Start server
Write-Host ""
Write-Host "=== Server starting at http://localhost:5000 ===" -ForegroundColor Cyan
Write-Host "     Mobile/network: http://192.168.x.x:5000" -ForegroundColor Cyan
Write-Host "     Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""
& $PYTHON wsgi.py
