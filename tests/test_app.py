"""Tests for application drive event behavior."""

from unittest.mock import Mock

from smbhost.app import _sync_configured_share_on_drive_added
from smbhost.config import AuthMode, ConfigManager, ShareConfig
from smbhost.drive_detector import DriveInfo


def test_configured_drive_is_auto_enabled_when_reconnected(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")
    cfg.add_share(
        ShareConfig(
            drive_uuid="drive-1",
            drive_label="OldLabel",
            share_name="Backup",
            mount_point="/media/user/OldMount",
            auth_mode=AuthMode.GUEST,
        )
    )

    smb = Mock()
    smb.update_share.return_value = True

    drive = DriveInfo(
        device_path="/dev/sdb1",
        device_name="sdb1",
        uuid="drive-1",
        label="BackupDrive",
        size_bytes=1024,
        fstype="ntfs",
        mount_point="/media/user/BackupDrive",
        model="",
        serial="",
        removable=True,
    )

    _sync_configured_share_on_drive_added(cfg, smb, drive)

    updated = cfg.get_share("drive-1")
    assert updated is not None
    assert updated.mount_point == "/media/user/BackupDrive"
    assert updated.drive_label == "BackupDrive"
    smb.update_share.assert_called_once()
    smb.add_share.assert_not_called()


def test_configured_drive_with_same_mount_is_reapplied(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")
    cfg.add_share(
        ShareConfig(
            drive_uuid="drive-2",
            drive_label="BackupDrive",
            share_name="Backup",
            mount_point="/media/user/BackupDrive",
            auth_mode=AuthMode.GUEST,
        )
    )

    smb = Mock()
    smb.add_share.return_value = True

    drive = DriveInfo(
        device_path="/dev/sdb1",
        device_name="sdb1",
        uuid="drive-2",
        label="BackupDrive",
        size_bytes=1024,
        fstype="ntfs",
        mount_point="/media/user/BackupDrive",
        model="",
        serial="",
        removable=True,
    )

    _sync_configured_share_on_drive_added(cfg, smb, drive)

    smb.add_share.assert_called_once()
    smb.update_share.assert_not_called()


def test_unmounted_configured_drive_is_not_auto_enabled(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")
    cfg.add_share(
        ShareConfig(
            drive_uuid="drive-3",
            drive_label="BackupDrive",
            share_name="Backup",
            mount_point="/media/user/BackupDrive",
            auth_mode=AuthMode.GUEST,
        )
    )

    smb = Mock()

    drive = DriveInfo(
        device_path="/dev/sdb1",
        device_name="sdb1",
        uuid="drive-3",
        label="BackupDrive",
        size_bytes=1024,
        fstype="ntfs",
        mount_point="",
        model="",
        serial="",
        removable=True,
    )

    _sync_configured_share_on_drive_added(cfg, smb, drive)

    smb.add_share.assert_not_called()
    smb.update_share.assert_not_called()