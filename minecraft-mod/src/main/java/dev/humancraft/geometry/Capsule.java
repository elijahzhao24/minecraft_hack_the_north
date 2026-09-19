package dev.humancraft.geometry;

import java.util.Optional;

/** Finite capsule: the set of points within {@code radius} of segment {@code a -> b}. */
public record Capsule(Vector3 a, Vector3 b, double radius) implements Shape {
	public Capsule {
		if (!a.isFinite() || !b.isFinite() || !Double.isFinite(radius) || !(radius > 0)) {
			throw new IllegalArgumentException("capsule requires finite endpoints and radius > 0");
		}
	}

	public Segment segment() {
		return new Segment(a, b);
	}

	@Override
	public Aabb bounds() {
		Vector3 r = new Vector3(radius, radius, radius);
		return new Aabb(a.min(b).sub(r), a.max(b).add(r));
	}

	@Override
	public Optional<RayHit> intersect(Ray ray, double maxDistance) {
		Segment seg = segment();
		if (seg.squaredDistanceTo(ray.origin()) <= radius * radius) {
			return Optional.of(new RayHit(0.0, ray.origin()));
		}
		if (seg.isDegenerate()) {
			return new Sphere(a, radius).intersect(ray, maxDistance);
		}

		double best = Double.POSITIVE_INFINITY;

		// Cylinder body, restricted to the open span between the two caps.
		Vector3 ba = b.sub(a);
		Vector3 oa = ray.origin().sub(a);
		Vector3 rd = ray.direction();
		double baba = ba.dot(ba);
		double bard = ba.dot(rd);
		double baoa = ba.dot(oa);
		double rdoa = rd.dot(oa);
		double oaoa = oa.dot(oa);
		double qa = baba - bard * bard;
		double qb = baba * rdoa - baoa * bard;
		double qc = baba * oaoa - baoa * baoa - radius * radius * baba;
		if (qa > Geometry.EPSILON) {
			double h = qb * qb - qa * qc;
			if (h >= 0) {
				double t = (-qb - Math.sqrt(h)) / qa;
				double y = baoa + t * bard;
				if (t >= 0 && y >= 0 && y <= baba) {
					best = t;
				}
			}
		}

		// End caps. The origin is outside the capsule, so the entry into the union is the nearest entry.
		for (Vector3 c : new Vector3[] {a, b}) {
			Optional<RayHit> cap = new Sphere(c, radius).intersect(ray, maxDistance);
			if (cap.isPresent() && cap.get().distance() < best) {
				best = cap.get().distance();
			}
		}

		if (!Double.isFinite(best) || best > maxDistance) {
			return Optional.empty();
		}
		return Optional.of(new RayHit(best, ray.at(best)));
	}

	@Override
	public boolean overlaps(Aabb box) {
		return segment().squaredDistanceTo(box) <= radius * radius;
	}

	@Override
	public Capsule transform(Vector3 anchor, double scale) {
		return new Capsule(anchor.add(a.scale(scale)), anchor.add(b.scale(scale)), radius * scale);
	}

	@Override
	public String typeName() {
		return "capsule";
	}
}
