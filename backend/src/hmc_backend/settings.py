"""Process configuration.

``Settings`` reads ``HMC_``-prefixed environment variables layered over
checked-in, non-secret defaults. Secrets (e.g. the Sentry DSN) come only from
the environment and are never echoed by ``/health``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    pair_skew_limit_ms: float = 80.0
    clock_uncertainty_limit_ms: float = 100.0
    hello_deadline_s: float = 10.0
    clock_probe_interval_s: float = 2.0
    # Backend-driven live capture: requests per second sent to both phones.
    live_rate_hz: float = 8.0

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
    max_range_m: float = Field(default=5.0, gt=0, le=5.0)
    confidence_min: int = 1  # ARKit confidence 0/1/2; accept >= this
    # Drop depth samples whose depth differs from any 4-neighbour by more than
    # this: LiDAR "flying pixels" along silhouette edges that otherwise render
    # as a halo of stray points. 0 disables the filter.
    depth_edge_max_step_m: float = 0.05
    # Single-phone operation: name the one real device. The other expected
    # device becomes virtual: the backend fabricates an empty view for it so
    # pairing and the rest of the pipeline run unchanged, and a phone trying to
    # connect under that id is refused.
    single_device: str | None = None
    # Warn on CharacterFrame quality when the two views' clouds sit this far
    # apart (symmetric median NN). A healthy opposed rig sits near the body's
    # surface thickness (~0.15-0.25m); a biased camera solve shows up above it.
    view_alignment_warn_m: float = Field(default=0.3, gt=0, le=2.0)
    # Forced visual assembly for physical opposing phones; never persisted as calibration.
    opposing_body_merge: bool = True
    body_merge_min_thickness_m: float = Field(default=0.12, gt=0, le=0.4)
    body_merge_seam_overlap_m: float = Field(default=0.01, ge=0, le=0.03)
    # Take pitch/roll from each frame's ARKit gravity-aligned pose instead of
    # trusting the nominal camera pose, and unproject with the lens intrinsics
    # the phone reports rather than the rig file's nominal K.
    gravity_align: bool = True
    use_frame_intrinsics: bool = True
    # Legacy path retained for configuration compatibility; never loaded by live capture.
    registration_path: str = "data/registration.json"
    simulation_mode: bool = False
    calibration_capture_hz: float = Field(default=3.0, gt=0, le=10)
    calibration_timeout_s: float = Field(default=30.0, gt=0, le=120)

    # Real inference is opt-in so fixtures and CI do not require model weights.
    vision_backend: Literal["fake", "mediapipe"] = "fake"
    collider_backend: Literal["fake", "anatomical"] = "fake"
    learn_subject_dimensions: bool = True
    debug_artifacts_dir: str | None = None

    # --- Paths ------------------------------------------------------------
    calibration_path: str = "data/calibration.json"
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

    @model_validator(mode="after")
    def _validate_depth_bounds(self):
        import math
        if not (math.isfinite(self.depth_min_m) and math.isfinite(self.depth_max_m)
                and 0 <= self.depth_min_m < self.depth_max_m
                and self.depth_min_m < self.max_range_m):
            raise ValueError("depth bounds must be finite, ordered and below max_range_m")
        return self


def load_settings() -> Settings:
    """Instantiate settings from the environment (call once at startup)."""
    return Settings()
