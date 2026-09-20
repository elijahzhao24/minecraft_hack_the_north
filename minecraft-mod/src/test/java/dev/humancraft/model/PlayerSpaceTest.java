package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class PlayerSpaceTest {
	@Test
	void localRootFollowsPlayerPositionAndYaw() {
		Vector3 position = new Vector3(10, 64, -3);
		assertEquals(position, PlayerSpace.worldPoint(Vector3.ZERO, position, 0));
		Vector3 turned = PlayerSpace.worldPoint(new Vector3(0, 1, 1), position, 90);
		assertEquals(9.0, turned.x(), 1e-9);
		assertEquals(65.0, turned.y(), 1e-9);
		assertEquals(-3.0, turned.z(), 1e-9);
	}
}
