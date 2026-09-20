"""CharacterProcessor: run the CPU-heavy stages for one paired capture.

``process(pair)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import (
    CharacterFrame,
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    TraceContext,
    ViewDetection,
)
from hmc_backend.observability import log_event, span
from hmc_backend.observability.quality import QualityMonitor, measure_view_quality
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.reconstruction.registration import (
    gravity_aligned,
    register_yaw_translation,
    with_stage_correction,
)
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector

PostFitHook = Callable[
    [PairedFrames, dict[str, ViewDetection], ColoredPointCloud, FittedCharacter], None
]


class CharacterProcessor:
    """Owns the vision/reconstruction stages and the frame assembler."""

    def __init__(
        self,
        detector: ViewDetector,
        fitter: CharacterFitter,
        calibration: RigCalibration,
        assembler: FrameAssembler,
        crop: CropBounds,
        *,
        voxel_size_m: float,
        max_points: int,
        confidence_min: int,
        observability_delay_ms: int = 0,
        quality_monitor: QualityMonitor | None = None,
        post_fit_hook: PostFitHook | None = None,
        reference_device: str | None = None,
        gravity_align: bool = False,
        use_frame_intrinsics: bool = False,
    ) -> None:
        self._detector = detector
        self._fitter = fitter
        self._calibration = calibration
        self._assembler = assembler
        self._crop = crop
        self._voxel_size_m = voxel_size_m
        self._max_points = max_points
        self._confidence_min = confidence_min
        self._observability_delay_ms = max(0, observability_delay_ms)
        self._quality_monitor = quality_monitor or QualityMonitor()
        self._post_fit_hook = post_fit_hook
        self._reference_device = reference_device
        self._gravity_align = gravity_align
        self._use_frame_intrinsics = use_frame_intrinsics
        # device -> 4x4 stage->stage correction learned by registration.
        self._corrections: dict[str, NDArray[np.float64]] = {}
        self._register_requested = threading.Event()
        self.last_registration: dict | None = None
        self.view_diagnostics: dict[str, dict] = {}

    # --- rig registration -------------------------------------------------

    @property
    def assembler(self) -> FrameAssembler:
        return self._assembler

    @property
    def corrections(self) -> dict[str, NDArray[np.float64]]:
        return dict(self._corrections)

    def set_corrections(self, corrections: dict[str, NDArray[np.float64]]) -> None:
        self._corrections = {k: np.asarray(v, np.float64).reshape(4, 4) for k, v in corrections.items()}

    def request_registration(self) -> None:
        """Align the non-reference camera onto the reference on the next pair."""
        self._register_requested.set()

    def _effective_calibration(self, device_id: str, frame):
        calib = self._calibration.camera(device_id)
        if self._use_frame_intrinsics:
            calib = replace(calib, K_rgb=frame.K_rgb, rgb_size=(frame.rgb.shape[1], frame.rgb.shape[0]))
        if self._gravity_align and self._calibration.board is None:
            calib = gravity_aligned(calib, frame)
        return calib if self._calibration.board is not None else with_stage_correction(calib, self._corrections.get(device_id))

    def _register(self, clouds_by_device: dict) -> None:
        devices = list(clouds_by_device)
        ref = self._reference_device if self._reference_device in clouds_by_device else devices[0]
        others = [d for d in devices if d != ref]
        if not others:
            return
        target = clouds_by_device[ref].xyz_stage_m
        for dev in others:
            source = clouds_by_device[dev].xyz_stage_m
            # First pass recovers a coarse placement error and is tuned to
            # tolerate large gaps. Once a correction exists the views nearly
            # coincide, so refine with tight matching and no yaw seeding.
            refine = dev in self._corrections
            kwargs = (
                {"initial_yaws_deg": (0.0,), "voxel_m": 0.01, "max_pair_distance_m": 0.08, "keep_fraction": 0.8}
                if refine
                else {}
            )
            try:
                t_inc, rms, inliers = register_yaw_translation(source, target, **kwargs)
            except ValueError as exc:
                self.last_registration = {"ok": False, "device_id": dev, "error": str(exc)}
                log_event("warning", "rig_registration_failed", device_id=dev, error=str(exc))
                continue
            if not np.isfinite(rms) or inliers < 30 or rms > 0.05:
                self.last_registration = {"ok": False, "device_id": dev, "error": "poor registration quality"}
                continue
            prev = self._corrections.get(dev, np.eye(4))
            self._corrections[dev] = t_inc @ prev
            yaw_deg = float(np.degrees(np.arctan2(t_inc[0, 2], t_inc[0, 0])))
            self.last_registration = {
                "ok": True,
                "device_id": dev,
                "rms_m": round(rms, 4),
                "inliers": inliers,
                "yaw_deg": round(yaw_deg, 2),
                "shift_m": [round(float(v), 3) for v in t_inc[:3, 3]],
                "pass": "refine" if refine else "coarse",
            }
            log_event("info", "rig_registered", **self.last_registration)

    @property
    def detector(self) -> ViewDetector:
        return self._detector

    @property
    def fitter(self) -> CharacterFitter:
        return self._fitter

    def process(
        self,
        pair: PairedFrames,
        *,
        mode: str = "snapshot",
        trace: TraceContext | None = None,
    ) -> CharacterFrame:
        """Run all stages and return one validated CharacterFrame."""
        frames = {pair.first.device_id: pair.first, pair.second.device_id: pair.second}

        self.view_diagnostics = {}
        detections = {}
        clouds = []
        clouds_by_device = {}
        source_bit = 1
        view_quality = []
        source_ids = ",".join(
            str(frame.source_frame_id or frame.capture_id) for frame in (pair.first, pair.second)
        )
        with span(
            "hmc.calibration_fusion",
            "detect, calibrate, reconstruct, fuse and fit",
            fusion_id=str(pair.pair_id),
            source_frame_ids=source_ids,
        ) as fusion_span:
            if self._observability_delay_ms:
                # Explicit, opt-in demo hook. Never enabled by default.
                time.sleep(self._observability_delay_ms / 1000)

            for device_id in (pair.first.device_id, pair.second.device_id):
                frame = frames[device_id]
                calib = self._effective_calibration(device_id, frame)
                with span("hmc.detect", "existing person mask + landmarks", camera_id=device_id):
                    detection = self._detector.detect_view(frame)
                detections[device_id] = detection
                view_quality.append(
                    measure_view_quality(
                        frame, detection, confidence_min=self._confidence_min
                    )
                )
                diagnostics = self.view_diagnostics.setdefault(device_id, {})
                with span("hmc.reconstruct", "masked depth -> calibrated points", camera_id=device_id) as reconstruct_span:
                    cloud = reconstruct_view(
                        frame,
                        detection,
                        calib,
                        self._crop,
                        confidence_min=self._confidence_min,
                        source_bit=source_bit,
                        diagnostics=diagnostics,
                        use_frame_intrinsics=self._use_frame_intrinsics,
                    )
                    reconstruct_span.set_data("point_count", cloud.count)
                clouds.append(cloud)
                clouds_by_device[device_id] = cloud
                source_bit <<= 1

            if self._register_requested.is_set() and self._calibration.board is None:
                self._register_requested.clear()
                self._register(clouds_by_device)
                # Rebuild corrected views so this frame already uses the new registration.
                for i, device_id in enumerate((pair.first.device_id, pair.second.device_id)):
                    if device_id in self._corrections:
                        with span("hmc.reconstruct", "rebuild registered view", camera_id=device_id) as reconstruct_span:
                            clouds[i] = reconstruct_view(
                                frames[device_id],
                                detections[device_id],
                                self._effective_calibration(device_id, frames[device_id]),
                                self._crop,
                                confidence_min=self._confidence_min,
                                source_bit=1 << i,
                                use_frame_intrinsics=self._use_frame_intrinsics,
                            )
                            reconstruct_span.set_data("point_count", clouds[i].count)

            seed = _seed_from_pair(pair)
            with span("hmc.fuse", "merge, voxel downsample and cap") as merge_span:
                merged = merge_clouds(
                    clouds, voxel_size_m=self._voxel_size_m, max_points=self._max_points, seed=seed
                )
                merge_span.set_data("point_count", merged.count)

            # Fit against the front camera's calibration (registration reference).
            effective = {dev: self._effective_calibration(dev, frame) for dev, frame in frames.items()}
            if hasattr(self._fitter, "set_view_calibrations"):
                self._fitter.set_view_calibrations(effective)
            front_calib = effective[pair.first.device_id]
            with span("hmc.fit", "landmarks and colliders"):
                fitted = self._fitter.fit_character(pair, detections, merged, front_calib)

            if self._post_fit_hook is not None:
                try:
                    self._post_fit_hook(pair, detections, merged, fitted)
                except Exception as exc:  # noqa: BLE001 - diagnostics must never block publishing
                    log_event(
                        "warning",
                        "post_fit_hook_failed",
                        pair_id=str(pair.pair_id),
                        error=type(exc).__name__,
                    )

            warnings = ["empty_cloud"] if merged.count == 0 else []
            for i, (dev, diag) in enumerate(self.view_diagnostics.items()):
                diag["contribution_count"] = int(np.count_nonzero(merged.source_mask & (1 << i)))
                if diag["contribution_count"] == 0:
                    reason = next((key for key in ("valid_depth", "after_range", "after_confidence", "after_mask", "after_stage")
                                   if diag.get(key) == 0), "after_merge")
                    warnings.append(f"missing_view:{dev}:{reason}")
            with span("hmc.assemble", "immutable CharacterFrame"):
                result = self._assembler.assemble(
                    pair,
                    merged,
                    fitted,
                    mode=mode,
                    calibration_id=self._calibration.calibration_id,
                    trace=trace or TraceContext(),
                    extra_warnings=tuple(warnings),
                )
            fusion_span.set_data("frame_id", result.frame_id)
            fusion_span.set_data("point_count", result.quality.point_count)

        for quality in view_quality:
            self._quality_monitor.observe(
                frame_id=result.frame_id,
                fusion_id=pair.pair_id,
                calibration_id=self._calibration.calibration_id,
                quality=quality,
            )
        return result


def _seed_from_pair(pair: PairedFrames) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    return (int(pair.first.sequence) << 16) ^ int(pair.second.sequence) ^ _uuid_low(pair.pair_id)


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
