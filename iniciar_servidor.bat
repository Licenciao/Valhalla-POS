@echo off
chcp 65001 >nul
title Servidor Valhalla — Iniciando...

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo ======================================================================
echo    INICIANDO SERVIDOR CENTRAL VALHALLA (INVENTARIO Y VENTAS)
echo ======================================================================
echo.

:: 1. Obtener la IP local
for /f "tokens=*" %%i in ('powershell -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' -and $_.IPAddress -notlike '169.254*' } | Select-Object -ExpandProperty IPAddress -First 1"') do set "SERVER_IP=%%i"

if "%SERVER_IP%"=="" set "SERVER_IP=127.0.0.1"

:: 2. Verificar si Docker está disponible
where docker >nul 2>&1
if %errorLevel% equ 0 (
    echo [1/2] Iniciando mediante Docker Compose...
    docker compose up -d
) else (
    echo [1/2] Iniciando mediante motor nativo en segundo plano (0.0.0.0:8000)...
    set "DJANGO_DEBUG=1"
    set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
    set "DJANGO_SQLITE_PATH=db.sqlite3"
    set "DJANGO_ALLOWED_HOSTS=*"
    
    cd /d "%ROOT_DIR%apps\api"
    start "Valhalla_Backend_Daemon" /min .venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 --noreload
)

cd /d "%ROOT_DIR%"

echo.
echo [2/2] Esperando inicializaci n del servicio...
timeout /t 2 /nobreak >nul

echo.
echo ======================================================================
echo    ✔ SERVIDOR EN L NEA Y ESCUCHANDO PETICIONES
echo ======================================================================
echo.
echo    * Acceso en esta PC:       http://127.0.0.1:8000/
echo    * Acceso desde Celular:   http://%SERVER_IP%:8000/
echo.
echo  Abriendo interfaz web en tu navegador predeterminado...
start http://127.0.0.1:8000/

echo.
echo  [Presiona cualquier tecla para minimizar esta ventana]
pause >nul
