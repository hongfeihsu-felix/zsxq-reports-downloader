@echo off
:: Report Server — auto-restart on crash
:loop
echo [%date% %time%] Starting Report Server...
python C:\Users\Administrator\Daytrader\windows_serve.py 8080 C:\Users\Administrator\reports
echo [%date% %time%] Server stopped, restarting in 5s...
timeout /t 5 /nobreak >nul
goto loop
