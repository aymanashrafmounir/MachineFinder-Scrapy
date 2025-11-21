@echo off
echo ========================================
echo MachineFinder Scraper - Quick Setup
echo ========================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browsers...
playwright install chromium
if %errorlevel% neq 0 (
    echo ERROR: Failed to install Playwright browsers
    pause
    exit /b 1
)

echo.
echo [3/3] Setup complete!
echo.
echo ========================================
echo NEXT STEPS:
echo ========================================
echo 1. Edit config.json and add your:
echo    - Telegram bot token
echo    - Telegram chat ID
echo    - MachineFinder search URLs
echo.
echo 2. Run the scraper:
echo    - One-time: python main.py --once
echo    - Continuous: python main.py
echo ========================================
echo.
pause
