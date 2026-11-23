# setup_postgres_dev.ps1
# T-01 — Verifica PostgreSQL, crea la base de datos de desarrollo y configura
#         las variables de entorno para correr la API sin SQLite de prueba.
#
# Uso:
#   .\setup_postgres_dev.ps1 [-DbName inventario_dev] [-DbUser inventario] [-DbPassword <secret>]
#
# El script:
#   1. Verifica que psql esté disponible en PATH o en la ruta típica de instalación.
#   2. Crea el usuario y la base de datos si no existen.
#   3. Genera un archivo .env en apps/api/ con las variables necesarias.
#   4. Ejecuta `manage.py check --database default` para validar la conexión.
#   5. Ejecuta `manage.py migrate` y reporta el resultado.

[CmdletBinding()]
param(
    [string]$DbName     = "inventario_dev",
    [string]$DbUser     = "inventario",
    [string]$DbPassword = "",
    [string]$DbHost     = "localhost",
    [string]$DbPort     = "5432",
    [string]$PgSuperuser = "postgres"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ApiDir   = Join-Path $PSScriptRoot "..\apps\api"
$VenvPy   = Join-Path $ApiDir ".venv\Scripts\python.exe"
$EnvFile  = Join-Path $ApiDir ".env"

# ── 1. Localizar psql ────────────────────────────────────────────────────────
function Find-Psql {
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql) { return $psql.Source }

    $candidates = @(
        "C:\Program Files\PostgreSQL\17\bin\psql.exe",
        "C:\Program Files\PostgreSQL\16\bin\psql.exe",
        "C:\Program Files\PostgreSQL\15\bin\psql.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

$psqlPath = Find-Psql
if (-not $psqlPath) {
    Write-Error @"
psql no encontrado en PATH ni en rutas comunes de instalación.

Instala PostgreSQL 15 o superior desde https://www.postgresql.org/download/windows/
y asegúrate de que el directorio bin esté en PATH, o ejecuta este script
desde el directorio de instalación de PostgreSQL.
"@
    exit 1
}
Write-Host "✔ psql encontrado: $psqlPath" -ForegroundColor Green

# ── 2. Solicitar contraseña si no se proporcionó ─────────────────────────────
if (-not $DbPassword) {
    $secPwd = Read-Host "Contraseña para el usuario de BD '$DbUser'" -AsSecureString
    $DbPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPwd)
    )
}

# ── 3. Crear usuario y base de datos ─────────────────────────────────────────
Write-Host "`nCreando usuario '$DbUser' (si no existe)…"
$env:PGPASSWORD = ""  # usará autenticación de peer/trust para postgres superusuario
& $psqlPath -U $PgSuperuser -h $DbHost -p $DbPort -c `
    "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DbUser') THEN CREATE ROLE $DbUser LOGIN PASSWORD '$DbPassword'; END IF; END \$\$;" 2>&1

Write-Host "Creando base de datos '$DbName' (si no existe)…"
& $psqlPath -U $PgSuperuser -h $DbHost -p $DbPort -c `
    "SELECT 'CREATE DATABASE $DbName OWNER $DbUser' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DbName')\gexec" 2>&1

# ── 4. Generar archivo .env ───────────────────────────────────────────────────
Write-Host "`nGenerando $EnvFile…"

$secretKey = [Convert]::ToBase64String((1..48 | ForEach-Object { [byte](Get-Random -Maximum 256) }))

@"
# Generado por setup_postgres_dev.ps1 — $(Get-Date -Format "yyyy-MM-dd HH:mm")
# NO versionar este archivo; contiene secretos de desarrollo.

DJANGO_SECRET_KEY=$secretKey
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

POSTGRES_DB=$DbName
POSTGRES_USER=$DbUser
POSTGRES_PASSWORD=$DbPassword
POSTGRES_HOST=$DbHost
POSTGRES_PORT=$DbPort

API_VERSION=v1
"@ | Set-Content -Path $EnvFile -Encoding UTF8

Write-Host "✔ .env escrito en $EnvFile" -ForegroundColor Green
Write-Host "⚠  Nunca versiones este archivo." -ForegroundColor Yellow

# ── 5. Cargar .env y ejecutar checks ─────────────────────────────────────────
Write-Host "`nCargando variables de entorno desde .env…"
Get-Content $EnvFile | Where-Object { $_ -match "^\s*[^#].+=.+" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
}

if (-not (Test-Path $VenvPy)) {
    Write-Error "Entorno virtual no encontrado en $VenvPy. Ejecuta: uv sync en apps/api"
    exit 1
}

Write-Host "`nEjecutando manage.py check --database default…"
Push-Location $ApiDir
try {
    & $VenvPy manage.py check --database default
    if ($LASTEXITCODE -ne 0) {
        Write-Error "manage.py check falló con código $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Host "✔ check OK" -ForegroundColor Green

    Write-Host "`nEjecutando manage.py migrate…"
    & $VenvPy manage.py migrate
    if ($LASTEXITCODE -ne 0) {
        Write-Error "manage.py migrate falló con código $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Host "✔ migrate OK" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host @"

══════════════════════════════════════════════════════
✔ Setup completado.

Para ejecutar las pruebas contra PostgreSQL real:
  cd apps\api
  .venv\Scripts\python.exe manage.py test system

Para ejecutar las pruebas con SQLite aislado (CI):
  DJANGO_TEST_SQLITE=1 .venv\Scripts\python.exe manage.py test system
══════════════════════════════════════════════════════
"@ -ForegroundColor Cyan
