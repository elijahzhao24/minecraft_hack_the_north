package dev.humancraft.geometry;

import java.util.Optional;

/** Axis-aligned bounding box. Used for broad-phase culling and as the shape of a full Minecraft block. */
public record Aabb(Vector3 min, Vector3 max) {
	public Aabb {
		if (!min.isFinite() || !max.isFinite()) {
			throw new IllegalArgumentException("aabb must be finite");
		}
		if (min.x() > max.x() || min.y() > max.y() || min.z() > max.z()) {
			throw new IllegalArgumentException("aabb min must be <= max: " + min + " / " + max);
		}
	}

	/** The full cube occupying integer block cell (bx, by, bz). */
	public static Aabb unitCube(int bx, int by, int bz) {
		return new Aabb(new Vector3(bx, by, bz), new Vector3(bx + 1, by + 1, bz + 1));
	}

	public static Aabb around(Vector3 center, Vector3 halfExtents) {
		return new Aabb(center.sub(halfExtents), center.add(halfExtents));
	}

	public Vector3 center() {
		return min.add(max).scale(0.5);
	}

	public Vector3 halfExtents() {
		return max.sub(min).scale(0.5);
	}

	public Aabb union(Aabb o) {
		return new Aabb(min.min(o.min), max.max(o.max));
	}

	public Aabb inflate(double r) {
		Vector3 d = new Vector3(r, r, r);
		return new Aabb(min.sub(d), max.add(d));
	}

	public boolean intersects(Aabb o) {
		return min.x() <= o.max.x() && max.x() >= o.min.x()
				&& min.y() <= o.max.y() && max.y() >= o.min.y()
				&& min.z() <= o.max.z() && max.z() >= o.min.z();
	}

	public boolean contains(Vector3 p) {
		return p.x() >= min.x() && p.x() <= max.x()
				&& p.y() >= min.y() && p.y() <= max.y()
				&& p.z() >= min.z() && p.z() <= max.z();
	}

	public Vector3 closestPoint(Vector3 p) {
		return new Vector3(
				Math.max(min.x(), Math.min(max.x(), p.x())),
				Math.max(min.y(), Math.min(max.y(), p.y())),
				Math.max(min.z(), Math.min(max.z(), p.z())));
	}

	public double squaredDistanceTo(Vector3 p) {
		return closestPoint(p).distanceSquaredTo(p);
	}

	/**
	 * Slab test. Returns the entry distance (0 if the origin is inside) if the ray touches the box within
	 * [0, maxDistance].
	 */
	public Optional<Double> intersectRay(Ray ray, double maxDistance) {
		double tMin = 0.0;
		double tMax = maxDistance;
		for (int axis = 0; axis < 3; axis++) {
			double o = ray.origin().component(axis);
			double d = ray.direction().component(axis);
			double lo = min.component(axis);
			double hi = max.component(axis);
			if (Math.abs(d) < Geometry.EPSILON) {
				if (o < lo || o > hi) {
					return Optional.empty();
				}
				continue;
			}
			double inv = 1.0 / d;
			double t1 = (lo - o) * inv;
			double t2 = (hi - o) * inv;
			if (t1 > t2) {
				double tmp = t1;
				t1 = t2;
				t2 = tmp;
			}
			tMin = Math.max(tMin, t1);
			tMax = Math.min(tMax, t2);
			if (tMin > tMax) {
				return Optional.empty();
			}
		}
		return Optional.of(tMin);
	}
}
