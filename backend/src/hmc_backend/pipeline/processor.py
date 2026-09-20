"""CharacterProcessor: run the CPU-heavy stages for one paired capture.

``process(pair)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

import threading
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import CharacterFrame, PairedFrames, TraceContext
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.observability import log_event
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.reconstruction.registration import (
    gravity_aligned,
    register_yaw_translation,
    with_stage_correction,
)
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector


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
        self._reference_device = reference_device
        self._gravity_align = gravity_align
        self._use_frame_intrinsics = use_frame_intrinsics
        # device -> 4x4 stage->stage correction learned by registration.
        self._corrections: dict[str, NDArray[np.float64]] = {}
        self._register_requested = threading.Event()
        self.last_registration: dict | None = None

    # --- rig registration -------------------------------------------------

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
        if self._gravity_align:
            calib = gravity_aligned(calib, frame)
        return with_stage_correction(calib, self._corrections.get(device_id))

    def _register(self, clouds_by_device: dict) -> None:
        devices = list(clouds_by_device)
        ref = self._reference_device if self._reference_device in clouds_by_device else devices[0]
        others = [d for d in devices if d != ref]
        if not others:
            return
        target = clouds_by_device[ref].xyz_stage_m
        for dev in others:
            source = clouds_by_device[dev].xyz_stage_m
            try:
                t_inc, rms, inliers = register_yaw_translation(source, target)
            except ValueError as exc:
                self.last_registration = {"ok": False, "device_id": dev, "error": str(exc)}
                log_event("warning", "rig_registration_failed", device_id=dev, error=str(exc))
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
            }
            log_event("info", "rig_registered", **self.last_registration)

    def process(self, pair: PairedFrames, *, mode: str = "snapshot") -> CharacterFrame:
        """Run all stages and return one validated CharacterFrame."""
        frames = {pair.first.device_id: pair.first, pair.second.device_id: pair.second}

        detections = {}
        clouds = []
        clouds_by_device = {}
        source_bit = 1
        for device_id in (pair.first.device_id, pair.second.device_id):
            frame = frames[device_id]
            calib = self._effective_calibration(device_id, frame)
            detection = self._detector.detect_view(frame)
            detections[device_id] = detection
            cloud = reconstruct_view(
                frame,
                detection,
                calib,
                self._crop,
                confidence_min=self._confidence_min,
                source_bit=source_bit,
                use_frame_intrinsics=self._use_frame_intrinsics,
            )
            clouds.append(cloud)
            clouds_by_device[device_id] = cloud
            source_bit <<= 1

        if self._register_requested.is_set():
            self._register_requested.clear()
            self._register(clouds_by_device)
            # Rebuild the corrected view so this very frame already merges.
            for i, device_id in enumerate((pair.first.device_id, pair.second.device_id)):
                if device_id in self._corrections:
                    clouds[i] = reconstruct_view(
                        frames[device_id],
                        detections[device_id],
                        self._effective_calibration(device_id, frames[device_id]),
                        self._crop,
                        confidence_min=self._confidence_min,
                        source_bit=1 << i,
                        use_frame_intrinsics=self._use_frame_intrinsics,
                    )

        seed = _seed_from_pair(pair)
        merged = merge_clouds(
            clouds, voxel_size_m=self._voxel_size_m, max_points=self._max_points, seed=seed
        )

        # Fit against the front camera's calibration (registration reference).
        front_calib = self._calibration.camera(pair.first.device_id)
        fitted = self._fitter.fit_character(pair, detections, merged, front_calib)

        warnings = ()
        if merged.count == 0:
            warnings = ("empty_cloud",)

        return self._assembler.assemble(
            pair,
            merged,
            fitted,
            mode=mode,
            calibration_id=self._calibration.calibration_id,
            trace=TraceContext(),
            extra_warnings=warnings,
        )


def _seed_from_pair(pair: PairedFrames) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    return (int(pair.first.sequence) << 16) ^ int(pair.second.sequence) ^ _uuid_low(pair.pair_id)


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
