package dev.humancraft.network;

import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.StageToWorld;
import org.junit.jupiter.api.Test;

import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

class SharedScanCodecTest {
	@Test void downsamplingProducesAValidBoundedFrame() {
		var original = SyntheticHuman.frame(SyntheticHuman.Pose.BENT_LEFT_ARM, 42, Mode.LIVE, 20_000);
		byte[] encoded = SharedScanCodec.encode(original);
		var decoded = SharedScanCodec.decode(encoded);
		assertEquals(SharedScanCodec.MAX_POINTS, decoded.cloud().count());
		assertEquals(decoded.cloud().count(), decoded.header().quality().pointCount());
		assertEquals(original.header().colliders(), decoded.header().colliders());
		assertTrue(encoded.length <= SharedScanCodec.MAX_BYTES);
	}

	@Test void chunksReassembleAndRejectInconsistentMetadata() {
		var frame = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 7, Mode.SNAPSHOT, 6_000);
		byte[] encoded = SharedScanCodec.encode(frame);
		var parts = SharedScanCodec.chunks(encoded);
		UUID transfer = UUID.randomUUID();
		StageToWorld transform = new StageToWorld(new Vector3(0, 0, 0), 1);
		ScanTransfer assembler = null;
		Optional<byte[]> complete = Optional.empty();
		for (int i = 0; i < parts.size(); i++) {
			var chunk = chunk(transfer, frame.frameId(), i, parts.size(), encoded.length, parts.get(i), transform);
			if (assembler == null) assembler = new ScanTransfer(chunk, 100);
			complete = assembler.accept(chunk);
		}
		assertArrayEquals(encoded, complete.orElseThrow());

		var first = chunk(transfer, frame.frameId(), 0, parts.size(), encoded.length, parts.get(0), transform);
		ScanTransfer bad = new ScanTransfer(first, 100);
		bad.accept(first);
		var changed = chunk(transfer, frame.frameId() + 1, Math.min(1, parts.size() - 1), parts.size(), encoded.length,
				parts.get(Math.min(1, parts.size() - 1)), transform);
		assertThrows(ProtocolException.class, () -> bad.accept(changed));
	}

	private static HumanCraftPayloads.ScanChunk chunk(UUID transfer, long frame, int index, int count, int total, byte[] data,
			StageToWorld transform) {
		return new HumanCraftPayloads.ScanChunk(transfer, frame, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID,
				UUID.nameUUIDFromBytes("shared-test-fusion".getBytes()), UUID.nameUUIDFromBytes("shared-test-target".getBytes()),
				1, 1, transform.anchor().x(), transform.anchor().y(), transform.anchor().z(),
				transform.blocksPerMeter(), index, count, total, data);
	}
}
