package dev.humancraft.model;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.FrameQuality;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.SourceFrameRef;
import dev.humancraft.contract.TraceContext;

import java.util.List;
import java.util.UUID;

/**
 * A character frame after the stage→world transform. Landmarks and colliders are in world blocks. The point
 * cloud is kept in stage meters (wire layout) and is placed by applying {@link #transform()} on the GPU,
 * which is numerically identical to transforming each point and avoids a copy of up to 1.6 MB per frame.
 */
public record WorldSnapshot(
		long frameId,
		UUID fusionId,
		UUID sessionId,
		UUID calibrationId,
		Mode mode,
		StageToWorld transform,
		List<LandmarkDto> landmarks,
		List<ColliderDto> colliders,
		PointCloud stageCloud,
		FrameQuality quality,
		TraceContext trace,
		List<SourceFrameRef> sourceFrames) {

	public WorldSnapshot {
		landmarks = List.copyOf(landmarks);
		colliders = List.copyOf(colliders);
		sourceFrames = List.copyOf(sourceFrames);
	}

	public int validColliderCount() {
		return (int) colliders.stream().filter(ColliderDto::valid).count();
	}
}
