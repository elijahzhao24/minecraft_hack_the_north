package dev.humancraft.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HumanCraftConfigTest {
	@Test void existingEightFpsConfigMigratesToFifteen() throws IOException {
		Files.writeString(temp.resolve(HumanCraftConfig.FILE_NAME), "{\"liveRateHz\":8.0}");
		var config = HumanCraftConfig.load(temp);
		assertEquals(15.0, config.liveRateHz);
		assertTrue(config.mouseMovementEnabled);
		assertEquals(1, config.inputProfileVersion);
	}

	@Test void swingSettingsAreConfigurableAndBounded() {
		var config = new HumanCraftConfig();
		config.applyEnvironment(Map.of("HUMANCRAFT_ARM_SWING_ENABLED", "false", "HUMANCRAFT_ARM_SWING_SPEED_MPS", "2.25",
				"HUMANCRAFT_ARM_SWING_COOLDOWN_MS", "750"));
		config.clamp();
		assertFalse(config.armSwingEnabled);
		assertEquals(2.25, config.armSwingSpeedMps);
		assertEquals(750, config.armSwingCooldownMs);
		config.armSwingSpeedMps = Double.NaN;
		config.armSwingCooldownMs = -1;
		config.clamp();
		assertEquals(1.5, config.armSwingSpeedMps);
		assertEquals(500, config.armSwingCooldownMs);
	}

	@TempDir
	Path temp;

	@Test
	void environmentCanConfigureBackendAndSentryWithoutPersistingDsn() throws IOException {
		HumanCraftConfig config = new HumanCraftConfig();
		config.applyEnvironment(Map.of(
				"HUMANCRAFT_BACKEND_URL", "ws://10.0.0.8:9000/ws/character",
				"HUMANCRAFT_CAPTURE_ENABLED", "false",
				"HUMANCRAFT_SENTRY_DSN", "https://public@example.invalid/42",
				"HUMANCRAFT_BLOCKS_PER_METER", "2.5"));
		config.clamp();

		assertEquals("ws://10.0.0.8:9000/ws/character", config.backendUrl);
		assertFalse(config.captureEnabled);
		assertEquals(2.5, config.blocksPerMeter);
		assertFalse(config.sentry.dsn.isEmpty());

		config.save(temp);
		String written = Files.readString(temp.resolve(HumanCraftConfig.FILE_NAME));
		assertTrue(written.contains("ws://10.0.0.8:9000/ws/character"));
		assertFalse(written.contains("example.invalid"), "Sentry DSN must never be written to disk");
	}

	@Test
	void invalidRangesAreClampedBeforeRuntimeUse() {
		HumanCraftConfig config = new HumanCraftConfig();
		config.reconnectMinMs = -1;
		config.reconnectMaxMs = 10;
		config.liveFrameTtlMs = 0;
		config.blocksPerMeter = Double.NaN;
		config.pointSize = 100;
		config.sentry.tracesSampleRate = 2;

		config.clamp();

		assertEquals(100, config.reconnectMinMs);
		assertEquals(100, config.reconnectMaxMs);
		assertEquals(50, config.liveFrameTtlMs);
		assertEquals(1.0, config.blocksPerMeter);
		assertEquals(16, config.pointSize);
		assertEquals(1.0, config.sentry.tracesSampleRate);
	}
}
