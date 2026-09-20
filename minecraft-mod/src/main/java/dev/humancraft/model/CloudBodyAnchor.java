package dev.humancraft.model;

import dev.humancraft.geometry.Vector3;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/** Estimate the dominant body's center/floor without allowing sparse outliers or limbs to set the origin. */
public final class CloudBodyAnchor {
	private static final double CELL = 0.20;
	private static final double BODY_RADIUS = 0.65;
	public record Estimate(Vector3 root, double height, int bodyPoints, int totalPoints) {}
	private CloudBodyAnchor() {}

	private static long key(int x, int z) { return ((long) x << 32) | (z & 0xffffffffL); }

	public static Estimate estimate(PointCloud cloud) {
		int n = cloud.count();
		if (n == 0) return new Estimate(Vector3.ZERO, 0, 0, 0);
		Map<Long, Integer> density = new HashMap<>();
		for (int i = 0; i < n; i++) {
			if (!cloud.position(i).isFinite()) continue;
			int x = (int) Math.floor(cloud.x(i) / CELL), z = (int) Math.floor(cloud.z(i) / CELL);
			density.merge(key(x, z), 1, Integer::sum);
		}
		if (density.isEmpty()) return new Estimate(Vector3.ZERO, 0, 0, n);
		long peak = 0;
		int best = -1;
		for (long cell : density.keySet()) {
			int x = (int) (cell >> 32), z = (int) cell;
			int support = 0;
			for (int dx = -1; dx <= 1; dx++) for (int dz = -1; dz <= 1; dz++)
				support += density.getOrDefault(key(x + dx, z + dz), 0);
			if (support > best || (support == best && cell < peak)) { best = support; peak = cell; }
		}
		double cx = ((int) (peak >> 32) + .5) * CELL, cz = ((int) peak + .5) * CELL;
		float[] ys = new float[n];
		int[] body = new int[n];
		int count = 0;
		for (int i = 0; i < n; i++) {
			if (cloud.position(i).isFinite() && Math.hypot(cloud.x(i) - cx, cloud.z(i) - cz) <= BODY_RADIUS) {
				body[count] = i; ys[count++] = cloud.y(i);
			}
		}
		if (count == 0) return new Estimate(Vector3.ZERO, 0, 0, n);
		Arrays.sort(ys, 0, count);
		double floor = ys[Math.min(count - 1, (int) (count * .02))];
		double top = ys[Math.min(count - 1, (int) (count * .98))];
		double low = floor + .30 * (top - floor), high = floor + .70 * (top - floor);
		float[] xs = new float[count], zs = new float[count];
		int core = 0;
		for (int j = 0; j < count; j++) {
			int i = body[j];
			if (cloud.y(i) >= low && cloud.y(i) <= high) {
				xs[core] = cloud.x(i); zs[core++] = cloud.z(i);
			}
		}
		if (core == 0) {
			for (int i : Arrays.copyOf(body, count)) { xs[core] = cloud.x(i); zs[core++] = cloud.z(i); }
		}
		Arrays.sort(xs, 0, core); Arrays.sort(zs, 0, core);
		return new Estimate(new Vector3(xs[core / 2], floor, zs[core / 2]), top - floor, count, n);
	}
}
