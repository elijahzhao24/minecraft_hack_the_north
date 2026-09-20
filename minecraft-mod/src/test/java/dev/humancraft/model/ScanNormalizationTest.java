package dev.humancraft.model;

import dev.humancraft.contract.*;
import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;
import java.nio.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class ScanNormalizationTest {
	private static long sequence;
	private static CharacterFrame frame(double cloudX, List<LandmarkDto> landmarks) {
		ByteBuffer bytes = ByteBuffer.allocate(1600).order(ByteOrder.LITTLE_ENDIAN);
		for (int i = 0; i < 100; i++) bytes.putFloat((float) cloudX).putFloat(i * .018f).putFloat(4).putInt(-1);
		bytes.flip();
		long id = ++sequence;
		return new CharacterFrame(ArmSwingDetectorTest.header(id, id / 15.0, Mode.LIVE,
				ArmSwingDetectorTest.BASE.header().sessionId(), ArmSwingDetectorTest.BASE.header().calibrationId(), landmarks), new PointCloud(bytes, 100));
	}
	private static List<LandmarkDto> hips() {
		return List.of(ArmSwingDetectorTest.landmark("body.left_hip", new Vector3(2.2, .9, 4)),
				ArmSwingDetectorTest.landmark("body.right_hip", new Vector3(1.8, .9, 4)));
	}
	@Test void observedHipsStayCenteredAsCloudDensityChanges() {
		var normalization = new ScanNormalization();
		var first = normalization.transform(frame(2.1, hips()));
		var next = normalization.transform(frame(2.15, hips()));
		assertEquals(0, first.point(new Vector3(2, .9, 4)).x(), 1e-6);
		assertEquals(0, next.point(new Vector3(2, .9, 4)).z(), 1e-6);
		assertEquals(0, next.point(new Vector3(2, .9, 4)).x(), 1e-6);
	}
	@Test void lostHipsFollowCurrentCloudInsteadOfLeavingItThreeBlocksAway() {
		var normalization = new ScanNormalization();
		var first = normalization.transform(frame(2.1, hips()));
		var moved = normalization.transform(frame(5, List.of()));
		assertEquals(0, moved.point(new Vector3(5, .9, 4)).x(), 1e-6);
		assertEquals(first.blocksPerMeter(), moved.blocksPerMeter(), 1e-6);
		normalization.reset();
		var fallback = normalization.transform(frame(5, List.of()));
		assertEquals(0, fallback.point(new Vector3(5, 0, 4)).x(), 1e-6);
		assertNotEquals(first.anchor(), fallback.anchor());
	}
	@Test void feetAnchorAtGroundAndManualScaleIsPreserved() {
		var normalization = new ScanNormalization();
		var landmarks = new ArrayList<>(hips());
		landmarks.add(ArmSwingDetectorTest.landmark("body.left_heel", new Vector3(2, .025, 4)));
		var first = normalization.transform(frame(2.1, landmarks));
		assertEquals(0, first.point(new Vector3(2, 0, 4)).length(), 1e-6);
		normalization.scaleBy(1.1);
		var next = normalization.transform(frame(2.1, landmarks));
		assertEquals(first.blocksPerMeter() * 1.1, next.blocksPerMeter(), 1e-6);
	}
	@Test void fallbackNoiseIsSmoothedAndReinstallDoesNotMoveAnchor() {
		var normalization = new ScanNormalization();
		normalization.transform(frame(2, List.of()));
		var noisyFrame = frame(2.06, List.of());
		var next = normalization.transform(noisyFrame);
		double rootX = -next.anchor().x() / next.blocksPerMeter();
		assertTrue(rootX > 2 && rootX < 2.03, "six cm noise should be damped");
		assertEquals(next, normalization.transform(noisyFrame));
	}
	@Test void missingHipsDoNotCauseAnImmediateJumpAndWalkingHasBoundedLag() {
		var normalization = new ScanNormalization();
		normalization.transform(frame(2.1, hips()));
		var missing = normalization.transform(frame(2.1, List.of()));
		assertTrue(Math.abs(missing.anchor().x() / missing.blocksPerMeter() + 2) < .04);
		for (int i = 1; i <= 30; i++) {
			double x = 2.1 + i * .1;
			var moved = normalization.transform(frame(x, List.of()));
			assertTrue(Math.abs(moved.point(new Vector3(x, .9, 4)).x()) <= .151 * moved.blocksPerMeter());
		}
	}
}
