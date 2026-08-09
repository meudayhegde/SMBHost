"""SMB Manager for SMBHost.

Programmatic management of Samba configuration and users.
Writes /etc/samba/smb.conf and controls smbd/nmbd via systemctl.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from smbhost.config import AuthMode, ConfigManager, ShareConfig

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SAMBA_CONF = Path("/etc/samba/smb.conf")
SAMBA_CONF_BAK = Path("/etc/samba/smb.conf.smbhost.bak")
SAMBA_SERVICE = "smbd"
NMBD_SERVICE = "nmbd"


# ── SMB Manager ──────────────────────────────────────────────────────────────


class SMBManager:
    """Manages Samba configuration and services."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self._cfg = config_manager

    # ── Config File Management ────────────────────────────────────────────

    def write_config(self) -> bool:
        """Write the generated smb.conf to disk. Backs up existing config first.

        Returns True on success, False on failure.
        """
        try:
            generated = self._cfg.generate_smb_conf()

            # Backup existing config if it wasn't written by us
            if SAMBA_CONF.exists():
                existing = SAMBA_CONF.read_text(encoding="utf-8")
                if "SMBHost managed" not in existing:
                    shutil.copy2(SAMBA_CONF, SAMBA_CONF_BAK)
                    logger.info("Backed up original smb.conf to %s", SAMBA_CONF_BAK)

            SAMBA_CONF.write_text(generated, encoding="utf-8")
            os.chmod(SAMBA_CONF, 0o644)
            logger.info("Written smb.conf (%d bytes)", len(generated))
            return True

        except PermissionError:
            logger.error("Permission denied writing %s — run as root", SAMBA_CONF)
            return False
        except OSError as e:
            logger.error("Failed to write smb.conf: %s", e)
            return False

    def restore_backup(self) -> bool:
        """Restore the original smb.conf from backup."""
        if SAMBA_CONF_BAK.exists():
            shutil.copy2(SAMBA_CONF_BAK, SAMBA_CONF)
            logger.info("Restored smb.conf from backup")
            return True
        logger.warning("No backup found at %s", SAMBA_CONF_BAK)
        return False

    # ── Service Control ───────────────────────────────────────────────────

    def reload_service(self) -> bool:
        """Reload Samba configuration without interrupting active connections.

        Uses `smbcontrol smbd reload-config` or `systemctl reload smbd`.
        """
        # First, try the gentler smbcontrol approach
        success = self._run(["smbcontrol", "smbd", "reload-config"])
        if not success:
            # Fall back to systemctl reload
            success = self._run(["systemctl", "reload", SAMBA_SERVICE])
        if success:
            logger.info("Samba configuration reloaded")
        return success

    def restart_service(self) -> bool:
        """Full restart of Samba services (smbd + nmbd)."""
        success = True
        for svc in (SAMBA_SERVICE, NMBD_SERVICE):
            if not self._run(["systemctl", "restart", svc]):
                success = False
        if success:
            logger.info("Samba services restarted")
        return success

    def get_status(self) -> dict:
        """Get the status of Samba services."""
        status = {"smbd": "unknown", "nmbd": "unknown"}
        for svc in (SAMBA_SERVICE, NMBD_SERVICE):
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", svc],
                    capture_output=True, text=True, timeout=5,
                )
                status[svc] = result.stdout.strip()
            except (subprocess.TimeoutExpired, OSError):
                status[svc] = "error"
        return status

    def is_running(self) -> bool:
        """Check if smbd is currently running."""
        status = self.get_status()
        return status.get("smbd") == "active"

    # ── Share Operations ──────────────────────────────────────────────────

    def add_share(self, share: ShareConfig) -> bool:
        """Add a share: configure smb.conf, create SMB user if needed, reload.

        Returns True if everything succeeded.
        """
        # If user_pass mode, ensure the SMB user exists
        if share.auth_mode == AuthMode.USER_PASS and share.username:
            if not self._ensure_smb_user(share.username, share.password):
                return False

        return self._reconfigure()

    def remove_share(self, share: ShareConfig) -> bool:
        """Remove a share: optionally clean up SMB user, reconfigure, reload."""
        return self._reconfigure()

    def update_share(self, old_share: ShareConfig, new_share: ShareConfig) -> bool:
        """Update an existing share's configuration."""
        # If auth changed to user_pass, create user
        if new_share.auth_mode == AuthMode.USER_PASS and new_share.username:
            if not self._ensure_smb_user(new_share.username, new_share.password):
                return False
        return self._reconfigure()

    # ── User Management ───────────────────────────────────────────────────

    def _ensure_smb_user(self, username: str, password: str) -> bool:
        """Ensure an SMB user exists with the given password.

        Creates the system user if needed, then sets the SMB password.
        """
        if not username or not password:
            logger.error("Username and password required for USER_PASS auth mode")
            return False

        # Check if system user exists
        try:
            import pwd
            pwd.getpwnam(username)
        except KeyError:
            # Create system user (no login shell)
            logger.info("Creating system user: %s", username)
            if not self._run([
                "useradd", "--system", "--no-create-home",
                "--shell", "/usr/sbin/nologin", username,
            ]):
                logger.error("Failed to create system user: %s", username)
                return False

        # Set/update SMB password
        success = self._run_smbpasswd(username, password)
        if success:
            logger.info("SMB user %s configured", username)
        return success

    def _run_smbpasswd(self, username: str, password: str) -> bool:
        """Set SMB password for a user via smbpasswd."""
        try:
            proc = subprocess.run(
                ["smbpasswd", "-a", "-s", username],
                input=f"{password}\n{password}\n",
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                # If user already exists, use -s (silent set) instead
                proc2 = subprocess.run(
                    ["smbpasswd", "-s", "-a", username],
                    input=f"{password}\n{password}\n",
                    capture_output=True, text=True, timeout=10,
                )
                if proc2.returncode != 0:
                    logger.error("smbpasswd failed: %s", proc2.stderr.strip())
                    return False
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.error("smbpasswd error: %s", e)
            return False

    def remove_smb_user(self, username: str) -> bool:
        """Remove an SMB user."""
        return self._run(["smbpasswd", "-x", username])

    # ── Config Verification ───────────────────────────────────────────────

    def test_config(self) -> tuple[bool, str]:
        """Run `testparm -s` to validate smb.conf syntax.

        Returns (is_valid, output_or_error).
        """
        try:
            result = subprocess.run(
                ["testparm", "-s", str(SAMBA_CONF)],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout + result.stderr
            return result.returncode == 0, output.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return False, str(e)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _reconfigure(self) -> bool:
        """Write config, test it, and reload. Returns True on success."""
        if not self.write_config():
            return False

        is_valid, output = self.test_config()
        if not is_valid:
            logger.error("Invalid smb.conf: %s", output)
            return False

        return self.reload_service()

    @staticmethod
    def _run(cmd: list[str]) -> bool:
        """Run a command and return True if it succeeded."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("Command failed [%s]: %s", " ".join(cmd), result.stderr.strip())
                return False
            return True
        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return False
        except subprocess.TimeoutExpired:
            logger.error("Command timed out: %s", " ".join(cmd))
            return False
