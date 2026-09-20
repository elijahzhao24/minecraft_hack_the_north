package dev.humancraft.client.state;

import dev.humancraft.HumanCraft;
import dev.humancraft.client.backend.CharacterWebSocket;
import dev.humancraft.client.render.HumanRenderer;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.Mode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.StageToWorld;
import dev.humancraft.model.WorldSnapshot;
import dev.humancraft.network.HumanCraftPayloads;
import dev.humancraft.server.InstallRequest;
import dev.humancraft.telemetry.Telemetry;
import io.sentry.SentryLevel;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Render-thread state machine joining backend frames, logical-server acknowledgement and visible GPU state.
 * A pending cloud never becomes visible until the integrated server accepts the matching collider snapshot.
 */
public final class ClientSnapshotCoordinator {
	private final HumanCraftConfig config;
	private final HumanRenderer renderer;
	private final AtomicInteger probeIds = new AtomicInteger();

	private CharacterWebSocket backend;
	private CharacterFrame latestDecoded;
	private volatile long lastBackendFrameId = -1;
	private UUID lastBackendSession;
	private WorldSnapshot pending;
	private WorldSnapshot active;
	private long activeSinceMs;
	private boolean liveRequested;
	private Mode lastInstalledMode;
	/** Client ticks to wait after JOIN before the player's position is trustworthy. */
	private static final int ANCHOR_SETTLE_TICKS = 5;

	private int anchorPendingTicks;
	private boolean joined;
	private String backendStatus = "starting";
	private String serverStatus = "not in world";
	private String lastProbe = "none";
	private int contactCount;
	private UUID pendingCapture;
	private UUID targetPlayerId = new UUID(0, 0);
	private String avatarMode = "self";
	private long bindingGeneration;
	private long normalizationRevision;
	private boolean controllingSeparate;
	private double lockedBlocksPerMeter = Double.NaN;

	public ClientSnapshotCoordinator(HumanCraftConfig config, HumanRenderer renderer) {
		this.config = config;
		this.renderer = renderer;
	}

	public void setBackend(CharacterWebSocket backend) {
		this.backend = backend;
	}

	public void setBackendStatus(String status) {
		backendStatus = status;
	}

	public long lastDecodedFrameId() {
		return latestDecoded == null ? -1 : latestDecoded.frameId();
	}

	/** Cursor advertised to the backend; deliberately excludes the local frame-0 fixture. */
	public long lastBackendFrameId() {
		return lastBackendFrameId;
	}

	public long activeFrameId() {
		return active == null ? -1 : active.frameId();
	}

	public Optional<WorldSnapshot> active() {
		return Optional.ofNullable(active);
	}

	public void onJoin(Minecraft client) {
		joined = true;
		serverStatus = "ready";
		if (client.player != null && targetPlayerId.equals(new UUID(0, 0))) {
			targetPlayerId = client.player.getUUID();
			bindingGeneration = 1;
			normalizationRevision = 1;
		}
		if (config.anchorAuto) {
			// The player entity exists at JOIN but its position has not been synced
			// yet, so reading it here yields a placeholder well below the terrain
			// (observed: y = -60) and the snapshot is anchored underground. Defer
			// until the player has ticked; tick() applies it and reinstalls.
			anchorPendingTicks = ANCHOR_SETTLE_TICKS;
		}
		if (latestDecoded != null && hasCriticalTracking(latestDecoded)) {
			install(latestDecoded, "joined world");
		} else if (config.fixtureOnStart) {
			SyntheticHuman.Pose pose;
			try {
				pose = SyntheticHuman.Pose.valueOf(config.fixturePose.toUpperCase(Locale.ROOT));
			} catch (IllegalArgumentException e) {
				pose = SyntheticHuman.Pose.NEUTRAL;
			}
			receive(SyntheticHuman.frame(pose, 0, Mode.SNAPSHOT));
		}
	}

	public void receiveBackend(CharacterFrame frame) {
		if (!frame.header().sessionId().equals(lastBackendSession)) {
			lastBackendSession = frame.header().sessionId();
			lastBackendFrameId = frame.frameId();
		} else {
			lastBackendFrameId = Math.max(lastBackendFrameId, frame.frameId());
		}
		receive(frame);
	}

