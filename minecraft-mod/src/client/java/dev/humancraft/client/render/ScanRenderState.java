package dev.humancraft.client.render;

import java.util.UUID;

/** Small client-only bridge used by the player-render mixin. */
public final class ScanRenderState {
	public static HumanRenderer renderer;
	private static volatile UUID target;
	private static volatile boolean active;
	private ScanRenderState() {}
	public static void activate(UUID id) { target = id; active = true; }
	public static void clear() { active = false; target = null; }
	public static boolean replaces(UUID id) { return active && id.equals(target); }
}
