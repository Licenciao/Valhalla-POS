@echo off
chcp 65001 >nul
title Configurar Autoarranque — Servidor Valhalla

echo ======================================================================
echo    CONFIGURAR INICIO AUTOM TICO AL ENCENDER LA PC (TASK SCHEDULER)
echo ======================================================================
echo.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Solicitando permisos de Administrador para registrar la Tarea Programada...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

set "BATCH_PATH=%~dp0iniciar_servidor.bat"

echo Creando tarea programada 'Valhalla_Servidor_Autoarranque'...
schtasks /create /tn "Valhalla_Servidor_Autoarranque" /tr "\"%BATCH_PATH%\"" /sc onlogon /rl highest /f >nul 2>&1

if %errorLevel% equ 0 (
    echo.
    echo ✔ Tarea Programada registrada con  xito.
    echo   A partir de ahora, cada vez que enciendas la PC e inicies sesi n,
    echo   el servidor Valhalla se iniciar  autom ticamente en segundo plano.
) else (
    echo.
    echo [!] Hubo un error al registrar la tarea programada.
)

echo.
pause
