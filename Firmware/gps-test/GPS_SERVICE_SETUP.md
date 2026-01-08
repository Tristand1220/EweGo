# GPS Logger Service Installation Guide

## 📦 Installation Steps

### Step 1: Create Directory Structure

```bash
# Create directories
sudo mkdir -p /opt/gps/data

# Set ownership (adjust if not using root)
sudo chown -R root:root /opt/gps
sudo chmod -R 755 /opt/gps
```

---

### Step 2: Copy Files

```bash
# Copy the GPS logger script
sudo cp gps_logger_timesync.py /opt/gps/gps_logger.py
sudo chmod +x /opt/gps/gps_logger.py

# Copy the systemd service file
sudo cp gps-logger.service /etc/systemd/system/gps-logger.service
```

---

### Step 3: Verify Virtual Environment Path

The service file assumes your virtual environment is at `/home/pi/gps_env`. If it's elsewhere, edit the service file:

```bash
sudo nano /etc/systemd/system/gps-logger.service
```

Update this line:
```
ExecStart=/home/pi/gps_env/bin/python /opt/gps/gps_logger.py
```

---

### Step 4: Install and Start Service

```bash
# Reload systemd to recognize new service
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable gps-logger

# Start the service now
sudo systemctl start gps-logger

# Check status
sudo systemctl status gps-logger
```

---

## 📊 Monitoring the Service

### Check Service Status

```bash
# Quick status check
sudo systemctl status gps-logger

# Is it running?
sudo systemctl is-active gps-logger

# Is it enabled to start on boot?
sudo systemctl is-enabled gps-logger
```

---

### View Live Logs

```bash
# Follow live log output
sudo journalctl -u gps-logger -f

# View last 50 lines
sudo journalctl -u gps-logger -n 50

# View logs from today
sudo journalctl -u gps-logger --since today

# View logs with timestamps
sudo journalctl -u gps-logger -o short-precise
```

---

### Check Log Files

```bash
# List data files
ls -lh /opt/gps/data/

# Check latest log
ls -lt /opt/gps/data/ | head -5

# Monitor disk usage
du -sh /opt/gps/data/
```

---

## 🔧 Service Control Commands

```bash
# Start service
sudo systemctl start gps-logger

# Stop service
sudo systemctl stop gps-logger

# Restart service
sudo systemctl restart gps-logger

# Reload configuration (after editing service file)
sudo systemctl daemon-reload
sudo systemctl restart gps-logger

# Enable auto-start on boot
sudo systemctl enable gps-logger

# Disable auto-start on boot
sudo systemctl disable gps-logger
```

---

## 📝 Editing Configuration

To change GPS settings (serial port, NTRIP, etc.):

```bash
# Edit the main script
sudo nano /opt/gps/gps_logger.py

# Look for the CONFIGURATION section near the bottom
# Edit SERIAL_PORT, BAUDRATE, NTRIP_CONFIG as needed

# Restart service to apply changes
sudo systemctl restart gps-logger

# Check logs to verify
sudo journalctl -u gps-logger -n 20
```

---

## 🐛 Troubleshooting

### Service Won't Start

```bash
# Check detailed status
sudo systemctl status gps-logger

# Check logs for errors
sudo journalctl -u gps-logger -n 50

# Test script manually
source /home/pi/gps_env/bin/activate
python /opt/gps/gps_logger.py
# Ctrl+C to stop

# Common issues:
# 1. Virtual environment path wrong
# 2. Serial port permissions
# 3. Serial port already in use
# 4. Missing Python packages
```

---

### Permission Denied on Serial Port

```bash
# Check current permissions
ls -la /dev/ttyAMA1

# Add user to dialout group (if not running as root)
sudo usermod -a -G dialout root

# Or run as different user (edit service file):
sudo nano /etc/systemd/system/gps-logger.service
# Change: User=pi
# Change: Group=dialout
```

---

### Service Keeps Restarting

```bash
# Check restart count
systemctl show gps-logger -p NRestarts

# View recent failures
sudo journalctl -u gps-logger --since "10 minutes ago"

# Disable auto-restart temporarily for debugging
sudo nano /etc/systemd/system/gps-logger.service
# Comment out: # Restart=on-failure
sudo systemctl daemon-reload
sudo systemctl restart gps-logger
```

