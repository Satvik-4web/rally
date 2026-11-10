# RALLY 2.0 Unified Launcher
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  RALLY 2.0 — Autonomous Local AI Desktop Operator " -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Cyan

# 1. Check if backend is already listening on 8765
$backendRunning = $false
try {
    $res = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/health" -TimeoutSec 1 -ErrorAction SilentlyContinue
    if ($res.status -eq "ok") {
        $backendRunning = $true
        Write-Host "✓ Backend already running on http://127.0.0.1:8765" -ForegroundColor Green
    }
} catch {}

if (-not $backendRunning) {
    Write-Host "Starting RALLY backend service (Uvicorn on :8765)..." -ForegroundColor Yellow
    $pyExe = Join-Path $RootDir "services\orchestrator\.venv\Scripts\python.exe"
    $orchDir = Join-Path $RootDir "services\orchestrator"
    
    Start-Process -FilePath $pyExe -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765" -WorkingDirectory $orchDir -WindowStyle Hidden
    
    # Wait for backend to be ready
    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 400
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/health" -TimeoutSec 1 -ErrorAction SilentlyContinue
            if ($health.status -eq "ok") {
                $ready = $true
                break
            }
        } catch {}
    }
    if ($ready) {
        Write-Host "✓ Backend service online & listening for voice!" -ForegroundColor Green
    } else {
        Write-Host "⚠️ Backend is starting in background..." -ForegroundColor Yellow
    }
}

# 2. Launch Desktop App
Write-Host "Launching RALLY Desktop Companion..." -ForegroundColor Cyan
Set-Location (Join-Path $RootDir "apps\desktop")
npm run dev:electron
