"""Process configuration.

``Settings`` reads ``HMC_``-prefixed environment variables layered over
checked-in, non-secret defaults. Secrets (e.g. the Sentry DSN) come only from
the environment and are never echoed by ``/health``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
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

    # --- Allocation / framing guards -------------------------------------
    max_rgbd_bytes: int = 16_777_216  # 16 MiB
    max_character_bytes: int = 8_388_608  # 8 MiB
    max_header_bytes: int = 65_536

    # --- Queueing / backpressure -----------------------------------------
    device_queue_size: int = 2
    subscriber_queue_size: int = 1

    # --- Pairing / clock budgets -----------------------------------------
    pair_skew_limit_ms: float = 30.0
    clock_uncertainty_limit_ms: float = 100.0
    hello_deadline_s: float = 10.0
    clock_probe_interval_s: float = 2.0
    # Backend-driven live capture: requests per second sent to both phones.
    live_rate_hz: float = 8.0

    # --- Hacker Badge controller -----------------------------------------
    badge_controller_enabled: bool = False
    badge_controller_name: str = "HTN-Badge"
    badge_service_uuid: str = "7b1e1000-6f7a-4d19-9c4b-5a2c8f1e2026"
    badge_state_uuid: str = "7b1e1001-6f7a-4d19-9c4b-5a2c8f1e2026"
    badge_stale_timeout_ms: int = 250
    badge_scan_timeout_s: float = 5.0

    # --- Reconstruction tunables -----------------------------------------
    voxel_size_m: float = 0.006
    max_points: int = 80_000

    # Hard spatial crop in stage meters (rig-specific; generous defaults).
    stage_min_x_m: float = -2.5
    stage_max_x_m: float = 2.5
    stage_min_y_m: float = 0.0
    stage_max_y_m: float = 2.5
    stage_min_z_m: float = -2.5
    stage_max_z_m: float = 3.0

    depth_min_m: float = 0.2
    depth_max_m: float = 5.0
    confidence_min: int = 1  # ARKit confidence 0/1/2; accept >= this
    # Take pitch/roll from each frame's ARKit gravity-aligned pose instead of
    # trusting the nominal camera pose, and unproject with the lens intrinsics
    # the phone reports rather than the rig file's nominal K.
    gravity_align: bool = False
    use_frame_intrinsics: bool = True
    # --- Anchoring / acceptance ------------------------------------------
    calibration_max_position_error_m: float = 0.03
    calibration_max_reprojection_error_px: float = 3.0
    anchor_sample_count: int = 8
    anchor_timeout_s: float = 20.0

    # Real inference is opt-in so fixtures and CI do not require model weights.
    vision_backend: Literal["fake", "mediapipe"] = "fake"
    collider_backend: Literal["fake", "anatomical"] = "fake"
    learn_subject_dimensions: bool = True
    debug_artifacts_dir: str | None = None

    # --- Paths ------------------------------------------------------------
    calibration_path: str = "data/calibration.json"
    frame_tree_path: str = "data/frame_tree.json"
    recording_root: str = "data/recordings"
    model_manifest_path: str = "models/manifest.json"
    models_dir: str | None = None
    pose_model_path: str | None = None
    hand_model_path: str | None = None

    # --- Observability ----------------------------------------------------
    sentry_dsn: str | None = None
    environment: str = "development"
    release: str = "hmc-backend@0.1.0"
    traces_sample_rate: float = 0.2
    sentry_send_default_pii: bool = False
    # Opt-in demonstration hook; zero in every normal environment.
    observability_demo_delay_ms: int = 0

    @field_validator("expected_device_ids", mode="before")
    @classmethod
    def _split_device_ids(cls, value: object) -> object:
        """Allow ``HMC_EXPECTED_DEVICE_IDS=front-phone,side-phone``."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("expected_device_ids")
    @classmethod
    def _require_two_devices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != 2:
            raise ValueError("expected_device_ids must contain exactly two device IDs")
        if len(set(value)) != len(value):
            raise ValueError("expected_device_ids must be unique")
        return value

    @field_validator(
        "calibration_max_position_error_m",
        "calibration_max_reprojection_error_px",
        "anchor_timeout_s",
    )
    @classmethod
    def _require_positive_calibration_limits(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("calibration limits must be positive")
        return value

    @field_validator("anchor_sample_count")
    @classmethod
    def _require_enough_anchor_samples(cls, value: int) -> int:
        if value < 4:
            raise ValueError("anchor_sample_count must be at least 4")
        return value

    @field_validator("badge_stale_timeout_ms")
    @classmethod
    def _require_safe_badge_timeout(cls, value: int) -> int:
        if not 100 <= value <= 2_000:
            raise ValueError("badge_stale_timeout_ms must be between 100 and 2000")
        return value

    @field_validator("badge_scan_timeout_s")
    @classmethod
    def _require_positive_badge_scan_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("badge_scan_timeout_s must be positive")
        return value


def load_settings() -> Settings:
    """Instantiate settings from the environment (call once at startup)."""
    return Settings()
