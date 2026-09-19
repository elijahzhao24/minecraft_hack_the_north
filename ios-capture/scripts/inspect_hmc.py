#!/usr/bin/env python3
"""Validate an HMC1 RGBD envelope and print its decoded JSON header."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

FIXED_HEADER = struct.Struct("<4sHHII")
MAX_HEADER = 65_536
MAX_RGBD_PAYLOAD = 16 * 1024 * 1024


def inspect(path: Path) -> dict:
    packet = path.read_bytes()
    if len(packet) < FIXED_HEADER.size:
        raise ValueError("packet is shorter than the 16-byte HMC1 header")
    magic, version, message_type, header_length, payload_length = FIXED_HEADER.unpack_from(packet)
    if magic != b"HMC1" or version != 1 or message_type != 1:
        raise ValueError(f"unexpected envelope identity: {magic!r}, v{version}, type {message_type}")
    if header_length > MAX_HEADER or payload_length > MAX_RGBD_PAYLOAD:
        raise ValueError("declared length exceeds the v1 limit")
    if len(packet) != FIXED_HEADER.size + header_length + payload_length:
        raise ValueError("actual packet length does not match declared lengths")

    header_start = FIXED_HEADER.size
    payload_start = header_start + header_length
    header = json.loads(packet[header_start:payload_start].decode("utf-8"))
    expected_offset = 0
    names: set[str] = set()
    for descriptor in header["buffers"]:
        name = descriptor["name"]
        offset = descriptor["offset"]
        length = descriptor["length"]
        if not name or name in names:
            raise ValueError(f"duplicate or empty buffer name: {name!r}")
        if offset != expected_offset or offset + length > payload_length:
            raise ValueError(f"invalid/non-contiguous buffer range for {name}")
        names.add(name)
        expected_offset += length
    if expected_offset != payload_length:
        raise ValueError("descriptors do not cover the complete payload")

    depth = header["depth"]
    descriptors = {item["name"]: item for item in header["buffers"]}
    expected_depth = depth["width"] * depth["height"] * 4
    if descriptors["depth"]["length"] != expected_depth:
        raise ValueError("depth buffer length does not match float32 raster dimensions")
    if "confidence" in descriptors:
        expected_confidence = depth["width"] * depth["height"]
        if descriptors["confidence"]["length"] != expected_confidence:
            raise ValueError("confidence length does not match depth raster dimensions")

    print(json.dumps(header, indent=2, sort_keys=True))
    print(f"sha256  {hashlib.sha256(packet).hexdigest()}")
    return header


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    inspect(args.packet)


if __name__ == "__main__":
    main()
