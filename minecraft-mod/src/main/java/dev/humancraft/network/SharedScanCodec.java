package dev.humancraft.network;

import dev.humancraft.contract.BufferDescriptor;
import dev.humancraft.contract.CharacterFrameDecoder;
import dev.humancraft.contract.CharacterFrameEncoder;
import dev.humancraft.contract.CharacterFrameHeader;
import dev.humancraft.contract.FrameQuality;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.PointCloud;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;

/** Bounded wire representation used only between HumanCraft Minecraft clients. */
public final class SharedScanCodec {
	public static final int VERSION = 1;
	public static final int MAX_POINTS = 12_000;
	public static final int MAX_BYTES = 512 * 1024;
	public static final int CHUNK_BYTES = 24 * 1024;

	private SharedScanCodec() {}

	public static CharacterFrame prepare(CharacterFrame original) {
		PointCloud cloud = original.cloud().sampleAtMost(MAX_POINTS);
		CharacterFrameHeader old = original.header();
		long pointBytes = (long) cloud.count() * ProtocolLimits.POINT_RECORD_BYTES;
		List<BufferDescriptor> buffers = new ArrayList<>();
		buffers.add(new BufferDescriptor(CharacterFrameDecoder.POINTS_BUFFER, BufferDescriptor.ENCODING_POINTS,
				0, pointBytes, List.of((long) cloud.count())));
		if (cloud.hasSources()) {
			buffers.add(new BufferDescriptor("point_sources", "uint8", pointBytes, cloud.count(), List.of((long) cloud.count())));
		}
		FrameQuality quality = new FrameQuality(old.quality().valid(), cloud.count(), old.quality().validLandmarkCount(),
				old.quality().validColliderCount(), old.quality().warnings());
		CharacterFrameHeader header = new CharacterFrameHeader(old.sessionId(), old.calibrationId(), old.frameId(), old.fusionId(),
				old.sourceFrames(), old.normalizedCaptureTimeS(), old.pairSkewMs(), old.mode(), quality,
				old.landmarks(), old.colliders(), old.trace(), buffers);
		return new CharacterFrame(header, cloud);
	}

	public static byte[] encode(CharacterFrame frame) {
		byte[] bytes = CharacterFrameEncoder.encode(prepare(frame));
		if (bytes.length > MAX_BYTES) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "shared scan exceeds " + MAX_BYTES + " bytes");
		}
		return bytes;
	}

	public static CharacterFrame decode(byte[] bytes) {
		if (bytes.length == 0 || bytes.length > MAX_BYTES) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "shared scan byte length out of range");
		}
		CharacterFrame frame = CharacterFrameDecoder.decode(ByteBuffer.wrap(bytes));
		if (frame.cloud().count() > MAX_POINTS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "shared scan point count out of range");
		}
		return frame;
	}

	public static List<byte[]> chunks(byte[] bytes) {
		List<byte[]> result = new ArrayList<>((bytes.length + CHUNK_BYTES - 1) / CHUNK_BYTES);
		for (int offset = 0; offset < bytes.length; offset += CHUNK_BYTES) {
			int length = Math.min(CHUNK_BYTES, bytes.length - offset);
			result.add(java.util.Arrays.copyOfRange(bytes, offset, offset + length));
		}
		return List.copyOf(result);
	}
}
