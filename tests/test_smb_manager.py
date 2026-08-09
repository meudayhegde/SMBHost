"""Tests for SMBHost SMB manager.

Uses mocked subprocess and filesystem operations.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smbhost.config import AuthMode, ConfigManager, ShareConfig
from smbhost.smb_manager import SMBManager


@pytest.fixture
def cfg_mgr():
    """Create a ConfigManager with a temporary config file."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        mgr = ConfigManager(cfg_path)
        mgr.add_share(ShareConfig(
            drive_uuid="test-uuid",
            share_name="TestShare",
            mount_point="/mnt/test",
            auth_mode=AuthMode.GUEST,
        ))
        yield mgr


@pytest.fixture
def smb_mgr(cfg_mgr):
    """Create an SMBManager with a temporary config."""
    return SMBManager(cfg_mgr)


class TestSMBManager:
    @patch("smbhost.smb_manager.shutil.copy2")
    @patch("smbhost.smb_manager.SAMBA_CONF_BAK")
    @patch("smbhost.smb_manager.SAMBA_CONF")
    @patch("smbhost.smb_manager.SMBManager._run")
    def test_write_config(self, mock_run, mock_samba_conf, mock_samba_bak, mock_copy, smb_mgr, cfg_mgr):
        """Test that write_config writes the generated config to smb.conf."""
        mock_run.return_value = True
        mock_samba_conf.exists.return_value = False

        # Write to a temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            temp_conf = Path(f.name)

        try:
            with patch("smbhost.smb_manager.SAMBA_CONF", temp_conf):
                with patch("os.chmod"):
                    result = smb_mgr.write_config()

            content = temp_conf.read_text()
            assert result is True
            assert "[global]" in content
            assert "[TestShare]" in content
            assert "path = /mnt/test" in content
        finally:
            temp_conf.unlink(missing_ok=True)

    @patch("smbhost.smb_manager.SMBManager._run")
    def test_reload_service_smbcontrol_first(self, mock_run, smb_mgr):
        """Test reload tries smbcontrol first, then systemctl."""
        mock_run.return_value = True
        result = smb_mgr.reload_service()
        assert result is True
        # First call should be smbcontrol
        assert mock_run.call_count >= 1
        first_call = mock_run.call_args_list[0][0][0]
        assert "smbcontrol" in first_call

    @patch("subprocess.run")
    def test_get_status(self, mock_run, smb_mgr):
        """Test that get_status parses systemctl output correctly."""
        mock_active = MagicMock()
        mock_active.stdout = "active\n"
        mock_inactive = MagicMock()
        mock_inactive.stdout = "inactive\n"

        # smbd active, nmbd inactive
        mock_run.side_effect = [mock_active, mock_inactive]

        status = smb_mgr.get_status()
        assert status["smbd"] == "active"
        assert status["nmbd"] == "inactive"

    @patch("subprocess.run")
    def test_test_config(self, mock_run, smb_mgr):
        """Test that test_config returns validation results."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Loaded services file OK.\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        is_valid, output = smb_mgr.test_config()
        assert is_valid is True
        assert "OK" in output

    @patch("smbhost.smb_manager.SMBManager._run")
    def test_restart_service(self, mock_run, smb_mgr):
        """Test that restart restarts both smbd and nmbd."""
        mock_run.return_value = True
        result = smb_mgr.restart_service()
        assert result is True
        # Should have called restart for both services
        assert mock_run.call_count >= 2

    def test_is_running(self, smb_mgr):
        """Test is_running delegates to get_status."""
        with patch.object(smb_mgr, "get_status", return_value={"smbd": "active"}):
            assert smb_mgr.is_running() is True

        with patch.object(smb_mgr, "get_status", return_value={"smbd": "inactive"}):
            assert smb_mgr.is_running() is False
