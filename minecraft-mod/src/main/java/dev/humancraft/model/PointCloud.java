package dev.humancraft.model;

import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.geometry.Aabb;
import dev.humancraft.geometry.Vector3;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * Packed {@code xyzrgba16_le} records: float32 x,y,z followed by uint8 r,g,b,a. The buffer is kept in wire
 * layout so it can be uploaded to the GPU without re-encoding. Immutable after construction; every accessor
 * uses absolute indexing so the cloud can be read from any thread.
 */
public final class PointCloud {
	public static final PointCloud EMPTY = new PointCloud(ByteBuffer.allocate(0), 0);

	private final ByteBuffer data;
	private final int count;
	private final ByteBuffer sources;

	public PointCloud(ByteBuffer data, int count) {
		this(data, count, null);
	}

	public PointCloud(ByteBuffer data, int count, ByteBuffer sources) {
		if (count < 0 || count > ProtocolLimits.MAX_POINTS) {
			throw new IllegalArgumentException("point count out of range: " + count);
		}
		if (data.remaining() != count * ProtocolLimits.POINT_RECORD_BYTES) {
			throw new IllegalArgumentException("point buffer length " + data.remaining() + " != " + count + " * 16");
		}
		this.data = data.slice().asReadOnlyBuffer().order(ByteOrder.LITTLE_ENDIAN);
		this.count = count;
		if (sources != null && sources.remaining() != count) throw new IllegalArgumentException("source count mismatch");
		this.sources = sources == null ? null : sources.slice().asReadOnlyBuffer();
	}

	public boolean hasSources() { return sources != null; }

	public int sourceMask(int i) { return sources == null ? 0 : sources.get(i) & 0xFF; }

	/** First phone cyan, second magenta, shared yellow; unknown provenance gray. */
	public int sourceColor(int i) {
		return switch (sourceMask(i)) {
			case 1 -> 0x28D2F0;
			case 2 -> 0xE632D2;
			case 3 -> 0xFFE050;
			default -> 0x888888;
		};
	}

	public int count() {
		return count;
	}

	/** Read-only view over the packed records (position 0, limit = count*16, little-endian). */
	public ByteBuffer data() {
		return data.duplicate().order(ByteOrder.LITTLE_ENDIAN);
	}

	public float x(int i) {
		return data.getFloat(i * ProtocolLimits.POINT_RECORD_BYTES);
	}

	public float y(int i) {
		return data.getFloat(i * ProtocolLimits.POINT_RECORD_BYTES + 4);
	}

	public float z(int i) {
		return data.getFloat(i * ProtocolLimits.POINT_RECORD_BYTES + 8);
	}

	public int r(int i) {
		return data.get(i * ProtocolLimits.POINT_RECORD_BYTES + 12) & 0xFF;
	}

	public int g(int i) {
		return data.get(i * ProtocolLimits.POINT_RECORD_BYTES + 13) & 0xFF;
	}

	public int b(int i) {
		return data.get(i * ProtocolLimits.POINT_RECORD_BYTES + 14) & 0xFF;
	}

	public int a(int i) {
		return data.get(i * ProtocolLimits.POINT_RECORD_BYTES + 15) & 0xFF;
	}

	public Vector3 position(int i) {
		return new Vector3(x(i), y(i), z(i));
	}

	/** Throws if any coordinate is NaN or infinite. */
	public void requireFinite() {
		for (int i = 0; i < count; i++) {
			if (!Float.isFinite(x(i)) || !Float.isFinite(y(i)) || !Float.isFinite(z(i))) {
				throw new IllegalArgumentException("point " + i + " has a non-finite coordinate");
			}
		}
	}

	public Aabb bounds() {
		if (count == 0) {
			return new Aabb(Vector3.ZERO, Vector3.ZERO);
		}
		Vector3 min = position(0);
		Vector3 max = min;
		for (int i = 1; i < count; i++) {
			Vector3 p = position(i);
			min = min.min(p);
			max = max.max(p);
		}
		return new Aabb(min, max);
	}

	/** Applies {@code p' = anchor + scale * p} to every position, keeping colors. */
	public PointCloud transform(Vector3 anchor, double scale) {
		ByteBuffer out = ByteBuffer.allocate(count * ProtocolLimits.POINT_RECORD_BYTES).order(ByteOrder.LITTLE_ENDIAN);
		for (int i = 0; i < count; i++) {
			out.putFloat((float) (anchor.x() + scale * x(i)));
			out.putFloat((float) (anchor.y() + scale * y(i)));
			out.putFloat((float) (anchor.z() + scale * z(i)));
			out.put((byte) r(i));
			out.put((byte) g(i));
			out.put((byte) b(i));
			out.put((byte) a(i));
		}
		out.flip();
		return new PointCloud(out, count, sources);
	}

	/** Builder used by the synthetic fixture. */
	public static final class Builder {
		private final ByteBuffer out;
		private int count;

		public Builder(int capacity) {
			this.out = ByteBuffer.allocate(capacity * ProtocolLimits.POINT_RECORD_BYTES).order(ByteOrder.LITTLE_ENDIAN);
		}

		public Builder add(double x, double y, double z, int r, int g, int b, int a) {
			out.putFloat((float) x).putFloat((float) y).putFloat((float) z);
			out.put((byte) r).put((byte) g).put((byte) b).put((byte) a);
			count++;
			return this;
		}

		public PointCloud build() {
			ByteBuffer packed = out.duplicate();
			packed.flip();
			return new PointCloud(packed, count);
		}
	}
}
