package dev.humancraft.model;

import dev.humancraft.contract.*;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class ArmSwingDetectorTest {
	static final CharacterFrame BASE = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 0, Mode.LIVE, 16);
	static LandmarkDto landmark(String name, Vector3 p) {
		return new LandmarkDto(name, Optional.of(p), true, LandmarkSource.DEPTH_NEIGHBORHOOD,
				Optional.of(0.9), Optional.of(0.9), List.of("front-phone"), Optional.empty());
	}
	static CharacterFrameHeader header(long id, double time, Mode mode, UUID session, UUID calibration, List<LandmarkDto> landmarks) {
		var h = BASE.header();
		return new CharacterFrameHeader(session, calibration, id, h.fusionId(), h.sourceFrames(), time, 0,
				mode, h.quality(), landmarks, h.colliders(), h.trace(), h.buffers());
	}
	static List<LandmarkDto> arms(double wristZ, Vector3 translation) {
		List<LandmarkDto> result = new ArrayList<>();
		for (String side : List.of("left", "right")) {
			double x = side.equals("left") ? 0.2 : -0.2;
			result.add(landmark("body." + side + "_shoulder", new Vector3(x, 1.4, 0).add(translation)));
			result.add(landmark("body." + side + "_wrist", new Vector3(x, 1.1, wristZ).add(translation)));
		}
		return result;
	}
	static CharacterFrameHeader frame(long id, double z) { return frame(id, z, Vector3.ZERO); }
	static CharacterFrameHeader frame(long id, double z, Vector3 offset) {
		return header(id, id * 0.1, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), arms(z, offset));
	}
	static void settle(ArmSwingDetector d) { assertTrue(d.update(frame(0, 0), 1000).isEmpty()); assertTrue(d.update(frame(1, 0), 1100).isEmpty()); }

	@Test void fastWristTriggersOnceForBothArmsUntilSlowAndCooldown() {
		var d = new ArmSwingDetector(1.5, 500); settle(d);
		var swing = d.update(frame(2, 0.3), 1200).orElseThrow();
		assertTrue(swing.left()); assertEquals(3, swing.speedMetersPerSecond(), 1e-8);
		assertTrue(d.update(frame(3, 0.6), 1300).isEmpty());
		assertTrue(d.update(frame(4, 0.3), 1400).isEmpty());
		assertTrue(d.update(frame(5, 0.3), 1500).isEmpty());
		assertTrue(d.update(frame(6, 0.6), 1600).isEmpty(), "cooldown suppresses both arms");
		d.update(frame(7, 0.6), 1700);
		assertTrue(d.update(frame(8, 0.3), 1800).isPresent());
	}
	@Test void walkingAndLowAmplitudeJitterDoNotPunch() {
		var d = new ArmSwingDetector(1.5, 500); settle(d);
		assertTrue(d.update(frame(2, 0, new Vector3(0.5, 0.3, 0.4)), 1200).isEmpty());
		assertEquals(0, d.leftSpeed(), 1e-8);
		assertTrue(d.update(frame(3, 0.02), 1300).isEmpty());
	}
	@Test void missingOrModelPriorWristsCannotHit() {
		for (boolean missing : List.of(true, false)) {
			var d = new ArmSwingDetector(1.5, 500); settle(d);
			var landmarks = arms(0.3, Vector3.ZERO).stream().filter(l -> !missing || !l.name().endsWith("_wrist"))
					.map(l -> l.name().endsWith("_wrist") ? new LandmarkDto(l.name(), l.position(), true,
							LandmarkSource.REGISTERED_MODEL_PRIOR, l.confidence(), l.visibility(), List.of(), l.reprojectionErrorPx()) : l).toList();
			assertTrue(d.update(header(2, 0.2, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), landmarks), 1200).isEmpty());
			assertTrue(Double.isNaN(d.leftSpeed()));
			assertTrue(d.update(frame(3, 0.6), 1300).isEmpty(), "reacquisition warms up");
		}
	}
	@Test void staleGapOutOfOrderAndSnapshotsNeverSwing() {
		var d = new ArmSwingDetector(1.5, 500); settle(d);
		assertTrue(d.update(frame(1, 0.3), 1200).isEmpty());
		assertTrue(d.update(frame(2, 0.3), 1700).isEmpty(), "old arrival");
		assertTrue(d.update(frame(9, 0.6), 1700).isEmpty(), "capture gap");
		assertTrue(d.update(header(10, 1, Mode.SNAPSHOT, BASE.header().sessionId(), BASE.header().calibrationId(), arms(0, Vector3.ZERO)), 1800).isEmpty());
	}
	@Test void newSessionOrCalibrationNeedsNewBaseline() {
		for (boolean session : List.of(true, false)) {
			var d = new ArmSwingDetector(1.5, 500); settle(d);
			assertTrue(d.update(header(2, 0.2, Mode.LIVE, session ? UUID.randomUUID() : BASE.header().sessionId(),
					session ? BASE.header().calibrationId() : UUID.randomUUID(), arms(0.3, Vector3.ZERO)), 1200).isEmpty());
		}
	}
	@Test void handFallbackWorksButChangingLandmarkSourceNeedsSettling() {
		var d = new ArmSwingDetector(1.5, 500);
		for (int i = 0; i < 3; i++) {
			var landmarks = arms(i == 2 ? 0.3 : 0, Vector3.ZERO).stream()
					.filter(l -> !l.name().contains("right"))
					.map(l -> landmark(l.name().replace("body.left_wrist", "hand.left.wrist"), l.position().orElseThrow())).toList();
			var swing = d.update(header(i, i * 0.1, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), landmarks), 1000 + i * 100);
			assertEquals(i == 2, swing.isPresent());
		}
		assertTrue(d.update(frame(3, 0.6), 1800).isEmpty());
	}
	@Test void rightArmCanTriggerAloneAndThresholdIsConfigurable() {
		var d = new ArmSwingDetector(4, 500); settle(d);
		assertTrue(d.update(frame(2, 0.3), 1200).isEmpty());
		d.update(frame(3, 0.3), 1300);
		var lm = arms(0.3, Vector3.ZERO).stream().map(l -> l.name().equals("body.right_wrist")
				? landmark(l.name(), new Vector3(-0.2, 1.1, -0.3)) : l).toList();
		assertFalse(d.update(header(4, 0.4, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), lm), 1400).orElseThrow().left());
	}
	@Test void eitherHandTriggersImmediatelyAtFifteenFpsWithoutSlowPose() {
		for (String side : List.of("left", "right")) {
			var d = new ArmSwingDetector(1.5, 500);
			var base = arms(0, Vector3.ZERO).stream().filter(l -> l.name().contains(side)).toList();
			d.update(header(0, 0, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), base), 1000);
			var moving = arms(.12, Vector3.ZERO).stream().filter(l -> l.name().contains(side)).map(l ->
					new LandmarkDto(l.name(), l.position(), true, LandmarkSource.TRIANGULATED, l.confidence(),
							l.visibility(), List.of("front-phone", "rear-phone"), Optional.empty())).toList();
			var swing = d.update(header(1, 1.0 / 15, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), moving), 1067).orElseThrow();
			assertEquals(side.equals("left"), swing.left());
			assertEquals(1.8, swing.speedMetersPerSecond(), 1e-6);
		}
	}

	@Test void elbowCanTriggerWhenWristIsOccluded() {
		var d = new ArmSwingDetector(1.5, 500);
		for (int i = 0; i < 2; i++) {
			var lm = List.of(landmark("body.right_shoulder", new Vector3(0, 1.4, 0)),
					landmark("body.right_elbow", new Vector3(0, 1.15, i * .12)));
			var swing = d.update(header(i, i / 15.0, Mode.LIVE, BASE.header().sessionId(), BASE.header().calibrationId(), lm), 1000 + i * 67);
			assertEquals(i == 1, swing.isPresent());
		}
	}

	@Test void lowConfidenceAndImplausibleArmAreRejected() {
		var d = new ArmSwingDetector(1.5, 500); settle(d);
		assertTrue(d.update(frame(2, 2), 1200).isEmpty());
		var bad = landmark("body.left_wrist", new Vector3(.2, 1.1, .3));
		assertFalse(ArmSwingDetector.observed(new LandmarkDto(bad.name(), bad.position(), true, bad.source(),
				Optional.of(0.1), bad.visibility(), bad.observedBy(), Optional.empty())));
	}
}
