"""Systemd service manager for SMBHost.

Handles installation, uninstallation, and status queries
for the smbhost systemd service unit.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SERVICE_NAME = "smbhost"
SERVICE_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
SOURCE_SERVICE = Path(__file__).parent.parent / f"{SERVICE_NAME}.service"


# ── Systemd Manager ──────────────────────────────────────────────────────────


class SystemdManager:
    """Manages the smbhost systemd service."""

    @staticmethod
    def is_installed() -> bool:
        """Check if the service unit file is installed."""
        return SERVICE_FILE.exists()

    @staticmethod
    def install_service(service_source: Path | None = None) -> bool:
        """Install the systemd service unit file.

        Args:
            service_source: Path to the .service file. If None, uses the
                           bundled smbhost.service alongside this package.

        Returns True on success.
        """
        src = service_source or SOURCE_SERVICE
        if not src.exists():
            logger.error("Service unit file not found: %s", src)
            return False

        try:
            shutil.copy2(src, SERVICE_FILE)
            SERVICE_FILE.chmod(0o644)
            logger.info("Installed systemd service: %s", SERVICE_FILE)
        except PermissionError:
            logger.error("Permission denied — run as root to install service")
            return False
        except OSError as e:
            logger.error("Failed to copy service file: %s", e)
            return False

        return SystemdManager._run(["systemctl", "daemon-reload"])

    @staticmethod
    def uninstall_service() -> bool:
        """Remove the systemd service unit file and disable it first."""
        try:
            SystemdManager._run(["systemctl", "stop", f"{SERVICE_NAME}.service"])
            SystemdManager._run(["systemctl", "disable", f"{SERVICE_NAME}.service"])
            if SERVICE_FILE.exists():
                SERVICE_FILE.unlink()
                logger.info("Removed systemd service: %s", SERVICE_FILE)
            SystemdManager._run(["systemctl", "daemon-reload"])
            return True
        except PermissionError:
            logger.error("Permission denied — run as root to uninstall service")
            return False
        except OSError as e:
            logger.error("Failed to remove service file: %s", e)
            return False

    @staticmethod
    def is_enabled() -> bool:
        """Check if the service is enabled to start at boot."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{SERVICE_NAME}.service"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() == "enabled"
        except (subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def enable_service() -> bool:
        """Enable the service to start at boot."""
        return SystemdManager._run(["systemctl", "enable", f"{SERVICE_NAME}.service"])

    @staticmethod
    def disable_service() -> bool:
        """Disable the service from starting at boot."""
        return SystemdManager._run(["systemctl", "disable", f"{SERVICE_NAME}.service"])

    @staticmethod
    def start_service() -> bool:
        """Start the service."""
        return SystemdManager._run(["systemctl", "start", f"{SERVICE_NAME}.service"])

    @staticmethod
    def stop_service() -> bool:
        """Stop the service."""
        return SystemdManager._run(["systemctl", "stop", f"{SERVICE_NAME}.service"])

    @staticmethod
    def restart_service() -> bool:
        """Restart the service."""
        return SystemdManager._run(["systemctl", "restart", f"{SERVICE_NAME}.service"])

    @staticmethod
    def get_status() -> dict:
        """Get detailed service status."""
        status: dict = {
            "installed": SystemdManager.is_installed(),
            "enabled": SystemdManager.is_enabled(),
            "active": False,
            "uptime_seconds": 0,
        }

        try:
            # Check if active
            result = subprocess.run(
                ["systemctl", "is-active", f"{SERVICE_NAME}.service"],
                capture_output=True, text=True, timeout=5,
            )
            status["active"] = (result.stdout.strip() == "active")

            # Get service properties
            result = subprocess.run(
                [
                    "systemctl", "show", f"{SERVICE_NAME}.service",
                    "--property=ActiveEnterTimestampMonotonic",
                    "--property=MainPID",
                    "--property=LoadState",
                    "--property=SubState",
                ],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.lower()
                    if key == "loadstate":
                        status["load_state"] = val
                    elif key == "substate":
                        status["sub_state"] = val
                    elif key == "mainpid":
                        status["pid"] = int(val) if val.isdigit() else 0

        except (subprocess.TimeoutExpired, OSError):
            pass

        return status

    @staticmethod
    def get_logs(lines: int = 50) -> str:
        """Get recent service logs from journalctl."""
        try:
            result = subprocess.run(
                [
                    "journalctl", "-u", f"{SERVICE_NAME}.service",
                    f"-n{lines}", "--no-pager", "-o", "short-iso",
                ],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"Error retrieving logs: {e}"

    @staticmethod
    def _run(cmd: list[str]) -> bool:
        """Run a command and return True if it succeeded."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    logger.warning("Command [%s]: %s", " ".join(cmd), stderr)
                return False
            return True
        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return False
        except subprocess.TimeoutExpired:
            logger.error("Command timed out: %s", " ".join(cmd))
            return False
