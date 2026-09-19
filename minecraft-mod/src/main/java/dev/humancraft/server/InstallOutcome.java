package dev.humancraft.server;

import java.util.OptionalLong;

/**
 * Result of an install attempt. {@code activeFrameId} is the frame that is interactive after the attempt,
 * which on rejection is the previously accepted frame (if any) — an invalid install never discards state.
 */
public record InstallOutcome(boolean accepted, String code, String detail, OptionalLong activeFrameId) {
	public static final String ACCEPTED = "accepted";
	public static final String REJECTED_STALE = "stale_frame";
	public static final String REJECTED_INVALID = "invalid_snapshot";
	public static final String REJECTED_LIMIT = "limit_exceeded";
	public static final String REJECTED_OWNERSHIP = "not_owner";

	public static InstallOutcome accepted(long frameId) {
		return new InstallOutcome(true, ACCEPTED, "", OptionalLong.of(frameId));
	}

	public static InstallOutcome rejected(String code, String detail, OptionalLong previous) {
		return new InstallOutcome(false, code, detail, previous);
	}
}
