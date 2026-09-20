"""Persisted camera models plus a tf2-style static frame tree."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hmc_backend.calibration.model import (
    CalibrationError,
    RigCalibration,
    parse_rig_calibration,
    rig_to_json,
)
from hmc_backend.transforms import StaticTransform, TransformBuffer, TransformError, optical_frame

FRAME_TREE_SCHEMA = "hmc.rig_frame_tree"
FRAME_TREE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RigFrameTree:
    rig: RigCalibration
    transforms: TransformBuffer

    @classmethod
    def from_legacy_rig(cls, rig: RigCalibration) -> RigFrameTree:
        edges = [
            StaticTransform(
                "stage",
                optical_frame(camera.device_id),
                camera.T_stage_from_optical,
                "legacy_calibration",
            )
            for camera in rig.cameras.values()
        ]
        return cls(rig=rig, transforms=TransformBuffer(edges))


def frame_tree_to_json(tree: RigFrameTree) -> dict:
    legacy = rig_to_json(tree.rig)
    cameras = []
    for camera in legacy["cameras"]:
        entry = dict(camera)
        entry.pop("T_stage_from_optical_row_major")
        entry["optical_frame"] = optical_frame(entry["device_id"])
        cameras.append(entry)
    return {
        "schema": FRAME_TREE_SCHEMA,
        "schema_version": FRAME_TREE_SCHEMA_VERSION,
        "rig_id": legacy["calibration_id"],
        "created_at_utc": legacy["created_at_utc"],
        "root_frame": "stage",
        "stage_definition": legacy["stage_definition"],
        "board": legacy["board"],
        "cameras": cameras,
        "static_transforms": [
            {
                "parent_frame": edge.parent_frame,
                "child_frame": edge.child_frame,
                "T_parent_from_child_row_major": edge.matrix.reshape(-1).tolist(),
                "authority": edge.authority,
            }
            for edge in tree.transforms.transforms
        ],
        "validation": legacy["validation"],
    }


def parse_frame_tree(raw: dict) -> RigFrameTree:
    if raw.get("schema") != FRAME_TREE_SCHEMA:
        raise CalibrationError(f"unexpected frame-tree schema {raw.get('schema')!r}")
    if raw.get("schema_version") != FRAME_TREE_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported frame-tree schema_version {raw.get('schema_version')!r}")
    if raw.get("root_frame") != "stage":
        raise CalibrationError("frame-tree root_frame must be 'stage'")
    try:
        edges = [
            StaticTransform(
                str(entry["parent_frame"]),
                str(entry["child_frame"]),
                entry["T_parent_from_child_row_major"],
                str(entry.get("authority", "unknown")),
            )
            for entry in raw["static_transforms"]
        ]
        buffer = TransformBuffer(edges)
        cameras = []
        for camera in raw["cameras"]:
            device_id = str(camera["device_id"])
            expected_frame = optical_frame(device_id)
            if camera.get("optical_frame") != expected_frame:
                raise CalibrationError(f"unexpected optical frame for {device_id!r}")
            transform = buffer.lookup_transform("stage", expected_frame, 0.0)
            entry = dict(camera)
            entry.pop("optical_frame", None)
            entry["T_stage_from_optical_row_major"] = transform.reshape(-1).tolist()
            cameras.append(entry)
    except (KeyError, TypeError, TransformError) as exc:
        raise CalibrationError(f"invalid frame tree: {exc}") from exc
    rig = parse_rig_calibration({
        "schema": "hmc.rig_calibration",
        "schema_version": 1,
        "calibration_id": raw["rig_id"],
        "created_at_utc": raw["created_at_utc"],
        "stage_definition": raw.get("stage_definition", {}),
        "board": raw.get("board"),
        "cameras": cameras,
        "validation": raw.get("validation", {}),
    })
    return RigFrameTree(rig=rig, transforms=buffer)


def load_frame_tree(path: str | Path) -> RigFrameTree:
    source = Path(path)
    if not source.exists():
        raise CalibrationError(f"frame tree not found: {source}")
    try:
        return parse_frame_tree(json.loads(source.read_text()))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"frame tree is not valid JSON: {source}") from exc


def save_frame_tree(tree: RigFrameTree, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(frame_tree_to_json(tree), indent=2))
    temporary.replace(target)
