package dev.humancraft.server;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ProbeResultCode;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.Vector3;

import java.util.Optional;
import java.util.OptionalDouble;

/**
 * Server-authoritative "what body part am I looking at" query. The client only supplies intent (request id
 * and the frame it believes is active); eye position, look direction, reach and block occlusion all come
 * from the server.
 */
public final class ProbeService {
	/** Distance beyond reach that is still scanned so the client can distinguish OUT_OF_REACH from MISS. */
	public static final double SCAN_DISTANCE = 64.0;

	/** Supplied by the world: distance to the first solid block along the ray within {@code maxDistance}. */
	@FunctionalInterface
	public interface BlockOcclusion {
		OptionalDouble firstBlockDistance(Ray ray, double maxDistance);

		BlockOcclusion NONE = (ray, max) -> OptionalDouble.empty();
	}

	public record ProbeQuery(int requestId, long expectedFrameId) {}

	public record ProbeOutcome(
			int requestId,
			ProbeResultCode code,
			long frameId,
			Optional<String> colliderId,
			Optional<BodyPart> bodyPart,
			double distance,
			Optional<Vector3> point,
			int broadPhaseCandidates,
			int narrowPhaseTests) {

		static ProbeOutcome of(int requestId, ProbeResultCode code, long frameId) {
			return new ProbeOutcome(requestId, code, frameId, Optional.empty(), Optional.empty(), -1, Optional.empty(), 0, 0);
		}
	}

	private ProbeService() {}

	public static ProbeOutcome probe(ProbeQuery query, Optional<ServerSnapshot> snapshot, Ray eyeRay, double reach, BlockOcclusion occlusion) {
		if (snapshot.isEmpty()) {
			return ProbeOutcome.of(query.requestId(), ProbeResultCode.NO_ACTIVE_SNAPSHOT, -1);
		}
		ServerSnapshot active = snapshot.get();
		if (active.frameId() != query.expectedFrameId()) {
			return ProbeOutcome.of(query.requestId(), ProbeResultCode.FRAME_MISMATCH, active.frameId());
		}
		double scan = Math.max(reach, SCAN_DISTANCE);
		Optional<BodyRaycaster.BodyHit> hit = BodyRaycaster.closestHit(active.worldColliders(), eyeRay, scan);
		if (hit.isEmpty()) {
			return ProbeOutcome.of(query.requestId(), ProbeResultCode.MISS, active.frameId());
		}
		BodyRaycaster.BodyHit body = hit.get();
		OptionalDouble block = occlusion.firstBlockDistance(eyeRay, body.distance());
		if (block.isPresent() && block.getAsDouble() < body.distance()) {
			return new ProbeOutcome(query.requestId(), ProbeResultCode.BLOCK_OCCLUDED, active.frameId(),
					Optional.of(body.collider().id()), Optional.of(body.collider().bodyPart()), block.getAsDouble(),
					Optional.of(eyeRay.at(block.getAsDouble())), body.broadPhaseCandidates(), body.narrowPhaseTests());
		}
		ProbeResultCode code = body.distance() <= reach ? ProbeResultCode.HIT : ProbeResultCode.OUT_OF_REACH;
		return new ProbeOutcome(query.requestId(), code, active.frameId(), Optional.of(body.collider().id()),
				Optional.of(body.collider().bodyPart()), body.distance(), Optional.of(body.point()),
				body.broadPhaseCandidates(), body.narrowPhaseTests());
	}
}
