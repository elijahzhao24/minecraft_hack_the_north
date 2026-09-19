"""Process configuration.

``Settings`` reads ``HMC_``-prefixed environment variables layered over
checked-in, non-secret defaults. Secrets (e.g. the Sentry DSN) come only from
the environment and are never echoed by ``/health``.
"""

from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend runtime settings, validated at startup."""

    model_config = SettingsConfigDict(
        env_prefix="HMC_",
        env_file=".env",
        extra="ignore",
    )

    # --- Networking -------------------------------------------------------
    bind_host: str = "0.0.0.0"
    port: int = 8000

    # --- Capture identities ----------------------------------------------
    expected_device_ids: tuple[str, ...] = ("front-phone", "side-phone")
    front_device_id: str = "front-phone"
    min_capture_devices: int = 1

    # --- Allocation / framing guards -------------------------------------
    max_rgbd_bytes: int = 16_777_216  # 16 MiB
    max_character_bytes: int = 8_388_608  # 8 MiB
    max_header_bytes: int = 65_536

    # --- Queueing / backpressure -----------------------------------------
    device_queue_size: int = 2
    subscriber_queue_size: int = 1

    # --- Pairing / clock budgets -----------------------------------------
    pair_skew_limit_ms: float = 50.0
    clock_uncertainty_limit_ms: float = 20.0
    hello_deadline_s: float = 10.0
    clock_probe_interval_s: float = 2.0
    live_target_fps: float = 3.0
    live_capture_lead_ms: float = 40.0
    live_pair_timeout_ms: float = 300.0
    live_clock_samples: int = 3
    live_max_points: int = 20_000
    live_voxel_size_m: float = 0.025
    person_mask_threshold: float = 0.5
    require_real_vision: bool = False

    # --- Reconstruction tunables -----------------------------------------
    voxel_size_m: float = 0.015
    max_points: int = 50_000

    # Hard spatial crop in stage meters (rig-specific; generous defaults).
    stage_min_x_m: float = -1.5
    stage_max_x_m: float = 1.5
    stage_min_y_m: float = 0.0
    stage_max_y_m: float = 2.5
    stage_min_z_m: float = -1.5
    stage_max_z_m: float = 1.5

    depth_min_m: float = 0.2
    depth_max_m: float = 5.0
    confidence_min: int = 1  # ARKit confidence 0/1/2; accept >= this

    # --- Paths ------------------------------------------------------------
    calibration_path: str = "data/calibration.json"
    recording_root: str = "data/recordings"
    model_manifest_path: str = "models/manifest.json"
    pose_model_path: str | None = None
    hand_model_path: str | None = None

    # --- Observability ----------------------------------------------------
    sentry_dsn: str | None = None
    environment: str = "development"
    release: str = "hmc-backend@0.1.0"
    traces_sample_rate: float = 1.0

    @field_validator("expected_device_ids", mode="before")
    @classmethod
    def _split_device_ids(cls, value: object) -> object:
        """Allow ``HMC_EXPECTED_DEVICE_IDS=front-phone,side-phone``."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("expected_device_ids")
    @classmethod
    def _require_supported_devices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 2:
            raise ValueError("expected_device_ids must contain one or two device IDs")
        if len(set(value)) != len(value):
            raise ValueError("expected_device_ids must be unique")
        return value

    @model_validator(mode="after")
    def _validate_capture_device_count(self) -> Settings:
        if not 1 <= self.min_capture_devices <= len(self.expected_device_ids):
            raise ValueError("min_capture_devices must be between 1 and expected device count")
        if self.front_device_id not in self.expected_device_ids:
            raise ValueError("front_device_id must be one of expected_device_ids")
        return self


def load_settings() -> Settings:
    """Instantiate settings from the environment (call once at startup)."""
    return Settings()