	public void onDisconnect() {
		joined = false;
		pending = null;
		active = null;
		contactCount = 0;
		serverStatus = "not in world";
		renderer.clear();
	}

	public void receive(CharacterFrame frame) {
		if (latestDecoded != null
				&& latestDecoded.header().sessionId().equals(frame.header().sessionId())
				&& frame.frameId() <= latestDecoded.frameId()) {
			serverStatus = "ignored stale decoded frame " + frame.frameId();
			return;
		}
		latestDecoded = frame;
		// The cloud is worth showing even when the anatomy fit has not produced
		// head/torso/pelvis volumes yet; only probing depends on them.
		if (config.requireCriticalTracking && !hasCriticalTracking(frame)) {
			if (joined) {
				try {
					ClientPlayNetworking.send(new HumanCraftPayloads.ClearSnapshot());
				} catch (RuntimeException e) {
					HumanCraft.LOGGER.debug("Could not clear invalid tracked frame", e);
				}
			}
			clearLocal("tracking degraded: critical body unavailable");
			return;
		}
		if (pendingCapture != null && frame.header().sourceFrames().stream()
				.anyMatch(source -> pendingCapture.equals(source.captureId()))) {
			serverStatus = "capture complete: " + shortId(pendingCapture);
			pendingCapture = null;
		}
		if (joined) {
			install(frame, "decoded");
		}
	}

	private static boolean hasCriticalTracking(CharacterFrame frame) {
		if (!frame.header().quality().valid()) return false;
		java.util.EnumSet<dev.humancraft.contract.BodyPart> found =
				java.util.EnumSet.noneOf(dev.humancraft.contract.BodyPart.class);
		for (var collider : frame.header().colliders()) {
			if (collider.valid()) found.add(collider.bodyPart());
		}
		return found.contains(dev.humancraft.contract.BodyPart.HEAD)
				&& found.contains(dev.humancraft.contract.BodyPart.TORSO)
				&& found.contains(dev.humancraft.contract.BodyPart.PELVIS);
	}

	private void install(CharacterFrame frame, String reason) {
		// Each capture's cloud sits somewhere different relative to the stage
		// origin (wherever the subject stood), so while the anchor is automatic
		// re-place it for this frame's cloud: the figure lands in front of the
		// player every time, not only on the capture B happened to see.
		// In live mode only the first frame is placed; re-anchoring every frame
		// would make the figure chase the player's crosshair.
		boolean firstLive = frame.header().mode() == Mode.LIVE && lastInstalledMode != Mode.LIVE;
		if (config.anchorAuto && "decoded".equals(reason)
				&& (frame.header().mode() != Mode.LIVE || firstLive)) {
			Minecraft client = Minecraft.getInstance();
			if (setAutomaticAnchor(client)) {
				config.save(FabricLoader.getInstance().getConfigDir());
			}
		}
		lastInstalledMode = frame.header().mode();
		StageToWorld transform = playerLocalTransform(frame);
		WorldSnapshot next = transform.snapshot(frame);
		InstallRequest request = new InstallRequest(frame.frameId(), frame.header().sessionId(), frame.header().calibrationId(),
				frame.header().mode(), transform, frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count(),
				targetPlayerId, bindingGeneration, normalizationRevision);
		pending = next;
		serverStatus = "pending frame " + frame.frameId() + " (" + reason + ")";
		try (Telemetry.Span span = Telemetry.continueTransaction("client.install_snapshot", "hmc.install", frame.header().trace())) {
			span.data("frame_id", frame.frameId()).data("collider_count", frame.header().colliders().size())
					.data("point_count", frame.cloud().count());
			ClientPlayNetworking.send(HumanCraftPayloads.InstallSnapshot.of(request));
		} catch (RuntimeException e) {
			pending = null;
			serverStatus = "install send failed";
			Telemetry.captureException(e, "client.install.send");
		}
	}

