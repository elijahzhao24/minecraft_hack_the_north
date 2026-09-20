package dev.humancraft.contract;

import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.PointCloud;

import java.nio.ByteBuffer;
import java.util.List;

/**
 * Turns one binary WebSocket message into a validated {@link CharacterFrame}. Follows the contract's
 * validation order: envelope, JSON model, buffer descriptors, then buffer contents.
 */
public final class CharacterFrameDecoder {
	public static final String POINTS_BUFFER = "points";

	private CharacterFrameDecoder() {}

	public static CharacterFrame decode(ByteBuffer message) {
		Hmc1Envelope envelope = Hmc1Envelope.decode(message);
		if (envelope.messageType() != Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
					"expected CHARACTER_FRAME (2), got message type " + envelope.messageType());
		}
		CharacterFrameHeader header = CharacterFrameHeader.parse(envelope.headerText());
		BufferDescriptor points = validateBuffers(header.buffers(), envelope.payload().remaining());

		long count = points.shape().get(0);
		if (count > ProtocolLimits.MAX_POINTS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "point count " + count + " exceeds limit");
		}
		if (header.quality().pointCount() != count) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
					"quality.point_count " + header.quality().pointCount() + " != points shape " + count);
		}
		ByteBuffer slice = envelope.payload().slice((int) points.offset(), (int) points.length());
		ByteBuffer sources = null;
		for (BufferDescriptor b : header.buffers()) {
			if (!b.name().equals("point_sources")) continue;
			if (!b.encoding().equals("uint8") || b.shape().size() != 1 || b.shape().get(0) != count || b.length() != count) {
				throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE, "invalid point_sources buffer");
			}
			sources = envelope.payload().slice((int) b.offset(), (int) b.length());
			int devices = header.sourceFrames().size();
			if (devices > 8) throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "too many source devices");
			for (int i = 0; i < count; i++) {
				if ((sources.get(i) & 0xFF) >= (1 << devices))
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "source bit references absent camera");
			}
		}
		PointCloud cloud;
		try {
			cloud = new PointCloud(slice, (int) count, sources);
			cloud.requireFinite();
		} catch (IllegalArgumentException e) {
			throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, e.getMessage(), e);
		}
		return new CharacterFrame(header, cloud);
	}

	/** Validates descriptor ranges and packing, and returns the mandatory points descriptor. */
	static BufferDescriptor validateBuffers(List<BufferDescriptor> buffers, long payloadLength) {
		if (buffers.isEmpty()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "character frame has no buffers");
		}
		long cursor = 0;
		BufferDescriptor points = null;
		java.util.Set<String> names = new java.util.HashSet<>();
		for (BufferDescriptor b : buffers) {
			if (!names.add(b.name())) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "duplicate buffer name '" + b.name() + "'");
			}
			if (b.offset() != cursor) {
				throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE,
						"buffer '" + b.name() + "' offset " + b.offset() + " is not tightly packed (expected " + cursor + ")");
			}
			if (b.end() > payloadLength) {
				throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE, "buffer '" + b.name() + "' ends after payload");
			}
			cursor = b.end();
			if (b.name().equals(POINTS_BUFFER)) {
				if (!BufferDescriptor.ENCODING_POINTS.equals(b.encoding())) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "points buffer must use xyzrgba16_le");
				}
				if (b.shape().size() != 1) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "points shape must be [N]");
				}
				long expected = b.shape().get(0) * ProtocolLimits.POINT_RECORD_BYTES;
				if (b.length() != expected) {
					throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE,
							"points length " + b.length() + " != " + b.shape().get(0) + " * 16");
				}
				points = b;
			}
		}
		if (cursor != payloadLength) {
			throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE,
					"buffers end at " + cursor + " but payload is " + payloadLength + " bytes");
		}
		if (points == null) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "character frame is missing the 'points' buffer");
		}
		return points;
	}
}
