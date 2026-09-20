package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SwingArcTest {
	@Test void forwardHalfSphereHasInclusiveFiveBlockAndSideBoundaries() {
		assertTrue(SwingArc.contains(Vector3.ZERO, 0, new Vector3(0, 0, 4.99)));
		assertTrue(SwingArc.contains(Vector3.ZERO, 0, new Vector3(0, 0, 5)));
		assertFalse(SwingArc.contains(Vector3.ZERO, 0, new Vector3(0, 0, 5.01)));
		assertTrue(SwingArc.contains(Vector3.ZERO, 0, new Vector3(5, 0, 0)));
		assertTrue(SwingArc.contains(Vector3.ZERO, 0, new Vector3(-5, 0, 0)));
		assertFalse(SwingArc.contains(Vector3.ZERO, 0, new Vector3(0, 0, -0.01)));
		assertFalse(SwingArc.contains(Vector3.ZERO, 0, new Vector3(0, 4, 4)));
	}
	@Test void followsMinecraftYawAndPlayerPosition() {
		Vector3 player = new Vector3(100, 64, 200);
		assertTrue(SwingArc.contains(player, 90, player.add(new Vector3(-4, 0, 0))));
		assertFalse(SwingArc.contains(player, 90, player.add(new Vector3(4, 0, 0))));
		assertTrue(SwingArc.contains(player, 180, player.add(new Vector3(0, 0, -4))));
		assertFalse(SwingArc.contains(player, Float.NaN, player));
	}
}
