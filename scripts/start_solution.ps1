# scripts/start_solution.ps1
# Script de inicio orquestado para la solución completa (API Django + Cliente Flutter Windows)
#
# Uso:
#   .\scripts\start_solution.ps1 [-Port 8443] [-UseSqlite]

[CmdletBinding()]
param(
    [int]$Port = 8443,
    [switch]$UseSqlite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir   = Resolve-Path (Join-Path $PSScriptRoot "..")
$ApiDir    = Join-Path $RootDir "apps\api"
$ClientDir = Join-Path $RootDir "apps\clients"
$VenvPy    = Join-Path $ApiDir ".venv\Scripts\python.exe"
$Flutter   = "C:\Users\pablo\develop\flutter\bin\flutter.bat"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   SISTEMA DE INVENTARIO OFFLINE-FIRST — INICIO LOCAL     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Configurar Entorno
$env:DJANGO_SECRET_KEY = "local-dev-secret-key-valhalla-system-secure-32b"
if ($UseSqlite) {
    $env:DJANGO_TEST_SQLITE = "1"
    Write-Host "[1/3] Modo SQLite en memoria habilitado." -ForegroundColor Yellow
} else {
    Write-Host "[1/3] Modo estándar configurado." -ForegroundColor Green
}

# 2. Migraciones y Semilla Inicial
Write-Host "[2/3] Verificando base de datos y catálogo inicial..." -ForegroundColor Yellow
Push-Location $ApiDir
try {
    & $VenvPy manage.py migrate --noinput
    & $VenvPy manage.py seed_initial_business_data --confirm
} finally {
    Pop-Location
}

# 3. Lanzamiento
Write-Host "[3/3] Iniciando servicios..." -ForegroundColor Green
Write-Host "  • API Central: http://127.0.0.1:$Port/api/v1/health" -ForegroundColor Gray
Write-Host "  • Cliente Windows: Flutter Desktop" -ForegroundColor Gray
Write-Host ""
Write-Host "Para iniciar la API en una terminal separada ejecute:" -ForegroundColor Cyan
Write-Host "  cd apps/api; .venv/Scripts/python.exe manage.py runserver 127.0.0.1:$Port" -ForegroundColor White
Write-Host ""
Write-Host "Para iniciar el cliente Flutter ejecute:" -ForegroundColor Cyan
Write-Host "  cd apps/clients; C:\Users\pablo\develop\flutter\bin\flutter.bat run -d windows" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan
