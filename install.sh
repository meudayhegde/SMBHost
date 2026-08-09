#!/usr/bin/env bash
set -euo pipefail

# SMBHost Installer — distro-aware installation script
# Supports: Debian/Ubuntu, Fedora/RHEL, Arch Linux, openSUSE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/smbhost"
VENV_DIR="$APP_DIR/venv"
CONFIG_DIR="/etc/smbhost"
LOG_DIR="/var/log/smbhost"
SERVICE_FILE="/etc/systemd/system/smbhost.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }
info() { echo -e "${CYAN}[NOTE]${NC}  $*"; }

# ── Privilege check ─────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root (sudo)."
    exit 1
fi

# ── Distro detection ────────────────────────────────────────────
detect_distro() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        echo "${ID:-unknown}"
    else
        echo "unknown"
    fi
}

DISTRO=$(detect_distro)
log "Detected distribution: $DISTRO"

# ── Install system dependencies ─────────────────────────────────
install_deps() {
    case "$DISTRO" in
        debian|ubuntu|raspbian|linuxmint|pop)
            log "Installing dependencies via apt..."
            apt-get update -qq
            apt-get install -y -qq \
                python3 python3-venv python3-pip \
                samba samba-common-bin \
                udev \
                python3-dev gcc
            ;;
        fedora|rhel|centos|rocky|almalinux)
            log "Installing dependencies via dnf..."
            dnf install -y \
                python3 python3-pip python3-devel \
                samba samba-common-tools \
                systemd-udev \
                gcc
            ;;
        arch|manjaro|endeavouros)
            log "Installing dependencies via pacman..."
            pacman -Sy --noconfirm \
                python python-pip python-virtualenv \
                samba smbclient \
                udev \
                gcc
            ;;
        opensuse*|suse)
            log "Installing dependencies via zypper..."
            zypper install -y \
                python3 python3-pip python3-devel \
                samba samba-client \
                systemd-udev \
                gcc
            ;;
        *)
            warn "Unknown distribution: $DISTRO"
            warn "Attempting to proceed — you may need to install dependencies manually:"
            warn "  - python3, python3-venv, python3-pip"
            warn "  - samba, samba-common-bin"
            warn "  - udev"
            ;;
    esac
}

# ── Create directories ──────────────────────────────────────────
setup_dirs() {
    log "Creating application directories..."
    mkdir -p "$APP_DIR"
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$LOG_DIR"
    chmod 750 "$CONFIG_DIR"
    chmod 755 "$LOG_DIR"
}

# ── Set up virtual environment ──────────────────────────────────
setup_venv() {
    log "Setting up Python virtual environment..."
    if [[ -d "$VENV_DIR" ]]; then
        warn "Virtual environment exists — recreating..."
        rm -rf "$VENV_DIR"
    fi
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install "$SCRIPT_DIR" -q
}

# ── Install systemd service ─────────────────────────────────────
install_service() {
    log "Installing systemd service..."
    cp "$SCRIPT_DIR/smbhost.service" "$SERVICE_FILE"
    chmod 644 "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl enable smbhost.service
    systemctl restart smbhost.service
}

# ── Verify installation ─────────────────────────────────────────
verify() {
    log "Verifying installation..."

    if systemctl is-active --quiet smbhost.service; then
        log "✓ smbhost service is running"
    else
        warn "! smbhost service is not running — check: journalctl -u smbhost -n 50"
    fi

    # Find local IP for user convenience
    LOCAL_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "localhost")

    echo ""
    info "============================================"
    info "  SMBHost installation complete!"
    info "============================================"
    info "  Web UI:  http://${LOCAL_IP}:8080"
    info "  Config:  $CONFIG_DIR/config.yaml"
    info "  Logs:    journalctl -u smbhost -f"
    info "============================================"
    echo ""
}

# ── Main ────────────────────────────────────────────────────────
main() {
    echo ""
    log "═══════════════════════════════════════════"
    log "  SMBHost Installer"
    log "═══════════════════════════════════════════"
    echo ""

    install_deps
    setup_dirs
    setup_venv
    install_service
    verify
}

main "$@"
