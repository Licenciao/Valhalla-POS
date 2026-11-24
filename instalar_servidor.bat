@echo off
chcp 65001 >nul
title Instalador del Servidor Valhalla — Control de Inventario y Ventas

echo ======================================================================
echo    VALHALLA — INSTALADOR Y CONFIGURADOR DEL SERVIDOR CENTRAL
echo ======================================================================
echo.

:: 1. Verificar privilegios de Administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Solicitando permisos de Administrador para configurar el Firewall...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo [1/4] Configurando Firewall de Windows Defender para permitir conexiones locales...
netsh advfirewall firewall delete rule name="Valhalla Servidor (Puerto 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="Valhalla Servidor (Puerto 8000)" dir=in action=allow protocol=TCP localport=8000 profile=any >nul 2>&1
if %errorLevel% equ 0 (
    echo     ✔ Regla de Firewall creada exitosamente en puerto TCP 8000.
) else (
    echo     [!] Advertencia: No se pudo modificar el firewall automaticamente.
)

:: 2. Determinar directorio base
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo.
echo [2/4] Verificando motor de base de datos...
set "DJANGO_DEBUG=1"
set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
set "DJANGO_SQLITE_PATH=db.sqlite3"
set "DJANGO_ALLOWED_HOSTS=*"

cd /d "%ROOT_DIR%apps\api"
echo     ✔ Aplicando migraciones de base de datos...
"%ROOT_DIR%apps\api\.venv\Scripts\python.exe" manage.py migrate --noinput

cd /d "%ROOT_DIR%"

:: 3. Obtener IP local de la maquina
echo.
echo [3/4] Obteniendo direcciones de red disponibles...
set "SERVER_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "ipv4"') do (
    if not defined SERVER_IP (
        for /f "tokens=1" %%b in ("%%a") do set "SERVER_IP=%%b"
    )
)
if "%SERVER_IP%"=="" set "SERVER_IP=127.0.0.1"

echo.
echo ======================================================================
echo    ✔ INSTALACION Y CONFIGURACION COMPLETADA CON EXITO
echo ======================================================================
echo.
echo  Puedes acceder al sistema desde:
echo.
echo    * Esta PC (Localhost):  http://127.0.0.1:8000/
echo    * Celular / Otra PC:   http://%SERVER_IP%:8000/
echo.
echo  Credenciales de acceso:
echo    Usuario:     admin
echo    Contrasena:  Admin-password-123!
echo.
echo  Para iniciar o detener el servidor en cualquier momento usa:
echo    * iniciar_servidor.bat
echo    * detener_servidor.bat
echo ======================================================================
echo.
pause