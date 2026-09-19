package dev.humancraft.server;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.model.StageToWorld;

import java.util.List;
import java.util.UUID;

/**
 * Immutable interaction state owned by the logical server for one player. Colliders are already in world
 * blocks; the point cloud never reaches the server because it is render-only. {@code dimension} is the
 * world the anchor was placed in; the snapshot is meaningless anywhere else.
 */
public record ServerSnapshot(
		UUID owner,
		String dimension,
		long frameId,
		UUID sessionId,
		UUID calibrationId,
		Mode mode,
		StageToWorld transform,
		List<ColliderDto> worldColliders,
		long installedAtMillis) {

	public ServerSnapshot {
		worldColliders = List.copyOf(worldColliders);
	}

	/** Live frames expire after {@code ttlMillis}; snapshots persist until replaced or cleared. */
	public boolean isExpired(long nowMillis, long ttlMillis) {
		return mode == Mode.LIVE && nowMillis - installedAtMillis > ttlMillis;
	}

	public long validColliderCount() {
		return worldColliders.stream().filter(ColliderDto::valid).count();
	}
}
