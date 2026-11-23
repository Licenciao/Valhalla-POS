@echo off
chcp 65001 >nul
title Detener Servidor Valhalla

echo ======================================================================
echo    DETENIENDO SERVIDOR CENTRAL VALHALLA
echo ======================================================================
echo.

where docker >nul 2>&1
if %errorLevel% equ 0 (
    echo [1/2] Deteniendo contenedores Docker...
    docker compose down >nul 2>&1
)

echo [2/2] Cerrando procesos en puerto 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo ======================================================================
echo    ✔ SERVIDOR VALHALLA DETENIDO CORRECTAMENTE.
echo ======================================================================
echo.
pause
