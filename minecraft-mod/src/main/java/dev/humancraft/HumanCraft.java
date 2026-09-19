package dev.humancraft;

import net.fabricmc.api.ModInitializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class HumanCraft implements ModInitializer {
	public static final String MOD_ID = "humancraft";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	@Override
	public void onInitialize() {
		LOGGER.info("HumanCraft initializing");
	}
}
