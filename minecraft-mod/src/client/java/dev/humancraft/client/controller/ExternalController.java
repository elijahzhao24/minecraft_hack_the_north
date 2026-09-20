package dev.humancraft.client.controller;

import dev.humancraft.client.mixin.MinecraftAttackInvoker;
import dev.humancraft.config.HumanCraftConfig;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.KeyboardInput;

/** Main-thread controller state, fail-safe input merge, camera integration, and punch dispatch. */
public final class ExternalController {
	private static volatile ExternalController installed;
	private final HumanCraftConfig config;
	private ControllerState state = ControllerState.disconnected();
	private String transportStatus = "starting";
	private long receivedNanos;
	private long lastTickNanos;
	private boolean armed = true;
	private boolean homeWasDown;
	private boolean havePunchBaseline;
	private int punchBaseline;
	private boolean pendingAttack;

	public ExternalController(HumanCraftConfig config) {
		this.config = config;
		installed = this;
	}

	public void accept(ControllerState next) {
		long now = System.nanoTime();
		boolean newConnection = next.connected() && !state.connected();
		state = next;
		receivedNanos = now;
		if (!next.connected()) {
			havePunchBaseline = false;
			pendingAttack = false;
			homeWasDown = false;
			return;
		}
		boolean home = next.down(ControllerState.HOME);
		if (home && !homeWasDown) armed = !armed;
		homeWasDown = home;
		if (newConnection || !havePunchBaseline) {
			punchBaseline = next.punchCounter();
			havePunchBaseline = true;
			return;
		}
		int delta = (next.punchCounter() - punchBaseline) & 0xFFFF;
		punchBaseline = next.punchCounter();
		if (delta > 0 && delta < 0x8000 && armed) pendingAttack = true;
	}

	public void setTransportStatus(String status) {
		transportStatus = status;
	}

	public void tick(Minecraft client) {
		long now = System.nanoTime();
		double elapsed = lastTickNanos == 0 ? 0 : Math.min(0.05, (now - lastTickNanos) / 1_000_000_000.0);
		lastTickNanos = now;
		if (!active(client, now)) return;
		int yawAxis = axis(state.down(ControllerState.RIGHT), state.down(ControllerState.LEFT));
		int pitchAxis = axis(state.down(ControllerState.DOWN), state.down(ControllerState.UP));
		if (yawAxis != 0) client.player.setYRot(client.player.getYRot() + (float) (yawAxis * config.controllerYawDegreesPerSecond * elapsed));
		if (pitchAxis != 0) {
			float pitch = client.player.getXRot() + (float) (pitchAxis * config.controllerPitchDegreesPerSecond * elapsed);
			client.player.setXRot(Math.max(-90.0f, Math.min(90.0f, pitch)));
		}
		if (state.down(ControllerState.AUX1) && state.down(ControllerState.A)) client.player.setSprinting(true);
		if (pendingAttack) {
			pendingAttack = false;
			((MinecraftAttackInvoker) client).humancraft$startAttack();
		}
	}

	public String hudLine() {
		long ageMs = receivedNanos == 0 ? -1 : Math.max(0, (System.nanoTime() - receivedNanos) / 1_000_000);
		String link = state.connected() ? "connected" : transportStatus;
		return "badge: " + link + "  " + (armed ? "ARMED" : "DISARMED")
				+ (ageMs >= 0 ? "  age " + ageMs + " ms" : "")
				+ "  punch " + state.punchCounter();
	}

	private boolean active(Minecraft client, long now) {
		return armed && state.connected() && receivedNanos != 0
				&& now - receivedNanos <= config.controllerStaleMs * 1_000_000L
				&& client.player != null && client.player.isAlive() && client.screen == null && client.isWindowActive();
	}

	private static int axis(boolean positive, boolean negative) {
		return positive == negative ? 0 : positive ? 1 : -1;
	}

	/** Called at KeyboardInput.tick tail so vanilla keys and badge controls compose. */
	public static void applyMovement(KeyboardInput input) {
		ExternalController controller = installed;
		Minecraft client = Minecraft.getInstance();
		if (controller == null || !controller.active(client, System.nanoTime())) return;
		if (controller.state.down(ControllerState.A)) {
			input.forwardImpulse = Math.max(-1.0f, Math.min(1.0f, input.forwardImpulse + 1.0f));
		}
		if (controller.state.down(ControllerState.B)) input.jumping = true;
	}
}
