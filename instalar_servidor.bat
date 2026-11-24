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

:: 2. Configurar Firewall de Windows para todas las redes (Publica, Privada, Dominio)
echo [1/4] Configurando Firewall de Windows Defender para permitir conexiones locales...
netsh advfirewall firewall delete rule name="Valhalla Servidor (Puerto 8000)" >nul 2>&1
netsh advfirewall firewall add rule name="Valhalla Servidor (Puerto 8000)" dir=in action=allow protocol=TCP localport=8000 profile=any >nul 2>&1
if %errorLevel% equ 0 (
    echo     ✔ Regla de Firewall creada exitosamente en puerto TCP 8000 (Todas las redes).
) else (
    echo     [!] Advertencia: No se pudo modificar el firewall automaticamente.
)

:: 3. Determinar directorio base
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo.
echo [2/4] Verificando motor de ejecucion en esta computadora...

:: Buscar Python en el sistema
set "SYSTEM_PYTHON="
for %%P in (python.exe py.exe python3.exe) do (
    if not defined SYSTEM_PYTHON (
        where %%P >nul 2>&1
        if %errorLevel% equ 0 set "SYSTEM_PYTHON=%%P"
    )
)

if not defined SYSTEM_PYTHON (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "SYSTEM_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "SYSTEM_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)

if not defined SYSTEM_PYTHON (
    echo.
    echo     [!] Python no esta instalado en este equipo.
    echo     [*] Descargando e instalando Python 3.12 automaticamente...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "SYSTEM_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

:: 4. Verificar o crear el entorno virtual (.venv) exclusivo para esta PC
set "VENV_PYTHON=%ROOT_DIR%apps\api\.venv\Scripts\python.exe"
set "NEED_VENV=0"

if not exist "%VENV_PYTHON%" (
    set "NEED_VENV=1"
) else (
    "%VENV_PYTHON%" -c "import sys" >nul 2>&1
    if %errorLevel% neq 0 set "NEED_VENV=1"
)

if "%NEED_VENV%"=="1" (
    echo.
    echo [3/4] Creando entorno virtual limpio para este equipo...
    if defined SYSTEM_PYTHON (
        cd /d "%ROOT_DIR%apps\api"
        if exist ".venv" rmdir /s /q ".venv" >nul 2>&1
        "%SYSTEM_PYTHON%" -m venv .venv
        echo     ✔ Instalando librerias de Valhalla...
        "%VENV_PYTHON%" -m pip install --upgrade pip >nul 2>&1
        "%VENV_PYTHON%" -m pip install -r requirements.txt
    ) else (
        echo.
        echo     [!] ERROR CRITICO: No se encontro Python en esta computadora.
        echo         Por favor descarga e instala Python desde: https://www.python.org/
        echo         (Asegurate de marcar la casilla "Add python.exe to PATH").
        echo.
        pause
        exit /b 1
    )
) else (
    echo.
    echo [3/4] Entorno virtual verificado y listo.
)

:: 5. Base de datos y migraciones
set "DJANGO_DEBUG=1"
set "DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b"
set "DJANGO_SQLITE_PATH=db.sqlite3"
set "DJANGO_ALLOWED_HOSTS=*"

cd /d "%ROOT_DIR%apps\api"
echo.
echo     ✔ Aplicando migraciones de base de datos...
"%VENV_PYTHON%" manage.py migrate --noinput

cd /d "%ROOT_DIR%"

:: 6. Obtener IP local de la maquina
echo.
echo [4/4] Obteniendo direcciones de red disponibles...
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