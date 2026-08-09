"""Configuration manager for SMBHost.

Stores configuration as YAML at /etc/smbhost/config.yaml.
Uses Pydantic models for validation and defaults.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

# ── Constants ────────────────────────────────────────────────────────────────

CONFIG_DIR = Path("/etc/smbhost")
CONFIG_PATH = CONFIG_DIR / "config.yaml"
SAMBA_CONF = Path("/etc/samba/smb.conf")
DEFAULT_WEB_PORT = 8080
DEFAULT_WEB_BIND = "127.0.0.1"


# ── Auth Mode Enum ───────────────────────────────────────────────────────────

class AuthMode(str, Enum):
    """Authentication mode for an SMB share."""

    GUEST = "guest"           # Open access, no password
    USER_PASS = "user_pass"   # Username + password required
    GUEST_RO = "guest_ro"     # Guest read-only access


# ── Pydantic Models ──────────────────────────────────────────────────────────

class ShareConfig(BaseModel):
    """Per-drive SMB share configuration."""

    drive_uuid: str = Field(..., description="UUID of the block device")
    drive_label: str = Field(default="", description="Display label for the drive")
    share_name: str = Field(..., description="SMB share name")
    mount_point: str = Field(..., description="Filesystem mount point to share")
    auth_mode: AuthMode = Field(default=AuthMode.GUEST, description="Authentication mode")
    username: str = Field(default="", description="SMB username (only for user_pass mode)")
    password: str = Field(default="", description="SMB password (only for user_pass mode)")
    read_only: bool = Field(default=False, description="Force read-only access")
    browseable: bool = Field(default=True, description="Visible in network browse lists")
    enabled: bool = Field(default=True, description="Whether this share is active")

    @field_validator("share_name")
    @classmethod
    def share_name_valid(cls, v: str) -> str:
        """Validate share name — Samba limits to 15 chars, no spaces/special chars."""
        if not v:
            raise ValueError("Share name must not be empty")
        v = v.strip().replace(" ", "_")
        if len(v) > 15:
            v = v[:15]
        return v


class GlobalConfig(BaseModel):
    """Global SMBHost configuration."""

    workgroup: str = Field(default="WORKGROUP", description="Samba workgroup name")
    netbios_name: str = Field(default="SMBHOST", description="NetBIOS server name")
    server_string: str = Field(default="SMBHost File Server", description="Server description string")
    web_bind: str = Field(default=DEFAULT_WEB_BIND, description="Web UI bind address")
    web_port: int = Field(default=DEFAULT_WEB_PORT, ge=1, le=65535, description="Web UI port")
    allow_remote_ui: bool = Field(default=False, description="Allow web UI from non-localhost")
    log_level: str = Field(default="info", description="Logging level")

    @field_validator("netbios_name")
    @classmethod
    def netbios_name_valid(cls, v: str) -> str:
        """NetBIOS names are max 15 chars, uppercase, no special chars."""
        v = v.upper().strip()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        v = "".join(c for c in v if c in allowed)
        if len(v) > 15:
            v = v[:15]
        if not v:
            raise ValueError("NetBIOS name must not be empty")
        return v


class AppConfig(BaseModel):
    """Top-level application configuration."""

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    shares: dict[str, ShareConfig] = Field(default_factory=dict, description="Shares keyed by drive UUID")


# ── Config Manager ───────────────────────────────────────────────────────────

class ConfigManager:
    """Manages loading, saving, and accessing SMBHost configuration."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or CONFIG_PATH
        self._config: AppConfig = self._load_or_default()

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def global_config(self) -> GlobalConfig:
        return self._config.global_

    @property
    def shares(self) -> dict[str, ShareConfig]:
        return self._config.shares

    # ── Load / Save ──────────────────────────────────────────────────────

    def _load_or_default(self) -> AppConfig:
        """Load config from disk or return defaults."""
        if self._path.exists():
            try:
                raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
                return AppConfig.model_validate(raw)
            except Exception:
                # Corrupted config — back it up and use defaults
                backup = self._path.with_suffix(".yaml.bak")
                self._path.rename(backup)
        return AppConfig()

    def save(self) -> None:
        """Persist current config to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._config.model_dump(by_alias=True, exclude_none=True)
        self._path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")

    def reload(self) -> None:
        """Reload config from disk."""
        self._config = self._load_or_default()

    # ── Share CRUD ───────────────────────────────────────────────────────

    def get_share(self, drive_uuid: str) -> Optional[ShareConfig]:
        """Get share config for a specific drive."""
        return self._config.shares.get(drive_uuid)

    def add_share(self, share: ShareConfig) -> None:
        """Add or update a share for a drive."""
        self._config.shares[share.drive_uuid] = share
        self.save()

    def remove_share(self, drive_uuid: str) -> bool:
        """Remove a share by drive UUID. Returns True if it existed."""
        existed = drive_uuid in self._config.shares
        self._config.shares.pop(drive_uuid, None)
        if existed:
            self.save()
        return existed

    def update_share(self, drive_uuid: str, **kwargs) -> Optional[ShareConfig]:
        """Update fields on an existing share. Returns updated share or None."""
        share = self._config.shares.get(drive_uuid)
        if share is None:
            return None
        for key, value in kwargs.items():
            if hasattr(share, key):
                setattr(share, key, value)
        # Re-validate by creating a new instance
        updated = ShareConfig.model_validate(share.model_dump())
        self._config.shares[drive_uuid] = updated
        self.save()
        return updated

    def list_shares(self) -> list[ShareConfig]:
        """Return all configured shares as a list."""
        return list(self._config.shares.values())

    def list_enabled_shares(self) -> list[ShareConfig]:
        """Return only enabled shares."""
        return [s for s in self._config.shares.values() if s.enabled]

    # ── Global Config ────────────────────────────────────────────────────

    def update_global(self, **kwargs) -> GlobalConfig:
        """Update global settings and persist."""
        for key, value in kwargs.items():
            if hasattr(self._config.global_, key):
                setattr(self._config.global_, key, value)
        # Re-validate
        updated = GlobalConfig.model_validate(self._config.global_.model_dump())
        self._config.global_ = updated
        self.save()
        return updated

    # ── Samba Config Generation ──────────────────────────────────────────

    def generate_smb_conf(self) -> str:
        """Generate the full smb.conf content based on current configuration."""
        lines: list[str] = []
        gc = self._config.global_

        # Header
        lines.append("# ═══════════════════════════════════════════════════════════════")
        lines.append("# SMBHost managed smb.conf — do not edit between markers")
        lines.append("# ═══════════════════════════════════════════════════════════════")
        lines.append("")

        # Global section
        lines.append("[global]")
        lines.append(f"   workgroup = {gc.workgroup}")
        lines.append(f"   netbios name = {gc.netbios_name}")
        lines.append(f"   server string = {gc.server_string}")
        lines.append("   log file = /var/log/samba/log.%m")
        lines.append("   max log size = 1000")
        lines.append("   logging = file")
        lines.append("   map to guest = Bad User")
        lines.append("   usershare allow guests = yes")
        lines.append("   security = user")
        lines.append("   server role = standalone server")
        lines.append("   server min protocol = SMB2")
        lines.append("   ntlm auth = yes")
        lines.append("   obey pam restrictions = yes")
        lines.append("   unix password sync = yes")
        lines.append("   passwd program = /usr/bin/passwd %u")
        lines.append("   passwd chat = *Enter\\snew\\s*\\spassword:* %n\\n *Retype\\snew\\s*\\spassword:* %n\\n *password\\supdated\\ssuccessfully* .")
        lines.append("   pam password change = yes")
        lines.append("")

        # Share sections
        for share in self._config.shares.values():
            if not share.enabled:
                continue
            lines.append(f"# SMBHost: share for drive {share.drive_uuid} ({share.drive_label})")
            lines.append(f"[{share.share_name}]")
            lines.append(f"   path = {share.mount_point}")
            lines.append(f"   browseable = {'yes' if share.browseable else 'no'}")
            lines.append(f"   read only = {'yes' if share.read_only else 'no'}")
            lines.append("   create mask = 0755")
            lines.append("   directory mask = 0755")

            if share.auth_mode == AuthMode.GUEST:
                lines.append("   guest ok = yes")
                lines.append("   guest only = yes")
                lines.append("   force user = nobody")
                lines.append("   force group = nogroup")
            elif share.auth_mode == AuthMode.GUEST_RO:
                lines.append("   guest ok = yes")
                lines.append("   guest only = yes")
                lines.append("   read only = yes")
                lines.append("   force user = nobody")
                lines.append("   force group = nogroup")
            elif share.auth_mode == AuthMode.USER_PASS:
                lines.append("   guest ok = no")
                lines.append(f"   valid users = {share.username}")
            lines.append("")

        return "\n".join(lines) + "\n"
