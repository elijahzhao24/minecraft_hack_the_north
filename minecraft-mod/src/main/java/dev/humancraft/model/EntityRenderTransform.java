package dev.humancraft.model;

import org.joml.Matrix4f;
import org.joml.Matrix4fc;

/** Immediate VBO drawing must include both Minecraft's camera matrix and the entity pose. */
public final class EntityRenderTransform {
	private EntityRenderTransform() {}

	public static Matrix4f modelView(Matrix4fc cameraView, Matrix4fc entityPose) {
		// Vanilla buffered vertices get entityPose on the CPU and cameraView in
		// the shader. Our local-space VBO needs their product in the shader.
		return new Matrix4f(cameraView).mul(entityPose);
	}
}
