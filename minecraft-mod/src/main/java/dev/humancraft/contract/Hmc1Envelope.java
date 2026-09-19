package dev.humancraft.contract;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/**
 * The 16-byte {@code HMC1} framing (docs/contracts.md §2). {@link #decode} validates the fixed header, the
 * exact total length and the size limits before exposing slices; it never copies the payload.
 */
public record Hmc1Envelope(int messageType, ByteBuffer header, ByteBuffer payload) {
	public static final int MESSAGE_TYPE_RGBD_FRAME = 1;
	public static final int MESSAGE_TYPE_CHARACTER_FRAME = 2;

	public static Hmc1Envelope decode(ByteBuffer message) {
		ByteBuffer buf = message.slice().order(ByteOrder.LITTLE_ENDIAN);
		if (buf.remaining() < ProtocolLimits.ENVELOPE_HEADER_BYTES) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "message shorter than 16-byte envelope header");
		}
		for (int i = 0; i < 4; i++) {
			if (buf.get(i) != ProtocolLimits.MAGIC[i]) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "bad magic, expected HMC1");
			}
		}
		int version = Short.toUnsignedInt(buf.getShort(4));
		if (version != ProtocolLimits.ENVELOPE_VERSION) {
			throw new ProtocolException(ProtocolException.UNSUPPORTED_VERSION, "unsupported envelope version " + version);
		}
		int messageType = Short.toUnsignedInt(buf.getShort(6));
		if (messageType != MESSAGE_TYPE_RGBD_FRAME && messageType != MESSAGE_TYPE_CHARACTER_FRAME) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unknown message type " + messageType);
		}
		long headerLength = Integer.toUnsignedLong(buf.getInt(8));
		long payloadLength = Integer.toUnsignedLong(buf.getInt(12));
		if (headerLength > ProtocolLimits.MAX_JSON_HEADER_BYTES) {
			throw new ProtocolException(ProtocolException.FRAME_TOO_LARGE, "JSON header " + headerLength + " bytes exceeds limit");
		}
		if (payloadLength > ProtocolLimits.MAX_CHARACTER_PAYLOAD_BYTES) {
			throw new ProtocolException(ProtocolException.FRAME_TOO_LARGE, "payload " + payloadLength + " bytes exceeds limit");
		}
		long expected = ProtocolLimits.ENVELOPE_HEADER_BYTES + headerLength + payloadLength;
		if (buf.remaining() != expected) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
					"message length " + buf.remaining() + " != 16 + " + headerLength + " + " + payloadLength);
		}
		ByteBuffer header = buf.slice(ProtocolLimits.ENVELOPE_HEADER_BYTES, (int) headerLength).order(ByteOrder.LITTLE_ENDIAN);
		ByteBuffer payload = buf.slice(ProtocolLimits.ENVELOPE_HEADER_BYTES + (int) headerLength, (int) payloadLength)
				.order(ByteOrder.LITTLE_ENDIAN);
		return new Hmc1Envelope(messageType, header, payload);
	}

	/** Builds a complete envelope. Used by the fixture writer and tests. */
	public static byte[] encode(int messageType, String headerJson, byte[] payload) {
		byte[] header = headerJson.getBytes(StandardCharsets.UTF_8);
		ByteBuffer out = ByteBuffer.allocate(ProtocolLimits.ENVELOPE_HEADER_BYTES + header.length + payload.length)
				.order(ByteOrder.LITTLE_ENDIAN);
		out.put(ProtocolLimits.MAGIC);
		out.putShort((short) ProtocolLimits.ENVELOPE_VERSION);
		out.putShort((short) messageType);
		out.putInt(header.length);
		out.putInt(payload.length);
		out.put(header);
		out.put(payload);
		return out.array();
	}

	public String headerText() {
		return StrictJson.decodeUtf8(header);
	}
}
