"""Regression tests for share API sequencing and rollback."""

from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from smbhost.api import shares as shares_api
from smbhost.config import AuthMode, ConfigManager, ShareConfig


def _make_app(config: ConfigManager, smb_manager: Mock) -> FastAPI:
    app = FastAPI()
    app.state.config = config
    app.state.smb_manager = smb_manager
    app.include_router(shares_api.router)
    return app


def test_create_share_persists_before_samba(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")

    smb = Mock()

    def _add_share(share: ShareConfig) -> bool:
        # If config is saved first, SMB reconfigure can see this share.
        return cfg.get_share(share.drive_uuid) is not None

    smb.add_share.side_effect = _add_share

    client = TestClient(_make_app(cfg, smb))
    resp = client.post(
        "/api/shares",
        json={
            "drive_uuid": "u-create",
            "drive_label": "DriveA",
            "share_name": "DriveA",
            "mount_point": "/mnt/drivea",
            "auth_mode": "guest",
        },
    )

    assert resp.status_code == 201
    assert cfg.get_share("u-create") is not None


def test_create_share_rolls_back_on_samba_failure(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")

    smb = Mock()
    smb.add_share.return_value = False

    client = TestClient(_make_app(cfg, smb))
    resp = client.post(
        "/api/shares",
        json={
            "drive_uuid": "u-fail",
            "share_name": "FailShare",
            "mount_point": "/mnt/fail",
            "auth_mode": "guest",
        },
    )

    assert resp.status_code == 500
    assert cfg.get_share("u-fail") is None


def test_update_share_rolls_back_on_samba_failure(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")
    cfg.add_share(
        ShareConfig(
            drive_uuid="u-update",
            drive_label="DriveU",
            share_name="DriveU",
            mount_point="/mnt/driveu",
            auth_mode=AuthMode.GUEST,
        )
    )

    smb = Mock()
    smb.update_share.return_value = False

    client = TestClient(_make_app(cfg, smb))
    resp = client.put("/api/shares/u-update", json={"share_name": "Renamed"})

    assert resp.status_code == 500
    assert cfg.get_share("u-update").share_name == "DriveU"


def test_delete_share_rolls_back_on_samba_failure(tmp_path):
    cfg = ConfigManager(tmp_path / "config.yaml")
    cfg.add_share(
        ShareConfig(
            drive_uuid="u-delete",
            drive_label="DriveD",
            share_name="DriveD",
            mount_point="/mnt/drived",
            auth_mode=AuthMode.GUEST,
        )
    )

    smb = Mock()
    smb.remove_share.return_value = False

    client = TestClient(_make_app(cfg, smb))
    resp = client.delete("/api/shares/u-delete")

    assert resp.status_code == 500
    assert cfg.get_share("u-delete") is not None
