package dev.humancraft.geometry;

/** A ray with a unit-length direction. */
public record Ray(Vector3 origin, Vector3 direction) {
	public Ray {
		if (!origin.isFinite() || !direction.isFinite()) {
			throw new IllegalArgumentException("ray must be finite");
		}
		direction = direction.normalize();
	}

	public Vector3 at(double t) {
		return origin.add(direction.scale(t));
	}
}
