package dev.humancraft.model;

import org.joml.Matrix4f;
import org.joml.Vector3f;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class EntityRenderTransformTest {
	@Test void cloudOriginFollowsVanillaShadowUnderCameraRotationAndEntityYaw() {
		for (float yaw : new float[] {0, 35, 90, 180}) {
			Matrix4f view = new Matrix4f().rotateX(.25f).rotateY((float) Math.toRadians(yaw));
			Matrix4f pose = new Matrix4f().translation(2, -1.6f, -4).rotateY(.8f);
			Matrix4f originalView = new Matrix4f(view), originalPose = new Matrix4f(pose);
			Vector3f vanillaShadow = view.transformPosition(pose.transformPosition(new Vector3f()));
			Vector3f cloudOrigin = EntityRenderTransform.modelView(view, pose).transformPosition(new Vector3f());
			assertTrue(vanillaShadow.distance(cloudOrigin) < 1e-5);
			assertEquals(originalView, view);
			assertEquals(originalPose, pose);
			if (yaw == 90) assertTrue(vanillaShadow.distance(pose.transformPosition(new Vector3f())) > 3,
					"omitting the global camera matrix reproduces the several-block shadow offset");
		}
	}
}
