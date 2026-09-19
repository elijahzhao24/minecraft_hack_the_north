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
		byte[] payload = new byte[points.remaining()];
		points.get(payload);
		return Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, frame.header().toJson().toString(), payload);
	}
}
