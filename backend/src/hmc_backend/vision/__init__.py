"""Workflow-3 boundary protocols and a deterministic fake for fixtures."""

from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector
from hmc_backend.vision.protocols import (
    CharacterFitter,
    ViewDetector,
    ViewReconstructor,
)

__all__ = [
    "CharacterFitter",
    "FakeCharacterFitter",
    "FakePersonMaskDetector",
    "ViewDetector",
    "ViewReconstructor",
]
