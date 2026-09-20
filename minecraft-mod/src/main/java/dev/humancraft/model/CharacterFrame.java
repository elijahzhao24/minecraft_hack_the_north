package dev.humancraft.model;

import dev.humancraft.contract.CharacterFrameHeader;

/**
 * A fully decoded and validated {@code CHARACTER_FRAME} in stage coordinates (meters). This is the transport
 * model; it must be passed through {@link StageToWorld} before anything Minecraft-facing consumes it.
 */
public record CharacterFrame(CharacterFrameHeader header, PointCloud cloud) {
	/** A usable scan remains renderable when anatomy fitting loses some body parts. */
	public boolean hasRenderableCloud() {
		return header.quality().valid() && cloud.count() > 0;
	}

	public long frameId() {
		return header.frameId();
	}
}
