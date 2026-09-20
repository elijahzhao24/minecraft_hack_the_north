package dev.humancraft.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonParseException;
import dev.humancraft.HumanCraft;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.function.Consumer;

/**
 * {@code config/humancraft.json}. Written with defaults on first run; every value can be overridden by an
 * environment variable (see {@link #applyEnvironment}) so secrets such as the Sentry DSN never need to live
 * in the file. Mutable so the in-game debug keys can persist toggles.
 */
public final class HumanCraftConfig {
	public static final String FILE_NAME = "humancraft.json";
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

	/** Perception backend WebSocket URL (character stream). */
	public String backendUrl = "ws://127.0.0.1:8000/ws/character";
	/** False for LAN viewers that only receive another player's shared scan. */
	public boolean captureEnabled = true;
	/** Identifies this Minecraft client to the backend in {@code character_hello}. */
	public String clientId = "minecraft-local";
	/** Reconnect backoff bounds in milliseconds. */
	public int reconnectMinMs = 500;
	public int reconnectMaxMs = 15_000;
	/** Largest binary WebSocket message accepted (bytes); capped to the protocol maximum. */
	public int maxBinaryBytes = 16 + 65_536 + 8 * 1024 * 1024;
	/** Live frames older than this stop being interactive and are hidden. */
	public int liveFrameTtlMs = 500;
	public double liveRateHz = 15.0;
	/** Server reach used for probes when positive; otherwise the vanilla block interaction range. */
	public double probeReachBlocks = 0;
	/** Wrist speed relative to shoulder, in physical meters/second, before avatar scaling. */
	public boolean armSwingEnabled = true;
	public double armSwingSpeedMps = 1.5;
	public int armSwingCooldownMs = 500;
	public boolean mouseMovementEnabled = true;
	public double mouseTurnDegreesPerSecond = 30.0;
	/** Migrates the old 8 Hz default once, leaving other explicit rates unchanged. */
	public int inputProfileVersion = 0;

	/** Stage→world placement. When {@code anchorAuto} is true the anchor is placed in front of the player. */
	public boolean anchorAuto = true;
	public double anchorX = 0;
	public double anchorY = 64;
	public double anchorZ = 0;
	public double blocksPerMeter = 1.0;

	/** Debug visualisation defaults. */
	public boolean showCloud = true;
	public boolean showSkeleton = true;
	public boolean showColliders = true;
	public boolean sourceColors = false;
	public boolean showHud = true;
	public float pointSize = 3.0f;
	/** Load the built-in synthetic fixture when the backend is unreachable at startup. */
	public boolean fixtureOnStart = true;
	public String fixturePose = "NEUTRAL";
	/** Environment-only fault injection: mismatch_ids or stale_updates. */
	public transient String observabilityFault = "";

	public Sentry sentry = new Sentry();

	public static final class Sentry {
		/** Leave empty in the file; set HUMANCRAFT_SENTRY_DSN (or SENTRY_DSN) instead. */
		public String dsn = "";
		public String environment = "local";
		public double tracesSampleRate = 0.2;
		public boolean logs = true;
		public boolean debug = false;
	}

	public static HumanCraftConfig load(Path configDir) {
		Path file = configDir.resolve(FILE_NAME);
		HumanCraftConfig config = new HumanCraftConfig();
		if (Files.exists(file)) {
			try {
				String text = Files.readString(file, StandardCharsets.UTF_8);
				HumanCraftConfig parsed = GSON.fromJson(text, HumanCraftConfig.class);
				if (parsed != null) {
					config = parsed;
					if (config.sentry == null) {
						config.sentry = new Sentry();
					}
				}
			} catch (IOException | JsonParseException e) {
				HumanCraft.LOGGER.warn("Could not read {}; using defaults ({})", file, e.toString());
			}
		} else {
			config.save(configDir);
		}
		if (config.inputProfileVersion < 1) {
			if (config.liveRateHz == 8.0) config.liveRateHz = 15.0;
			config.inputProfileVersion = 1;
			config.save(configDir);
		}
		config.applyEnvironment(System.getenv());
		config.clamp();
		return config;
	}

	public void save(Path configDir) {
		try {
			Files.createDirectories(configDir);
			HumanCraftConfig copy = GSON.fromJson(GSON.toJson(this), HumanCraftConfig.class);
			copy.sentry.dsn = ""; // never persist the DSN, even if it arrived through the environment
			Files.writeString(configDir.resolve(FILE_NAME), GSON.toJson(copy) + "\n", StandardCharsets.UTF_8);
		} catch (IOException e) {
			HumanCraft.LOGGER.warn("Could not write {}: {}", configDir.resolve(FILE_NAME), e.toString());
		}
	}

