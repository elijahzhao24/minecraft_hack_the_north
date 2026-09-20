package dev.humancraft.client.input;

import dev.humancraft.client.state.ClientSnapshotCoordinator;
import dev.humancraft.network.HumanCraftPayloads;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;

/** Samples key intent at client tick rate; the server applies all movement and collision. */
public final class SeparatePlayerControl {
	private SeparatePlayerControl() {}

	public static void tick(Minecraft client, ClientSnapshotCoordinator coordinator) {
		if (!coordinator.controllingSeparate() || client.player == null) return;
		float forward = axis(client.options.keyUp.isDown(), client.options.keyDown.isDown());
		float strafe = axis(client.options.keyLeft.isDown(), client.options.keyRight.isDown());
		ClientPlayNetworking.send(new HumanCraftPayloads.ControlInput(
				strafe, forward, client.player.getYRot(), client.player.getXRot(),
				client.options.keyJump.isDown(), client.options.keyShift.isDown(), client.options.keySprint.isDown()));
		client.player.input.forwardImpulse = 0;
		client.player.input.leftImpulse = 0;
		client.player.input.jumping = false;
		client.player.input.shiftKeyDown = false;
	}

	private static float axis(boolean positive, boolean negative) {
		return positive == negative ? 0.0f : positive ? 1.0f : -1.0f;
	}
}
