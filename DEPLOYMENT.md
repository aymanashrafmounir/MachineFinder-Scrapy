# MachineFinder Scraper - Deployment Guide for Low-Resource Servers

## System Requirements

- **OS**: Debian/Ubuntu Linux
- **CPU**: 2 cores (minimum)
- **RAM**: 1GB (minimum)
- **Python**: 3.8 or higher
- **Disk Space**: ~500MB (for Chromium browser)

## Installation on Debian Server

### 1. Upload Project Files

```bash
# Using SCP from local machine
scp -r "asmo MachineFinder Scrapy" user@your-server:/home/user/

# Or using Git
git clone <your-repo-url>
cd "asmo MachineFinder Scrapy"
```

### 2. Run Setup Script

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

### 3. Configure

Edit `config.json`:
```bash
nano config.json
```

Add your Telegram credentials and search URLs.

## Running the Scraper

### One-Time Test Run

```bash
python3 main.py --once
```

### Continuous Monitoring (Recommended Methods)

#### Option 1: Using Screen (Easiest)

```bash
# Start a screen session
screen -S machinefinder

# Run the scraper
python3 main.py

# Detach from screen: Press Ctrl+A then D
# Reattach later: screen -r machinefinder
```

#### Option 2: Using nohup

```bash
nohup python3 main.py > scraper.log 2>&1 &

# Check if running
ps aux | grep main.py

# View logs
tail -f scraper.log
```

#### Option 3: Using systemd Service (Production)

Create service file:
```bash
sudo nano /etc/systemd/system/machinefinder.service
```

Add this content:
```ini
[Unit]
Description=MachineFinder Scraper
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/your-username/asmo MachineFinder Scrapy
ExecStart=/usr/bin/python3 /home/your-username/asmo MachineFinder Scrapy/main.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable machinefinder
sudo systemctl start machinefinder
sudo systemctl status machinefinder

# View logs
sudo journalctl -u machinefinder -f
```

## Memory Optimization

The scraper is optimized for low-resource environments:

- ✓ Single-process browser mode
- ✓ Disabled GPU acceleration
- ✓ JavaScript heap limited to 256MB
- ✓ No background tasks
- ✓ Browser closes after each scrape

**Expected Memory Usage**: ~300-500MB during scraping, ~50MB idle

## Monitoring

### Check Resource Usage

```bash
# Monitor memory while scraping
watch -n 1 free -h

# Check CPU usage
top
```

### Check Database

```bash
python3 check_db.py
```

### Check Logs

```bash
# If using nohup
tail -f scraper.log

# If using systemd
sudo journalctl -u machinefinder -f
```

## Troubleshooting

### Browser Won't Start

```bash
# Install missing dependencies
sudo playwright install-deps chromium
```

### Out of Memory

- Reduce `scrape_interval_minutes` to give system time to recover
- Scrape fewer URLs simultaneously
- Ensure TEST_MODE is enabled initially

### Permission Errors

```bash
# Fix file permissions
chmod +x setup_linux.sh
chmod 644 config.json
```

## Performance Tips

1. **Start with TEST_MODE**: Set `TEST_MODE = True` in main.py to send only 1 notification initially
2. **Monitor Resources**: Use `htop` or `free -h` to watch memory usage
3. **Increase Swap**: If experiencing memory issues, add swap space:
   ```bash
   sudo fallocate -l 1G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

## Stopping the Scraper

### If using screen:
```bash
screen -r machinefinder
# Press Ctrl+C
```

### If using nohup:
```bash
pkill -f main.py
```

### If using systemd:
```bash
sudo systemctl stop machinefinder
```

## Files Generated

- `machinefinder.db` - SQLite database with tracked machines
- `scraper.log` - Log file (if using nohup)
- `error_screenshot.png` - Debug screenshot if page fails to load
