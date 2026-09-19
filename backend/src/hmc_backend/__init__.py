"""Humans in Minecraft — Workflow 2 backend package.

One Python process that ingests HMC1 RGBD frames from two LiDAR iPhones,
estimates clock offsets, pairs frames, calibrates cameras into a shared metric
stage frame, reconstructs a person-only colored point cloud, assembles one
immutable ``CharacterFrame``, and publishes it to the Minecraft subscriber.

Protocol version 1. See ``docs/contracts.md`` for the normative wire contract.
"""

PROTOCOL_VERSION = 1

__all__ = ["PROTOCOL_VERSION"]
