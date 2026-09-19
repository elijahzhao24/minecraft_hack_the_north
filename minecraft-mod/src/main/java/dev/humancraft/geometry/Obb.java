package dev.humancraft.geometry;

import java.util.List;
import java.util.Optional;

/**
 * Oriented bounding box. {@code axes} are the three local unit axes expressed in the enclosing frame
 * (row-major on the wire: axes[0] is local +X, axes[1] local +Y, axes[2] local +Z). Half extents are
 * strictly positive.
 */
public record Obb(Vector3 center, List<Vector3> axes, Vector3 halfExtents) implements Shape {
	public Obb {
		if (!center.isFinite() || !halfExtents.isFinite()) {
			throw new IllegalArgumentException("obb requires finite center and half extents");
		}
		if (!(halfExtents.x() > 0) || !(halfExtents.y() > 0) || !(halfExtents.z() > 0)) {
			throw new IllegalArgumentException("obb half extents must be > 0: " + halfExtents);
		}
		if (axes == null || axes.size() != 3) {
			throw new IllegalArgumentException("obb requires exactly 3 axes");
		}
		axes = List.copyOf(axes);
		for (Vector3 axis : axes) {
			if (!axis.isFinite()) {
				throw new IllegalArgumentException("obb axis must be finite");
			}
			if (!Geometry.nearlyEqual(axis.length(), 1.0, Geometry.AXIS_NORM_TOLERANCE)) {
				throw new IllegalArgumentException("obb axis must be unit length: " + axis);
			}
		}
		for (int i = 0; i < 3; i++) {
			for (int j = i + 1; j < 3; j++) {
				if (Math.abs(axes.get(i).dot(axes.get(j))) > Geometry.AXIS_DOT_TOLERANCE) {
					throw new IllegalArgumentException("obb axes must be orthogonal");
				}
			}
		}
		double det = axes.get(0).dot(axes.get(1).cross(axes.get(2)));
		if (!Geometry.nearlyEqual(Math.abs(det), 1.0, Geometry.AXIS_DET_TOLERANCE)) {
			throw new IllegalArgumentException("obb axes must have |det| == 1, got " + det);
		}
	}

	/** Convenience constructor for an axis-aligned OBB. */
	public static Obb axisAligned(Vector3 center, Vector3 halfExtents) {
		return new Obb(center, List.of(Vector3.UNIT_X, Vector3.UNIT_Y, Vector3.UNIT_Z), halfExtents);
	}

	/**
	 * Builds a right-handed OBB whose local +Z is {@code forward} and local +Y is {@code up} (made orthogonal
	 * to forward). Local +X = Y × Z.
	 */
	public static Obb fromForwardAndUp(Vector3 center, Vector3 forward, Vector3 up, Vector3 halfExtents) {
		Vector3 z = forward.normalize();
		Vector3 y = up.sub(z.scale(up.dot(z)));
		if (y.lengthSquared() < Geometry.EPSILON) {
			throw new IllegalArgumentException("up must not be parallel to forward");
		}
		y = y.normalize();
		Vector3 x = y.cross(z).normalize();
		return new Obb(center, List.of(x, y, z), halfExtents);
	}

	public Vector3 toLocal(Vector3 world) {
		Vector3 d = world.sub(center);
		return new Vector3(d.dot(axes.get(0)), d.dot(axes.get(1)), d.dot(axes.get(2)));
	}

	public Vector3 toWorld(Vector3 local) {
		return center
				.add(axes.get(0).scale(local.x()))
				.add(axes.get(1).scale(local.y()))
				.add(axes.get(2).scale(local.z()));
	}

	public Vector3 rotateToLocal(Vector3 worldDir) {
		return new Vector3(worldDir.dot(axes.get(0)), worldDir.dot(axes.get(1)), worldDir.dot(axes.get(2)));
	}

	@Override
	public Aabb bounds() {
		double ex = 0;
		double ey = 0;
		double ez = 0;
		for (int i = 0; i < 3; i++) {
			Vector3 axis = axes.get(i);
			double h = halfExtents.component(i);
			ex += Math.abs(axis.x()) * h;
			ey += Math.abs(axis.y()) * h;
			ez += Math.abs(axis.z()) * h;
		}
		return Aabb.around(center, new Vector3(ex, ey, ez));
	}

	@Override
	public Optional<RayHit> intersect(Ray ray, double maxDistance) {
		Vector3 lo = toLocal(ray.origin());
		Vector3 ld = rotateToLocal(ray.direction());
		double tMin = 0.0;
		double tMax = maxDistance;
		for (int axis = 0; axis < 3; axis++) {
			double o = lo.component(axis);
			double d = ld.component(axis);
			double h = halfExtents.component(axis);
			if (Math.abs(d) < Geometry.EPSILON) {
				if (o < -h || o > h) {
					return Optional.empty();
				}
				continue;
			}
			double inv = 1.0 / d;
			double t1 = (-h - o) * inv;
			double t2 = (h - o) * inv;
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
		return Optional.of(new RayHit(tMin, ray.at(tMin)));
	}

	@Override
	public boolean overlaps(Aabb box) {
		Vector3 boxCenter = box.center();
		Vector3 boxHalf = box.halfExtents();
		Vector3 delta = center.sub(boxCenter);
		Vector3[] boxAxes = {Vector3.UNIT_X, Vector3.UNIT_Y, Vector3.UNIT_Z};

		for (Vector3 axis : boxAxes) {
			if (separated(axis, delta, boxHalf, boxAxes)) {
				return false;
			}
		}
		for (Vector3 axis : axes) {
			if (separated(axis, delta, boxHalf, boxAxes)) {
				return false;
			}
		}
		for (Vector3 a : boxAxes) {
			for (Vector3 b : axes) {
				Vector3 axis = a.cross(b);
				if (axis.lengthSquared() < Geometry.EPSILON) {
					continue;
				}
				if (separated(axis, delta, boxHalf, boxAxes)) {
					return false;
				}
			}
		}
		return true;
	}

	private boolean separated(Vector3 axis, Vector3 delta, Vector3 boxHalf, Vector3[] boxAxes) {
		double distance = Math.abs(delta.dot(axis));
		double boxRadius = 0;
		for (int i = 0; i < 3; i++) {
			boxRadius += Math.abs(boxAxes[i].dot(axis)) * boxHalf.component(i);
		}
		double obbRadius = 0;
		for (int i = 0; i < 3; i++) {
			obbRadius += Math.abs(axes.get(i).dot(axis)) * halfExtents.component(i);
		}
		return distance > boxRadius + obbRadius + Geometry.EPSILON;
	}

	@Override
	public Obb transform(Vector3 anchor, double scale) {
		return new Obb(anchor.add(center.scale(scale)), axes, halfExtents.scale(scale));
	}

	@Override
	public String typeName() {
		return "obb";
	}
}
