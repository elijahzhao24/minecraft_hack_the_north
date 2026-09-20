package dev.humancraft.network;

import dev.humancraft.contract.ProtocolException;

import java.util.Arrays;
import java.util.Optional;

/** Reassembles one bounded latest-wins visual transfer. Callers own timeout and identity scope. */
public final class ScanTransfer {
	public static final long TIMEOUT_MS = 1_000;
	private final HumanCraftPayloads.ScanChunk first;
	private final byte[][] chunks;
	private final long startedAtMillis;
	private int received;
	private int receivedBytes;

	public ScanTransfer(HumanCraftPayloads.ScanChunk first, long nowMillis) {
		validate(first);
		this.first = first;
		this.chunks = new byte[first.chunkCount()][];
		this.startedAtMillis = nowMillis;
	}

	public java.util.UUID transferId() { return first.transferId(); }
	public HumanCraftPayloads.ScanChunk metadata() { return first; }
	public boolean expired(long nowMillis) { return nowMillis - startedAtMillis > TIMEOUT_MS; }

	public Optional<byte[]> accept(HumanCraftPayloads.ScanChunk chunk) {
		validate(chunk);
		if (!sameMetadata(first, chunk)) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "scan chunks have inconsistent metadata");
		}
		byte[] old = chunks[chunk.chunkIndex()];
		if (old != null) {
			if (!Arrays.equals(old, chunk.data())) throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "conflicting duplicate chunk");
			return Optional.empty();
		}
		chunks[chunk.chunkIndex()] = chunk.data().clone();
		received++;
		receivedBytes += chunk.data().length;
		if (receivedBytes > first.totalBytes()) throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "scan chunks exceed declared size");
		if (received != chunks.length) return Optional.empty();
		if (receivedBytes != first.totalBytes()) throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "scan chunks do not match declared size");
		byte[] result = new byte[receivedBytes];
		int offset = 0;
		for (byte[] part : chunks) { System.arraycopy(part, 0, result, offset, part.length); offset += part.length; }
		return Optional.of(result);
	}

	public static void validate(HumanCraftPayloads.ScanChunk p) {
		int maxChunks = (SharedScanCodec.MAX_BYTES + SharedScanCodec.CHUNK_BYTES - 1) / SharedScanCodec.CHUNK_BYTES;
		if (p.frameId() < 0 || p.bindingGeneration() < 0 || p.normalizationRevision() < 0)
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "negative scan identity field");
		if (p.chunkCount() <= 0 || p.chunkCount() > maxChunks || p.chunkIndex() < 0 || p.chunkIndex() >= p.chunkCount())
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "scan chunk index/count out of range");
		if (p.totalBytes() <= 0 || p.totalBytes() > SharedScanCodec.MAX_BYTES || p.data().length == 0
				|| p.data().length > SharedScanCodec.CHUNK_BYTES)
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "scan chunk byte size out of range");
		if (!Double.isFinite(p.anchorX()) || !Double.isFinite(p.anchorY()) || !Double.isFinite(p.anchorZ())
				|| !Double.isFinite(p.blocksPerMeter()) || p.blocksPerMeter() <= 0)
			throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, "invalid scan transform");
	}

	private static boolean sameMetadata(HumanCraftPayloads.ScanChunk a, HumanCraftPayloads.ScanChunk b) {
		return a.transferId().equals(b.transferId()) && a.frameId() == b.frameId() && a.sessionId().equals(b.sessionId())
				&& a.calibrationId().equals(b.calibrationId()) && java.util.Objects.equals(a.fusionId(), b.fusionId())
				&& a.targetPlayerId().equals(b.targetPlayerId()) && a.bindingGeneration() == b.bindingGeneration()
				&& a.normalizationRevision() == b.normalizationRevision()
				&& Double.compare(a.anchorX(), b.anchorX()) == 0 && Double.compare(a.anchorY(), b.anchorY()) == 0
				&& Double.compare(a.anchorZ(), b.anchorZ()) == 0 && Double.compare(a.blocksPerMeter(), b.blocksPerMeter()) == 0
				&& a.chunkCount() == b.chunkCount() && a.totalBytes() == b.totalBytes();
	}
}
