"""Flask web server for the phone dashboard.

Serves a mobile-friendly dashboard that:
  1. Reads GPS from the phone's browser (Geolocation API)
  2. Sends position updates to the backend
  3. Displays current speed, speed limit, Vmax status
  4. Provides controls for enable/disable, emergency override, offset
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from flask import Flask, jsonify, render_template, request

if TYPE_CHECKING:
    from slower.config import Config
    from slower.gps.provider import GPSProvider
    from slower.limiter.controller import SpeedLimiterController

logger = logging.getLogger(__name__)


def create_app(
    config: Config,
    controller: SpeedLimiterController,
    gps: GPSProvider,
) -> Flask:
    """Create and configure the Flask application."""

    app = Flask(
        __name__,
        template_folder=str(__file__).replace("server.py", "templates"),
        static_folder=str(__file__).replace("server.py", "static"),
    )
    app.config["SECRET_KEY"] = "slower-local-only"

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/gps", methods=["POST"])
    def update_gps():
        """Receive GPS position update from phone browser."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        try:
            pos = gps.update(
                lat=float(data["latitude"]),
                lon=float(data["longitude"]),
                speed_mps=data.get("speed"),
                heading=data.get("heading"),
                accuracy_m=float(data.get("accuracy", 50)),
            )
            return jsonify({
                "ok": True,
                "position": {
                    "lat": pos.latitude,
                    "lon": pos.longitude,
                    "speed_mph": pos.speed_mph,
                    "accuracy_m": pos.accuracy_m,
                },
            })
        except (KeyError, ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/status")
    def get_status():
        """Get current limiter state for dashboard."""
        s = controller.state
        return jsonify({
            "running": s.running,
            "active_mode": s.active_mode,
            "gps_connected": s.gps_connected,
            "dme_connected": s.dme_connected,
            "current_speed_mph": round(s.current_speed_mph, 1) if s.current_speed_mph else None,
            "speed_limit_mph": s.current_speed_limit_mph,
            "target_vmax_mph": s.target_vmax_mph,
            "actual_vmax_kmh": s.actual_vmax_kmh,
            "road_name": s.road_name,
            "speed_limit_source": s.speed_limit_source,
            "emergency_override": s.emergency_override,
            "offset_mph": s.offset_mph,
            "last_error": s.last_error,
            "messages": s.status_messages[-10:],
        })

    @app.route("/api/control/mode", methods=["POST"])
    def set_mode():
        """Toggle active/monitor mode."""
        data = request.get_json()
        active = bool(data.get("active", False))
        controller.set_active_mode(active)
        return jsonify({"ok": True, "active_mode": active})

    @app.route("/api/control/offset", methods=["POST"])
    def set_offset():
        """Set speed offset above limit."""
        data = request.get_json()
        offset = int(data.get("offset_mph", 5))
        controller.set_offset(offset)
        return jsonify({"ok": True, "offset_mph": controller.state.offset_mph})

    @app.route("/api/control/emergency", methods=["POST"])
    def emergency():
        """Toggle emergency override."""
        data = request.get_json()
        active = bool(data.get("active", False))
        controller.emergency_override(active)
        return jsonify({"ok": True, "emergency_override": active})

    @app.route("/api/control/start", methods=["POST"])
    def start_limiter():
        """Start the limiter control loop."""
        controller.start()
        return jsonify({"ok": True})

    @app.route("/api/control/stop", methods=["POST"])
    def stop_limiter():
        """Stop the limiter control loop."""
        controller.stop()
        return jsonify({"ok": True})

    return app
