@echo off
chcp 65001 >nul
title Detener Servidor Valhalla

echo ======================================================================
echo    DETENIENDO SERVIDOR CENTRAL VALHALLA
echo ======================================================================
echo.

echo [1/2] Cerrando procesos en puerto 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a >nul 2>&1
)

echo [2/2] Cerrando ventanas de servicio Valhalla...
taskkill /F /FI "WINDOWTITLE eq Valhalla_Servidor_Central*" >nul 2>&1

echo.
echo ======================================================================
echo    ✔ SERVIDOR VALHALLA DETENIDO CORRECTAMENTE.
echo ======================================================================
echo.
pause