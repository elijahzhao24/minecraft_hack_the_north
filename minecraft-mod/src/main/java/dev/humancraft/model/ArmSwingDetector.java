package dev.humancraft.model;

import dev.humancraft.contract.CharacterFrameHeader;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.contract.LandmarkSource;
import dev.humancraft.contract.Mode;
import dev.humancraft.geometry.Vector3;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

/** Detects a fast wrist excursion in stage meters, independently of avatar normalization. */
public final class ArmSwingDetector {
	public record Swing(boolean left, double speedMetersPerSecond) {}
	private record Sample(Vector3 relative, String provenance) {}
	private static final class Arm {
		Sample previous;
		double speed = Double.NaN;
		boolean armed;
		void reset() { previous = null; speed = Double.NaN; armed = false; }
	}
	private final Arm left = new Arm(), right = new Arm();
	private final double threshold;
	private final long cooldownMs;
	private UUID session, calibration;
	private String phoneSessions;
	private long lastFrame = -1, lastArrival = -1, lastSwing = -1;
	private double lastCapture;

	public ArmSwingDetector(double threshold, long cooldownMs) {
		if (!Double.isFinite(threshold) || threshold <= 0 || cooldownMs < 0)
			throw new IllegalArgumentException("invalid swing settings");
		this.threshold = threshold;
		this.cooldownMs = cooldownMs;
	}

	public void reset() {
		left.reset(); right.reset(); session = null; calibration = null;
		phoneSessions = null; lastFrame = -1; lastArrival = -1; lastSwing = -1;
	}

	public double leftSpeed() { return left.speed; }
	public double rightSpeed() { return right.speed; }

	public Optional<Swing> update(CharacterFrameHeader frame, long arrivalMs) {
		if (frame.mode() != Mode.LIVE || !frame.quality().valid()) {
			reset();
			return Optional.empty();
		}
		String phones = frame.sourceFrames().stream()
				.map(s -> s.deviceId() + ":" + s.sessionId()).sorted().collect(Collectors.joining("|"));
		boolean changed = !frame.sessionId().equals(session) || !frame.calibrationId().equals(calibration)
				|| !phones.equals(phoneSessions);
		if (!changed && frame.frameId() <= lastFrame) return Optional.empty();
		double dt = frame.normalizedCaptureTimeS() - lastCapture;
		boolean gap = changed || lastArrival < 0 || arrivalMs < lastArrival || arrivalMs - lastArrival > 350
				|| dt < 0.02 || dt > 0.35;
		if (gap) { left.reset(); right.reset(); }
		session = frame.sessionId(); calibration = frame.calibrationId(); phoneSessions = phones;
		lastFrame = frame.frameId(); lastCapture = frame.normalizedCaptureTimeS(); lastArrival = arrivalMs;
		boolean l = updateArm(left, sample(frame.landmarks(), "left"), dt);
		boolean r = updateArm(right, sample(frame.landmarks(), "right"), dt);
		if ((!l && !r) || (lastSwing >= 0 && arrivalMs - lastSwing < cooldownMs)) return Optional.empty();
		lastSwing = arrivalMs;
		boolean useLeft = l && (!r || left.speed >= right.speed);
		return Optional.of(new Swing(useLeft, useLeft ? left.speed : right.speed));
	}

	private boolean updateArm(Arm arm, Sample current, double dt) {
		if (current == null) { arm.reset(); return false; }
		Sample previous = arm.previous;
		arm.previous = current;
		if (previous == null || !previous.provenance.equals(current.provenance)) {
			arm.speed = Double.NaN;
			arm.armed = false;
			return false;
		}
		double distance = current.relative.distanceTo(previous.relative);
		arm.speed = distance / dt;
		if (!Double.isFinite(arm.speed) || arm.speed > 12) {
			// A tracking jump must settle before it can arm again.
			arm.speed = Double.NaN; arm.armed = false;
			return false;
		}
		if (arm.speed < threshold * 0.4) arm.armed = true;
		if (arm.armed && arm.speed >= threshold && distance >= 0.08) {
			arm.armed = false;
			return true;
		}
		return false;
	}

	/** Model-prior fills cannot cause damage when the wrist is actually occluded. */
	public static boolean observed(LandmarkDto l) {
		return l.valid() && l.position().isPresent() && !l.observedBy().isEmpty()
				&& (l.source() == LandmarkSource.DEPTH_NEIGHBORHOOD || l.source() == LandmarkSource.TRIANGULATED)
				&& l.confidence().orElse(1.0) >= 0.35 && l.visibility().orElse(1.0) >= 0.5;
	}

	private static Sample sample(List<LandmarkDto> landmarks, String side) {
		LandmarkDto shoulder = find(landmarks, "body." + side + "_shoulder");
		LandmarkDto wrist = find(landmarks, "body." + side + "_wrist");
		if (wrist == null) wrist = find(landmarks, "hand." + side + ".wrist");
		if (shoulder == null || wrist == null) return null;
		Vector3 relative = wrist.position().orElseThrow().sub(shoulder.position().orElseThrow());
		if (relative.length() < 0.1 || relative.length() > 1.2) return null;
		return new Sample(relative, provenance(shoulder) + "/" + provenance(wrist));
	}

	private static String provenance(LandmarkDto l) {
		return l.name() + ":" + l.source() + ":" + l.observedBy().stream().sorted().collect(Collectors.joining(","));
	}

	private static LandmarkDto find(List<LandmarkDto> landmarks, String name) {
		return landmarks.stream().filter(l -> l.name().equals(name) && observed(l)).findFirst().orElse(null);
	}
}
