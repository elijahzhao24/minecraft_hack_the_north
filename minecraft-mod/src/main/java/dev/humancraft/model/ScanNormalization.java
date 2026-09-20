package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import java.util.UUID;

/** Uses the visible dense body as the player origin; every geometry consumer shares this transform. */
public final class ScanNormalization {
	private double scale = Double.NaN;
	private UUID session, calibration;
	private CloudBodyAnchor.Estimate estimate;

	public void reset() { scale = Double.NaN; session = null; calibration = null; estimate = null; }
	public void scaleBy(double multiplier) {
		if (!Double.isFinite(multiplier) || multiplier <= 0) throw new IllegalArgumentException("invalid scale multiplier");
		scale = Math.max(0.1, Math.min(8, (Double.isFinite(scale) ? scale : 1) * multiplier));
	}
	public CloudBodyAnchor.Estimate estimate() { return estimate; }

	public StageToWorld transform(CharacterFrame frame) {
		if (!frame.header().sessionId().equals(session) || !frame.header().calibrationId().equals(calibration)) reset();
		session = frame.header().sessionId(); calibration = frame.header().calibrationId();
		if (frame.cloud().count() == 0) return new StageToWorld(Vector3.ZERO, 1);
		estimate = CloudBodyAnchor.estimate(frame.cloud());
		Vector3 root = estimate.root();
		// A visible foot can refine the floor, but stale/inferred joints cannot
		// drag the cloud away from the body that the user actually sees.
		var feet = frame.header().landmarks().stream().filter(ArmSwingDetector::observed)
				.filter(l -> l.name().endsWith("_heel") || l.name().endsWith("_foot_index"))
				.map(l -> l.position().orElseThrow())
				.filter(p -> Math.hypot(p.x() - root.x(), p.z() - root.z()) < .65)
				.mapToDouble(p -> p.y() - .025).filter(y -> Math.abs(y - root.y()) <= .12).min();
		double floor = feet.orElse(root.y());
		if (!Double.isFinite(scale)) {
			double height = estimate.height();
			scale = height > .5 ? Math.max(.1, Math.min(8, 1.8 / height)) : 1;
		}
		return new StageToWorld(new Vector3(-root.x() * scale, -floor * scale, -root.z() * scale), scale);
	}
}
