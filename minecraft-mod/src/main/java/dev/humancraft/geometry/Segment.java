package dev.humancraft.geometry;

/** Finite line segment helpers shared by the capsule routines. */
public record Segment(Vector3 a, Vector3 b) {
	public Vector3 at(double t) {
		return a.lerp(b, t);
	}

	public Vector3 direction() {
		return b.sub(a);
	}

	public boolean isDegenerate() {
		return direction().lengthSquared() < Geometry.EPSILON * Geometry.EPSILON;
	}

	/** Parameter t in [0,1] of the point on the segment closest to p. */
	public double closestParameter(Vector3 p) {
		Vector3 d = direction();
		double dd = d.lengthSquared();
		if (dd < Geometry.EPSILON * Geometry.EPSILON) {
			return 0.0;
		}
		double t = p.sub(a).dot(d) / dd;
		return Math.max(0.0, Math.min(1.0, t));
	}

	public Vector3 closestPoint(Vector3 p) {
		return at(closestParameter(p));
	}

	public double squaredDistanceTo(Vector3 p) {
		return closestPoint(p).distanceSquaredTo(p);
	}

	/**
	 * Squared distance between the segment and an axis-aligned box. The distance from a point on the
	 * segment to a convex set is a convex function of the segment parameter, so a fixed-iteration golden
	 * section search converges deterministically to the minimum.
	 */
	public double squaredDistanceTo(Aabb box) {
		if (box.contains(a) || box.contains(b)) {
			return 0.0;
		}
		double lo = 0.0;
		double hi = 1.0;
		final double phi = (Math.sqrt(5.0) - 1.0) / 2.0;
		double x1 = hi - phi * (hi - lo);
		double x2 = lo + phi * (hi - lo);
		double f1 = box.squaredDistanceTo(at(x1));
		double f2 = box.squaredDistanceTo(at(x2));
		for (int i = 0; i < 100; i++) {
			if (f1 < f2) {
				hi = x2;
				x2 = x1;
				f2 = f1;
				x1 = hi - phi * (hi - lo);
				f1 = box.squaredDistanceTo(at(x1));
			} else {
				lo = x1;
				x1 = x2;
				f1 = f2;
				x2 = lo + phi * (hi - lo);
				f2 = box.squaredDistanceTo(at(x2));
			}
		}
		double best = Math.min(f1, f2);
		best = Math.min(best, box.squaredDistanceTo(a));
		best = Math.min(best, box.squaredDistanceTo(b));
		return best;
	}
}
