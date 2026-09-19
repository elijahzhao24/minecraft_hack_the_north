package dev.humancraft.contract;

/**
 * Rejection of a wire message. {@code code} is a stable machine value (see docs/contracts.md error codes)
 * that is reported to the backend in {@code character_ack} and attached to Sentry events.
 */
public final class ProtocolException extends RuntimeException {
	public static final String UNSUPPORTED_VERSION = "unsupported_version";
	public static final String INVALID_MESSAGE = "invalid_message";
	public static final String INVALID_BUFFER_RANGE = "invalid_buffer_range";
	public static final String FRAME_TOO_LARGE = "frame_too_large";
	public static final String LIMIT_EXCEEDED = "limit_exceeded";
	public static final String NON_FINITE_GEOMETRY = "non_finite_geometry";
	public static final String INVALID_COLLIDER_GEOMETRY = "invalid_collider_geometry";
	public static final String SEQUENCE_REPLAYED = "sequence_replayed";

	private final String code;

	public ProtocolException(String code, String message) {
		super(message);
		this.code = code;
	}

	public ProtocolException(String code, String message, Throwable cause) {
		super(message, cause);
		this.code = code;
	}

	public String code() {
		return code;
	}

	@Override
	public String toString() {
		return code + ": " + getMessage();
	}
}
