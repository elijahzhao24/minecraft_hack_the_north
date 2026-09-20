package dev.humancraft.client.input;

import com.mojang.blaze3d.platform.InputConstants;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.client.state.ClientSnapshotCoordinator;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;
import org.lwjgl.glfw.GLFW;

/** All demo/debug controls are regular configurable Minecraft key mappings. */
public final class HumanCraftKeybindings {
	private static final String CATEGORY = "key.categories.humancraft";
	private static final double ANCHOR_STEP = 0.25;

	// Defaults avoid vanilla bindings (P is Social Interactions) and anything a
	// laptop cannot press without Fn or a numeric keypad. All are rebindable in
	// Options -> Controls -> HumanCraft.

	private final HumanCraftConfig config;
	private final ClientSnapshotCoordinator coordinator;

	private final KeyMapping reconnect = key("reconnect", GLFW.GLFW_KEY_J);
	private final KeyMapping recapture = key("recapture", GLFW.GLFW_KEY_G);
	private final KeyMapping live = key("toggle_live", GLFW.GLFW_KEY_V);
	private final KeyMapping registerRig = key("register_rig", GLFW.GLFW_KEY_N);
	private final KeyMapping clear = key("clear", GLFW.GLFW_KEY_C);
	private final KeyMapping probe = key("probe", GLFW.GLFW_KEY_R);
	private final KeyMapping cloud = key("toggle_cloud", GLFW.GLFW_KEY_O);
	private final KeyMapping skeleton = key("toggle_skeleton", GLFW.GLFW_KEY_I);
	private final KeyMapping colliders = key("toggle_colliders", GLFW.GLFW_KEY_U);
	private final KeyMapping sourceColors = key("toggle_source_colors", GLFW.GLFW_KEY_Y);
	private final KeyMapping hud = key("toggle_hud", GLFW.GLFW_KEY_H);
	private final KeyMapping anchorForward = key("anchor_forward", GLFW.GLFW_KEY_UP);
	private final KeyMapping anchorBack = key("anchor_back", GLFW.GLFW_KEY_DOWN);
	private final KeyMapping anchorLeft = key("anchor_left", GLFW.GLFW_KEY_LEFT);
	private final KeyMapping anchorRight = key("anchor_right", GLFW.GLFW_KEY_RIGHT);
	private final KeyMapping anchorUp = key("anchor_up", GLFW.GLFW_KEY_RIGHT_BRACKET);
	private final KeyMapping anchorDown = key("anchor_down", GLFW.GLFW_KEY_LEFT_BRACKET);
	private final KeyMapping anchorReset = key("anchor_reset", GLFW.GLFW_KEY_B);
	private final KeyMapping scaleUp = key("scale_up", GLFW.GLFW_KEY_EQUAL);
	private final KeyMapping scaleDown = key("scale_down", GLFW.GLFW_KEY_MINUS);
	private final KeyMapping pointSizeUp = key("point_size_up", GLFW.GLFW_KEY_PERIOD);
	private final KeyMapping pointSizeDown = key("point_size_down", GLFW.GLFW_KEY_COMMA);

	public HumanCraftKeybindings(HumanCraftConfig config, ClientSnapshotCoordinator coordinator) {
		this.config = config;
		this.coordinator = coordinator;
	}

	private static KeyMapping key(String name, int glfwKey) {
		return KeyBindingHelper.registerKeyBinding(new KeyMapping(
				"key.humancraft." + name, InputConstants.Type.KEYSYM, glfwKey, CATEGORY));
	}

	public void tick(Minecraft client) {
		consume(reconnect, coordinator::reconnect);
		consume(recapture, coordinator::requestCapture);
		consume(live, coordinator::toggleLive);
		consume(registerRig, coordinator::registerRig);
		consume(clear, coordinator::clear);
		consume(probe, coordinator::probe);
		consume(cloud, () -> {
			config.showCloud = !config.showCloud;
			savedMessage("cloud", config.showCloud);
		});
		consume(skeleton, () -> {
			config.showSkeleton = !config.showSkeleton;
			savedMessage("skeleton", config.showSkeleton);
		});
		consume(colliders, () -> {
			config.showColliders = !config.showColliders;
			savedMessage("colliders", config.showColliders);
		});
		consume(sourceColors, () -> {
			config.sourceColors = !config.sourceColors;
			coordinator.refreshRenderer();
			savedMessage("stage-side registration colors", config.sourceColors);
		});
		consume(hud, () -> {
			config.showHud = !config.showHud;
			save();
		});
		consume(anchorForward, () -> coordinator.moveAnchor(0, 0, ANCHOR_STEP));
		consume(anchorBack, () -> coordinator.moveAnchor(0, 0, -ANCHOR_STEP));
		consume(anchorLeft, () -> coordinator.moveAnchor(-ANCHOR_STEP, 0, 0));
		consume(anchorRight, () -> coordinator.moveAnchor(ANCHOR_STEP, 0, 0));
		consume(anchorUp, () -> coordinator.moveAnchor(0, ANCHOR_STEP, 0));
		consume(anchorDown, () -> coordinator.moveAnchor(0, -ANCHOR_STEP, 0));
		consume(anchorReset, () -> coordinator.resetAnchor(client));
		consume(scaleUp, () -> coordinator.scaleBy(1.1));
		consume(scaleDown, () -> coordinator.scaleBy(1.0 / 1.1));
		consume(pointSizeUp, () -> {
			config.pointSize = Math.min(16f, config.pointSize + 0.5f);
			coordinator.refreshRenderer();
			savedMessage(String.format(java.util.Locale.ROOT, "point size: %.1f", config.pointSize), true);
		});
		consume(pointSizeDown, () -> {
			config.pointSize = Math.max(1f, config.pointSize - 0.5f);
			coordinator.refreshRenderer();
			savedMessage(String.format(java.util.Locale.ROOT, "point size: %.1f", config.pointSize), true);
		});
	}

	private static void consume(KeyMapping key, Runnable action) {
		while (key.consumeClick()) {
			action.run();
		}
	}

	private void savedMessage(String layer, boolean enabled) {
		save();
		Minecraft client = Minecraft.getInstance();
		if (client.player != null) {
			client.player.sendSystemMessage(Component.literal("HumanCraft " + layer + ": " + (enabled ? "on" : "off")));
		}
	}

	private void save() {
		config.save(FabricLoader.getInstance().getConfigDir());
	}
}
