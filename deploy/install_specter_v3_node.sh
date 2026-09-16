#!/usr/bin/env bash
# ==============================================================================
# SPECTER CORE v3.0 // AUTONOMOUS VPS NODE INSTALLER
# Author / Architect: Guilherme Peralta Novaes
# License: MIT
# Supports: Ubuntu, Debian, Alpine, Fedora, Rocky Linux
# Usage: curl -sSL https://raw.githubusercontent.com/.../install.sh | bash
# ==============================================================================

set -euo pipefail

echo "================================================================="
echo "   SPECTER CORE v3.0 // SOVEREIGN MESH NODE INSTALLER           "
echo "   Architect: Guilherme Peralta Novaes                           "
echo "================================================================="

INSTALL_DIR="/opt/specter-core"
SERVICE_NAME="specter-node"

# 1. Detect OS and install Python 3 + Pip + SQLite
echo "[1/5] Detecting package manager and installing dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv sqlite3 curl
elif command -v apk >/dev/null 2>&1; then
    apk update
    apk add python3 py3-pip sqlite curl bash
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip sqlite curl
fi

# 2. Setup isolated workspace
echo "[2/5] Setting up isolated directory at ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}/Core/storage"
mkdir -p "${INSTALL_DIR}/Core/logs"
mkdir -p "${INSTALL_DIR}/Core/exports"

# 3. Create virtual environment
echo "[3/5] Initializing Python virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
source "${INSTALL_DIR}/venv/bin/activate"
pip install --upgrade pip --quiet
pip install aiohttp --quiet

# 4. Create systemd service for 24/7 resilience
echo "[4/5] Configuring systemd daemon for 24/7 autonomous supervision..."
if command -v systemctl >/dev/null 2>&1; then
    cat <<EOF > /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=Specter Core v3 Autonomous Node
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/Core/unified_inference_gateway.py
Restart=always
RestartSec=5s
Environment=SPECTER_PORT=8080
Environment=SPECTER_HOST=0.0.0.0
Environment=SPECTER_STORAGE=${INSTALL_DIR}/Core/storage

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable ${SERVICE_NAME}
    echo "[+] Systemd service ${SERVICE_NAME} installed and enabled."
fi

# 5. Summary
echo "================================================================="
echo "[+] SPECTER CORE v3.0 NODE INSTALLED SUCCESSFULLY!"
echo "    Directory: ${INSTALL_DIR}"
echo "    Service:   systemctl start ${SERVICE_NAME}"
echo "    Endpoints: http://127.0.0.1:8080/health"
echo "    Author:    Guilherme Peralta Novaes"
echo "================================================================="
