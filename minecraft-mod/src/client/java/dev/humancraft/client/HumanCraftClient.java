package dev.humancraft.client;

import dev.humancraft.HumanCraft;
import dev.humancraft.client.backend.CharacterWebSocket;
import dev.humancraft.client.input.HumanCraftKeybindings;
import dev.humancraft.client.input.SeparatePlayerControl;
import dev.humancraft.client.input.AnatomyAttackInput;
import dev.humancraft.client.render.HumanCraftHud;
import dev.humancraft.client.render.HumanRenderer;
import dev.humancraft.client.state.ClientSnapshotCoordinator;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.network.HumanCraftPayloads;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.client.rendering.v1.HudRenderCallback;
import net.minecraft.client.Minecraft;

public final class HumanCraftClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		HumanCraft.LOGGER.info("HumanCraft client initializing");
		HumanCraftConfig config = HumanCraft.config();
		HumanCraftPayloads.register();

		HumanRenderer renderer = new HumanRenderer(config);
		renderer.register();
		ClientSnapshotCoordinator snapshots = new ClientSnapshotCoordinator(config, renderer);
		CharacterWebSocket backend = new CharacterWebSocket(
				config,
				snapshots::lastBackendFrameId,
				frame -> Minecraft.getInstance().execute(() -> snapshots.receiveBackend(frame)),
				status -> Minecraft.getInstance().execute(() -> snapshots.setBackendStatus(status)));
		snapshots.setBackend(backend);

		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.SnapshotAck.TYPE,
				(payload, context) -> snapshots.onAck(payload));
		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ProbeResult.TYPE,
				(payload, context) -> snapshots.onProbeResult(payload));
		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ContactState.TYPE,
				(payload, context) -> snapshots.onContactState(payload));
		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.AvatarState.TYPE,
				(payload, context) -> snapshots.onAvatarState(payload));
		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.DebugState.TYPE,
				(payload, context) -> snapshots.setDebug(payload.enabled()));

		ClientPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.CameraCalibrationRequest.TYPE,
				(payload, context) -> snapshots.registerRig());

		HumanCraftKeybindings keys = new HumanCraftKeybindings(config, snapshots);
		HumanCraftHud hud = new HumanCraftHud(config, snapshots);
		ClientTickEvents.END_CLIENT_TICK.register(client -> {
			keys.tick(client);
			snapshots.tick(client);
		});
		ClientTickEvents.START_CLIENT_TICK.register(client -> SeparatePlayerControl.tick(client, snapshots));
		ClientTickEvents.END_CLIENT_TICK.register(client -> AnatomyAttackInput.tick(client, snapshots));
		HudRenderCallback.EVENT.register((graphics, tickCounter) -> hud.render(graphics));

		ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> snapshots.onJoin(client));
		ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> snapshots.onDisconnect());
		ClientLifecycleEvents.CLIENT_STARTED.register(client -> backend.start());
		ClientLifecycleEvents.CLIENT_STOPPING.register(client -> {
			backend.close();
			renderer.close();
		});
	}
}
