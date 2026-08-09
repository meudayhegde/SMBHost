"""System API endpoints for SMBHost.

Provides status checks, log retrieval, and service control endpoints.
"""

from __future__ import annotations

import platform
import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request

from smbhost import __version__

router = APIRouter(prefix="/api/system", tags=["system"])

# Track when the server started
_start_time = time.monotonic()


@router.get("/status")
async def get_status(request: Request) -> dict:
    """Get the overall system status including Samba health."""
    smb = request.app.state.smb_manager

    samba_status = smb.get_status()
    samba_running = samba_status.get("smbd") == "active"

    uptime_seconds = int(time.monotonic() - _start_time)
    uptime_str = str(timedelta(seconds=uptime_seconds))

    return {
        "app": {
            "version": __version__,
            "uptime_seconds": uptime_seconds,
            "uptime": uptime_str,
            "python": platform.python_version(),
        },
        "samba": {
            "running": samba_running,
            "smbd": samba_status.get("smbd", "unknown"),
            "nmbd": samba_status.get("nmbd", "unknown"),
        },
        "host": {
            "hostname": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }


@router.get("/logs")
async def get_logs(request: Request, lines: int = 50) -> dict:
    """Get recent application logs."""
    from smbhost.systemd_manager import SystemdManager

    logs = SystemdManager.get_logs(lines=lines)
    return {"lines": lines, "logs": logs}


@router.post("/samba/reload")
async def reload_samba(request: Request) -> dict:
    """Reload Samba configuration."""
    smb = request.app.state.smb_manager

    if smb.reload_service():
        return {"detail": "Samba configuration reloaded"}
    raise HTTPException(status_code=500, detail="Failed to reload Samba")


@router.post("/samba/restart")
async def restart_samba(request: Request) -> dict:
    """Restart Samba services."""
    smb = request.app.state.smb_manager

    if smb.restart_service():
        return {"detail": "Samba services restarted"}
    raise HTTPException(status_code=500, detail="Failed to restart Samba")


@router.get("/samba/test")
async def test_samba_config(request: Request) -> dict:
    """Validate the current Samba configuration."""
    smb = request.app.state.smb_manager

    is_valid, output = smb.test_config()
    return {"valid": is_valid, "output": output}


@router.get("/health")
async def health_check() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok", "version": __version__}