	public void onAck(HumanCraftPayloads.SnapshotAck ack) {
		if (ack.frameId() == -1 && "cleared".equals(ack.code())) {
			clearLocal("cleared by server");
			return;
		}
		if (pending == null || ack.frameId() != pending.frameId()) {
			serverStatus = "ignored mismatched ack " + ack.frameId();
			return;
		}
		if (ack.bindingGeneration() != bindingGeneration || ack.normalizationRevision() != normalizationRevision) {
			serverStatus = "ignored stale binding ack " + ack.frameId();
			return;
		}
		if (!ack.accepted()) {
			pending = null;
			serverStatus = "rejected: " + ack.code() + " — " + ack.detail();
			Telemetry.log(SentryLevel.WARNING, "snapshot rejected frame=%d code=%s detail=%s",
					ack.frameId(), ack.code(), ack.detail());
			return;
		}
		active = pending;
		pending = null;
		activeSinceMs = System.currentTimeMillis();
		serverStatus = "active frame " + active.frameId() + " (" + ack.validColliders() + " colliders)";
		try {
			renderer.activate(active, targetPlayerId);
		} catch (RuntimeException e) {
			clearLocal("renderer upload failed");
			Telemetry.captureException(e, "client.renderer.upload");
		}
	}

	public void onProbeResult(HumanCraftPayloads.ProbeResult result) {
		String part = result.bodyPart().isEmpty() ? "-" : result.bodyPart();
		lastProbe = result.code() + " " + part + (result.distance() >= 0 ? String.format(Locale.ROOT, " @ %.2f", result.distance()) : "");
		Telemetry.log(SentryLevel.INFO, "probe frame=%d request=%d code=%s body_part=%s collider=%s distance=%.4f",
				result.frameId(), result.requestId(), result.code(), part, result.colliderId(), result.distance());
		message(Component.literal("HumanCraft probe: " + lastProbe));
	}

	public void onContactState(HumanCraftPayloads.ContactState state) {
		if (active != null && state.frameId() == active.frameId()) {
			contactCount = state.contacts().size();
		}
	}

	public void onAvatarState(HumanCraftPayloads.AvatarState state) {
		boolean normalizationChanged = normalizationRevision != state.normalizationRevision();
		pending = null;
		active = null;
		contactCount = 0;
		renderer.clear();
		targetPlayerId = state.targetPlayerId();
		avatarMode = state.mode();
		bindingGeneration = state.bindingGeneration();
		normalizationRevision = state.normalizationRevision();
		controllingSeparate = state.controlling();
		if (normalizationChanged) lockedBlocksPerMeter = Double.NaN;
		if (joined && latestDecoded != null && hasCriticalTracking(latestDecoded)) {
			install(latestDecoded, "avatar binding changed");
		}
	}

	public void setDebug(boolean enabled) {
		config.showSkeleton = enabled;
		config.showColliders = enabled;
		config.showHud = enabled;
		config.save(FabricLoader.getInstance().getConfigDir());
		refreshRenderer();
	}

	public boolean controllingSeparate() {
		return controllingSeparate;
	}

	public boolean hasActiveAvatar() { return active != null; }
	public UUID targetPlayerId() { return targetPlayerId; }

	public void tick(Minecraft client) {
		if (anchorPendingTicks > 0) {
			anchorPendingTicks--;
			if (anchorPendingTicks == 0) {
				if (setAutomaticAnchor(client)) {
					persistAndReinstall("anchor ready");
				} else {
					// Position still not synced (slow world load); keep retrying
					// instead of leaving the persisted anchor below the world.
					anchorPendingTicks = ANCHOR_SETTLE_TICKS;
				}
			}
		}
		if (active != null && active.mode() == Mode.LIVE
				&& System.currentTimeMillis() - activeSinceMs > config.liveFrameTtlMs) {
			try {
				ClientPlayNetworking.send(new HumanCraftPayloads.ClearSnapshot());
			} catch (RuntimeException e) {
				HumanCraft.LOGGER.debug("Could not send stale-frame clear", e);
			}
			clearLocal("live frame expired");
		}
	}

	public void probe() {
		if (active == null) {
			message(Component.literal("HumanCraft: no active snapshot"));
			return;
		}
		ClientPlayNetworking.send(new HumanCraftPayloads.ProbeRequest(probeIds.incrementAndGet(), active.frameId()));
	}

