@echo off
echo =========================================
echo         StockAI Local Startup
echo =========================================
echo.

echo Checking and installing dependencies...
pip install -r requirements.txt -q
echo Dependencies are ready.
echo.

:: Open browser after a 3 second delay to let the server start
start /b cmd /c "timeout /t 3 /nobreak > nul & start http://127.0.0.1:8080"

echo [1/2] Web server is starting...
echo [2/2] Your browser will open automatically in 3 seconds.
echo.
echo Please keep this window open to keep the server running.
echo Close this window or press Ctrl+C to stop the server.
echo.

python web_server.py
