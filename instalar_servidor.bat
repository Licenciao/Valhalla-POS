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
if errorlevel 1 goto :firewall_no_admin

netsh advfirewall firewall delete rule name="Valhalla Servidor (Puerto 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="Valhalla Servidor (Puerto 8000)" dir=in action=allow protocol=TCP localport=8000 profile=any >nul 2>&1
if errorlevel 1 goto :firewall_warn
echo     ✔ Regla de Firewall configurada exitosamente en puerto TCP 8000.
goto :firewall_done

:firewall_warn
echo     [!] Advertencia: No se pudo crear la regla del Firewall automaticamente.
goto :firewall_done

:firewall_no_admin
echo     [i] Modo usuario estandar (sin privilegios de Administrador).
echo         El sistema funcionara localmente en http://127.0.0.1:8000/
echo         Para permitir conexion de otros celulares por WiFi,
echo         ejecuta este archivo haciendo clic derecho y 'Ejecutar como Administrador'.

:firewall_done
echo.
echo [2/4] Verificando interprete de Python (3.11 o superior)...

set "SYSTEM_PYTHON="

call :test_cmd py -3.12
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd py -3.13
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd py -3.11
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd py -3
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd python
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd python3
if defined SYSTEM_PYTHON goto :python_found

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("C:\Python*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("%ProgramFiles%\Python*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("%APPDATA%\uv\python\*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

:: Intentar instalacion automatica por winget
echo.
echo     [!] No se encontro Python 3.11+ activo en el sistema.
echo     [*] Intentando instalar Python 3.12 automaticamente via winget...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo     [i] winget no pudo completar la instalacion automatica.

call :test_cmd py -3.12
if defined SYSTEM_PYTHON goto :python_found

call :test_cmd py -3
if defined SYSTEM_PYTHON goto :python_found

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

for /d %%D in ("%ProgramFiles%\Python*") do (
    call :test_exe "%%D\python.exe"
    if defined SYSTEM_PYTHON goto :python_found
)

echo.
echo ======================================================================
echo   [!] ERROR: No se encontro Python 3.11+ en esta computadora.
echo ======================================================================
echo.
echo   Para continuar, realiza estos simples pasos:
echo   1. Descarga Python 3.12 desde https://www.python.org/downloads/
echo   2. Abre el instalador y marca la casilla:
echo      [X] Add python.exe to PATH
echo   3. Vuelve a ejecutar este instalador (instalar_servidor.bat).
echo.
pause
exit /b 1

:python_found
echo     ✔ Python detectado correctamente: %SYSTEM_PYTHON%

:: 3. Crear entorno virtual limpio exclusivo para este equipo
set "VENV_PYTHON=%ROOT_DIR%apps\api\.venv\Scripts\python.exe"

echo.
echo [3/4] Preparando entorno virtual y dependencias...

if not exist "%VENV_PYTHON%" goto :create_venv
"%VENV_PYTHON%" -c "import django, rest_framework, cryptography, corsheaders" >nul 2>&1
if not errorlevel 1 goto :venv_ok

"%VENV_PYTHON%" -m pip --version >nul 2>&1
if not errorlevel 1 goto :install_deps

:create_venv
echo     [*] Generando entorno virtual en apps\api\.venv...
cd /d "%ROOT_DIR%apps\api"
if exist ".venv" rmdir /s /q ".venv" >nul 2>&1
if exist ".venv" (
    echo     [!] Advertencia: No se pudo limpiar por completo la carpeta .venv anterior.
    echo         Cierra cualquier terminal o editor usando Python si el paso siguiente falla.
)
"%SYSTEM_PYTHON%" -m venv .venv
if errorlevel 1 goto :venv_error
if not exist ".venv\Scripts\python.exe" goto :venv_error

:install_deps
cd /d "%ROOT_DIR%apps\api"
echo     [*] Verificando pip dentro del entorno virtual...
"%VENV_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 "%VENV_PYTHON%" -m ensurepip --upgrade

echo     [*] Instalando librerias desde requirements.txt...
"%VENV_PYTHON%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_error
cd /d "%ROOT_DIR%"
goto :venv_ok

:venv_error
echo.
echo     [!] ERROR: No se pudo generar la carpeta .venv usando:
echo         "%SYSTEM_PYTHON%" -m venv .venv
cd /d "%ROOT_DIR%"
pause
exit /b 1

:pip_error
echo.
echo     [!] ERROR: Fallo la descarga o instalacion de librerias desde requirements.txt.
echo         Verifica que esta computadora tenga conexion a internet e intenta de nuevo.
cd /d "%ROOT_DIR%"
pause
exit /b 1

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
if errorlevel 1 (
    echo     [!] ERROR: No se pudieron aplicar las migraciones de base de datos.
    cd /d "%ROOT_DIR%"
    pause
    exit /b 1
)
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
exit /b 0

:: ======================================================================
:: Subrutinas de deteccion segura de Python
:: ======================================================================
:test_cmd
if defined SYSTEM_PYTHON exit /b 0
%* -c "import sys, venv; from typing import Self; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('%* -c "import sys; print(sys.executable)" 2^>nul') do (
    if exist "%%P" set "SYSTEM_PYTHON=%%P"
)
exit /b 0

:test_exe
if defined SYSTEM_PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys, venv; from typing import Self; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "SYSTEM_PYTHON=%~1"
exit /b 0