	public void clear() {
		if (!joined) {
			clearLocal("cleared locally");
			return;
		}
		ClientPlayNetworking.send(new HumanCraftPayloads.ClearSnapshot());
		serverStatus = "clearing";
	}

	public void requestCapture() {
		if (backend == null) {
			message(Component.literal("HumanCraft: backend client not ready"));
			return;
		}
		Optional<UUID> capture = backend.requestCapture(Mode.SNAPSHOT);
		if (capture.isPresent()) {
			pendingCapture = capture.get();
			serverStatus = "capture dispatched: " + shortId(pendingCapture);
		} else {
			message(Component.literal("HumanCraft: backend is not connected"));
		}
	}

	/** Starts or stops the backend-driven live loop; frames then arrive continuously. */
	public void toggleLive() {
		if (backend == null) {
			message(Component.literal("HumanCraft: backend client not ready"));
			return;
		}
		boolean next = !liveRequested;
		if (backend.setLive(next, config.liveRateHz)) {
			liveRequested = next;
			serverStatus = next ? "live requested" : "live stop requested";
			message(Component.literal("HumanCraft live: " + (next ? "on" : "off")));
		} else {
			message(Component.literal("HumanCraft: backend is not connected"));
		}
	}

	/** Asks the backend to snap the two cameras together using the person as the target. */
	public void registerRig() {
		if (backend == null || !backend.registerRig()) {
			message(Component.literal("HumanCraft: backend is not connected"));
			return;
		}
		message(Component.literal("HumanCraft: aligning cameras on the next capture — hold still"));
	}

	public boolean isLiveRequested() {
		return liveRequested;
	}

	public void reconnect() {
		if (backend != null) {
			backend.reconnectNow();
		}
	}

	public void moveAnchor(double dx, double dy, double dz) {
		config.anchorAuto = false;
		config.anchorX += dx;
		config.anchorY += dy;
		config.anchorZ += dz;
		persistAndReinstall("anchor moved");
	}

	public void resetAnchor(Minecraft client) {
		config.anchorAuto = true;
		if (setAutomaticAnchor(client)) {
			persistAndReinstall("anchor reset");
		} else {
			// Player not positioned yet; let tick() place it once they are.
			anchorPendingTicks = ANCHOR_SETTLE_TICKS;
		}
	}

	public void scaleBy(double multiplier) {
		lockedBlocksPerMeter = Math.max(0.1, Math.min(8.0,
				(Double.isFinite(lockedBlocksPerMeter) ? lockedBlocksPerMeter : 1.0) * multiplier));
		persistAndReinstall("calibration scale adjusted");
	}

	/** Rebuilds GPU buffers after a render-only debug option (for example source-color mode) changes. */
	public void refreshRenderer() {
		if (active != null) {
			renderer.activate(active, targetPlayerId);
		}
	}

	private void persistAndReinstall(String reason) {
		config.save(FabricLoader.getInstance().getConfigDir());
		if (joined && latestDecoded != null && hasCriticalTracking(latestDecoded)) {
			install(latestDecoded, reason);
		}
	}

