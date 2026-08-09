# SMBHost

**Auto-detect attached drives and expose them as SMB shares — with a web UI for configuration.**

SMBHost runs as a systemd service on Linux. On startup, it detects all attached block devices (USB drives, external HDDs, etc.), and exposes each one as a configurable Samba (SMB) share on your local network. A built-in web UI lets you manage shares, set authentication, and monitor the system from any browser.

---

## Features

- 🔌 **Auto-detection** — Detects drives as they're plugged in (via udev). Removes shares when drives are unplugged.
- 🌐 **Web UI** — Clean, dark-themed dashboard accessible from any device on your network.
- 🔐 **Flexible Auth** — Per-share authentication: guest access, guest read-only, or username/password.
- ⚡ **Real-time Updates** — WebSocket push for instant UI updates when drives change.
- 🔄 **Auto-start** — Installs as a systemd service, starts on boot, survives reboots.
- 📋 **REST API** — Full OpenAPI-documented API for automation and integration.
- 🐧 **Distro Support** — Debian/Ubuntu, Fedora/RHEL, Arch Linux, openSUSE.

---

## Quick Start

### Requirements

- Linux (Debian/Ubuntu, Fedora, Arch, openSUSE)
- Python 3.9+
- Samba (`samba` package)
- Root/sudo access

### Installation

```bash
# Clone and install
git clone https://github.com/example/smbhost.git
cd smbhost
sudo ./install.sh
```

The installer will:
1. Detect your distribution and install system dependencies (python3, samba, udev)
2. Create a Python virtual environment at `/opt/smbhost/venv`
3. Install smbhost and its Python dependencies
4. Install and enable the systemd service
5. Start the service

After installation, open the web UI:

```
http://<your-server-ip>:8080
```

### Manual Install

```bash
# Install system dependencies
sudo apt install python3 python3-venv samba udev   # Debian/Ubuntu
# OR
sudo dnf install python3 python3-pip samba systemd-udev   # Fedora

# Set up virtual environment
sudo mkdir -p /opt/smbhost
sudo python3 -m venv /opt/smbhost/venv
sudo /opt/smbhost/venv/bin/pip install .

# Install systemd service
sudo cp smbhost.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smbhost
```

### Uninstallation

```bash
sudo /opt/smbhost/venv/bin/python -m smbhost --uninstall
sudo rm -rf /opt/smbhost /etc/smbhost /var/log/smbhost
```

---

## Usage

### Web UI

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Overview: drive count, active shares, uptime |
| **Drives** | List detected drives, create/edit/remove shares |
| **Settings** | Global Samba settings (workgroup, NetBIOS name), web UI bind/port |
| **System** | Service status, Samba restart/reload, log viewer |

### CLI

```bash
# Start the server manually
smbhost --bind 0.0.0.0 --port 8080 --debug

# Install as systemd service
sudo smbhost --install

# Generate smb.conf to stdout (dry-run)
smbhost --generate-smb-conf

# Show version
smbhost --version
```

### Accessing SMB Shares

From **macOS**: `smb://<server-ip>/<share-name>` in Finder → Go → Connect to Server.

From **Windows**: `\\<server-ip>\<share-name>` in File Explorer address bar.

From **Linux**: `smb://<server-ip>/<share-name>` in your file manager, or:
```bash
smbclient //<server-ip>/<share-name> -U <username>
```

---

## Configuration

Configuration is stored at `/etc/smbhost/config.yaml`:

```yaml
global:
  workgroup: WORKGROUP
  netbios_name: SMBHOST
  server_string: SMBHost File Server
  web_bind: 127.0.0.1
  web_port: 8080
  allow_remote_ui: false

shares:
  <drive-uuid>:
    drive_uuid: "..."
    drive_label: "MyDrive"
    share_name: "MyDrive"
    mount_point: "/media/user/MyDrive"
    auth_mode: guest      # guest | guest_ro | user_pass
    username: ""
    password: ""
    read_only: false
    browseable: true
    enabled: true
```

You can edit this file directly and Samba will be reconfigured on the next reload.

---

## API Documentation

Once the server is running, visit `/docs` for the interactive OpenAPI (Swagger) documentation.

Key endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/drives` | List all detected drives |
| `WS` | `/api/drives/events` | Real-time drive add/remove events |
| `GET` | `/api/shares` | List all configured shares |
| `POST` | `/api/shares` | Create a new share |
| `PUT` | `/api/shares/{uuid}` | Update a share |
| `DELETE` | `/api/shares/{uuid}` | Remove a share |
| `GET` | `/api/system/status` | System and Samba status |
| `GET` | `/api/system/logs` | Recent service logs |
| `POST` | `/api/system/samba/reload` | Reload Samba config |
| `POST` | `/api/system/samba/restart` | Restart Samba services |

---

## Architecture

```
smbhost/
├── __init__.py          # Package init, version
├── __main__.py          # CLI entry point
├── app.py               # FastAPI application factory
├── config.py            # YAML config manager (Pydantic models)
├── drive_detector.py    # udev monitor + lsblk fallback
├── smb_manager.py       # Samba config & user management
├── systemd_manager.py   # systemd service install/control
├── api/
│   ├── drives.py        # Drive REST + WebSocket endpoints
│   ├── shares.py        # Share CRUD endpoints
│   └── system.py        # System status/control endpoints
├── templates/
│   └── index.html       # SPA web UI (Alpine.js)
└── static/
    ├── css/style.css
    └── js/app.js
```

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check smbhost/ tests/

# Run locally (no root needed for UI dev)
python -m smbhost --debug --port 8080
```

---

## Security Notes

- **Bind to localhost by default**: The web UI binds to `127.0.0.1`. To allow remote access, set bind to `0.0.0.0` in settings — only do this on trusted networks.
- **Root execution**: The service runs as root because it needs to write `/etc/samba/smb.conf` and manage system services. The systemd unit uses several hardening options (`ProtectSystem=strict`, `NoNewPrivileges=yes`).
- **SMB passwords**: Stored in Samba's own password database (`smbpasswd`), not in the config file. The config file stores only the username.

---

## License

MIT
