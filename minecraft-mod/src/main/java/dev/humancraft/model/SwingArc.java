package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;

/** Five-block sphere intersected with the forward horizontal half-space (180 degrees). */
public final class SwingArc {
	public static final double REACH = 5.0;
	private SwingArc() {}
	public static boolean contains(Vector3 origin, float yawDegrees, Vector3 target) {
		Vector3 delta = target.sub(origin);
		double yaw = Math.toRadians(yawDegrees);
		return delta.isFinite() && Float.isFinite(yawDegrees) && delta.lengthSquared() <= REACH * REACH
				&& -Math.sin(yaw) * delta.x() + Math.cos(yaw) * delta.z() >= -1e-9;
	}
}
