"""Configuration loader for Slower."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_SEARCH_PATHS = [
    Path("config.yaml"),
    Path("config.yml"),
    Path.home() / ".config" / "slower" / "config.yaml",
]


@dataclass
class CableConfig:
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    timeout: float = 0.5


@dataclass
class VehicleConfig:
    dme_type: str = "MSV70"
    dme_request_id: int = 0x612
    dme_response_id: int = 0x612


@dataclass
class LimiterConfig:
    active: bool = False
    offset_mph: int = 5
    min_vmax_mph: int = 15
    max_vmax_mph: int = 155
    update_interval_sec: int = 3
    gps_loss_grace_sec: int = 10


@dataclass
class SpeedLimitsConfig:
    primary: str = "osm"
    google_api_key: str = ""
    cache_ttl_sec: int = 30
    search_radius_m: int = 50


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 5555
    local_only: bool = False


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = ""


@dataclass
class Config:
    cable: CableConfig = field(default_factory=CableConfig)
    vehicle: VehicleConfig = field(default_factory=VehicleConfig)
    limiter: LimiterConfig = field(default_factory=LimiterConfig)
    speed_limits: SpeedLimitsConfig = field(default_factory=SpeedLimitsConfig)
    web: WebConfig = field(default_factory=WebConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from YAML file.

    Searches config.yaml in CWD, then ~/.config/slower/config.yaml.
    Environment variables override: SLOWER_CABLE_PORT, SLOWER_GOOGLE_API_KEY, etc.
    """
    cfg = Config()

    # Find config file
    config_path = None
    if path:
        config_path = Path(path)
    else:
        for p in CONFIG_SEARCH_PATHS:
            if p.exists():
                config_path = p
                break

    if config_path and config_path.exists():
        logger.info("Loading config from %s", config_path)
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

        if "cable" in raw:
            cfg.cable = CableConfig(**raw["cable"])
        if "vehicle" in raw:
            v = raw["vehicle"]
            cfg.vehicle = VehicleConfig(
                dme_type=v.get("dme_type", "MSV70"),
                dme_request_id=int(v.get("dme_request_id", 0x612)),
                dme_response_id=int(v.get("dme_response_id", 0x612)),
            )
        if "limiter" in raw:
            cfg.limiter = LimiterConfig(**raw["limiter"])
        if "speed_limits" in raw:
            cfg.speed_limits = SpeedLimitsConfig(**raw["speed_limits"])
        if "web" in raw:
            cfg.web = WebConfig(**raw["web"])
        if "logging" in raw:
            cfg.logging = LoggingConfig(**raw["logging"])
    else:
        logger.warning("No config file found, using defaults")

    # Environment variable overrides
    if env_port := os.environ.get("SLOWER_CABLE_PORT"):
        cfg.cable.port = env_port
    if env_key := os.environ.get("SLOWER_GOOGLE_API_KEY"):
        cfg.speed_limits.google_api_key = env_key
    if os.environ.get("SLOWER_ACTIVE", "").lower() in ("1", "true", "yes"):
        cfg.limiter.active = True

    return cfg
