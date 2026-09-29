@echo off
rem ================================================================
rem  AUD-VDJ-MON-01 · Lanzador de cabina (doble clic)
rem  Usa .venv si existe; si no, el Python del sistema.
rem  Pasa --panic-key "CTRL+F12" como argumento para la Fase 2:
rem     run_monitor.bat --panic-key "CTRL+F12"
rem ================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [AUD-VDJ-MON-01] Endpoints WASAPI Loopback detectados:
"%PY%" aud_vdj_mon_01.py --list
if errorlevel 1 (
    echo.
    echo [ERROR] No se encontraron endpoints loopback.
    echo         Verifica que la tarjeta USB este conectada y que
    echo         Virtual DJ no la este usando en modo ASIO exclusivo.
    pause
    exit /b 1
)

echo.
echo [AUD-VDJ-MON-01] Iniciando monitor v1.2.0 ...
echo.
"%PY%" aud_vdj_mon_01.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] El monitor termino con codigo %errorlevel%.
    pause
)
endlocal
