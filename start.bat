@echo off
chcp 65001 > nul
title Network and Ping Watchdog
echo ========================================================
echo   Network and Ping Watchdog (Live NOC Monitor)
echo ========================================================
echo [INFO] Запуск сетевого мониторинга на http://localhost:8006 ...
start http://localhost:8006
python -m uvicorn main:app --host 0.0.0.0 --port 8006
pause
