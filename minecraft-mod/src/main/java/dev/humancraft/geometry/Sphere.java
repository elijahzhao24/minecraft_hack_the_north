package dev.humancraft.geometry;

import java.util.Optional;

public record Sphere(Vector3 center, double radius) implements Shape {
	public Sphere {
		if (!center.isFinite() || !Double.isFinite(radius) || !(radius > 0)) {
			throw new IllegalArgumentException("sphere requires finite center and radius > 0");
		}
	}

	@Override
	public Aabb bounds() {
		return Aabb.around(center, new Vector3(radius, radius, radius));
	}

	@Override
	public Optional<RayHit> intersect(Ray ray, double maxDistance) {
		Vector3 oc = ray.origin().sub(center);
		double c = oc.lengthSquared() - radius * radius;
		if (c <= 0) {
			return Optional.of(new RayHit(0.0, ray.origin()));
		}
		double b = oc.dot(ray.direction());
		if (b > 0) {
			return Optional.empty(); // sphere is behind the origin
		}
		double disc = b * b - c;
		if (disc < 0) {
			return Optional.empty();
		}
		double t = -b - Math.sqrt(disc);
		if (t < 0) {
			t = 0;
		}
		if (t > maxDistance) {
			return Optional.empty();
		}
		return Optional.of(new RayHit(t, ray.at(t)));
	}

	@Override
	public boolean overlaps(Aabb box) {
		return box.squaredDistanceTo(center) <= radius * radius;
	}

	@Override
	public Sphere transform(Vector3 anchor, double scale) {
		return new Sphere(anchor.add(center.scale(scale)), radius * scale);
	}

	@Override
	public String typeName() {
		return "sphere";
	}
}