	/** Environment overrides. Package-private entry so tests can pass a synthetic map. */
	void applyEnvironment(Map<String, String> env) {
		override(env, "HUMANCRAFT_BACKEND_URL", v -> backendUrl = v);
		override(env, "HUMANCRAFT_CAPTURE_ENABLED", v -> captureEnabled = Boolean.parseBoolean(v));
		override(env, "HUMANCRAFT_CLIENT_ID", v -> clientId = v);
		override(env, "HUMANCRAFT_LIVE_RATE_HZ", v -> liveRateHz = Double.parseDouble(v));
		override(env, "HUMANCRAFT_MOUSE_MOVEMENT_ENABLED", v -> mouseMovementEnabled = Boolean.parseBoolean(v));
		override(env, "HUMANCRAFT_MOUSE_TURN_DEGREES_PER_SECOND", v -> mouseTurnDegreesPerSecond = Double.parseDouble(v));
		override(env, "HUMANCRAFT_LIVE_TTL_MS", v -> liveFrameTtlMs = Integer.parseInt(v));
		override(env, "HUMANCRAFT_ARM_SWING_ENABLED", v -> armSwingEnabled = Boolean.parseBoolean(v));
		override(env, "HUMANCRAFT_ARM_SWING_SPEED_MPS", v -> armSwingSpeedMps = Double.parseDouble(v));
		override(env, "HUMANCRAFT_ARM_SWING_COOLDOWN_MS", v -> armSwingCooldownMs = Integer.parseInt(v));
		override(env, "HUMANCRAFT_BLOCKS_PER_METER", v -> blocksPerMeter = Double.parseDouble(v));
		override(env, "HUMANCRAFT_FIXTURE_ON_START", v -> fixtureOnStart = Boolean.parseBoolean(v));
		override(env, "HUMANCRAFT_OBSERVABILITY_FAULT", v -> observabilityFault = v);
		override(env, "HUMANCRAFT_SENTRY_DSN", v -> sentry.dsn = v);
		if (sentry.dsn.isEmpty()) {
			override(env, "SENTRY_DSN", v -> sentry.dsn = v);
		}
		override(env, "HUMANCRAFT_SENTRY_ENVIRONMENT", v -> sentry.environment = v);
		override(env, "HUMANCRAFT_SENTRY_TRACES_SAMPLE_RATE", v -> sentry.tracesSampleRate = Double.parseDouble(v));
		override(env, "HUMANCRAFT_SENTRY_DEBUG", v -> sentry.debug = Boolean.parseBoolean(v));
	}

	private static void override(Map<String, String> env, String key, Consumer<String> apply) {
		String value = env.get(key);
		if (value == null || value.isBlank()) {
			return;
		}
		try {
			apply.accept(value.trim());
		} catch (RuntimeException e) {
			HumanCraft.LOGGER.warn("Ignoring invalid {}={}", key, value);
		}
	}

	void clamp() {
		reconnectMinMs = Math.max(100, reconnectMinMs);
		reconnectMaxMs = Math.max(reconnectMinMs, reconnectMaxMs);
		maxBinaryBytes = Math.min(Math.max(1024, maxBinaryBytes), dev.humancraft.contract.ProtocolLimits.MAX_MESSAGE_BYTES);
		liveFrameTtlMs = Math.max(50, liveFrameTtlMs);
		if (!Double.isFinite(mouseTurnDegreesPerSecond)) mouseTurnDegreesPerSecond = 30;
		mouseTurnDegreesPerSecond = Math.max(5, Math.min(90, mouseTurnDegreesPerSecond));
		if (!Double.isFinite(armSwingSpeedMps)) armSwingSpeedMps = 1.5;
		armSwingSpeedMps = Math.max(0.3, Math.min(10, armSwingSpeedMps));
		armSwingCooldownMs = Math.max(500, Math.min(5000, armSwingCooldownMs));
		if (!Double.isFinite(liveRateHz) || liveRateHz <= 0) {
			liveRateHz = 15.0;
		}
		liveRateHz = Math.min(30.0, liveRateHz);
		if (!(blocksPerMeter > 0) || !Double.isFinite(blocksPerMeter)) {
			blocksPerMeter = 1.0;
		}
		blocksPerMeter = Math.min(8.0, Math.max(0.1, blocksPerMeter));
		pointSize = Math.min(16f, Math.max(1f, pointSize));
		sentry.tracesSampleRate = Math.min(1.0, Math.max(0.0, sentry.tracesSampleRate));
		if (!Double.isFinite(anchorX) || !Double.isFinite(anchorY) || !Double.isFinite(anchorZ)) {
			anchorAuto = true;
			anchorX = 0;
			anchorY = 64;
			anchorZ = 0;
		}
	}
}