	/**
	 * Places the figure three blocks in front of the player with its feet on the ground.
	 *
	 * <p>Returns {@code false} and leaves the anchor untouched when the player's
	 * position is not trustworthy yet, so a snapshot is never anchored below the
	 * world. Callers that need a guaranteed placement should retry on a later
	 * tick rather than using the stale value.
	 */
	private boolean setAutomaticAnchor(Minecraft client) {
		if (client.player == null || client.level == null) {
			return false;
		}
		double y = client.player.getY();
		// Before the position packet arrives the player sits at a placeholder
		// below the terrain. Anything at or under the build floor is not a real
		// standing position.
		if (!Double.isFinite(y) || y <= client.level.getMinBuildHeight()) {
			return false;
		}
		// The placeholder (y = -60) is above the 1.21 build floor (-64), so the
		// height check alone is not enough: also require the player's chunk to
		// be loaded and the player to be standing on something (or to have
		// been in the world long enough that the spawn packet must have landed).
		if (!client.level.hasChunkAt(client.player.blockPosition())) {
			return false;
		}
		if (!client.player.onGround() && client.player.tickCount < 60) {
			return false;
		}
		Vec3 look = client.player.getLookAngle();
		double length = Math.hypot(look.x, look.z);
		double dx = length > 1e-6 ? look.x / length : 0;
		double dz = length > 1e-6 ? look.z / length : 1;
		// Target: the figure itself (not the stage origin) stands three blocks
		// ahead with its lowest point on the ground. The cloud is offset from the
		// stage origin by however far the subject stood from the camera, so
		// anchoring the origin alone can leave the figure beside or behind you.
		double offX = 0, offY = 0, offZ = 0;
		if (latestDecoded != null && latestDecoded.cloud().count() > 0) {
			var cloud = latestDecoded.cloud();
			int n = cloud.count();
			double sx = 0, sz = 0, minY = Double.POSITIVE_INFINITY;
			for (int i = 0; i < n; i++) {
				sx += cloud.x(i);
				sz += cloud.z(i);
				minY = Math.min(minY, cloud.y(i));
			}
			offX = sx / n * config.blocksPerMeter;
			offZ = sz / n * config.blocksPerMeter;
			offY = minY * config.blocksPerMeter;
		}
		config.anchorX = Math.floor(client.player.getX() + dx * 3.0) + 0.5 - offX;
		config.anchorY = Math.floor(y) - offY;
		config.anchorZ = Math.floor(client.player.getZ() + dz * 3.0) + 0.5 - offZ;
		return true;
	}

	private StageToWorld transform() {
		return new StageToWorld(new Vector3(config.anchorX, config.anchorY, config.anchorZ), config.blocksPerMeter);
	}

	private StageToWorld playerLocalTransform(CharacterFrame frame) {
		var cloud = frame.cloud();
		if (cloud.count() == 0) return new StageToWorld(Vector3.ZERO, 1.0);
		double minY = Double.POSITIVE_INFINITY, maxY = Double.NEGATIVE_INFINITY, sumX = 0, sumZ = 0;
		for (int i = 0; i < cloud.count(); i++) {
			minY = Math.min(minY, cloud.y(i));
			maxY = Math.max(maxY, cloud.y(i));
			sumX += cloud.x(i);
			sumZ += cloud.z(i);
		}
		double height = maxY - minY;
		if (!Double.isFinite(lockedBlocksPerMeter)) {
			lockedBlocksPerMeter = height > 0.5 ? Math.max(0.1, Math.min(8.0, 1.8 / height)) : 1.0;
		}
		double rootX = sumX / cloud.count();
		double rootZ = sumZ / cloud.count();
		return new StageToWorld(new Vector3(-rootX * lockedBlocksPerMeter, -minY * lockedBlocksPerMeter,
				-rootZ * lockedBlocksPerMeter), lockedBlocksPerMeter);
	}

	private void clearLocal(String reason) {
		pending = null;
		active = null;
		contactCount = 0;
		serverStatus = reason;
		renderer.clear();
	}

	public List<String> hudLines() {
		List<String> lines = new ArrayList<>();
		lines.add("HumanCraft — backend: " + backendStatus);
		lines.add("server: " + serverStatus);
		lines.add("avatar: " + avatarMode + "  target: " + shortId(targetPlayerId)
				+ (controllingSeparate ? "  CONTROLLED" : ""));
		lines.add("frames decoded/pending/active: " + lastDecodedFrameId() + "/"
				+ (pending == null ? "-" : pending.frameId()) + "/" + (active == null ? "-" : active.frameId()));
		if (active != null) {
			long age = System.currentTimeMillis() - activeSinceMs;
			lines.add("calibration: " + shortId(active.calibrationId()) + "  age: " + age + " ms");
			lines.add("points/landmarks/colliders: " + active.stageCloud().count() + "/" + active.landmarks().size()
					+ "/" + active.validColliderCount() + "  contacts: " + contactCount);
		}
		lines.add("last probe: " + lastProbe);
		return lines;
	}

	private static String shortId(UUID id) {
		return id.toString().substring(0, 8);
	}

	private static void message(Component component) {
		Minecraft client = Minecraft.getInstance();
		if (client.player != null) {
			client.player.sendSystemMessage(component);
		}
	}
}
