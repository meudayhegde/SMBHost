"""Drive API endpoints for SMBHost.

Provides REST endpoints for listing and querying detected drives,
and a WebSocket endpoint for real-time drive events.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/drives", tags=["drives"])


@router.get("")
async def list_drives(request: Request) -> dict:
    """Get all detected drives, including their share configuration status."""
    detector = request.app.state.drive_detector
    config = request.app.state.config

    drives = detector.get_drives()
    result = []
    for drive in drives:
        share = config.get_share(drive.uuid)
        result.append({
            "uuid": drive.uuid,
            "label": drive.display_name,
            "device_path": drive.device_path,
            "device_name": drive.device_name,
            "size_bytes": drive.size_bytes,
            "size_human": drive.size_human,
            "fstype": drive.fstype,
            "mount_point": drive.mount_point,
            "is_mounted": drive.is_mounted,
            "model": drive.model,
            "serial": drive.serial,
            "removable": drive.removable,
            "is_system": drive.is_system,
            "share": {
                "configured": share is not None,
                "share_name": share.share_name if share else None,
                "auth_mode": share.auth_mode.value if share else None,
                "read_only": share.read_only if share else None,
                "enabled": share.enabled if share else None,
                "browseable": share.browseable if share else None,
            } if share else None,
        })

    return {"drives": result, "count": len(result)}


@router.get("/{drive_uuid}")
async def get_drive(drive_uuid: str, request: Request) -> dict:
    """Get details for a specific drive."""
    from fastapi import HTTPException

    detector = request.app.state.drive_detector
    drive = detector.get_drive_by_uuid(drive_uuid)

    if drive is None:
        raise HTTPException(status_code=404, detail="Drive not found")

    return {
        "uuid": drive.uuid,
        "label": drive.display_name,
        "device_path": drive.device_path,
        "device_name": drive.device_name,
        "size_bytes": drive.size_bytes,
        "size_human": drive.size_human,
        "fstype": drive.fstype,
        "mount_point": drive.mount_point,
        "is_mounted": drive.is_mounted,
        "model": drive.model,
        "serial": drive.serial,
        "removable": drive.removable,
        "is_system": drive.is_system,
    }
