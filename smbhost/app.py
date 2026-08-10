"""FastAPI application factory for SMBHost.

Creates the web application with all API routes, WebSocket support,
static file serving, and lifecycle management for background services.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from smbhost.config import ConfigManager
from smbhost.drive_detector import DriveDetector, DriveInfo
from smbhost.smb_manager import SMBManager

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _sync_configured_share_on_drive_added(
    config: ConfigManager,
    smb_manager: SMBManager,
    drive: DriveInfo,
) -> None:
    """Re-apply a saved share when its drive is detected again."""
    share = config.get_share(drive.uuid)
    if share is None or not share.enabled:
        return

    if not drive.mount_point:
        logger.info("Skipping auto-enable for %s: drive is not mounted", drive.uuid)
        return

    if share.mount_point != drive.mount_point or share.drive_label != drive.display_name:
        previous_share = share.model_copy(deep=True)
        updated_share = config.update_share(
            drive.uuid,
            mount_point=drive.mount_point,
            drive_label=drive.display_name,
        )
        if updated_share is None:
            logger.warning("Failed to refresh saved share metadata for drive %s", drive.uuid)
            return

        if not smb_manager.update_share(previous_share, updated_share):
            config.add_share(previous_share)
            logger.warning("Failed to auto-enable configured share for drive %s", drive.uuid)
        return

    if not smb_manager.add_share(share):
        logger.warning("Failed to auto-enable configured share for drive %s", drive.uuid)


# ── WebSocket Connection Manager ─────────────────────────────────────────────


class ConnectionManager:
    """Manages WebSocket connections for broadcasting drive events."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, event: str, data: dict) -> None:
        """Broadcast a JSON event to all connected clients."""
        import json as _json
        payload = _json.dumps({"event": event, "data": data})
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


# ── Application Factory ──────────────────────────────────────────────────────


def create_app(config: ConfigManager) -> FastAPI:
    """Build and return the FastAPI application."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR)) if TEMPLATES_DIR.exists() else None
    ws_manager = ConnectionManager()
    drive_detector = DriveDetector()
    smb_manager = SMBManager(config)

    # Share state for dependency injection via app.state
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Application lifespan: start/stop background services."""
        # Start drive monitoring in the background
        monitor_task = asyncio.create_task(drive_detector.start_monitoring())
        app.state.monitor_task = monitor_task

        # Write initial Samba config on startup
        app.state.smb_manager = smb_manager
        try:
            smb_manager.write_config()
            smb_manager.reload_service()
        except Exception:
            logger.exception("Failed to write initial Samba config")

        logger.info("SMBHost application started")
        yield

        # Shutdown
        await drive_detector.stop_monitoring()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("SMBHost application stopped")

    app = FastAPI(
        title="SMBHost",
        description="Auto-detect drives and expose them as SMB shares",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store shared state
    app.state.config = config
    app.state.smb_manager = smb_manager
    app.state.drive_detector = drive_detector
    app.state.ws_manager = ws_manager
    app.state.templates = templates

    # ── Register drive event callbacks ───────────────────────────────────
    def on_drive_event(action: str, drive: DriveInfo) -> None:
        """Callback invoked when a drive is added or removed."""
        if action == "added":
            _sync_configured_share_on_drive_added(config, smb_manager, drive)

        data = {
            "action": action,
            "uuid": drive.uuid,
            "label": drive.display_name,
            "device_path": drive.device_path,
            "size_bytes": drive.size_bytes,
            "fstype": drive.fstype,
            "mount_point": drive.mount_point,
            "is_mounted": drive.is_mounted,
            "model": drive.model,
            "removable": drive.removable,
        }
        # Schedule broadcast on the event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast(f"drive_{action}", data))
        except RuntimeError:
            pass  # No running loop (e.g., during tests)

    drive_detector.on_event(on_drive_event)

    # ── API Routes ───────────────────────────────────────────────────────
    from smbhost.api import drives as drives_api
    from smbhost.api import shares as shares_api
    from smbhost.api import system as system_api

    app.include_router(drives_api.router)
    app.include_router(shares_api.router)
    app.include_router(system_api.router)

    # ── WebSocket Route ──────────────────────────────────────────────────
    @app.websocket("/api/drives/events")
    async def drives_events_ws(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        try:
            # Keep connection alive, waiting for messages (client may send pings)
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)
        except Exception:
            ws_manager.disconnect(ws)

    # ── Page Routes ──────────────────────────────────────────────────────
    if templates is not None:
        from fastapi import Request

        _index_template = templates.env.get_template("index.html")

        def _render_page(request: Request) -> HTMLResponse:
            return HTMLResponse(
                _index_template.render(request=request, version="0.1.0"),
            )

        @app.get("/")
        async def index(request: Request):
            return _render_page(request)

        @app.get("/drives")
        async def drives_page(request: Request):
            return _render_page(request)

        @app.get("/settings")
        async def settings_page(request: Request):
            return _render_page(request)

        @app.get("/system")
        async def system_page(request: Request):
            return _render_page(request)

    # ── Static Files ─────────────────────────────────────────────────────
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
