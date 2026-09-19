package dev.humancraft.geometry;

/** Immutable double-precision 3-vector. Pure JVM; no Minecraft dependency so geometry is unit-testable. */
public record Vector3(double x, double y, double z) {
	public static final Vector3 ZERO = new Vector3(0, 0, 0);
	public static final Vector3 UNIT_X = new Vector3(1, 0, 0);
	public static final Vector3 UNIT_Y = new Vector3(0, 1, 0);
	public static final Vector3 UNIT_Z = new Vector3(0, 0, 1);

	public Vector3 add(Vector3 o) {
		return new Vector3(x + o.x, y + o.y, z + o.z);
	}

	public Vector3 sub(Vector3 o) {
		return new Vector3(x - o.x, y - o.y, z - o.z);
	}

	public Vector3 scale(double s) {
		return new Vector3(x * s, y * s, z * s);
	}

	public Vector3 negate() {
		return new Vector3(-x, -y, -z);
	}

	public double dot(Vector3 o) {
		return x * o.x + y * o.y + z * o.z;
	}

	public Vector3 cross(Vector3 o) {
		return new Vector3(y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x);
	}

	public double lengthSquared() {
		return dot(this);
	}

	public double length() {
		return Math.sqrt(lengthSquared());
	}

	public double distanceTo(Vector3 o) {
		return sub(o).length();
	}

	public double distanceSquaredTo(Vector3 o) {
		return sub(o).lengthSquared();
	}

	/** Returns the unit vector, or throws if this vector is degenerate. */
	public Vector3 normalize() {
		double len = length();
		if (!(len > Geometry.EPSILON)) {
			throw new IllegalArgumentException("Cannot normalize a zero-length vector");
		}
		return scale(1.0 / len);
	}

	public Vector3 lerp(Vector3 o, double t) {
		return new Vector3(x + (o.x - x) * t, y + (o.y - y) * t, z + (o.z - z) * t);
	}

	public Vector3 min(Vector3 o) {
		return new Vector3(Math.min(x, o.x), Math.min(y, o.y), Math.min(z, o.z));
	}

	public Vector3 max(Vector3 o) {
		return new Vector3(Math.max(x, o.x), Math.max(y, o.y), Math.max(z, o.z));
	}

	public Vector3 abs() {
		return new Vector3(Math.abs(x), Math.abs(y), Math.abs(z));
	}

	public double component(int axis) {
		return switch (axis) {
			case 0 -> x;
			case 1 -> y;
			case 2 -> z;
			default -> throw new IllegalArgumentException("axis " + axis);
		};
	}

	public boolean isFinite() {
		return Double.isFinite(x) && Double.isFinite(y) && Double.isFinite(z);
	}

	public double[] toArray() {
		return new double[] {x, y, z};
	}

	public static Vector3 of(double[] xyz) {
		if (xyz.length != 3) {
			throw new IllegalArgumentException("expected 3 components, got " + xyz.length);
		}
		return new Vector3(xyz[0], xyz[1], xyz[2]);
	}

	@Override
	public String toString() {
		return String.format("(%.4f, %.4f, %.4f)", x, y, z);
	}
}
