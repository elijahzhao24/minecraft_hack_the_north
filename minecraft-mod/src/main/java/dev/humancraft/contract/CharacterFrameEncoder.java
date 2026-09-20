package dev.humancraft.contract;

import dev.humancraft.model.CharacterFrame;

import java.nio.ByteBuffer;

/**
 * Produces the exact bytes the Python backend is expected to send. Used to freeze golden fixtures and to
 * exercise the decoder; the game never encodes character frames.
 */
public final class CharacterFrameEncoder {
	private CharacterFrameEncoder() {}

	public static byte[] encode(CharacterFrame frame) {
		ByteBuffer points = frame.cloud().data();
		int pointBytes = points.remaining();
		byte[] payload = new byte[pointBytes + (frame.cloud().hasSources() ? frame.cloud().count() : 0)];
		points.get(payload, 0, pointBytes);
		if (frame.cloud().hasSources()) {
			for (int i = 0; i < frame.cloud().count(); i++) payload[pointBytes + i] = (byte) frame.cloud().sourceMask(i);
		}
		return Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, frame.header().toJson().toString(), payload);
	}
}
