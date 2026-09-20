package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class CloudBodyAnchorTest {
	private static PointCloud cloud(List<Vector3> points) {
		ByteBuffer bytes = ByteBuffer.allocate(points.size() * 16).order(ByteOrder.LITTLE_ENDIAN);
		for (Vector3 p : points) bytes.putFloat((float) p.x()).putFloat((float) p.y()).putFloat((float) p.z()).putInt(-1);
		bytes.flip();
		return new PointCloud(bytes, points.size());
	}
	@Test void denseBodyDefinesRootDespiteSparseFloorWallNoiseAndExtendedArm() {
		Random random = new Random(4);
		List<Vector3> points = new ArrayList<>();
		for (int i = 0; i < 6000; i++) points.add(new Vector3(
				2 + (random.nextDouble() - .5) * .4, random.nextDouble() * 1.8,
				4 + (random.nextDouble() - .5) * .25));
		for (int i = 0; i < 1600; i++) points.add(new Vector3(
				random.nextDouble() * 20 - 10, random.nextDouble() * 8 - 3, random.nextDouble() * 20 - 10));
		for (int i = 0; i < 400; i++) points.add(new Vector3(2.4 + random.nextDouble(), 1.3, 4));
		var body = CloudBodyAnchor.estimate(cloud(points));
		assertEquals(2, body.root().x(), .04);
		assertEquals(4, body.root().z(), .04);
		assertEquals(0, body.root().y(), .08);
		assertEquals(1.8, body.height(), .12);
		assertTrue(body.bodyPoints() < body.totalPoints() - 1000);
	}
	@Test void everyCaptureRecentersWhenPersonMovesAndEmptyCloudIsSafe() {
		List<Vector3> first = new ArrayList<>();
		for (int i = 0; i < 100; i++) first.add(new Vector3(2, i * .018, 4));
		var a = CloudBodyAnchor.estimate(cloud(first));
		var b = CloudBodyAnchor.estimate(cloud(first.stream().map(p -> p.add(new Vector3(3, 0, -2))).toList()));
		assertEquals(3, b.root().x() - a.root().x(), 1e-6);
		assertEquals(-2, b.root().z() - a.root().z(), 1e-6);
		assertEquals(0, CloudBodyAnchor.estimate(PointCloud.EMPTY).bodyPoints());
	}
}
