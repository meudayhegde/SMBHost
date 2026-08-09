"""Drive detector for SMBHost.

Uses pyudev for real-time monitoring of block device add/remove events.
Falls back to lsblk polling when pyudev is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Data Model ────────────────────────────────────────────────────────────────


@dataclass
class DriveInfo:
    """Information about a detected block device."""

    device_path: str          # e.g. /dev/sdb1
    device_name: str          # e.g. sdb1
    uuid: str                 # Filesystem UUID
    label: str                # Filesystem label
    size_bytes: int           # Total size in bytes
    fstype: str               # Filesystem type (ext4, ntfs, exfat, etc.)
    mount_point: str          # Current mount point (empty if not mounted)
    model: str = ""           # Drive model/manufacturer string
    serial: str = ""          # Drive serial number
    removable: bool = False   # Whether it's a removable device
    is_system: bool = False   # Whether it's a system/boot drive

    @property
    def size_human(self) -> str:
        """Human-readable size string."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if self.size_bytes < 1024:
                return f"{self.size_bytes:.1f} {unit}"
            self.size_bytes //= 1024  # type: ignore[operator]
        return f"{self.size_bytes:.1f} PB"  # type: ignore[has-type]

    @property
    def is_mounted(self) -> bool:
        return bool(self.mount_point)

    @property
    def display_name(self) -> str:
        """Best human-readable name for this drive."""
        return self.label or self.uuid[:8] or self.device_name


# ── Drive Detector ────────────────────────────────────────────────────────────


