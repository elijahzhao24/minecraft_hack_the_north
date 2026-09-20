package dev.humancraft.client.input;

import dev.humancraft.client.state.ClientSnapshotCoordinator;
import dev.humancraft.network.HumanCraftPayloads;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;
import net.minecraft.world.phys.EntityHitResult;

public final class AnatomyAttackInput {
	private static boolean down;
	private static int requests;
	private AnatomyAttackInput() {}

	public static void tick(Minecraft client, ClientSnapshotCoordinator coordinator) {
		boolean now = client.options.keyAttack.isDown();
		if (now && !down && coordinator.hasActiveAvatar()) {
			boolean vanillaTargetsScan = client.hitResult instanceof EntityHitResult hit
					&& hit.getEntity().getUUID().equals(coordinator.targetPlayerId());
			if (!vanillaTargetsScan) ClientPlayNetworking.send(new HumanCraftPayloads.AnatomyAttack(++requests));
		}
		down = now;
	}
}