---

### Disk Space Issues

```bash
# Check available space
df -h /opt/gps/data

# Find large files
du -sh /opt/gps/data/*

# Set up log rotation (see below)
```

---

## 🗑️ Log Rotation Setup

Prevent disk from filling up with old logs:

```bash
# Create rotation script
sudo nano /opt/gps/rotate_logs.sh
```

Add:
```bash
#!/bin/bash
# Keep last 30 days of GPS logs

LOG_DIR=/opt/gps/data
DAYS_TO_KEEP=30

# Delete files older than 30 days
find $LOG_DIR -name "gps_log_*.ubx" -mtime +$DAYS_TO_KEEP -delete
find $LOG_DIR -name "gps_log_*_timesync.csv" -mtime +$DAYS_TO_KEEP -delete

echo "$(date): Cleaned logs older than $DAYS_TO_KEEP days" >> /var/log/gps-rotation.log
```

```bash
# Make executable
sudo chmod +x /opt/gps/rotate_logs.sh

# Test it
sudo /opt/gps/rotate_logs.sh

# Add to crontab (runs daily at 3 AM)
sudo crontab -e
```

Add line:
```
0 3 * * * /opt/gps/rotate_logs.sh
```

---

## 📊 Service Statistics

```bash
# View service uptime
systemctl show gps-logger -p ActiveEnterTimestamp

# Memory usage
systemctl show gps-logger -p MemoryCurrent

# Check data collection rate
ls -l /opt/gps/data/ | tail -5
```

---

## 🔄 Updating the Logger

```bash
# Stop service
sudo systemctl stop gps-logger

# Backup old version
sudo cp /opt/gps/gps_logger.py /opt/gps/gps_logger.py.backup

# Copy new version
sudo cp gps_logger_timesync.py /opt/gps/gps_logger.py

# Restart service
sudo systemctl start gps-logger

# Verify
sudo systemctl status gps-logger
sudo journalctl -u gps-logger -n 20
```

---

## ✅ Verification Checklist

After installation, verify:

- [ ] Service file installed: `ls -la /etc/systemd/system/gps-logger.service`
- [ ] Script installed: `ls -la /opt/gps/gps_logger.py`
- [ ] Data directory exists: `ls -la /opt/gps/data/`
- [ ] Service enabled: `sudo systemctl is-enabled gps-logger`
- [ ] Service running: `sudo systemctl is-active gps-logger`
- [ ] No errors in logs: `sudo journalctl -u gps-logger -n 20`
- [ ] Data files being created: `ls -lt /opt/gps/data/ | head -3`
- [ ] Time offset near zero: Check journalctl output for "Time offset"

---

## 📁 File Locations Summary

```
/opt/gps/
├── gps_logger.py              # Main GPS logger script
└── data/                      # GPS log files directory
    ├── gps_log_20260107_120000.ubx           # Raw UBX data
    ├── gps_log_20260107_120000_timesync.csv  # Time correlation
    └── ...

/etc/systemd/system/
└── gps-logger.service         # Systemd service file

/home/pi/gps_env/              # Python virtual environment
└── bin/python                 # Python interpreter with packages

Logs:
- systemd journal: sudo journalctl -u gps-logger
- System logs: /var/log/syslog (search for "gps-logger")
```

---

## 🚀 Quick Start Commands

```bash
# Complete installation in one go
sudo mkdir -p /opt/gps/data
sudo cp gps_logger_timesync.py /opt/gps/gps_logger.py
sudo chmod +x /opt/gps/gps_logger.py
sudo cp gps-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gps-logger
sudo systemctl start gps-logger
sudo systemctl status gps-logger

# Monitor it
sudo journalctl -u gps-logger -f
```

---

## 🆘 Emergency Commands

```bash
# Stop immediately
sudo systemctl stop gps-logger

# Disable (prevent auto-start)
sudo systemctl disable gps-logger

# Remove service
sudo systemctl stop gps-logger
sudo systemctl disable gps-logger
sudo rm /etc/systemd/system/gps-logger.service
sudo systemctl daemon-reload

# Clear all logs
sudo journalctl --rotate
sudo journalctl --vacuum-time=1s
```

---

*Service runs continuously in the background, logging GPS data to /opt/gps/data/*
*Use `journalctl` commands to monitor operation*
