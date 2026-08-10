"""System API endpoints for SMBHost.

Provides status checks, log retrieval, and service control endpoints.
"""

import platform
import socket
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
            "ip": _get_server_ip(),
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

    if not smb.write_config():
        raise HTTPException(status_code=500, detail="Failed to write Samba config")

    if smb.reload_service():
        return {"detail": "Samba configuration reloaded"}
    raise HTTPException(status_code=500, detail="Failed to reload Samba")


@router.post("/samba/restart")
async def restart_samba(request: Request) -> dict:
    """Restart Samba services."""
    smb = request.app.state.smb_manager

    if not smb.write_config():
        raise HTTPException(status_code=500, detail="Failed to write Samba config")

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


@router.get("/config")
async def get_config(request: Request) -> dict:
    """Get the current global configuration."""
    cfg = request.app.state.config.global_config
    return {
        "workgroup": cfg.workgroup,
        "netbios_name": cfg.netbios_name,
        "server_string": cfg.server_string,
        "web_bind": cfg.web_bind,
        "web_port": cfg.web_port,
    }


@router.put("/config")
async def update_config(request: Request) -> dict:
    """Update global configuration settings."""
    import json as _json

    cfg = request.app.state.config
    smb = request.app.state.smb_manager

    body = await request.json()
    updates = {k: v for k, v in body.items() if v is not None and hasattr(cfg.global_config, k)}

    if not updates:
        return {"detail": "No valid fields to update"}

    cfg.update_global(**updates)

    # Re-apply Samba config with new settings
    smb.write_config()
    smb.reload_service()

    return {
        "detail": "Configuration updated",
        "config": {
            "workgroup": cfg.global_config.workgroup,
            "netbios_name": cfg.global_config.netbios_name,
            "server_string": cfg.global_config.server_string,
            "web_bind": cfg.global_config.web_bind,
            "web_port": cfg.global_config.web_port,
        },
    }


def _get_server_ip() -> str:
    """Detect the server's primary IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Doesn't actually connect — just gets the route
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""
