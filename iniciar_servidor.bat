@echo off
chcp 65001 >nul
title Servidor Valhalla — Control Central

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo ======================================================================
echo    INICIANDO SERVIDOR CENTRAL VALHALLA (INVENTARIO Y VENTAS)
echo ======================================================================
echo.

:: 1. Verificar que el entorno virtual exista y sea valido
set "VENV_PYTHON=%ROOT_DIR%apps\api\.venv\Scripts\python.exe"
set "NEED_INSTALL=0"
if not exist "%VENV_PYTHON%" set "NEED_INSTALL=1"
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys" >nul 2>&1
    if %errorLevel% neq 0 set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="1" (
    echo [!] El sistema aun no esta configurado en esta computadora.
    echo [*] Ejecutando el instalador automatico por primera vez...
    echo.
    call "%ROOT_DIR%instalar_servidor.bat"
    if not exist "%VENV_PYTHON%" (
        echo [!] No se pudo completar la instalacion. Abortando.
        pause
        exit /b 1
    )
)

:: 2. Obtener la IP local usando ipconfig
set "SERVER_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "ipv4"') do (
    if not defined SERVER_IP (
        for /f "tokens=1" %%b in ("%%a") do set "SERVER_IP=%%b"
    )
)
if "%SERVER_IP%"=="" set "SERVER_IP=127.0.0.1"

:: 3. Verificar si ya esta corriendo en el puerto 8000
netstat -ano | findstr :8000 | findstr LISTENING >nul 2>&1
if %errorLevel% equ 0 (
    echo [i] El servidor ya se encuentra activo en el puerto 8000.
    goto :server_ready
)

:: 4. Iniciar motor central en segundo plano
echo [1/2] Iniciando motor central en segundo plano...
set "DJANGO_DEBUG=1"
set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
set "DJANGO_SQLITE_PATH=db.sqlite3"
set "DJANGO_ALLOWED_HOSTS=*"

cd /d "%ROOT_DIR%apps\api"
start "Valhalla_Servidor_Central" /min cmd /k ".venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 --noreload"
cd /d "%ROOT_DIR%"

echo.
echo [2/2] Esperando inicializacion del servicio...
set "ATTEMPTS=0"
:wait_server
ping 127.0.0.1 -n 2 >nul
netstat -ano | findstr :8000 | findstr LISTENING >nul 2>&1
if %errorLevel% equ 0 goto :server_ready
set /a ATTEMPTS+=1
if %ATTEMPTS% lss 8 goto :wait_server

:server_ready
echo.
echo ======================================================================
echo    ✔ SERVIDOR EN LINEA Y ESCUCHANDO PETICIONES
echo ======================================================================
echo.
echo    * Acceso en esta PC:       http://127.0.0.1:8000/
echo    * Acceso desde Celular:   http://%SERVER_IP%:8000/
echo.
echo  Abriendo interfaz web en tu navegador predeterminado...
start http://127.0.0.1:8000/

echo.
echo  [Puedes cerrar esta ventana; el servidor continuara en segundo plano]
pause >nul