package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import java.util.Arrays;
import java.util.UUID;

/** Hip-based player origin with a robust torso fallback; shared by every geometry consumer. */
public final class ScanNormalization {
	private double scale = Double.NaN;
	private UUID session, calibration;
	private Vector3 root;
	private long frameId = -1;
	private double captureTime;
	private String anchorSource = "waiting";

	public void reset() {
		scale = Double.NaN; session = null; calibration = null; root = null; frameId = -1;
		anchorSource = "waiting";
	}
	public void scaleBy(double multiplier) {
		if (!Double.isFinite(multiplier) || multiplier <= 0) throw new IllegalArgumentException("invalid scale multiplier");
		scale = Math.max(0.1, Math.min(8, (Double.isFinite(scale) ? scale : 1) * multiplier));
	}
	public String anchorSource() { return anchorSource; }

	public StageToWorld transform(CharacterFrame frame) {
		if (!frame.header().sessionId().equals(session) || !frame.header().calibrationId().equals(calibration)) reset();
		session = frame.header().sessionId(); calibration = frame.header().calibrationId();
		if (frame.cloud().count() == 0) return new StageToWorld(Vector3.ZERO, 1);
		// Display toggles and server reinstalls must not advance the temporal filter.
		if (frameId == frame.frameId() && root != null) return currentTransform();
		var cloud = frame.cloud();
		double[] xs = new double[cloud.count()], ys = new double[cloud.count()], zs = new double[cloud.count()];
		int count = 0;
		for (int i = 0; i < cloud.count(); i++) {
			if (!Float.isFinite(cloud.x(i)) || !Float.isFinite(cloud.y(i)) || !Float.isFinite(cloud.z(i))) continue;
			xs[count] = cloud.x(i); ys[count] = cloud.y(i); zs[count++] = cloud.z(i);
		}
		if (count == 0) return root == null ? new StageToWorld(Vector3.ZERO, 1) : currentTransform();
		Arrays.sort(ys, 0, count);
		double floorEstimate = percentile(ys, count, .02);
		double height = percentile(ys, count, .98) - floorEstimate;
		double[] coreX = new double[count], coreZ = new double[count];
		int coreCount = 0;
		for (int i = 0; i < cloud.count(); i++) {
			if (Float.isFinite(cloud.x(i)) && Float.isFinite(cloud.z(i))
					&& cloud.y(i) >= floorEstimate + height * .30 && cloud.y(i) <= floorEstimate + height * .65) {
				coreX[coreCount] = cloud.x(i); coreZ[coreCount++] = cloud.z(i);
			}
		}
		if (coreCount == 0) { coreX = xs; coreZ = zs; coreCount = count; }
		Arrays.sort(coreX, 0, coreCount); Arrays.sort(coreZ, 0, coreCount);
		Vector3 core = new Vector3(percentile(coreX, coreCount, .5), floorEstimate, percentile(coreZ, coreCount, .5));
		var left = frame.header().landmarks().stream().filter(ArmSwingDetector::observed)
				.filter(l -> l.name().equals("body.left_hip")).findFirst();
		var right = frame.header().landmarks().stream().filter(ArmSwingDetector::observed)
				.filter(l -> l.name().equals("body.right_hip")).findFirst();
		Vector3 target = core;
		anchorSource = "smoothed torso fallback";
		if (left.isPresent() && right.isPresent()) {
			Vector3 hips = left.get().position().orElseThrow().lerp(right.get().position().orElseThrow(), .5);
			if (Math.hypot(hips.x() - core.x(), hips.z() - core.z()) <= .35) {
				target = new Vector3(hips.x(), floorEstimate, hips.z());
				anchorSource = "smoothed hips";
			}
		}
		// A visible foot can refine the floor, but stale/inferred joints cannot
		// drag the cloud away from the body that the user actually sees.
		var feet = frame.header().landmarks().stream().filter(ArmSwingDetector::observed)
				.filter(l -> l.name().endsWith("_heel") || l.name().endsWith("_foot_index"))
				.map(l -> l.position().orElseThrow())
				.filter(p -> Math.hypot(p.x() - core.x(), p.z() - core.z()) < .65)
				.mapToDouble(p -> p.y() - .025).filter(y -> Math.abs(y - floorEstimate) <= .12).min();
		double floor = feet.orElse(height < .6 && root != null ? root.y() : floorEstimate);
		if (!Double.isFinite(scale)) {
			scale = height > .5 ? Math.max(.1, Math.min(8, 1.8 / height)) : 1;
		}
		target = new Vector3(target.x(), floor, target.z());
		double time = frame.header().normalizedCaptureTimeS();
		double dt = time - captureTime;
		if (root == null || dt <= 0 || dt > .5 || target.sub(root).length() > .75) {
			root = target;
		} else {
			double alpha = 1 - Math.exp(-dt / .18);
			double x = smooth(root.x(), target.x(), alpha, .01);
			double z = smooth(root.z(), target.z(), alpha, .01);
			// Limit lag to 15 cm horizontally, so walking cannot leave the scan behind.
			double lag = Math.hypot(x - target.x(), z - target.z());
			if (lag > .15) { x = target.x() + (x - target.x()) * .15 / lag; z = target.z() + (z - target.z()) * .15 / lag; }
			root = new Vector3(x, smooth(root.y(), target.y(), alpha, .02), z);
		}
		frameId = frame.frameId(); captureTime = time;
		return currentTransform();
	}

	private StageToWorld currentTransform() { return new StageToWorld(root.scale(-scale), scale); }
	private static double percentile(double[] sorted, int count, double q) { return sorted[(int) ((count - 1) * q)]; }
	private static double smooth(double from, double to, double alpha, double deadband) {
		return Math.abs(to - from) <= deadband ? from : from + (to - from) * alpha;
	}
}
