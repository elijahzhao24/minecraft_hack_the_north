package dev.humancraft;

import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.server.HumanCraftServer;
import dev.humancraft.telemetry.Telemetry;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;

public final class HumanCraft implements ModInitializer {
	public static final String MOD_ID = "humancraft";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	private static HumanCraftConfig config;

	/** Shared by the client and the integrated server, which live in the same JVM. */
	public static synchronized HumanCraftConfig config() {
		if (config == null) {
			config = HumanCraftConfig.load(FabricLoader.getInstance().getConfigDir());
		}
		return config;
	}

	public static String modVersion() {
		return FabricLoader.getInstance().getModContainer(MOD_ID)
				.map(ModContainer::getMetadata)
				.map(m -> m.getVersion().getFriendlyString())
				.orElse("dev");
	}

	@Override
	public void onInitialize() {
		LOGGER.info("HumanCraft initializing");
		HumanCraftConfig cfg = config();
		Telemetry.init(cfg, modVersion(), Map.of(
				"minecraft", FabricLoader.getInstance().getModContainer("minecraft").map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse("unknown"),
				"fabric_loader", FabricLoader.getInstance().getModContainer("fabricloader").map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse("unknown"),
				"env", FabricLoader.getInstance().getEnvironmentType().name().toLowerCase()));
		Runtime.getRuntime().addShutdownHook(new Thread(Telemetry::shutdown, "humancraft-sentry-shutdown"));
		HumanCraftServer.init(cfg);
	}
}
