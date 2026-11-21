#!/usr/bin/env bash
# Standalone native installer for Raspberry Pi 5 (Systemd Service)
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "=== Installing New Media Analyser on Raspberry Pi ==="

# Check Python3
if ! command -v python3 &>/dev/null; then
    echo "Installing python3, venv, and git..."
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git curl
fi

# Create virtual environment if missing
if [ ! -d ".venv_linux" ]; then
    echo "Creating virtual environment (.venv_linux)..."
    python3 -m venv .venv_linux
fi

source .venv_linux/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright Chromium with Linux ARM64 system dependencies
echo "Installing Chromium for Playwright..."
playwright install --with-deps chromium

mkdir -p data/profiles output

# Create systemd service
SERVICE_FILE="/etc/systemd/system/new-media-analyser.service"
echo "Registering systemd service at $SERVICE_FILE..."

sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=New Media Analyser Web Application
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv_linux/bin/python app.py
Restart=always
RestartSec=5
Environment=PORT=8080
Environment=HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable new-media-analyser
sudo systemctl restart new-media-analyser

echo "=== Deployment Complete! ==="
echo "Status: sudo systemctl status new-media-analyser"
echo "Open in browser: http://$(hostname -I | awk '{print \$1}'):8080"
