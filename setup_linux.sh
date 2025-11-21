#!/bin/bash

# MachineFinder Scraper - Linux/Debian Setup Script
# For servers with limited resources (2 CPU, 1GB RAM)

echo "========================================="
echo "MachineFinder Scraper - Setup"
echo "========================================="
echo ""

# Check Python version
echo "[1/5] Checking Python..."
python3 --version || { echo "Python 3 not found. Please install Python 3.8+"; exit 1; }

# Install Python dependencies
echo ""
echo "[2/5] Installing Python dependencies..."
pip3 install -r requirements.txt || { echo "Failed to install dependencies"; exit 1; }

# Install Playwright browsers (Chromium only to save space)
echo ""
echo "[3/5] Installing Playwright Chromium browser..."
playwright install chromium || { echo "Failed to install Playwright"; exit 1; }

# Install system dependencies for Playwright (Debian/Ubuntu)
echo ""
echo "[4/5] Installing system dependencies..."
sudo playwright install-deps chromium || echo "Note: Some system dependencies may need manual installation"

# Make scripts executable
chmod +x setup_linux.sh 2>/dev/null

echo ""
echo "[5/5] Setup complete!"
echo ""
echo "========================================="
echo "NEXT STEPS:"
echo "========================================="
echo "1. Edit config.json and add your:"
echo "   - Telegram bot token"
echo "   - Telegram chat ID"
echo "   - MachineFinder search URLs"
echo ""
echo "2. Run the scraper:"
echo "   - One-time: python3 main.py --once"
echo "   - Continuous: python3 main.py"
echo ""
echo "3. For persistent monitoring:"
echo "   - Using screen: screen -S machinefinder"
echo "                   python3 main.py"
echo "                   (Ctrl+A then D to detach)"
echo "   - Using nohup: nohup python3 main.py > scraper.log 2>&1 &"
echo "========================================="
