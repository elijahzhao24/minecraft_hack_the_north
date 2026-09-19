package dev.humancraft.server;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.RayHit;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Vector3;

import java.util.List;
import java.util.Optional;

/** Broad-phase AABB slab test, then exact narrow phase; picks the closest hit deterministically. */
public final class BodyRaycaster {
	public record BodyHit(ColliderDto collider, double distance, Vector3 point, int broadPhaseCandidates, int narrowPhaseTests) {}

	private BodyRaycaster() {}

	public static Optional<BodyHit> closestHit(List<ColliderDto> worldColliders, Ray ray, double maxDistance) {
		ColliderDto best = null;
		RayHit bestHit = null;
		int candidates = 0;
		int narrow = 0;
		double limit = maxDistance;
		for (ColliderDto collider : worldColliders) {
			if (!collider.valid() || collider.geometry().isEmpty()) {
				continue;
			}
			Shape shape = collider.geometry().get();
			if (shape.bounds().intersectRay(ray, limit).isEmpty()) {
				continue;
			}
			candidates++;
			narrow++;
			Optional<RayHit> hit = shape.intersect(ray, limit);
			// Strictly-closer wins; on an exact tie the earlier collider in wire order is kept.
			if (hit.isPresent() && (bestHit == null || hit.get().distance() < bestHit.distance())) {
				best = collider;
				bestHit = hit.get();
				limit = bestHit.distance();
			}
		}
		if (best == null) {
			return Optional.empty();
		}
		return Optional.of(new BodyHit(best, bestHit.distance(), bestHit.point(), candidates, narrow));
	}
}
