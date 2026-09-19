package dev.humancraft.server;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.model.StageToWorld;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;
import java.util.UUID;

/**
 * Per-player active snapshots. Confined to the logical server thread; every mutation replaces the immutable
 * {@link ServerSnapshot} wholesale so probes never observe a half-installed frame.
 */
public final class SnapshotStore {
	/** Colliders larger than this (in stage meters) cannot describe a human and are rejected. */
	public static final double MAX_COLLIDER_SIZE_M = ProtocolLimits.MAX_COLLIDER_SIZE_M;
	/** Largest scale the server accepts; keeps world colliders and contact scans bounded. */
	public static final double MAX_BLOCKS_PER_METER = 8.0;
	public static final double MIN_BLOCKS_PER_METER = 0.1;

	private final Map<UUID, ServerSnapshot> active = new HashMap<>();
	private final Map<UUID, Cursor> cursors = new HashMap<>();
	private final long liveTtlMillis;

	private record Cursor(UUID sessionId, long lastFrameId) {}

	public SnapshotStore(long liveTtlMillis) {
		if (liveTtlMillis <= 0) {
			throw new IllegalArgumentException("liveTtlMillis must be positive");
		}
		this.liveTtlMillis = liveTtlMillis;
	}

	public long liveTtlMillis() {
		return liveTtlMillis;
	}

	public InstallOutcome install(UUID owner, String dimension, InstallRequest request, long nowMillis) {
		OptionalLong previous = activeFrameId(owner, nowMillis);
		try {
			validate(request);
		} catch (ProtocolException e) {
			String code = e.code().equals(ProtocolException.LIMIT_EXCEEDED) ? InstallOutcome.REJECTED_LIMIT : InstallOutcome.REJECTED_INVALID;
			return InstallOutcome.rejected(code, e.code() + ": " + e.getMessage(), previous);
		}

		Cursor cursor = cursors.get(owner);
		boolean placementReinstall = cursor != null
				&& cursor.sessionId().equals(request.sessionId())
				&& request.frameId() == cursor.lastFrameId()
				&& active.containsKey(owner);
		if (cursor != null && cursor.sessionId().equals(request.sessionId())
				&& (request.frameId() < cursor.lastFrameId() || (request.frameId() == cursor.lastFrameId() && !placementReinstall))) {
			return InstallOutcome.rejected(InstallOutcome.REJECTED_STALE,
					"frame " + request.frameId() + " is not newer than " + cursor.lastFrameId(), previous);
		}

		StageToWorld transform = request.transform();
		List<ColliderDto> world = new ArrayList<>(request.stageColliders().size());
		for (ColliderDto c : request.stageColliders()) {
			world.add(transform.collider(c));
		}
		ServerSnapshot snapshot = new ServerSnapshot(owner, dimension, request.frameId(), request.sessionId(), request.calibrationId(),
				request.mode(), transform, world, nowMillis);
		active.put(owner, snapshot);
		cursors.put(owner, new Cursor(request.sessionId(), request.frameId()));
		return InstallOutcome.accepted(request.frameId());
	}

	/** Active, unexpired snapshot. Expired live frames are dropped so stale hitboxes never linger. */
	public Optional<ServerSnapshot> active(UUID owner, long nowMillis) {
		ServerSnapshot snapshot = active.get(owner);
		if (snapshot == null) {
			return Optional.empty();
		}
		if (snapshot.isExpired(nowMillis, liveTtlMillis)) {
			active.remove(owner);
			return Optional.empty();
		}
		return Optional.of(snapshot);
	}

	public OptionalLong activeFrameId(UUID owner, long nowMillis) {
		return active(owner, nowMillis).map(s -> OptionalLong.of(s.frameId())).orElse(OptionalLong.empty());
	}

	/** Clears the snapshot but keeps the monotonic cursor so a replayed frame is still rejected. */
	public boolean clear(UUID owner) {
		return active.remove(owner) != null;
	}

	/** Logout / world unload: forget everything about the player, including the cursor. */
	public void forget(UUID owner) {
		active.remove(owner);
		cursors.remove(owner);
	}

	public void forgetAll() {
		active.clear();
		cursors.clear();
	}

	public int size() {
		return active.size();
	}

	static void validate(InstallRequest request) {
		if (request.frameId() < 0) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "frame_id must be non-negative");
		}
		if (request.stageColliders().size() > ProtocolLimits.MAX_COLLIDERS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "more than " + ProtocolLimits.MAX_COLLIDERS + " colliders");
		}
		if (request.landmarkCount() < 0 || request.landmarkCount() > ProtocolLimits.MAX_LANDMARKS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "landmark count out of range");
		}
		if (request.pointCount() < 0 || request.pointCount() > ProtocolLimits.MAX_POINTS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "point count out of range");
		}
		StageToWorld t = request.transform();
		if (!t.anchor().isFinite() || !Double.isFinite(t.blocksPerMeter())) {
			throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, "transform must be finite");
		}
		if (t.blocksPerMeter() < MIN_BLOCKS_PER_METER || t.blocksPerMeter() > MAX_BLOCKS_PER_METER) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
					"blocks_per_meter " + t.blocksPerMeter() + " outside [" + MIN_BLOCKS_PER_METER + ", " + MAX_BLOCKS_PER_METER + "]");
		}
		if (Math.abs(t.anchor().x()) > 3.0e7 || Math.abs(t.anchor().z()) > 3.0e7 || Math.abs(t.anchor().y()) > 4096) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "anchor outside the world");
		}
		Set<String> ids = new HashSet<>();
		for (ColliderDto c : request.stageColliders()) {
			if (!ids.add(c.id())) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "duplicate collider id '" + c.id() + "'");
			}
			c.requireSizeAtMost(MAX_COLLIDER_SIZE_M);
			if (c.geometry().isPresent()) {
				var b = c.geometry().get().bounds();
				if (b.min().length() > 100 || b.max().length() > 100) {
					throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY,
							"collider '" + c.id() + "' is more than 100 m from the stage origin");
				}
			}
		}
	}
}
