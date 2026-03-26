"""Slower - GPS-based speed limiter for 2006 BMW 325xi.

Main entry point. Initializes all components and starts the web dashboard.

Usage:
    slower                          # Run with config.yaml
    slower --config /path/to.yaml   # Custom config
    slower --monitor                # Force monitor-only mode (no DME writes)
    slower --no-dme                 # Skip DME connection (GPS + dashboard only)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from slower.config import load_config


def setup_logging(level: str, log_file: str) -> None:
    """Configure logging for all modules."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slower - GPS speed limiter for BMW E90 325xi"
    )
    parser.add_argument("--config", "-c", help="Path to config.yaml")
    parser.add_argument(
        "--monitor", action="store_true",
        help="Force monitor-only mode (no DME write commands)",
    )
    parser.add_argument(
        "--no-dme", action="store_true",
        help="Skip DME connection entirely (GPS + dashboard only)",
    )
    parser.add_argument(
        "--port", type=int,
        help="Override web dashboard port",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)

    logger = logging.getLogger("slower")
    logger.info("=== Slower v0.1.0 - BMW GPS Speed Limiter ===")
    logger.info("Vehicle: E90 325xi (N52 / %s DME)", config.vehicle.dme_type)

    if args.monitor:
        config.limiter.active = False
    if args.port:
        config.web.port = args.port

    # Initialize GPS provider
    from slower.gps.provider import GPSProvider
    gps = GPSProvider()

    # Initialize DME connection (unless --no-dme)
    dme = None
    if not args.no_dme:
        from slower.bmw.connection import KDCANConnection
        from slower.bmw.e90_dme import E90DME
        from slower.bmw.uds import UDSClient

        try:
            conn = KDCANConnection(config.cable)
            conn.connect()
            uds = UDSClient(conn)
            dme = E90DME(uds)

            if not dme.initialize():
                logger.error("Failed to initialize DME - falling back to monitor mode")
                config.limiter.active = False
                dme = None
            else:
                status = dme.get_status()
                logger.info(
                    "DME connected - Current Vmax: %s km/h, Active: %s",
                    status.vmax_speed_kmh, status.vmax_active,
                )
        except Exception as e:
            logger.error("DME connection failed: %s", e)
            logger.info("Continuing in GPS + dashboard only mode")
            dme = None
    else:
        logger.info("DME connection skipped (--no-dme)")

    # Initialize controller
    from slower.limiter.controller import SpeedLimiterController
    controller = SpeedLimiterController(config, dme, gps)

    # Create web app
    from slower.web.server import create_app
    app = create_app(config, controller, gps)

    # Handle shutdown gracefully
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        controller.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Print access instructions
    mode = "ACTIVE" if config.limiter.active else "MONITOR"
    dme_status = "Connected" if dme else "Not connected"
    logger.info("Mode: %s | DME: %s", mode, dme_status)
    logger.info(
        "Dashboard: http://localhost:%d (open on your phone)", config.web.port,
    )
    logger.info("Press Ctrl+C to stop")

    # Start Flask (blocking)
    app.run(
        host=config.web.host,
        port=config.web.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
