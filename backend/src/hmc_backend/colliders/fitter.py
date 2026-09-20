"""AnatomicalCharacterFitter: fused landmarks -> validated collider set.

Implements the :class:`hmc_backend.vision.protocols.CharacterFitter` protocol.
Per paired capture it:

1. fuses per-view detections into canonical 3D landmarks with provenance
   (:mod:`hmc_backend.vision.landmarks`);
2. assigns merged-cloud points to body segments once and fits the eight limb
   capsules, two hand OBBs, two foot OBBs, chest/pelvis OBBs and the head
   sphere from those points;
3. validates every collider (violations disable, never pad) and completes the
   stable required set;
4. updates :class:`SubjectDimensions` conservatively from colliders that
   survived validation, so later occluded poses can fall back to this subject's
   measured radii/widths rather than global defaults.

The fitter keeps the last :class:`FitReport` for the debug JSON; nothing in
the report is serialized to the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.colliders.fit_capsule import fit_all_limbs
from hmc_backend.colliders.fit_common import FitConfig, FitOutcome
from hmc_backend.colliders.fit_foot import fit_foot
from hmc_backend.colliders.fit_hand import fit_hand
from hmc_backend.colliders.fit_torso import fit_chest, fit_head, fit_pelvis
from hmc_backend.colliders.models import DisabledCollider, FittedCollider, to_transport
from hmc_backend.colliders.subject import SubjectDimensions
from hmc_backend.colliders.typed_validate import (
    ValidationConfig,
    coverage_report,
    finalize_collider_set,
)
from hmc_backend.contracts.internal import (
    CameraCalibration,
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    ViewDetection,
)
from hmc_backend.vision.landmarks import FusionConfig, FusionReport, ViewInput, fuse_landmarks
from hmc_backend.vision.model_mapping import SIDES, Side


@dataclass(slots=True)
class FitReport:
    fusion: FusionReport | None = None
    colliders: dict[str, dict] = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    subject: dict = field(default_factory=dict)
    point_count: int = 0
    warnings: list[str] = field(default_factory=list)


class AnatomicalCharacterFitter:
    def __init__(
        self,
        rig: RigCalibration,
        *,
        fusion: FusionConfig | None = None,
        fit: FitConfig | None = None,
        validation: ValidationConfig | None = None,
        subject: SubjectDimensions | None = None,
        planted_feet: dict[Side, bool] | None = None,
        learn_subject: bool = True,
    ) -> None:
        self._rig = rig
        self._fusion_cfg = fusion or FusionConfig()
        self._fit_cfg = fit or FitConfig()
        self._validation_cfg = validation or ValidationConfig()
        self._subject = subject or SubjectDimensions()
        self._planted = planted_feet or {}
        self._learn_subject = learn_subject
        self._last_report = FitReport()
        self._last_typed: tuple[FittedCollider, ...] = ()

    # --- state -----------------------------------------------------------------

    @property
    def subject(self) -> SubjectDimensions:
        return self._subject

    @property
    def last_report(self) -> FitReport:
        return self._last_report

    @property
    def last_typed_colliders(self) -> tuple[FittedCollider, ...]:
        """Typed colliders from the most recent fit (for overlays/tests)."""
        return self._last_typed

    def set_planted(self, side: Side, planted: bool | None) -> None:
        """Explicit foot classification (e.g. from a calibration pose); ``None`` returns to automatic."""
        if planted is None:
            self._planted.pop(side, None)
        else:
            self._planted[side] = planted

    # --- protocol ----------------------------------------------------------------

    def set_view_calibrations(self, calibrations):
        self._view_calibrations = calibrations

    def set_view_warps(self, warps):
        self._view_warps = warps

    def fit_character(
        self,
        pair: PairedFrames,
        detections: dict[str, ViewDetection],
        cloud: ColoredPointCloud,
        calibration: CameraCalibration,
    ) -> FittedCharacter:
        del calibration  # per-view calibrations come from the rig; the front camera is the stage reference
        report = FitReport(point_count=cloud.count)
        views = [
            ViewInput(frame, detections[frame.device_id], getattr(self, "_view_calibrations", self._rig.cameras)[frame.device_id],
                      getattr(self, "_view_warps", {}).get(frame.device_id))
            for frame in (pair.first, pair.second)
            if frame.device_id in detections
        ]
        fusion = fuse_landmarks(views, self._fusion_cfg)
        report.fusion = fusion.report
        lms = fusion.by_name()

        xyz = np.asarray(cloud.xyz_stage_m, dtype=np.float32).reshape(-1, 3)
        if xyz.shape[0] == 0:
            report.warnings.append("empty_cloud")

        cfg, subject = self._fit_cfg, self._subject
        outcomes, segments, assignment = fit_all_limbs(lms, xyz, subject, cfg)
        for side in SIDES:
            outcomes.append(fit_hand(side, lms, xyz, assignment, segments, subject, cfg))
            outcomes.append(fit_foot(side, lms, xyz, assignment, segments, subject, cfg, planted=self._planted.get(side)))
        outcomes.append(fit_head(lms, xyz, assignment, segments, subject, cfg))
        outcomes.append(fit_chest(lms, xyz, assignment, segments, subject, cfg))
        outcomes.append(fit_pelvis(lms, xyz, assignment, segments, subject, cfg))

        typed = finalize_collider_set([o.collider for o in outcomes], self._validation_cfg)
        self._last_typed = typed
        by_id = {c.id: c for c in typed}
        self._apply_subject_updates(outcomes, by_id)

        for o in outcomes:
            final = by_id.get(o.collider.id)
            entry = dict(o.report)
            entry["fit_source"] = final.fit_source.value if final is not None else "dropped"
            if isinstance(final, DisabledCollider):
                entry["disabled_reason"] = final.reason
            report.colliders[o.collider.id] = entry
        report.coverage = coverage_report(typed)
        report.subject = self._subject.report()
        report.warnings.extend(fusion.report.warnings)
        self._last_report = report

        return FittedCharacter(landmarks=fusion.landmarks, colliders=tuple(to_transport(c) for c in typed))

    # --- helpers -----------------------------------------------------------------

    def _apply_subject_updates(self, outcomes: list[FitOutcome], by_id: dict[str, FittedCollider]) -> None:
        if not self._learn_subject:
            return
        subject = self._subject
        for o in outcomes:
            final = by_id.get(o.collider.id)
            if final is None or isinstance(final, DisabledCollider):
                continue  # a collider that failed validation must not teach the subject
            for name, side, est in o.subject_updates:
                subject = subject.updated(name, est, side, min_samples=self._fit_cfg.subject_min_samples)
        self._subject = subject
