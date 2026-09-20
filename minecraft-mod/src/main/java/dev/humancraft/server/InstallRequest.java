package dev.humancraft.server;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.model.StageToWorld;

import java.util.List;
import java.util.UUID;

/** What the client asks the logical server to make interactive. Colliders are in stage meters. */
public record InstallRequest(
		long frameId,
		UUID sessionId,
		UUID calibrationId,
		Mode mode,
		StageToWorld transform,
		List<ColliderDto> stageColliders,
		int landmarkCount,
		int pointCount,
		UUID fusionId,
		String sourceFrameIds) {

	public InstallRequest(long frameId, UUID sessionId, UUID calibrationId, Mode mode, StageToWorld transform,
			List<ColliderDto> stageColliders, int landmarkCount, int pointCount) {
		this(frameId, sessionId, calibrationId, mode, transform, stageColliders, landmarkCount, pointCount, null, "");
	}

	public InstallRequest {
		stageColliders = List.copyOf(stageColliders);
		sourceFrameIds = sourceFrameIds == null ? "" : sourceFrameIds;
	}
}
