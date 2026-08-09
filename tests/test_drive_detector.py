"""Tests for SMBHost drive detector.

Uses mocked subprocess calls since we're not on a real Linux system.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smbhost.drive_detector import DriveDetector, DriveInfo


# ── Sample lsblk output ──────────────────────────────────────────────────────

SAMPLE_LSBLK = {
    "blockdevices": [
        {
            "name": "sda",
            "uuid": None,
            "label": None,
            "size": "256G",
            "fstype": None,
            "mountpoint": None,
            "model": "Samsung SSD",
            "serial": "S123",
            "rm": "0",
            "rota": "0",
            "children": [
                {
                    "name": "sda1",
                    "uuid": "root-uuid-1234",
                    "label": None,
                    "size": "255G",
                    "fstype": "ext4",
                    "mountpoint": "/",
                    "model": None,
                    "serial": None,
                    "rm": "0",
                    "rota": "0",
                }
            ],
        },
        {
            "name": "sdb",
            "uuid": "ext-uuid-5678",
            "label": "BackupDrive",
            "size": "1T",
            "fstype": "ntfs",
            "mountpoint": "/media/user/BackupDrive",
            "model": "WD Elements",
            "serial": "W123",
            "rm": "1",
            "rota": "1",
        },
    ]
}

SAMPLE_PROC_MOUNTS = "/dev/sda1 / ext4 rw,relatime 0 0\n"


class TestDriveInfo:
    def test_size_human(self):
        d = DriveInfo(
            device_path="/dev/sdb1", device_name="sdb1",
            uuid="abc", label="Test", size_bytes=1073741824,  # 1 GB
            fstype="ext4", mount_point="/mnt",
        )
        assert "GB" in d.size_human

    def test_display_name_prefers_label(self):
        d = DriveInfo(
            device_path="/dev/sdb1", device_name="sdb1",
            uuid="abc-1234-5678", label="MyDrive", size_bytes=1024,
            fstype="vfat", mount_point="",
        )
        assert d.display_name == "MyDrive"

    def test_display_name_falls_back_to_uuid(self):
        d = DriveInfo(
            device_path="/dev/sdb1", device_name="sdb1",
            uuid="abcd1234", label="", size_bytes=1024,
            fstype="vfat", mount_point="",
        )
        assert d.display_name == "abcd1234"

    def test_is_mounted(self):
        d = DriveInfo(
            device_path="/dev/sdb1", device_name="sdb1",
            uuid="a", label="", size_bytes=1,
            fstype="ext4", mount_point="/mnt",
        )
        assert d.is_mounted is True

        d2 = DriveInfo(
            device_path="/dev/sdc1", device_name="sdc1",
            uuid="b", label="", size_bytes=1,
            fstype="ext4", mount_point="",
        )
        assert d2.is_mounted is False


class TestDriveDetector:
    @patch("subprocess.run")
    @patch.object(Path, "read_text")
    def test_scan_drives(self, mock_read, mock_run):
        """Test that drives are correctly parsed from lsblk output."""
        # Mock lsblk call
        mock_lsblk = MagicMock()
        mock_lsblk.returncode = 0
        mock_lsblk.stdout = json.dumps(SAMPLE_LSBLK)

        # Mock blkid call for root device
        mock_blkid = MagicMock()
        mock_blkid.returncode = 0
        mock_blkid.stdout = "root-uuid-1234\n"

        mock_run.side_effect = [mock_lsblk, mock_blkid]
        mock_read.return_value = SAMPLE_PROC_MOUNTS

        detector = DriveDetector()

        # First call to _scan_drives will also trigger _scan_system_uuids
        drives = detector._scan_drives(exclude_system=False)
        assert len(drives) >= 2  # sda1 + sdb

        # Find the backup drive
        backup = next((d for d in drives if d.label == "BackupDrive"), None)
        assert backup is not None
        assert backup.fstype == "ntfs"
        assert backup.removable is True
        assert backup.mount_point == "/media/user/BackupDrive"

    @patch("subprocess.run")
    @patch.object(Path, "read_text")
    def test_system_drive_excluded(self, mock_read, mock_run):
        """Test that system drives are excluded when exclude_system=True."""
        mock_lsblk = MagicMock()
        mock_lsblk.returncode = 0
        mock_lsblk.stdout = json.dumps(SAMPLE_LSBLK)

        mock_blkid = MagicMock()
        mock_blkid.returncode = 0
        mock_blkid.stdout = "root-uuid-1234\n"

        # _scan_system_uuids calls lsblk then blkid; _scan_drives calls lsblk
        mock_run.side_effect = [mock_lsblk, mock_blkid, mock_lsblk]
        mock_read.return_value = SAMPLE_PROC_MOUNTS

        detector = DriveDetector()
        # Populate system UUIDs first, then scan
        detector._scan_system_uuids()
        drives = detector._scan_drives(exclude_system=True)

        # root-uuid-1234 should be excluded
        root_drives = [d for d in drives if d.uuid == "root-uuid-1234"]
        assert len(root_drives) == 0

        # Backup drive should still be present
        backup = [d for d in drives if d.uuid == "ext-uuid-5678"]
        assert len(backup) == 1

    @patch("subprocess.run")
    def test_lsblk_failure_graceful(self, mock_run):
        """Test that lsblk failure returns empty list gracefully."""
        mock_run.side_effect = OSError("lsblk not found")
        detector = DriveDetector()
        drives = detector._scan_drives()
        assert drives == []

    @staticmethod
    def test_parse_size():
        detector = DriveDetector()
        assert detector._parse_size("1.5G") == int(1.5 * 1024**3)
        assert detector._parse_size("500M") == 500 * 1024**2
        assert detector._parse_size("2T") == 2 * 1024**4
        assert detector._parse_size("") == 0
        assert detector._parse_size("0") == 0

    @staticmethod
    def test_flatten_devices():
        detector = DriveDetector()
        nested = [
            {"name": "sda", "children": [
                {"name": "sda1"},
                {"name": "sda2"},
            ]},
            {"name": "sdb"},
        ]
        flat = detector._flatten_devices(nested)
        names = [d["name"] for d in flat]
        assert names == ["sda", "sda1", "sda2", "sdb"]
