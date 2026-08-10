"""Tests for SMBHost configuration manager."""

import tempfile
from pathlib import Path

import pytest

from smbhost.config import (
    AppConfig,
    AuthMode,
    ConfigManager,
    GlobalConfig,
    ShareConfig,
)


class TestGlobalConfig:
    def test_defaults(self):
        gc = GlobalConfig()
        assert gc.workgroup == "WORKGROUP"
        assert gc.netbios_name == "SMBHOST"
        assert gc.web_port == 8080
        assert gc.web_bind == "127.0.0.1"
        assert gc.allow_remote_ui is False

    def test_netbios_name_validation(self):
        gc = GlobalConfig(netbios_name="My Server!")
        assert gc.netbios_name == "MYSERVER"  # Uppercased, special chars stripped

    def test_netbios_name_truncation(self):
        gc = GlobalConfig(netbios_name="ThisNameIsTooLongForNetBIOS")
        assert len(gc.netbios_name) <= 15

    def test_web_port_range(self):
        with pytest.raises(Exception):
            GlobalConfig(web_port=0)
        with pytest.raises(Exception):
            GlobalConfig(web_port=99999)


class TestShareConfig:
    def test_valid_share(self):
        share = ShareConfig(
            drive_uuid="abc-123",
            drive_label="MyDrive",
            share_name="MyDrive",
            mount_point="/mnt/drive",
            auth_mode=AuthMode.GUEST,
        )
        assert share.share_name == "MyDrive"
        assert share.enabled is True
        assert share.browseable is True

    def test_share_name_sanitization(self):
        share = ShareConfig(
            drive_uuid="abc",
            share_name="My Drive 2024!",
            mount_point="/mnt",
        )
        assert " " not in share.share_name
        assert share.share_name == "My_Drive_2024!"

    def test_share_name_truncation(self):
        share = ShareConfig(
            drive_uuid="abc",
            share_name="VeryLongShareNameThatExceedsLimit",
            mount_point="/mnt",
        )
        assert len(share.share_name) <= 15


class TestAppConfig:
    def test_empty_default(self):
        cfg = AppConfig()
        assert cfg.global_.workgroup == "WORKGROUP"
        assert cfg.shares == {}

    def test_parse_from_dict(self):
        data = {
            "global": {"workgroup": "MYGROUP", "web_port": 9090},
            "shares": {
                "uuid-1": {
                    "drive_uuid": "uuid-1",
                    "drive_label": "Disk1",
                    "share_name": "Disk1",
                    "mount_point": "/mnt/disk1",
                    "auth_mode": "guest",
                }
            },
        }
        cfg = AppConfig.model_validate(data)
        assert cfg.global_.workgroup == "MYGROUP"
        assert cfg.global_.web_port == 9090
        assert len(cfg.shares) == 1
        assert cfg.shares["uuid-1"].share_name == "Disk1"


class TestConfigManager:
    def test_load_defaults_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)
            assert mgr.global_config.workgroup == "WORKGROUP"

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)
            mgr.global_config.workgroup = "TESTGRP"
            mgr.save()

            # Reload from disk
            mgr2 = ConfigManager(cfg_path)
            assert mgr2.global_config.workgroup == "TESTGRP"

    def test_add_get_remove_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            share = ShareConfig(
                drive_uuid="uuid-test",
                share_name="TestShare",
                mount_point="/mnt/test",
            )
            mgr.add_share(share)

            retrieved = mgr.get_share("uuid-test")
            assert retrieved is not None
            assert retrieved.share_name == "TestShare"

            assert mgr.remove_share("uuid-test") is True
            assert mgr.get_share("uuid-test") is None

    def test_list_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            mgr.add_share(ShareConfig(
                drive_uuid="a", share_name="A", mount_point="/mnt/a",
            ))
            mgr.add_share(ShareConfig(
                drive_uuid="b", share_name="B", mount_point="/mnt/b",
            ))

            shares = mgr.list_shares()
            assert len(shares) == 2

    def test_update_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            share = ShareConfig(
                drive_uuid="uuid-x",
                share_name="OldName",
                mount_point="/mnt/x",
            )
            mgr.add_share(share)

            updated = mgr.update_share("uuid-x", share_name="NewName", read_only=True)
            assert updated is not None
            assert updated.share_name == "NewName"
            assert updated.read_only is True

    def test_generate_smb_conf(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            mgr.global_config.workgroup = "TEST"
            mgr.global_config.netbios_name = "TESTHOST"

            mgr.add_share(ShareConfig(
                drive_uuid="u1",
                drive_label="MyDisk",
                share_name="MyDisk",
                mount_point="/mnt/mydisk",
                auth_mode=AuthMode.GUEST,
            ))

            conf = mgr.generate_smb_conf()

            assert "[global]" in conf
            assert "workgroup = TEST" in conf
            assert "netbios name = TESTHOST" in conf
            assert "[MyDisk]" in conf
            assert "path = /mnt/mydisk" in conf
            assert "guest ok = yes" in conf

    def test_generate_smb_conf_user_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            mgr.add_share(ShareConfig(
                drive_uuid="u2",
                share_name="Secure",
                mount_point="/mnt/secure",
                auth_mode=AuthMode.USER_PASS,
                username="bob",
            ))

            conf = mgr.generate_smb_conf()
            assert "guest ok = no" in conf
            assert "valid users = bob" in conf
            assert "force user = root" in conf
            assert "force group = root" in conf

    def test_generate_smb_conf_guest_forces_root_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            mgr.add_share(ShareConfig(
                drive_uuid="u-guest",
                share_name="GuestShare",
                mount_point="/mnt/guest",
                auth_mode=AuthMode.GUEST,
            ))

            conf = mgr.generate_smb_conf()
            assert "[GuestShare]" in conf
            assert "guest ok = yes" in conf
            assert "guest only = yes" in conf
            assert "force user = root" in conf
            assert "force group = root" in conf

    def test_disabled_share_not_in_conf(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.yaml"
            mgr = ConfigManager(cfg_path)

            mgr.add_share(ShareConfig(
                drive_uuid="u3",
                share_name="Disabled",
                mount_point="/mnt/off",
                enabled=False,
            ))

            conf = mgr.generate_smb_conf()
            assert "[Disabled]" not in conf
