"""Share API endpoints for SMBHost.

Provides CRUD endpoints for managing SMB share configurations.
Each operation updates the Samba configuration and reloads smbd.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from smbhost.config import AuthMode, ShareConfig

router = APIRouter(prefix="/api/shares", tags=["shares"])


# ── Request/Response Models ──────────────────────────────────────────────────


class ShareCreateRequest(BaseModel):
    """Request body for creating a new share."""
    drive_uuid: str
    drive_label: str = ""
    share_name: str
    mount_point: str
    auth_mode: AuthMode = AuthMode.GUEST
    username: str = ""
    password: str = ""
    read_only: bool = False
    browseable: bool = True


class ShareUpdateRequest(BaseModel):
    """Request body for updating an existing share. All fields optional."""
    share_name: str | None = None
    auth_mode: AuthMode | None = None
    username: str | None = None
    password: str | None = None
    read_only: bool | None = None
    browseable: bool | None = None
    enabled: bool | None = None


class ShareResponse(BaseModel):
    """Response model for share data."""
    drive_uuid: str
    drive_label: str
    share_name: str
    mount_point: str
    auth_mode: str
    username: str
    read_only: bool
    browseable: bool
    enabled: bool


def _share_to_response(share: ShareConfig) -> ShareResponse:
    """Convert a ShareConfig to an API response."""
    return ShareResponse(
        drive_uuid=share.drive_uuid,
        drive_label=share.drive_label,
        share_name=share.share_name,
        mount_point=share.mount_point,
        auth_mode=share.auth_mode.value,
        username=share.username,
        read_only=share.read_only,
        browseable=share.browseable,
        enabled=share.enabled,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("")
async def list_shares(request: Request) -> dict:
    """List all configured shares."""
    config = request.app.state.config
    shares = config.list_shares()
    return {
        "shares": [_share_to_response(s) for s in shares],
        "count": len(shares),
    }


@router.get("/{drive_uuid}")
async def get_share(drive_uuid: str, request: Request) -> ShareResponse:
    """Get share configuration for a specific drive."""
    config = request.app.state.config
    share = config.get_share(drive_uuid)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found for this drive")
    return _share_to_response(share)


@router.post("", status_code=201)
async def create_share(body: ShareCreateRequest, request: Request) -> ShareResponse:
    """Create a new SMB share for a drive."""
    config = request.app.state.config
    smb = request.app.state.smb_manager

    # Check if share already exists for this drive
    if config.get_share(body.drive_uuid):
        raise HTTPException(status_code=409, detail="Share already exists for this drive")

    # Build share config
    share = ShareConfig(
        drive_uuid=body.drive_uuid,
        drive_label=body.drive_label,
        share_name=body.share_name,
        mount_point=body.mount_point,
        auth_mode=body.auth_mode,
        username=body.username,
        password=body.password,
        read_only=body.read_only,
        browseable=body.browseable,
        enabled=True,
    )

    # Apply to Samba
    if not smb.add_share(share):
        raise HTTPException(status_code=500, detail="Failed to configure Samba share")

    # Save to config
    config.add_share(share)

    return _share_to_response(share)


@router.put("/{drive_uuid}")
async def update_share(drive_uuid: str, body: ShareUpdateRequest, request: Request) -> ShareResponse:
    """Update an existing share configuration."""
    config = request.app.state.config
    smb = request.app.state.smb_manager

    old_share = config.get_share(drive_uuid)
    if old_share is None:
        raise HTTPException(status_code=404, detail="Share not found for this drive")

    # Build update dict (only non-None fields)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return _share_to_response(old_share)

    # Apply update to config
    updated = config.update_share(drive_uuid, **updates)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update share")

    # Apply to Samba
    if not smb.update_share(old_share, updated):
        raise HTTPException(status_code=500, detail="Failed to reconfigure Samba")

    return _share_to_response(updated)


@router.delete("/{drive_uuid}")
async def delete_share(drive_uuid: str, request: Request) -> dict:
    """Remove a share configuration."""
    config = request.app.state.config
    smb = request.app.state.smb_manager

    share = config.get_share(drive_uuid)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found for this drive")

    # Remove from Samba
    if not smb.remove_share(share):
        raise HTTPException(status_code=500, detail="Failed to remove Samba share")

    # Remove from config
    config.remove_share(drive_uuid)

    return {"detail": "Share removed", "drive_uuid": drive_uuid}
