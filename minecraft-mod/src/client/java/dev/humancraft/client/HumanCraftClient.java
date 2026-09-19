package dev.humancraft.client;

import dev.humancraft.HumanCraft;
import net.fabricmc.api.ClientModInitializer;

public final class HumanCraftClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		HumanCraft.LOGGER.info("HumanCraft client initializing");
	}
}
