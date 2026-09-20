package dev.humancraft.model;

import dev.humancraft.contract.CharacterFrameHeader;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.contract.LandmarkSource;
import dev.humancraft.contract.Mode;
import dev.humancraft.geometry.Vector3;

import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.Set;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

/** Detects a fast wrist excursion in stage meters, independently of avatar normalization. */
public final class ArmSwingDetector {
	public record Swing(boolean left, double speedMetersPerSecond) {}
	private record Sample(Vector3 relative, String anchor, Set<String> cameras) {}
	private static final class Arm {
		Map<String, Sample> previous = Map.of();
		double speed = Double.NaN;
		void reset() { previous = Map.of(); speed = Double.NaN; }
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
		boolean gap = changed || lastArrival < 0 || arrivalMs < lastArrival || arrivalMs - lastArrival > 500
				|| dt < 0.02 || dt > 0.5;
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

	private boolean updateArm(Arm arm, Map<String, Sample> current, double dt) {
		double fastest = Double.NaN;
		for (var entry : current.entrySet()) {
			Sample sample = entry.getValue(), previous = arm.previous.get(entry.getKey());
			if (previous == null || !previous.anchor.equals(sample.anchor)) continue;
			double distance = sample.relative.distanceTo(previous.relative);
			// Ignore a large discontinuity when a completely different camera takes over.
			// Adding/removing a contributing camera or depth->triangulated changes are normal.
			boolean sharedCamera = sample.cameras.stream().anyMatch(previous.cameras::contains);
			if (!sharedCamera && distance > 0.12) continue;
			double speed = distance / dt;
			if (!Double.isFinite(speed) || speed > 12) continue;
			if (Double.isNaN(fastest) || speed > fastest) fastest = speed;
		}
		arm.previous = current;
		arm.speed = fastest;
		// Either arm can trigger as soon as its speed crosses the threshold.
		// No slow pose or 8 cm/frame requirement; the shared cooldown limits repeats.
		return Double.isFinite(fastest) && fastest >= threshold;
	}

	/** Model-prior fills cannot cause damage when the wrist is actually occluded. */
	public static boolean observed(LandmarkDto l) {
		return l.valid() && l.position().isPresent() && !l.observedBy().isEmpty()
				&& (l.source() == LandmarkSource.DEPTH_NEIGHBORHOOD || l.source() == LandmarkSource.TRIANGULATED)
				&& l.confidence().orElse(1.0) >= 0.35 && l.visibility().orElse(1.0) >= 0.5;
	}

	private static Map<String, Sample> sample(List<LandmarkDto> landmarks, String side) {
		LandmarkDto anchor = find(landmarks, "body." + side + "_shoulder");
		if (anchor == null) anchor = find(landmarks, "body." + side + "_hip");
		if (anchor == null) return Map.of();
		Map<String, Sample> samples = new HashMap<>();
		for (String name : List.of("body." + side + "_wrist", "hand." + side + ".wrist", "body." + side + "_elbow")) {
			LandmarkDto joint = find(landmarks, name);
			if (joint == null) continue;
			Vector3 relative = joint.position().orElseThrow().sub(anchor.position().orElseThrow());
			if (relative.length() < 0.05 || relative.length() > 1.4) continue;
			samples.put(name, new Sample(relative, anchor.name(), Set.copyOf(joint.observedBy())));
		}
		return samples;
	}

	private static LandmarkDto find(List<LandmarkDto> landmarks, String name) {
		return landmarks.stream().filter(l -> l.name().equals(name) && observed(l)).findFirst().orElse(null);
	}
}
