"""Subject-specific body dimensions with labelled provenance.

Radii and widths fitted from a clean neutral calibration pose are retained per
calibration/subject session so later occluded poses can fall back to them
(``fit_source=subject_default``) instead of to anonymous global defaults
(``global_default``, always labelled and last resort).

Every value carries its estimate, source, sample count, and robust spread, and
:meth:`SubjectDimensions.updated` refuses to overwrite a good measurement with
a noisier one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal

from hmc_backend.contracts.enums import FitSource

Side = Literal["left", "right"]

# Anatomical upper bounds (meters) that no fitted dimension may exceed.
MAX_LIMB_RADIUS_M: Final = 0.16
MAX_HAND_WIDTH_M: Final = 0.16
MAX_HAND_THICKNESS_M: Final = 0.07
MAX_FOOT_WIDTH_M: Final = 0.16
MAX_FOOT_THICKNESS_M: Final = 0.14


@dataclass(frozen=True, slots=True)
class DimensionEstimate:
    value_m: float
    source: FitSource
    sample_count: int
    spread_m: float  # robust spread (MAD) of the samples that produced value_m

    def is_better_than(self, other: DimensionEstimate | None, *, min_samples: int) -> bool:
        """Observed beats default; among observed, more samples and lower spread win."""
        if other is None:
            return True
        rank = {FitSource.OBSERVED: 2, FitSource.SUBJECT_DEFAULT: 1, FitSource.GLOBAL_DEFAULT: 0}
        if rank[self.source] != rank[other.source]:
            return rank[self.source] > rank[other.source]
        if self.sample_count < min_samples:
            return False
        if other.sample_count < min_samples:
            return True
        # Both well sampled: only replace when clearly tighter.
        return self.spread_m < 0.75 * other.spread_m


@dataclass(frozen=True, slots=True)
class SideValues:
    left: DimensionEstimate
    right: DimensionEstimate

    def get(self, side: Side) -> DimensionEstimate:
        return self.left if side == "left" else self.right

    def with_side(self, side: Side, est: DimensionEstimate) -> SideValues:
        return replace(self, **{side: est})


def _global(value: float) -> DimensionEstimate:
    return DimensionEstimate(value, FitSource.GLOBAL_DEFAULT, 0, 0.0)


def _both(value: float) -> SideValues:
    return SideValues(_global(value), _global(value))


SIDED_FIELDS: Final[tuple[str, ...]] = (
    "upper_arm_radius_m",
    "forearm_radius_m",
    "thigh_radius_m",
    "shin_radius_m",
    "hand_width_m",
    "hand_thickness_m",
    "foot_width_m",
    "foot_thickness_m",
)


@dataclass(frozen=True, slots=True)
class SubjectDimensions:
    upper_arm_radius_m: SideValues = field(default_factory=lambda: _both(0.045))
    forearm_radius_m: SideValues = field(default_factory=lambda: _both(0.04))
    thigh_radius_m: SideValues = field(default_factory=lambda: _both(0.075))
    shin_radius_m: SideValues = field(default_factory=lambda: _both(0.05))
    hand_width_m: SideValues = field(default_factory=lambda: _both(0.085))
    hand_thickness_m: SideValues = field(default_factory=lambda: _both(0.03))
    foot_width_m: SideValues = field(default_factory=lambda: _both(0.095))
    foot_thickness_m: SideValues = field(default_factory=lambda: _both(0.07))
    # Non-sided extras used by torso/head fitting.
    head_radius_m: DimensionEstimate = field(default_factory=lambda: _global(0.11))
    torso_depth_m: DimensionEstimate = field(default_factory=lambda: _global(0.22))

    def get(self, name: str, side: Side | None = None) -> DimensionEstimate:
        v = getattr(self, name)
        if isinstance(v, SideValues):
            if side is None:
                raise ValueError(f"{name} is sided; pass side")
            return v.get(side)
        return v

    def updated(
        self,
        name: str,
        est: DimensionEstimate,
        side: Side | None = None,
        *,
        min_samples: int = 30,
    ) -> SubjectDimensions:
        """Return a copy with ``est`` installed only if it beats the current value."""
        if est.source is FitSource.DISABLED:
            return self
        current = self.get(name, side)
        if not est.is_better_than(current, min_samples=min_samples):
            return self
        v = getattr(self, name)
        changes: dict[str, Any]
        if isinstance(v, SideValues):
            assert side is not None
            changes = {name: v.with_side(side, est)}
        else:
            changes = {name: est}
        return replace(self, **changes)

    def as_subject_default(self, name: str, side: Side | None = None) -> DimensionEstimate:
        """The stored value relabelled for use in a later pose.

        An observed subject measurement becomes ``subject_default``; a global
        default stays ``global_default`` so the label is never upgraded.
        """
        cur = self.get(name, side)
        if cur.source is FitSource.OBSERVED:
            return replace(cur, source=FitSource.SUBJECT_DEFAULT)
        return cur

    def report(self) -> dict:
        out: dict = {}
        for f in SIDED_FIELDS:
            sv: SideValues = getattr(self, f)
            out[f] = {s: _est_doc(sv.get(s)) for s in ("left", "right")}
        out["head_radius_m"] = _est_doc(self.head_radius_m)
        out["torso_depth_m"] = _est_doc(self.torso_depth_m)
        return out


def _est_doc(e: DimensionEstimate) -> dict:
    return {"value_m": e.value_m, "source": e.source.value, "samples": e.sample_count, "spread_m": e.spread_m}
