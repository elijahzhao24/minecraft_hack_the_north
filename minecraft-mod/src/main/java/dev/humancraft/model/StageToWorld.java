package dev.humancraft.model;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Vector3;

import java.util.List;

/**
 * The single stage→world mapping: {@code p_world_blocks = anchor + blocksPerMeter * p_stage_m}. Stage axes
 * already match Minecraft's (+Y up), so no rotation is applied. Every consumer (points, landmarks, collider
 * centers, endpoints, radii, half extents, hit points) goes through this class so scale can never be applied
 * to positions but forgotten for sizes.
 */
public record StageToWorld(Vector3 anchor, double blocksPerMeter) {
	public StageToWorld {
		if (!anchor.isFinite()) {
			throw new IllegalArgumentException("anchor must be finite");
		}
		if (!Double.isFinite(blocksPerMeter) || !(blocksPerMeter > 0)) {
			throw new IllegalArgumentException("blocksPerMeter must be > 0");
		}
	}

	public Vector3 point(Vector3 stageMeters) {
		return anchor.add(stageMeters.scale(blocksPerMeter));
	}

	public Vector3 toStage(Vector3 worldBlocks) {
		return worldBlocks.sub(anchor).scale(1.0 / blocksPerMeter);
	}

	public double length(double meters) {
		return meters * blocksPerMeter;
	}

	public Shape shape(Shape stage) {
		return stage.transform(anchor, blocksPerMeter);
	}

	public ColliderDto collider(ColliderDto stage) {
		return stage.withGeometry(stage.geometry().map(this::shape));
	}

	public LandmarkDto landmark(LandmarkDto stage) {
		return stage.withPosition(stage.position().map(this::point));
	}

	public List<ColliderDto> colliders(List<ColliderDto> stage) {
		return stage.stream().map(this::collider).toList();
	}

	public List<LandmarkDto> landmarks(List<LandmarkDto> stage) {
		return stage.stream().map(this::landmark).toList();
	}

	/** Converts a decoded stage frame into a world-space snapshot (colliders + landmarks in blocks). */
	public WorldSnapshot snapshot(CharacterFrame frame) {
		return new WorldSnapshot(
				frame.header().frameId(),
				frame.header().sessionId(),
				frame.header().calibrationId(),
				frame.header().mode(),
				this,
				landmarks(frame.header().landmarks()),
				colliders(frame.header().colliders()),
				frame.cloud(),
				frame.header().quality(),
				frame.header().trace(),
				frame.header().sourceFrames());
	}
}
