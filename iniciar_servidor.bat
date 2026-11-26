@echo off
chcp 65001 >nul
title Servidor Valhalla — Control Central

echo ======================================================================
echo    __
echo    \ \__________    VALHALLA POS
echo     \ \  _  _  /    Iniciando Servidor Central - Inventario y Ventas
echo      \ \______/
echo       O      O
echo ======================================================================
echo.

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

set "VENV_PYTHON=%ROOT_DIR%apps\api\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" goto :needs_install
"%VENV_PYTHON%" -c "import django, rest_framework, cryptography, corsheaders" >nul 2>&1
if not errorlevel 1 goto :venv_ready

:needs_install
echo [!] El sistema aun no esta configurado o faltan librerias en esta PC.
echo [*] Ejecutando el instalador automatico...
echo.
call "%ROOT_DIR%instalar_servidor.bat"

if not exist "%VENV_PYTHON%" goto :install_failed
"%VENV_PYTHON%" -c "import django, rest_framework, cryptography, corsheaders" >nul 2>&1
if not errorlevel 1 goto :venv_ready

:install_failed
echo.
echo [!] No se pudo completar la instalacion del entorno.
echo     Revisa los mensajes anteriores para ver el detalle.
echo.
pause
exit /b 1

:venv_ready
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
if not errorlevel 1 (
    echo [i] El servidor ya se encuentra activo en el puerto 8000.
    goto :server_ready
)

:: 4. Aplicar migraciones pendientes e iniciar motor central en segundo plano
set "DJANGO_DEBUG=1"
set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
set "DJANGO_SQLITE_PATH=db.sqlite3"
set "DJANGO_ALLOWED_HOSTS=*"
set "DJANGO_CSRF_TRUSTED_ORIGINS=http://%SERVER_IP%:8000,http://127.0.0.1:8000,http://localhost:8000"

echo [1/3] Aplicando migraciones de base de datos...
cd /d "%ROOT_DIR%apps\api"
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 (
    cd /d "%ROOT_DIR%"
    echo.
    echo [!] ERROR: Fallo al aplicar migraciones en la base de datos.
    pause
    exit /b 1
)

echo [2/3] Iniciando motor central en segundo plano...
start "Valhalla_Servidor_Central" /min cmd /k ".venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 --noreload"
cd /d "%ROOT_DIR%"

echo.
echo [3/3] Esperando inicializacion del servicio...
set "ATTEMPTS=0"
:wait_server
ping 127.0.0.1 -n 2 >nul
netstat -ano | findstr :8000 | findstr LISTENING >nul 2>&1
if not errorlevel 1 goto :server_ready
set /a ATTEMPTS+=1
if %ATTEMPTS% lss 12 goto :wait_server

echo.
echo ======================================================================
echo   [!] ERROR: El servidor no respondio en el puerto 8000.
echo ======================================================================
echo.
echo   Revisa la ventana minimizada 'Valhalla_Servidor_Central' en tu
echo   barra de tareas para ver si ocurrio algun error al arrancar.
echo.
pause
exit /b 1

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
echo  El servidor esta funcionando en segundo plano.
echo  Puedes cerrar esta ventana cuando desees.
echo.
pause
