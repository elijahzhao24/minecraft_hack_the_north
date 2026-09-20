package dev.humancraft.client.input;

import dev.humancraft.client.state.ClientSnapshotCoordinator;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.network.HumanCraftPayloads;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.Input;
import org.lwjgl.glfw.GLFW;

/** Side-button turning and forwarding of vanilla movement bindings to the separate avatar. */
public final class MouseMovement {
	private static HumanCraftConfig config;
	private static ClientSnapshotCoordinator snapshots;
	private MouseMovement() {}

	public static void configure(HumanCraftConfig settings, ClientSnapshotCoordinator coordinator) {
		config = settings;
		snapshots = coordinator;
	}

	public static boolean active() {
		Minecraft client = Minecraft.getInstance();
		return config != null && config.mouseMovementEnabled && snapshots.hasActiveAvatar()
				&& client.player != null && client.player.isAlive() && client.screen == null
				&& client.isWindowActive() && client.mouseHandler.isMouseGrabbed();
	}

	public static boolean consumes(int button) {
		return active() && (button == GLFW.GLFW_MOUSE_BUTTON_4 || button == GLFW.GLFW_MOUSE_BUTTON_5);
	}

	public static void apply(Input input) {
		Minecraft client = Minecraft.getInstance();
		if (config == null || client.player == null) return;
		if (active()) {
			long window = client.getWindow().getWindow();
			int turn = (pressed(window, GLFW.GLFW_MOUSE_BUTTON_5) ? 1 : 0)
					- (pressed(window, GLFW.GLFW_MOUSE_BUTTON_4) ? 1 : 0);
			// Input ticks at 20 Hz; this is independent of rendering/capture FPS.
			client.player.setYRot(client.player.getYRot() + turn * (float) config.mouseTurnDegreesPerSecond / 20f);

		}
		if (snapshots.controllingSeparate()) {
			boolean focused = client.screen == null && client.isWindowActive() && client.mouseHandler.isMouseGrabbed();
			ClientPlayNetworking.send(new HumanCraftPayloads.ControlInput(
					focused ? input.leftImpulse : 0, focused ? input.forwardImpulse : 0,
					client.player.getYRot(), client.player.getXRot(), focused && input.jumping,
					focused && input.shiftKeyDown, focused && client.options.keySprint.isDown()));
			input.forwardImpulse = input.leftImpulse = 0;
			input.up = input.down = input.left = input.right = input.jumping = input.shiftKeyDown = false;
		}
	}

	private static boolean pressed(long window, int button) {
		return GLFW.glfwGetMouseButton(window, button) == GLFW.GLFW_PRESS;
	}
}
