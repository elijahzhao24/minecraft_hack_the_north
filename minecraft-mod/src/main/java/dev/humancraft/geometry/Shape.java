package dev.humancraft.geometry;

import java.util.Optional;

/**
 * A convex collider volume. All three wire collider types implement exact ray intersection and exact
 * overlap against an axis-aligned box (a full Minecraft block), plus a conservative AABB for broad phase.
 *
 * <p>Shapes are frame-agnostic: the same instance can describe stage meters or world blocks. Use
 * {@link #transform(Vector3, double)} to move between frames with one anchor and one uniform scale.
 */
public sealed interface Shape permits Sphere, Capsule, Obb {
	/** Conservative axis-aligned bounds used for broad phase. */
	Aabb bounds();

	/**
	 * Nearest intersection along the ray within {@code [0, maxDistance]}. A ray origin inside the shape
	 * reports a hit at distance 0 at the origin itself.
	 */
	Optional<RayHit> intersect(Ray ray, double maxDistance);

	/** Exact overlap test against an axis-aligned box (touching counts as overlap). */
	boolean overlaps(Aabb box);

	/** Applies {@code p' = anchor + scale * p} to every point and scales every length by {@code scale}. */
	Shape transform(Vector3 anchor, double scale);

	/** Wire-level type name (matches {@code collider.type} in the HMC1 header). */
	String typeName();
}
