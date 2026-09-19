package dev.humancraft.model;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Aabb;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class StageToWorldTest {
	private static final StageToWorld T = new StageToWorld(new Vector3(100.5, 64, -32.5), 2.0);

	@Test
	void pointsLandmarksAndCollidersUseTheSameMapping() {
		CharacterFrame frame = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 7, Mode.SNAPSHOT, 500);
		WorldSnapshot world = T.snapshot(frame);

		assertEquals(frame.header().landmarks().size(), world.landmarks().size());
		for (int i = 0; i < world.landmarks().size(); i++) {
			LandmarkDto stage = frame.header().landmarks().get(i);
			LandmarkDto w = world.landmarks().get(i);
			assertEquals(T.point(stage.position().orElseThrow()), w.position().orElseThrow());
		}

		for (int i = 0; i < world.colliders().size(); i++) {
			ColliderDto stage = frame.header().colliders().get(i);
			ColliderDto w = world.colliders().get(i);
			assertEquals(stage.id(), w.id());
			assertEquals(stage.bodyPart(), w.bodyPart());
			Shape ws = w.geometry().orElseThrow();
			switch (stage.geometry().orElseThrow()) {
				case Sphere s -> {
					assertEquals(T.point(s.center()), ((Sphere) ws).center());
					assertEquals(T.length(s.radius()), ((Sphere) ws).radius(), 1e-12);
				}
				case Capsule c -> {
					assertEquals(T.point(c.a()), ((Capsule) ws).a());
					assertEquals(T.point(c.b()), ((Capsule) ws).b());
					assertEquals(T.length(c.radius()), ((Capsule) ws).radius(), 1e-12);
				}
				case Obb o -> {
					assertEquals(T.point(o.center()), ((Obb) ws).center());
					assertEquals(o.axes(), ((Obb) ws).axes());
					assertEquals(o.halfExtents().scale(2.0), ((Obb) ws).halfExtents());
				}
			}
		}

		PointCloud transformed = frame.cloud().transform(T.anchor(), T.blocksPerMeter());
		for (int i = 0; i < transformed.count(); i += 37) {
			Vector3 expected = T.point(frame.cloud().position(i));
			assertEquals(expected.x(), transformed.x(i), 1e-4);
			assertEquals(expected.y(), transformed.y(i), 1e-4);
			assertEquals(expected.z(), transformed.z(i), 1e-4);
			assertEquals(frame.cloud().r(i), transformed.r(i));
		}
	}

	@Test
	void everyFixturePointLiesOnSomeWorldCollider() {
		// Points are sampled on collider surfaces, so after the transform each must be within a hair of one.
		CharacterFrame frame = SyntheticHuman.frame(SyntheticHuman.Pose.BENT_LEFT_ARM, 7, Mode.SNAPSHOT, 400);
		WorldSnapshot world = T.snapshot(frame);
		Vector3 eps = new Vector3(1e-3, 1e-3, 1e-3);
		for (int i = 0; i < frame.cloud().count(); i++) {
			Vector3 p = T.point(frame.cloud().position(i));
			Aabb probe = new Aabb(p.sub(eps), p.add(eps));
			boolean near = false;
			for (ColliderDto c : world.colliders()) {
				if (c.geometry().orElseThrow().overlaps(probe)) {
					near = true;
					break;
				}
			}
			assertTrue(near, "point " + i + " at " + p + " is not on any collider surface");
		}
	}

	@Test
	void hitPointsScaleWithTheSnapshot() {
		Sphere stage = new Sphere(new Vector3(0, 1.6, 0), 0.1);
		Sphere world = (Sphere) T.shape(stage);
		Ray ray = new Ray(T.point(new Vector3(0, 1.6, 3)), new Vector3(0, 0, -1));
		double distance = world.intersect(ray, 100).orElseThrow().distance();
		assertEquals(T.length(3 - 0.1), distance, 1e-9);
		assertTrue(T.point(new Vector3(0, 1.6, 0.1)).sub(ray.at(distance)).length() < 1e-9);
	}

	@Test
	void rejectsInvalidTransforms() {
		assertThrows(IllegalArgumentException.class, () -> new StageToWorld(Vector3.ZERO, 0));
		assertThrows(IllegalArgumentException.class, () -> new StageToWorld(Vector3.ZERO, Double.NaN));
		assertThrows(IllegalArgumentException.class, () -> new StageToWorld(new Vector3(Double.NaN, 0, 0), 1));
	}
}
