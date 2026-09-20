package dev.humancraft.model;

import dev.humancraft.contract.*;
import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;
import java.nio.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class ScanNormalizationTest {
	private static CharacterFrame frame(double cloudX, List<LandmarkDto> landmarks) {
		ByteBuffer bytes = ByteBuffer.allocate(1600).order(ByteOrder.LITTLE_ENDIAN);
		for (int i = 0; i < 100; i++) bytes.putFloat((float) cloudX).putFloat(i * .018f).putFloat(4).putInt(-1);
		bytes.flip();
		return new CharacterFrame(ArmSwingDetectorTest.header(1, 1, Mode.LIVE,
				ArmSwingDetectorTest.BASE.header().sessionId(), ArmSwingDetectorTest.BASE.header().calibrationId(), landmarks), new PointCloud(bytes, 100));
	}
	private static List<LandmarkDto> hips() {
		return List.of(ArmSwingDetectorTest.landmark("body.left_hip", new Vector3(2.2, .9, 4)),
				ArmSwingDetectorTest.landmark("body.right_hip", new Vector3(1.8, .9, 4)));
	}
	@Test void unevenCloudAndArmMotionCannotPullHipsOffPlayerOrigin() {
		var normalization = new ScanNormalization();
		var first = normalization.transform(frame(2.1, hips()));
		var next = normalization.transform(frame(2.15, hips()));
		assertEquals(0, first.point(new Vector3(2, .9, 4)).x(), 1e-8);
		assertEquals(0, next.point(new Vector3(2, .9, 4)).z(), 1e-8);
		assertEquals(first, next);
	}
	@Test void lostHipsFollowCurrentCloudInsteadOfLeavingItThreeBlocksAway() {
		var normalization = new ScanNormalization();
		var first = normalization.transform(frame(2.1, hips()));
		var moved = normalization.transform(frame(5, List.of()));
		assertEquals(0, moved.point(new Vector3(5, .9, 4)).x(), 1e-8);
		assertEquals(first.blocksPerMeter(), moved.blocksPerMeter(), 1e-8);
		normalization.reset();
		var fallback = normalization.transform(frame(5, List.of()));
		assertEquals(0, fallback.point(new Vector3(5, 0, 4)).x(), 1e-8);
		assertNotEquals(first.anchor(), fallback.anchor());
	}
	@Test void feetAnchorAtGroundAndManualScaleIsPreserved() {
		var normalization = new ScanNormalization();
		var landmarks = new ArrayList<>(hips());
		landmarks.add(ArmSwingDetectorTest.landmark("body.left_heel", new Vector3(2, .025, 4)));
		var first = normalization.transform(frame(2.1, landmarks));
		assertEquals(0, first.point(new Vector3(2, 0, 4)).length(), 1e-8);
		normalization.scaleBy(1.1);
		var next = normalization.transform(frame(2.1, landmarks));
		assertEquals(first.blocksPerMeter() * 1.1, next.blocksPerMeter(), 1e-8);
	}
}
