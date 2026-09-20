package dev.humancraft.model;

import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.geometry.Vector3;
import java.util.Arrays;
import java.util.Optional;
import java.util.UUID;

/** Centers the scan on its hips instead of the distribution of visible surface samples. */
public final class ScanNormalization {
	private double scale = Double.NaN;
	private Vector3 root;
	private UUID session, calibration;

	public void reset() { scale = Double.NaN; root = null; session = null; calibration = null; }
	public void scaleBy(double multiplier) {
		if (!Double.isFinite(multiplier) || multiplier <= 0) throw new IllegalArgumentException("invalid scale multiplier");
		scale = Math.max(0.1, Math.min(8, (Double.isFinite(scale) ? scale : 1) * multiplier));
	}

	public StageToWorld transform(CharacterFrame frame) {
		if (!frame.header().sessionId().equals(session) || !frame.header().calibrationId().equals(calibration)) reset();
		session = frame.header().sessionId(); calibration = frame.header().calibrationId();
		int n = frame.cloud().count();
		if (n == 0) return new StageToWorld(Vector3.ZERO, 1);
		float[] xs = new float[n], ys = new float[n], zs = new float[n];
		for (int i = 0; i < n; i++) {
			xs[i] = frame.cloud().x(i); ys[i] = frame.cloud().y(i); zs[i] = frame.cloud().z(i);
		}
		Arrays.sort(xs); Arrays.sort(ys); Arrays.sort(zs);
		double floor = ys[(int) (n * 0.02)], top = ys[Math.min(n - 1, (int) (n * 0.98))];
		// Feet can disappear behind the body; keep the last floor instead of lifting a partial torso.
		var feet = frame.header().landmarks().stream().filter(ArmSwingDetector::observed)
				.filter(l -> l.name().endsWith("_heel") || l.name().endsWith("_foot_index"))
				.mapToDouble(l -> l.position().orElseThrow().y()).min();
		if (feet.isPresent()) floor = feet.getAsDouble() - 0.025;
		else if (root != null && top - floor < 0.6) floor = root.y();
		if (!Double.isFinite(scale)) {
			double height = top - floor;
			scale = height > 0.5 ? Math.max(0.1, Math.min(8, 1.8 / height)) : 1;
		}
		Optional<Vector3> l = position(frame, "body.left_hip"), r = position(frame, "body.right_hip");
		// Re-estimate the visible body center every frame. Freezing the old root
		// when hips disappear leaves the scan meters away after the person moves.
		float[] coreX = new float[n], coreZ = new float[n];
		int coreCount = 0;
		double low = floor + (top - floor) * 0.30, high = floor + (top - floor) * 0.65;
		for (int i = 0; i < n; i++) {
			if (frame.cloud().y(i) >= low && frame.cloud().y(i) <= high) {
				coreX[coreCount] = frame.cloud().x(i); coreZ[coreCount++] = frame.cloud().z(i);
			}
		}
		Arrays.sort(coreX, 0, coreCount); Arrays.sort(coreZ, 0, coreCount);
		Vector3 visibleCenter = coreCount > 10 ? new Vector3(coreX[coreCount / 2], floor, coreZ[coreCount / 2])
				: new Vector3(xs[n / 2], floor, zs[n / 2]);
		Vector3 center = l.isPresent() && r.isPresent() ? l.get().lerp(r.get(), 0.5) : visibleCenter;
		// Reject a stale or misregistered pelvis that does not belong to the visible torso.
		if (Math.hypot(center.x() - visibleCenter.x(), center.z() - visibleCenter.z()) > 0.35) center = visibleCenter;
		root = new Vector3(center.x(), floor, center.z());
		return new StageToWorld(root.scale(-scale), scale);
	}

	private static Optional<Vector3> position(CharacterFrame frame, String name) {
		return frame.header().landmarks().stream().filter(l -> l.name().equals(name))
				.filter(ArmSwingDetector::observed).findFirst().flatMap(LandmarkDto::position);
	}
}
