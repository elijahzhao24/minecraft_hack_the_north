package dev.humancraft.geometry;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ShapeRayTest {
	private static final double TOL = 1e-9;

	private static Ray ray(double ox, double oy, double oz, double dx, double dy, double dz) {
		return new Ray(new Vector3(ox, oy, oz), new Vector3(dx, dy, dz));
	}

	// ---- sphere -------------------------------------------------------------------------------

	@Test
	void sphereHeadOnHitReportsEntryDistance() {
		Sphere s = new Sphere(new Vector3(0, 0, 10), 2);
		RayHit hit = s.intersect(ray(0, 0, 0, 0, 0, 1), 100).orElseThrow();
		assertEquals(8.0, hit.distance(), TOL);
		assertEquals(new Vector3(0, 0, 8), hit.point());
	}

	@Test
	void sphereMissAndBehindOrigin() {
		Sphere s = new Sphere(new Vector3(0, 5, 10), 2);
		assertTrue(s.intersect(ray(0, 0, 0, 0, 0, 1), 100).isEmpty());
		assertTrue(s.intersect(ray(0, 5, 20, 0, 0, 1), 100).isEmpty(), "sphere behind the origin");
		assertTrue(s.intersect(ray(0, 5, 0, 0, 0, 1), 5).isEmpty(), "beyond max distance");
	}

	@Test
	void sphereTangentRayCountsAsHit() {
		Sphere s = new Sphere(new Vector3(0, 2, 10), 2);
		Optional<RayHit> hit = s.intersect(ray(0, 0, 0, 0, 0, 1), 100);
		assertTrue(hit.isPresent());
		assertEquals(10.0, hit.get().distance(), 1e-6);
	}

	@Test
	void sphereOriginInsideHitsAtZero() {
		Sphere s = new Sphere(new Vector3(0, 0, 0), 2);
		RayHit hit = s.intersect(ray(0.5, 0, 0, 1, 0, 0), 100).orElseThrow();
		assertEquals(0.0, hit.distance(), TOL);
		assertEquals(new Vector3(0.5, 0, 0), hit.point());
	}

	@Test
	void sphereRejectsInvalidGeometry() {
		assertThrows(IllegalArgumentException.class, () -> new Sphere(Vector3.ZERO, 0));
		assertThrows(IllegalArgumentException.class, () -> new Sphere(Vector3.ZERO, Double.NaN));
		assertThrows(IllegalArgumentException.class, () -> new Sphere(new Vector3(Double.POSITIVE_INFINITY, 0, 0), 1));
	}

	// ---- capsule ------------------------------------------------------------------------------

	@Test
	void capsuleBodyHitPerpendicular() {
		Capsule c = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 2, 0), 0.5);
		RayHit hit = c.intersect(ray(5, 1, 0, -1, 0, 0), 100).orElseThrow();
		assertEquals(4.5, hit.distance(), 1e-9);
		assertEquals(0.5, hit.point().x(), 1e-9);
	}

	@Test
	void capsuleCapHitAlongAxis() {
		Capsule c = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 2, 0), 0.5);
		RayHit hit = c.intersect(ray(0, 10, 0, 0, -1, 0), 100).orElseThrow();
		assertEquals(7.5, hit.distance(), 1e-9, "enters through the top hemisphere");
	}

	@Test
	void capsuleParallelRayOutsideRadiusMisses() {
		Capsule c = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 2, 0), 0.5);
		assertTrue(c.intersect(ray(1, -5, 0, 0, 1, 0), 100).isEmpty());
		// Parallel but within radius: enters through the bottom cap.
		RayHit hit = c.intersect(ray(0.3, -5, 0, 0, 1, 0), 100).orElseThrow();
		assertEquals(5 - Math.sqrt(0.25 - 0.09), hit.distance(), 1e-9);
	}

	@Test
	void capsuleMissesBesideTheSegment() {
		Capsule c = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 2, 0), 0.5);
		assertTrue(c.intersect(ray(5, 3, 0, -1, 0, 0), 100).isEmpty(), "above the top cap");
		assertTrue(c.intersect(ray(5, 1, 0.6, -1, 0, 0), 100).isEmpty(), "offset beyond radius");
	}

	@Test
	void capsuleOriginInsideAndDegenerate() {
		Capsule c = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 2, 0), 0.5);
		assertEquals(0.0, c.intersect(ray(0.1, 1, 0, 1, 0, 0), 100).orElseThrow().distance(), TOL);
		Capsule degenerate = new Capsule(new Vector3(0, 0, 0), new Vector3(0, 0, 0), 0.5);
		assertEquals(4.5, degenerate.intersect(ray(5, 0, 0, -1, 0, 0), 100).orElseThrow().distance(), 1e-9);
	}

	@Test
	void capsuleRejectsInvalidGeometry() {
		assertThrows(IllegalArgumentException.class, () -> new Capsule(Vector3.ZERO, Vector3.UNIT_Y, -1));
		assertThrows(IllegalArgumentException.class, () -> new Capsule(Vector3.ZERO, new Vector3(0, Double.NaN, 0), 1));
	}

	// ---- obb ----------------------------------------------------------------------------------

	@Test
	void axisAlignedObbBehavesLikeAabb() {
		Obb box = Obb.axisAligned(new Vector3(0, 0, 10), new Vector3(1, 1, 1));
		RayHit hit = box.intersect(ray(0, 0, 0, 0, 0, 1), 100).orElseThrow();
		assertEquals(9.0, hit.distance(), TOL);
		assertTrue(box.intersect(ray(0, 1.5, 0, 0, 0, 1), 100).isEmpty());
	}

	@Test
	void rotatedObbHitMatchesAnalyticCorner() {
		// Box rotated 45 degrees about Y: its corner points straight at the origin along +Z.
		double s = Math.sqrt(0.5);
		Obb box = new Obb(new Vector3(0, 0, 10),
				List.of(new Vector3(s, 0, -s), Vector3.UNIT_Y, new Vector3(s, 0, s)), new Vector3(1, 1, 1));
		RayHit hit = box.intersect(ray(0, 0, 0, 0, 0, 1), 100).orElseThrow();
		assertEquals(10 - Math.sqrt(2), hit.distance(), 1e-9);
		// (1.2, *, 8.8) is inside the box's AABB but outside the rotated diamond footprint.
		Ray throughCornerGap = ray(1.2, -5, 8.8, 0, 1, 0);
		assertTrue(box.intersect(throughCornerGap, 100).isEmpty());
		assertTrue(box.bounds().intersectRay(throughCornerGap, 100).isPresent(), "broad phase would pass");
	}

	@Test
	void obbOriginInsideHitsAtZero() {
		Obb box = Obb.axisAligned(new Vector3(0, 0, 0), new Vector3(1, 1, 1));
		assertEquals(0.0, box.intersect(ray(0.2, 0.2, 0.2, 1, 0, 0), 100).orElseThrow().distance(), TOL);
	}

	@Test
	void obbRejectsBadAxes() {
		assertThrows(IllegalArgumentException.class, () -> new Obb(Vector3.ZERO,
				List.of(Vector3.UNIT_X, new Vector3(0.5, 1, 0), Vector3.UNIT_Z), new Vector3(1, 1, 1)));
		assertThrows(IllegalArgumentException.class, () -> new Obb(Vector3.ZERO,
				List.of(Vector3.UNIT_X, Vector3.UNIT_Y, new Vector3(0, 0, 2)), new Vector3(1, 1, 1)));
		assertThrows(IllegalArgumentException.class, () -> Obb.axisAligned(Vector3.ZERO, new Vector3(1, 0, 1)));
	}

	// ---- transform consistency ------------------------------------------------------------

	@Test
	void transformedShapesHitAtScaledDistance() {
		Vector3 anchor = new Vector3(100, 64, -20);
		double scale = 2.5;
		Sphere s = new Sphere(new Vector3(0, 1, 0), 0.2);
		Sphere ws = s.transform(anchor, scale);
		Ray stageRay = ray(0, 1, 5, 0, 0, -1);
		Ray worldRay = new Ray(anchor.add(stageRay.origin().scale(scale)), stageRay.direction());
		double stageT = s.intersect(stageRay, 100).orElseThrow().distance();
		double worldT = ws.intersect(worldRay, 100).orElseThrow().distance();
		assertEquals(stageT * scale, worldT, 1e-9);

		Capsule c = new Capsule(new Vector3(0, 0.5, 0), new Vector3(0, 1.5, 0), 0.1).transform(anchor, scale);
		assertEquals(new Vector3(100, 64 + 1.25, -20), c.a());
		assertEquals(0.25, c.radius(), TOL);

		Obb o = Obb.axisAligned(new Vector3(0, 1, 0), new Vector3(0.1, 0.2, 0.3)).transform(anchor, scale);
		assertEquals(new Vector3(0.25, 0.5, 0.75), o.halfExtents());
		assertEquals(Vector3.UNIT_X, o.axes().get(0), "axes are directions and must not scale");
	}

	@Test
	void aabbSlabTestHandlesParallelAxes() {
		Aabb box = new Aabb(new Vector3(-1, -1, 5), new Vector3(1, 1, 7));
		assertEquals(5.0, box.intersectRay(ray(0, 0, 0, 0, 0, 1), 100).orElseThrow(), TOL);
		assertTrue(box.intersectRay(ray(2, 0, 0, 0, 0, 1), 100).isEmpty(), "parallel and outside slab");
		assertEquals(0.0, box.intersectRay(ray(0, 0, 6, 0, 0, 1), 100).orElseThrow(), TOL, "origin inside");
		assertFalse(box.intersects(new Aabb(new Vector3(2, 2, 2), new Vector3(3, 3, 3))));
	}
}
