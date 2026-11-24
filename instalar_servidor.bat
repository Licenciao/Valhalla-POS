@echo off
chcp 65001 >nul
title Instalador del Servidor Valhalla — Control de Inventario y Ventas

echo ======================================================================
echo    VALHALLA — INSTALADOR Y CONFIGURADOR DEL SERVIDOR CENTRAL
echo ======================================================================
echo.

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

:: 1. Comprobar permisos y configurar Firewall si es Administrador
echo [1/4] Comprobando configuracion de red y Firewall...
net session >nul 2>&1
if %errorLevel% equ 0 goto :firewall_admin

echo     [i] Modo usuario estandar (sin privilegios de Administrador).
echo         El sistema funcionara localmente en http://127.0.0.1:8000/
echo         Para permitir conexion de otros celulares por WiFi,
echo         ejecuta este archivo haciendo clic derecho y 'Ejecutar como Administrador'.
goto :firewall_done

:firewall_admin
netsh advfirewall firewall delete rule name="Valhalla Servidor (Puerto 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="Valhalla Servidor (Puerto 8000)" dir=in action=allow protocol=TCP localport=8000 profile=any >nul 2>&1
echo     ✔ Regla de Firewall configurada exitosamente en puerto TCP 8000.

:firewall_done
echo.
echo [2/4] Verificando interprete de Python...

set "SYSTEM_PYTHON="

call :test_candidate "py -3"
if defined SYSTEM_PYTHON goto :python_found

call :test_candidate "python"
if defined SYSTEM_PYTHON goto :python_found

call :test_candidate "python3"
if defined SYSTEM_PYTHON goto :python_found

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    call :test_candidate "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("C:\Python*") do (
    call :test_candidate "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("%ProgramFiles%\Python*") do (
    call :test_candidate "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("%APPDATA%\uv\python\*") do (
    call :test_candidate "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

:: Intentar instalacion automatica por winget
echo.
echo     [!] No se encontro un interprete de Python activo en el sistema.
echo     [*] Intentando instalar Python 3.12 automaticamente via winget...
winget install Python.Python.3.12 --scope currentuser --silent --accept-package-agreements --accept-source-agreements >nul 2>&1

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    call :test_candidate "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

echo.
echo ======================================================================
echo   [!] ERROR: No se encontro Python en esta computadora.
echo ======================================================================
echo.
echo   Para continuar, realiza estos simples pasos:
echo   1. Descarga Python desde https://www.python.org/downloads/
echo   2. Abre el instalador y marca la casilla:
echo      [X] Add python.exe to PATH
echo   3. Vuelve a ejecutar este instalador (instalar_servidor.bat).
echo.
pause
exit /b 1

:test_candidate
if defined SYSTEM_PYTHON exit /b
%~1 -c "import sys" >nul 2>&1
if %errorLevel% equ 0 (
    set "SYSTEM_PYTHON=%~1"
)
exit /b

:python_found
echo     ✔ Python detectado correctamente: %SYSTEM_PYTHON%

:: 3. Crear entorno virtual limpio exclusivo para este equipo
set "VENV_PYTHON=%ROOT_DIR%apps\api\.venv\Scripts\python.exe"

echo.
echo [3/4] Preparando entorno virtual y dependencias...

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys" >nul 2>&1
    if %errorLevel% equ 0 goto :venv_ok
)

echo     [*] Generando entorno virtual en apps\api\.venv...
cd /d "%ROOT_DIR%apps\api"
if exist ".venv" rmdir /s /q ".venv" >nul 2>&1
"%SYSTEM_PYTHON%" -m venv .venv

if not exist ".venv\Scripts\python.exe" (
    echo     [!] ERROR: No se pudo generar la carpeta .venv.
    cd /d "%ROOT_DIR%"
    pause
    exit /b 1
)

echo     [*] Instalando librerias desde requirements.txt...
"%VENV_PYTHON%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PYTHON%" -m pip install -r requirements.txt
cd /d "%ROOT_DIR%"

:venv_ok
echo     ✔ Entorno virtual listo y validado.

:: 4. Base de datos y migraciones
set "DJANGO_DEBUG=1"
set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
set "DJANGO_SQLITE_PATH=db.sqlite3"
set "DJANGO_ALLOWED_HOSTS=*"

echo.
echo     [*] Aplicando migraciones de base de datos...
cd /d "%ROOT_DIR%apps\api"
"%VENV_PYTHON%" manage.py migrate --noinput
cd /d "%ROOT_DIR%"

:: 5. Obtener IP local
echo.
echo [4/4] Consultando direccion de red local...
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
echo  Direcciones de acceso al sistema:
echo.
echo    * En esta PC (Localhost):   http://127.0.0.1:8000/
echo    * En la red WiFi / Celular: http://%SERVER_IP%:8000/
echo.
echo  Credenciales por defecto:
echo    Usuario:     admin
echo    Contrasena:  Admin-password-123!
echo.
echo  Para encender el servidor ejecuta: iniciar_servidor.bat
echo ======================================================================
echo.
pause
