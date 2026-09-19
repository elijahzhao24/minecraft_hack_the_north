package dev.humancraft.geometry;

/** Distance along a ray (in ray units, i.e. blocks in world space) and the hit point. */
public record RayHit(double distance, Vector3 point) {
	public RayHit {
		if (!(distance >= 0) || !Double.isFinite(distance)) {
			throw new IllegalArgumentException("hit distance must be finite and non-negative: " + distance);
		}
	}
}
