package dev.humancraft.geometry;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Exact shape-vs-full-cube overlap used by the contact target. */
class ShapeOverlapTest {
	private static final Aabb CUBE = Aabb.unitCube(0, 0, 0);

	@Test
	void sphereOverlap() {
		assertTrue(new Sphere(new Vector3(0.5, 0.5, 0.5), 0.1).overlaps(CUBE), "inside");
		assertTrue(new Sphere(new Vector3(1.2, 0.5, 0.5), 0.25).overlaps(CUBE), "touching face");
		assertFalse(new Sphere(new Vector3(1.3, 0.5, 0.5), 0.25).overlaps(CUBE));
		// Corner case: near the corner the AABB-of-sphere overlaps but the sphere does not.
		Sphere nearCorner = new Sphere(new Vector3(1.2, 1.2, 1.2), 0.3);
		assertTrue(nearCorner.bounds().intersects(CUBE));
		assertFalse(nearCorner.overlaps(CUBE));
	}

	@Test
	void capsuleOverlapUsesSegmentDistance() {
		// Horizontal capsule passing over the cube, radius reaches the top face.
		assertTrue(new Capsule(new Vector3(-1, 1.2, 0.5), new Vector3(2, 1.2, 0.5), 0.25).overlaps(CUBE));
		assertFalse(new Capsule(new Vector3(-1, 1.3, 0.5), new Vector3(2, 1.3, 0.5), 0.25).overlaps(CUBE));
		// Diagonal capsule that skims past a corner: its AABB overlaps but the segment stays far away.
		Capsule diagonal = new Capsule(new Vector3(1.5, -0.5, 0.5), new Vector3(-0.5, 1.5, 0.5), 0.2);
		assertTrue(diagonal.bounds().intersects(CUBE));
		assertTrue(diagonal.overlaps(CUBE), "this one actually crosses the cube");
		Capsule skim = new Capsule(new Vector3(2.0, 0.5, 0.5), new Vector3(0.5, 2.0, 0.5), 0.2);
		assertTrue(skim.bounds().intersects(CUBE));
		assertFalse(skim.overlaps(CUBE), "segment stays 0.35 away from the corner");
		// Endpoint inside.
		assertTrue(new Capsule(new Vector3(0.5, 0.5, 0.5), new Vector3(5, 5, 5), 0.01).overlaps(CUBE));
	}

	@Test
	void segmentToBoxDistanceIsExactForSimpleCases() {
		Segment above = new Segment(new Vector3(-1, 1.5, 0.5), new Vector3(2, 1.5, 0.5));
		assertEquals(0.25, above.squaredDistanceTo(CUBE), 1e-9);
		Segment diagonalNear = new Segment(new Vector3(2.0, 0.5, 0.5), new Vector3(0.5, 2.0, 0.5));
		double expected = Math.pow((1.0 / Math.sqrt(2)) * 0.5, 2); // distance from (1,1) to line x+y=2.5
		assertEquals(expected, diagonalNear.squaredDistanceTo(CUBE), 1e-9);
	}

	@Test
	void axisAlignedObbOverlap() {
		assertTrue(Obb.axisAligned(new Vector3(1.2, 0.5, 0.5), new Vector3(0.25, 0.1, 0.1)).overlaps(CUBE));
		assertFalse(Obb.axisAligned(new Vector3(1.3, 0.5, 0.5), new Vector3(0.25, 0.1, 0.1)).overlaps(CUBE));
	}

	@Test
	void turnedFootObbOverlapDiffersFromItsAabb() {
		// A long thin "foot" box rotated 45 degrees about Y, placed diagonally near the cube's corner.
		double s = Math.sqrt(0.5);
		List<Vector3> axes = List.of(new Vector3(s, 0, -s), Vector3.UNIT_Y, new Vector3(s, 0, s));
		Vector3 half = new Vector3(0.05, 0.05, 0.3);
		// Pointing along (1,0,1): its near face sits just past the corner (1,*,1) on the diagonal.
		Obb pointingAway = new Obb(new Vector3(1.23, 0.5, 1.23), axes, half);
		assertTrue(pointingAway.bounds().intersects(CUBE), "AABB alone would report contact");
		assertFalse(pointingAway.overlaps(CUBE), "oriented test knows the box misses the corner");

		// The same box moved slightly toward the corner touches it.
		Obb touching = new Obb(new Vector3(1.15, 0.5, 1.15), axes, half);
		assertTrue(touching.overlaps(CUBE));

		// A box rotated to point along (1,0,-1) at the same center misses even further.
		List<Vector3> other = List.of(new Vector3(s, 0, s), Vector3.UNIT_Y, new Vector3(s, 0, -s));
		assertFalse(new Obb(new Vector3(1.23, 0.5, 1.23), other, half).overlaps(CUBE));
	}

	@Test
	void obbOwnAxisSeparatesWhenCubeAxesDoNot() {
		// Diamond (rotated 45 degrees about Z) whose projections onto the cube's axes all overlap.
		Vector3 x = new Vector3(1, 1, 0).normalize();
		Vector3 y = new Vector3(-1, 1, 0).normalize();
		Vector3 z = Vector3.UNIT_Z;
		Obb tilted = new Obb(new Vector3(1.3, 1.3, 0.5), List.of(x, y, z), new Vector3(0.3, 0.3, 0.3));
		assertTrue(tilted.bounds().intersects(CUBE));
		assertFalse(tilted.overlaps(CUBE));
		Obb closer = new Obb(new Vector3(1.15, 1.15, 0.5), List.of(x, y, z), new Vector3(0.3, 0.3, 0.3));
		assertTrue(closer.overlaps(CUBE));
	}
}
