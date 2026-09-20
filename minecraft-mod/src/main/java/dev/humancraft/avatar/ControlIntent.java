package dev.humancraft.avatar;

/** Client input intent. The server remains authoritative over motion and collisions. */
public record ControlIntent(
		float strafe,
		float forward,
		float yaw,
		float pitch,
		boolean jump,
		boolean sneak,
		boolean sprint,
		long receivedAtMillis) {
	public ControlIntent {
		strafe = clamp(strafe);
		forward = clamp(forward);
		pitch = Math.max(-90.0f, Math.min(90.0f, pitch));
	}

	private static float clamp(float value) {
		return Float.isFinite(value) ? Math.max(-1.0f, Math.min(1.0f, value)) : 0.0f;
	}

	public static ControlIntent idle(long nowMillis) {
		return new ControlIntent(0, 0, 0, 0, false, false, false, nowMillis);
	}
}