class DriveDetector:
    """Detects and monitors attached block devices."""

    # System paths to exclude from detection
    _SYSTEM_PREFIXES = frozenset({
        "/dev/loop",      # Loop devices
        "/dev/ram",       # RAM disks
        "/dev/zram",      # Compressed RAM
        "/dev/dm-",       # Device mapper (LVM, LUKS)
        "/dev/md",        # MD RAID
    })

    _SYSTEM_UUIDS: frozenset[str] = frozenset()  # Populated on first scan

    def __init__(self) -> None:
        self._callbacks: list[Callable[[str, DriveInfo], None]] = []
        self._monitor = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────

    def on_event(self, callback: Callable[[str, DriveInfo], None]) -> None:
        """Register a callback for drive events. Called with ("added"|"removed", DriveInfo)."""
        self._callbacks.append(callback)

    async def start_monitoring(self) -> None:
        """Start real-time drive monitoring via pyudev."""
        self._running = True
        try:
            import pyudev
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="block")

            self._monitor = monitor

            # Also do initial scan to populate system UUIDs
            self._scan_system_uuids()

            logger.info("Drive monitor started (pyudev)")

            # We need to run the monitor in a thread since pyudev is synchronous
            loop = asyncio.get_running_loop()
            monitor_fd = monitor.fileno()

            while self._running:
                # Wait for readability on the monitor fd
                await loop.run_in_executor(None, monitor.poll, 1)

                device = monitor.poll(0)
                if device is None:
                    await asyncio.sleep(0.1)
                    continue

                action = device.action
                if action not in ("add", "remove"):
                    continue

                drive = self._udev_to_drive(device)
                if drive is None:
                    continue

                if action == "add":
                    if drive.uuid in self._SYSTEM_UUIDS:
                        continue
                    logger.info("Drive added: %s (%s)", drive.display_name, drive.device_path)
                else:
                    logger.info("Drive removed: %s (%s)", drive.display_name, drive.device_path)

                for cb in self._callbacks:
                    try:
                        cb(action + "d", drive)  # "added" / "removed"
                    except Exception:
                        logger.exception("Error in drive event callback")

        except ImportError:
            logger.warning("pyudev not available — falling back to periodic polling")
            await self._poll_loop()

    async def stop_monitoring(self) -> None:
        """Stop drive monitoring."""
        self._running = False

    # ── Scanning ──────────────────────────────────────────────────────────

    def get_drives(self) -> list[DriveInfo]:
        """Get a snapshot of all currently attached drives (excluding system drives)."""
        return self._scan_drives(exclude_system=True)

    def get_all_drives(self) -> list[DriveInfo]:
        """Get all block devices including system drives."""
        return self._scan_drives(exclude_system=False)

    def get_drive_by_uuid(self, uuid: str) -> Optional[DriveInfo]:
        """Find a drive by its filesystem UUID."""
        for drive in self._scan_drives(exclude_system=False):
            if drive.uuid == uuid:
                return drive
        return None

    # ── Internal: Scanning ────────────────────────────────────────────────

    def _scan_system_uuids(self) -> None:
        """Identify which UUIDs belong to system/boot drives so we can filter them."""
        mounts_to_check = {"/", "/boot", "/boot/efi", "/efi"}
        uuids: set[str] = set()

        # Use lsblk to find devices backing those mount points
        try:
            result = subprocess.run(
                ["lsblk", "-J", "-o", "UUID,MOUNTPOINT"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                devices = json.loads(result.stdout).get("blockdevices", [])
                for dev in self._flatten_devices(devices):
                    mp = dev.get("mountpoint", "") or ""
                    uuid = dev.get("uuid", "") or ""
                    if mp.rstrip("/") in mounts_to_check and uuid:
                        uuids.add(uuid)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

        # Also add the root device UUID from /proc/mounts
        try:
            for line in Path("/proc/mounts").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "/":
                    dev_path = parts[0]
                    result = subprocess.run(
                        ["blkid", "-s", "UUID", "-o", "value", dev_path],
                        capture_output=True, text=True, timeout=5,
                    )
                    uuid_val = result.stdout.strip()
                    if uuid_val:
                        uuids.add(uuid_val)
                    break
        except (OSError, subprocess.TimeoutExpired):
            pass

        self._SYSTEM_UUIDS = frozenset(uuids)
        logger.debug("System UUIDs: %s", self._SYSTEM_UUIDS)

    def _scan_drives(self, exclude_system: bool = True) -> list[DriveInfo]:
        """Scan all block devices using lsblk."""
        drives: list[DriveInfo] = []
        try:
            result = subprocess.run(
                [
                    "lsblk", "-J", "-o",
                    "NAME,UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,RM,ROTA",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.error("lsblk failed: %s", result.stderr)
                return drives

            devices = json.loads(result.stdout).get("blockdevices", [])
            for dev in self._flatten_devices(devices):
                uuid_val = dev.get("uuid", "") or ""
                fstype = dev.get("fstype", "") or ""

                # Skip devices without filesystem UUID
                if not uuid_val:
                    continue
                # Skip devices without a filesystem type
                if not fstype:
                    continue

                name = dev.get("name", "")
                dev_path = f"/dev/{name}"

                # Skip system-internal devices
                if any(dev_path.startswith(p) for p in self._SYSTEM_PREFIXES):
                    continue

                # Skip swap
                if fstype == "swap":
                    continue

                # Build DriveInfo
                size_str = dev.get("size", "0")
                try:
                    size_bytes = self._parse_size(size_str)
                except ValueError:
                    size_bytes = 0

                drive = DriveInfo(
                    device_path=dev_path,
                    device_name=name,
                    uuid=uuid_val,
                    label=dev.get("label", "") or "",
                    size_bytes=size_bytes,
                    fstype=fstype,
                    mount_point=dev.get("mountpoint", "") or "",
                    model=dev.get("model", "") or "",
                    serial=dev.get("serial", "") or "",
                    removable=dev.get("rm", "0") == "1",
                    is_system=(uuid_val in self._SYSTEM_UUIDS),
                )

                if exclude_system and drive.is_system:
                    continue

                drives.append(drive)

        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            logger.error("Failed to scan drives: %s", e)

        return drives

    def _flatten_devices(self, devices: list[dict]) -> list[dict]:
        """Flatten lsblk tree into a flat list, including partitions."""
        result: list[dict] = []
        for dev in devices:
            result.append(dev)
            children = dev.get("children", [])
            if children:
                result.extend(self._flatten_devices(children))
        return result

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Parse lsblk size string (e.g. '1.5G', '500M', '2T') to bytes."""
        size_str = size_str.strip().upper()
        if not size_str:
            return 0
        if size_str[-1].isalpha():
            num = float(size_str[:-1])
            unit = size_str[-1]
            multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
            return int(num * multipliers.get(unit, 1))
        return int(float(size_str))

    # ── Internal: pyudev Conversion ───────────────────────────────────────

    def _udev_to_drive(self, device) -> Optional[DriveInfo]:
        """Convert a pyudev device to DriveInfo, or None if not a usable drive."""
        dev_name = device.device_node or ""
        if not dev_name:
            return None

        # Skip system device types
        if any(dev_name.startswith(p) for p in self._SYSTEM_PREFIXES):
            return None

        # Try to get UUID via blkid
        uuid_val = device.get("ID_FS_UUID", "")
        if not uuid_val:
            return None

        fstype = device.get("ID_FS_TYPE", "")
        if not fstype or fstype == "swap":
            return None

        # Parse size from sysfs
        size_bytes = 0
        try:
            size_path = Path(f"/sys/block/{device.sys_name}/size")
            if size_path.exists():
                sector_count = int(size_path.read_text().strip())
                size_bytes = sector_count * 512
        except (OSError, ValueError):
            pass

        return DriveInfo(
            device_path=dev_name,
            device_name=os.path.basename(dev_name),
            uuid=uuid_val,
            label=device.get("ID_FS_LABEL_ENC", "") or device.get("ID_FS_LABEL", ""),
            size_bytes=size_bytes,
            fstype=fstype,
            mount_point="",  # pyudev doesn't reliably have this; use separate lookup
            model=device.get("ID_MODEL_ENC", "") or device.get("ID_MODEL", ""),
            serial=device.get("ID_SERIAL_SHORT", ""),
            removable=device.get("ID_BUS", "") == "usb",
            is_system=(uuid_val in self._SYSTEM_UUIDS),
        )

    # ── Internal: Polling Fallback ─────────────────────────────────────────

    async def _poll_loop(self, interval: float = 5.0) -> None:
        """Fallback polling loop when pyudev is unavailable."""
        previous_uuids: set[str] = set()
        first_scan = True

        while self._running:
            try:
                current = self._scan_drives(exclude_system=True)
                current_uuids = {d.uuid for d in current}

                if not first_scan:
                    # Detect newly added drives
                    added = current_uuids - previous_uuids
                    for uuid_val in added:
                        drive = next((d for d in current if d.uuid == uuid_val), None)
                        if drive:
                            logger.info("Drive detected (poll): %s", drive.display_name)
                            for cb in self._callbacks:
                                try:
                                    cb("added", drive)
                                except Exception:
                                    logger.exception("Error in drive event callback")

                    # Detect removed drives
                    removed = previous_uuids - current_uuids
                    for uuid_val in removed:
                        # We need a placeholder DriveInfo for removed
                        placeholder = DriveInfo(
                            device_path="", device_name="", uuid=uuid_val,
                            label="", size_bytes=0, fstype="", mount_point="",
                        )
                        logger.info("Drive removed (poll): %s", uuid_val)
                        for cb in self._callbacks:
                            try:
                                cb("removed", placeholder)
                            except Exception:
                                logger.exception("Error in drive event callback")

                previous_uuids = current_uuids
                first_scan = False

            except Exception:
                logger.exception("Error in polling loop")

            await asyncio.sleep(interval)
