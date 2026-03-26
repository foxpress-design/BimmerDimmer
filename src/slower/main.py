"""Slower - GPS-based speed limiter for 2006 BMW 325xi.

Main entry point. Initializes all components and starts the web dashboard.

Usage:
    slower                          # Run with config.yaml
    slower --config /path/to.yaml   # Custom config
    slower --monitor                # Force monitor-only mode (no DME writes)
    slower --no-dme                 # Skip DME connection (GPS + dashboard only)
    slower --reset                  # Reset DME Vmax to factory default and exit
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
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset DME Vmax to factory default and exit (recovery mode)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)

    logger = logging.getLogger("slower")
    logger.info("=== BimmerDimmer v0.3.0 - BMW GPS Speed Limiter ===")
    logger.info("Vehicle: E90 325xi (N52 / %s DME)", config.vehicle.dme_type)

    if args.monitor:
        config.limiter.active = False
    if args.port:
        config.web.port = args.port

    # Handle --reset: connect to DME, reset Vmax, and exit
    if args.reset:
        from slower.bmw.connection import KDCANConnection
        from slower.bmw.e90_dme import E90DME
        from slower.bmw.recovery import reset_vmax
        from slower.bmw.uds import UDSClient

        logger.info("Reset mode: connecting to DME to restore factory Vmax...")
        try:
            conn = KDCANConnection(config.cable)
            conn.connect()
            uds = UDSClient(conn)
            dme = E90DME(uds)
            reset_vmax(dme)
            logger.info("Reset complete.")
        except Exception as e:
            logger.error("Reset failed: %s", e)
            sys.exit(1)
        sys.exit(0)

    # Initialize GPS provider
    from slower.gps.provider import GPSProvider
    gps = GPSProvider()

    # Initialize DME connection (unless --no-dme)
    dme = None
    if not args.no_dme:
        from slower.bmw.connection import KDCANConnection
        from slower.bmw.e90_dme import E90DME
        from slower.bmw.recovery import check_stale_vmax
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
                # Startup recovery: check for stale Vmax from a previous crash
                if check_stale_vmax(dme):
                    logger.warning(
                        "Stale Vmax detected from previous session - disabling as safety measure"
                    )
                    dme.disable_vmax()
        except Exception as e:
            logger.error("DME connection failed: %s", e)
            logger.info("Continuing in GPS + dashboard only mode")
            dme = None
    else:
        logger.info("DME connection skipped (--no-dme)")

    # Initialize transports
    from slower.bmw.safety import ConnectionMonitor
    from slower.transport.wifi import WiFiTransport

    connection_monitor = ConnectionMonitor()
    wifi_transport = WiFiTransport()
    wifi_transport.start(gps)
    connection_monitor.add_gps_transport("wifi")

    if config.transports.ble:
        try:
            from slower.transport.ble import BLETransport
            ble_transport = BLETransport()
            ble_transport.start(gps)
            connection_monitor.add_gps_transport("ble")
        except Exception as e:
            logger.warning("BLE transport unavailable: %s", e)

    if config.transports.spp:
        try:
            from slower.transport.spp import SPPTransport
            spp_transport = SPPTransport(channel=config.transports.spp_channel)
            spp_transport.start(gps)
            connection_monitor.add_gps_transport("spp")
        except Exception as e:
            logger.warning("SPP transport unavailable: %s", e)

    # Initialize controller
    from slower.limiter.controller import SpeedLimiterController
    controller = SpeedLimiterController(config, dme, gps, connection_monitor=connection_monitor)

    # Create web app
    from slower.web.server import create_app
    app = create_app(config, controller, gps, wifi_transport=wifi_transport)

    # Handle shutdown gracefully
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        controller.stop()
        from slower.bmw.watchdog import remove_heartbeat
        remove_heartbeat()
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
