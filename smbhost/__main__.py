"""SMBHost CLI entry point.

Usage:
    smbhost [--bind ADDR] [--port PORT] [--debug]
    smbhost --install
    smbhost --uninstall
    smbhost --version
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from smbhost import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="smbhost",
        description="SMBHost — Auto-detect drives and expose them as SMB shares",
    )
    parser.add_argument(
        "--bind", default="127.0.0.1",
        help="Web UI bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Web UI port (default: 8080)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Install systemd service and exit",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Uninstall systemd service and exit",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"smbhost {__version__}",
    )
    parser.add_argument(
        "--generate-smb-conf", action="store_true",
        help="Generate smb.conf to stdout and exit",
    )
    parser.add_argument(
        "--config-dir", default="/etc/smbhost",
        help="Configuration directory (default: /etc/smbhost)",
    )
    return parser.parse_args(argv)


def setup_logging(debug: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    setup_logging(args.debug)

    # ── Install / Uninstall modes ──────────────────────────────────────
    if args.install:
        from smbhost.systemd_manager import SystemdManager
        if SystemdManager.is_installed():
            print("SMBHost service is already installed.")
            if SystemdManager.start_service():
                print("Service started.")
            return 0
        if SystemdManager.install_service():
            SystemdManager.enable_service()
            SystemdManager.start_service()
            print("SMBHost service installed and started.")
            print(f"Web UI: http://{args.bind}:{args.port}")
            return 0
        print("ERROR: Failed to install service. Run as root.", file=sys.stderr)
        return 1

    if args.uninstall:
        from smbhost.systemd_manager import SystemdManager
        if SystemdManager.uninstall_service():
            print("SMBHost service uninstalled.")
            return 0
        print("ERROR: Failed to uninstall service. Run as root.", file=sys.stderr)
        return 1

    # ── Generate smb.conf mode ─────────────────────────────────────────
    if args.generate_smb_conf:
        from smbhost.config import ConfigManager
        cfg = ConfigManager()
        print(cfg.generate_smb_conf())
        return 0

    # ── Run server ─────────────────────────────────────────────────────
    return asyncio.run(run_server(args))


async def run_server(args: argparse.Namespace) -> int:
    """Start the FastAPI web server with all services."""
    import uvicorn

    from smbhost.app import create_app
    from smbhost.config import ConfigManager

    # Load configuration
    config = ConfigManager()

    # Override bind/port from CLI args
    bind = args.bind or config.global_config.web_bind
    port = args.port or config.global_config.web_port

    # Create the application
    app = create_app(config)

    # Build the uvicorn config
    log_level = "debug" if args.debug else config.global_config.log_level

    print(f"SMBHost v{__version__} starting...")
    print(f"Web UI: http://{bind}:{port}")
    print(f"Config: {config._path}")

    server_config = uvicorn.Config(
        app,
        host=bind,
        port=port,
        log_level=log_level,
        access_log=args.debug,
    )
    server = uvicorn.Server(server_config)

    try:
        await server.serve()
    except KeyboardInterrupt:
        print("\nShutting down...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
