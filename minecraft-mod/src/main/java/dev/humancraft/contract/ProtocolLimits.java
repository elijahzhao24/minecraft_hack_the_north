package dev.humancraft.contract;

/** Protocol v1 constants and hard limits (docs/contracts.md §2). Enforced before any allocation. */
public final class ProtocolLimits {
	public static final int PROTOCOL_VERSION = 1;
	public static final int ENVELOPE_VERSION = 1;
	public static final int ENVELOPE_HEADER_BYTES = 16;
	public static final byte[] MAGIC = {'H', 'M', 'C', '1'};

	public static final int MAX_JSON_HEADER_BYTES = 65_536;
	public static final int MAX_CHARACTER_PAYLOAD_BYTES = 8 * 1024 * 1024;
	public static final int MAX_POINTS = 100_000;
	public static final int MAX_LANDMARKS = 256;
	public static final int MAX_COLLIDERS = 128;
	public static final int MAX_COLLIDER_ID_BYTES = 64;
	/** Radii and half extents are capped at 1 meter (stage frame). */
	public static final double MAX_COLLIDER_SIZE_M = 1.0;
	public static final int POINT_RECORD_BYTES = 16;

	/** Largest binary WebSocket message the client will ever assemble. */
	public static final int MAX_MESSAGE_BYTES = ENVELOPE_HEADER_BYTES + MAX_JSON_HEADER_BYTES + MAX_CHARACTER_PAYLOAD_BYTES;

	public static final String CHARACTER_FRAME_SCHEMA = "hmc.character_frame";
	public static final int CHARACTER_FRAME_SCHEMA_VERSION = 2;

	private ProtocolLimits() {}
}
