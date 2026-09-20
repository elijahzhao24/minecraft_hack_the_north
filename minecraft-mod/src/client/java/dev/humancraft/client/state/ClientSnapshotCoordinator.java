package dev.humancraft.client.state;

import dev.humancraft.HumanCraft;
import dev.humancraft.client.backend.CharacterWebSocket;
import dev.humancraft.client.render.HumanRenderer;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.Mode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.ArmSwingDetector;
import dev.humancraft.model.ScanNormalization;
import dev.humancraft.model.StageToWorld;
import dev.humancraft.model.WorldSnapshot;
import dev.humancraft.network.HumanCraftPayloads;
import dev.humancraft.network.ScanTransfer;
import dev.humancraft.network.SharedScanCodec;
import dev.humancraft.server.InstallRequest;
import dev.humancraft.telemetry.Telemetry;
import dev.humancraft.telemetry.FrameConsistencyMonitor;
import io.sentry.SentryLevel;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.List;
import java.util.Locale;
import java.util.LinkedHashMap;
import java.util.HashMap;
import java.util.Map;
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
	private final FrameConsistencyMonitor consistency;

	private CharacterWebSocket backend;
	private CharacterFrame latestDecoded;
	private volatile long lastBackendFrameId = -1;
	private UUID lastBackendSession;
	private record PendingInstall(CharacterFrame frame, WorldSnapshot snapshot, long receivedAtMillis) {}
	private final LinkedHashMap<String, PendingInstall> pending = new LinkedHashMap<>();
	private WorldSnapshot active;
	private long activeSinceMs;
	private long lastReceivedMs;
	private long displayedReceivedMs;
	private boolean invalidFrameStreak;
	private boolean holdingLastScan;
	// Clear acknowledgements are ordered; automatic dropout clears retain the visual.
	private final ArrayDeque<Boolean> retainVisualOnClear = new ArrayDeque<>();
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
	private final ScanNormalization normalization = new ScanNormalization();
	private final ArmSwingDetector swings;
	private String lastSwing = "none";
	private final ArrayDeque<HumanCraftPayloads.ScanChunk> outgoingScanChunks = new ArrayDeque<>();
	private long lastSharedAtMillis;
	private record Incoming(long publication, UUID target, ScanTransfer transfer) {}
	private final Map<UUID, Incoming> incomingScans = new HashMap<>();
	private final Map<UUID, Long> remotePublications = new HashMap<>();
	private final Map<UUID, UUID> remoteTargets = new HashMap<>();

	public ClientSnapshotCoordinator(HumanCraftConfig config, HumanRenderer renderer) {
		this.config = config;
		this.renderer = renderer;
		this.swings = new ArmSwingDetector(config.armSwingSpeedMps, config.armSwingCooldownMs);
		this.consistency = new FrameConsistencyMonitor("mismatch_ids".equals(config.observabilityFault) ? 1 : 2);
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
		if (latestDecoded != null && hasRenderableCloud(latestDecoded)) {
			install(latestDecoded, "joined world");
		} else if (config.captureEnabled && config.fixtureOnStart) {
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
		if ("stale_updates".equals(config.observabilityFault) && lastBackendFrameId >= 0) {
			return; // explicit demo hook: simulate a stream that stops after one frame
		}
		if (!frame.header().sessionId().equals(lastBackendSession)) {
			lastBackendSession = frame.header().sessionId();
			lastBackendFrameId = frame.frameId();
		} else {
			lastBackendFrameId = Math.max(lastBackendFrameId, frame.frameId());
		}
		consistency.fresh(frame.frameId(), frame.header().fusionId(), sourceFrameIds(frame));
		receive(frame);
	}

	public void onDisconnect() {
		joined = false;
		retainVisualOnClear.clear();
		invalidFrameStreak = false;
		holdingLastScan = false;
		swings.reset();
		normalization.reset();
		pending.clear();
		outgoingScanChunks.clear();
		incomingScans.clear();
		remotePublications.clear();
		remoteTargets.clear();
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
		lastReceivedMs = System.currentTimeMillis();
		if (!hasRenderableCloud(frame)) {
			swings.reset();
			pending.clear();
			contactCount = 0;
			holdingLastScan = active != null;
			if (joined && !invalidFrameStreak) {
				try {
					sendClear(true);
					invalidFrameStreak = true;
				} catch (RuntimeException e) {
					HumanCraft.LOGGER.debug("Could not clear invalid tracked frame", e);
				}
			}
			serverStatus = holdingLastScan ? "tracking lost; holding last scan (interactions cleared)" : "waiting for usable cloud";
			return;
		}
		invalidFrameStreak = false;
		if (pendingCapture != null && frame.header().sourceFrames().stream()
				.anyMatch(source -> pendingCapture.equals(source.captureId()))) {
			serverStatus = "capture complete: " + shortId(pendingCapture);
			pendingCapture = null;
		}
		if (joined) {
			install(frame, "decoded");
		}
	}

	private static boolean hasRenderableCloud(CharacterFrame frame) {
		return frame.hasRenderableCloud();
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
		serverStatus = "pending frame " + frame.frameId() + " (" + reason + ")";
		try (Telemetry.Span span = Telemetry.continueTransaction("client.install_snapshot", "hmc.install", frame.header().trace())) {
			span.data("frame_id", frame.frameId()).data("fusion_id", frame.header().fusionId())
					.data("payload_size", (long) frame.cloud().count() * 16)
					.data("collider_count", frame.header().colliders().size()).data("point_count", frame.cloud().count());
			WorldSnapshot next = Telemetry.timed(span, "hmc.prepare_cloud", "stage-to-world render snapshot",
					() -> transform.snapshot(frame));
			UUID sentFusionId = frame.header().fusionId();
			if ("mismatch_ids".equals(config.observabilityFault)) sentFusionId = UUID.randomUUID();
			InstallRequest request = new InstallRequest(frame.frameId(), frame.header().sessionId(), frame.header().calibrationId(),
					frame.header().mode(), transform, frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count(),
					sentFusionId, sourceFrameIds(frame), targetPlayerId, bindingGeneration, normalizationRevision);
			pending.put(pendingKey(frame.header().sessionId(), frame.frameId(), sentFusionId, bindingGeneration, normalizationRevision),
					new PendingInstall(frame, next, lastReceivedMs));
			while (pending.size() > 32) pending.remove(pending.keySet().iterator().next());
			ClientPlayNetworking.send(HumanCraftPayloads.InstallSnapshot.of(request));
			// Ordered immediately after install: server validates the exact live frame before causing damage.
			// Reinstalls caused by display toggles or binding changes must never replay physical gestures.
			if (config.armSwingEnabled && "decoded".equals(reason)) {
				swings.update(frame.header(), System.currentTimeMillis()).ifPresent(swing -> {
					ClientPlayNetworking.send(new HumanCraftPayloads.ArmSwing(frame.frameId(), frame.header().sessionId(),
							frame.header().calibrationId(), targetPlayerId, bindingGeneration, normalizationRevision, swing.left()));
					lastSwing = String.format(Locale.ROOT, "%s %.2f m/s", swing.left() ? "left" : "right", swing.speedMetersPerSecond());
				});
			}
		} catch (RuntimeException e) {
			pending.clear();
			serverStatus = "install send failed";
			Telemetry.captureException(e, "client.install.send");
		}
	}

	public void onAck(HumanCraftPayloads.SnapshotAck ack) {
		if (ack.frameId() == -1 && "cleared".equals(ack.code())) {
			if ("world changed".equals(ack.detail())) {
				clearLocal("world changed");
			} else if (!Boolean.TRUE.equals(retainVisualOnClear.pollFirst())) {
				clearLocal("cleared by server");
			}
			return;
		}
		Map.Entry<String, PendingInstall> match = pending.entrySet().stream()
				.filter(e -> e.getValue().snapshot().frameId() == ack.frameId()
						&& java.util.Objects.equals(e.getValue().snapshot().fusionId(), ack.fusionId())
						&& ack.bindingGeneration() == bindingGeneration && ack.normalizationRevision() == normalizationRevision)
				.reduce((a, b) -> b).orElse(null);
		if (match == null) {
			serverStatus = "ignored mismatched ack " + ack.frameId();
			return;
		}
		PendingInstall accepted = match.getValue();
		pending.remove(match.getKey());
		consistency.compatible(ack.frameId(), accepted.snapshot().fusionId(), ack.fusionId(), sourceFrameIds(accepted.snapshot()));
		if (ack.bindingGeneration() != bindingGeneration || ack.normalizationRevision() != normalizationRevision) {
			serverStatus = "ignored stale binding ack " + ack.frameId();
			return;
		}
		if (!ack.accepted()) {
			serverStatus = "rejected: " + ack.code() + " — " + ack.detail();
			Telemetry.log(SentryLevel.WARNING, "snapshot rejected frame=%d code=%s detail=%s",
					ack.frameId(), ack.code(), ack.detail());
			return;
		}
		// Render the complete accepted cloud. The LAN point budget applies only
		// when encoding the relay, never to the capturing player's appearance.
		WorldSnapshot next = accepted.snapshot();
		if (active != null && active.sessionId().equals(next.sessionId()) && next.frameId() < active.frameId()) return;
		try {
			renderer.activate(next, targetPlayerId);
			active = next;
			displayedReceivedMs = accepted.receivedAtMillis();
			activeSinceMs = System.currentTimeMillis();
			holdingLastScan = false;
			serverStatus = "active frame " + active.frameId() + " (" + ack.validColliders() + " colliders)";
			queueSharedScan(accepted.frame(), next);
		} catch (RuntimeException e) {
			// activate builds replacement buffers before releasing the old scan.
			holdingLastScan = active != null;
			swings.reset();
			contactCount = 0;
			serverStatus = "renderer upload failed; holding last scan";
			try { sendClear(true); } catch (RuntimeException clearError) {
				HumanCraft.LOGGER.debug("Could not clear failed render snapshot", clearError);
			}
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
		if (!holdingLastScan && active != null && state.frameId() == active.frameId()) {
			contactCount = state.contacts().size();
		}
	}

	public void onAvatarState(HumanCraftPayloads.AvatarState state) {
		boolean normalizationChanged = normalizationRevision != state.normalizationRevision();
		boolean unchanged = state.targetPlayerId().equals(targetPlayerId)
				&& state.bindingGeneration() == bindingGeneration
				&& !normalizationChanged
				&& state.mode().equals(avatarMode);
		controllingSeparate = state.controlling();
		if (unchanged) {
			// A re-sent identical binding must not wipe the rendered figure.
			return;
		}
		pending.clear();
		active = null;
		contactCount = 0;
		renderer.clear(targetPlayerId);
		targetPlayerId = state.targetPlayerId();
		avatarMode = state.mode();
		bindingGeneration = state.bindingGeneration();
		normalizationRevision = state.normalizationRevision();
		controllingSeparate = state.controlling();
		swings.reset();
		if (normalizationChanged) normalization.reset();
		if (joined && latestDecoded != null && hasRenderableCloud(latestDecoded)) {
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
		for (int i = 0; i < 8 && !outgoingScanChunks.isEmpty(); i++) {
			ClientPlayNetworking.send(outgoingScanChunks.removeFirst());
		}
		long now = System.currentTimeMillis();
		incomingScans.entrySet().removeIf(e -> e.getValue().transfer().expired(now));
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
		// While a live session is running a late frame keeps the previous figure
		// on screen; clearing would flash the vanilla player model back in.
		if (active != null && active.mode() == Mode.LIVE && !liveRequested
				&& System.currentTimeMillis() - activeSinceMs > config.liveFrameTtlMs) {
			consistency.checkStale(config.liveFrameTtlMs);
			// Server-side TTL still expires interactions; retain the scan for diagnosis.
			contactCount = 0;
			serverStatus = "live frame expired; displaying stale scan";
			activeSinceMs = System.currentTimeMillis();
		}
	}

	private static String sourceFrameIds(CharacterFrame frame) {
		return frame.header().sourceFrames().stream()
				.map(source -> source.sourceFrameId() == null ? source.captureId() : source.sourceFrameId())
				.map(String::valueOf).collect(java.util.stream.Collectors.joining(","));
	}

	private static String sourceFrameIds(WorldSnapshot snapshot) {
		return snapshot.sourceFrames().stream()
				.map(source -> source.sourceFrameId() == null ? source.captureId() : source.sourceFrameId())
				.map(String::valueOf).collect(java.util.stream.Collectors.joining(","));
	}

	private static String pendingKey(UUID session, long frame, UUID fusion, long generation, long revision) {
		return session + ":" + frame + ":" + fusion + ":" + generation + ":" + revision;
	}

	private void queueSharedScan(CharacterFrame frame, WorldSnapshot snapshot) {
		if (!config.captureEnabled) return;
		long now = System.currentTimeMillis();
		if (now - lastSharedAtMillis < 100) return;
		try {
			byte[] encoded = SharedScanCodec.encode(frame);
			List<byte[]> parts = SharedScanCodec.chunks(encoded);
			UUID transfer = UUID.randomUUID();
			StageToWorld transform = snapshot.transform();
			outgoingScanChunks.clear();
			for (int i = 0; i < parts.size(); i++) {
				outgoingScanChunks.addLast(new HumanCraftPayloads.ScanChunk(transfer, frame.frameId(), frame.header().sessionId(),
						frame.header().calibrationId(), frame.header().fusionId(), targetPlayerId, bindingGeneration,
						normalizationRevision, transform.anchor().x(), transform.anchor().y(), transform.anchor().z(),
						transform.blocksPerMeter(), i, parts.size(), encoded.length, parts.get(i)));
			}
			lastSharedAtMillis = now;
		} catch (RuntimeException e) {
			outgoingScanChunks.clear();
			HumanCraft.LOGGER.warn("Could not prepare shared scan frame {}", frame.frameId(), e);
		}
	}

	public void onSharedScanChunk(HumanCraftPayloads.SharedScanChunk payload) {
		long latest = remotePublications.getOrDefault(payload.ownerId(), -1L);
		if (payload.publication() < latest) return;
		HumanCraftPayloads.ScanChunk chunk = payload.chunk();
		Incoming incoming = incomingScans.get(payload.ownerId());
		if (incoming == null || incoming.publication() != payload.publication()
				|| !incoming.transfer().transferId().equals(chunk.transferId())) {
			if (payload.publication() < latest) return;
			remotePublications.put(payload.ownerId(), payload.publication());
			incoming = new Incoming(payload.publication(), chunk.targetPlayerId(), new ScanTransfer(chunk, System.currentTimeMillis()));
			incomingScans.put(payload.ownerId(), incoming);
		}
		try {
			Optional<byte[]> completed = incoming.transfer().accept(chunk);
			if (completed.isEmpty()) return;
			CharacterFrame frame = SharedScanCodec.decode(completed.get());
			if (frame.frameId() != chunk.frameId() || !frame.header().sessionId().equals(chunk.sessionId())
					|| !frame.header().calibrationId().equals(chunk.calibrationId())
					|| !java.util.Objects.equals(frame.header().fusionId(), chunk.fusionId())) {
				throw new IllegalArgumentException("shared scan identity does not match relay metadata");
			}
			UUID previousTarget = remoteTargets.put(payload.ownerId(), chunk.targetPlayerId());
			if (previousTarget != null && !previousTarget.equals(chunk.targetPlayerId())) renderer.clear(previousTarget);
			StageToWorld transform = new StageToWorld(new Vector3(chunk.anchorX(), chunk.anchorY(), chunk.anchorZ()), chunk.blocksPerMeter());
			renderer.activate(transform.snapshot(frame), chunk.targetPlayerId());
			incomingScans.remove(payload.ownerId());
		} catch (RuntimeException e) {
			incomingScans.remove(payload.ownerId());
			HumanCraft.LOGGER.warn("Rejected shared scan from {}", payload.ownerId(), e);
		}
	}

	public void onSharedScanRemoved(HumanCraftPayloads.SharedScanRemoved payload) {
		long latest = remotePublications.getOrDefault(payload.ownerId(), -1L);
		if (payload.publication() < latest) return;
		remotePublications.put(payload.ownerId(), payload.publication());
		incomingScans.remove(payload.ownerId());
		UUID target = remoteTargets.remove(payload.ownerId());
		renderer.clear(target == null ? payload.targetPlayerId() : target);
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
		sendClear(false);
		serverStatus = "clearing";
	}

	private void sendClear(boolean keepVisual) {
		ClientPlayNetworking.send(new HumanCraftPayloads.ClearSnapshot());
		retainVisualOnClear.addLast(keepVisual);
	}

	public void requestCapture() {
		if (!config.captureEnabled) {
			message(Component.literal("HumanCraft: capture is disabled on this viewer"));
			return;
		}
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
		if (!config.captureEnabled) {
			message(Component.literal("HumanCraft: capture is disabled on this viewer"));
			return;
		}
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

	/** Starts guided calibration using the stationary printed floor board. */
	public void registerRig() {
		if (!config.captureEnabled) {
			message(Component.literal("HumanCraft: capture is disabled on this viewer"));
			return;
		}
		if (backend == null || !backend.registerRig()) {
			message(Component.literal("HumanCraft: backend is not connected"));
			return;
		}
		message(Component.literal("HumanCraft: camera calibration started — board face up on floor, phones still, step out"));
	}

	public boolean isLiveRequested() {
		return liveRequested;
	}

	public void reconnect() {
		if (!config.captureEnabled) message(Component.literal("HumanCraft: viewer mode has no capture backend"));
		else if (backend != null) {
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
		normalization.scaleBy(multiplier);
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
		if (joined && latestDecoded != null && hasRenderableCloud(latestDecoded)) {
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
		return normalization.transform(frame);
	}

	private void clearLocal(String reason) {
		swings.reset();
		holdingLastScan = false;
		pending.clear();
		outgoingScanChunks.clear();
		active = null;
		contactCount = 0;
		serverStatus = reason;
		renderer.clear(targetPlayerId);
	}

	public List<String> hudLines() {
		List<String> lines = new ArrayList<>();
		lines.add("HumanCraft — " + (config.captureEnabled ? "backend: " + backendStatus : "viewer mode"));
		lines.add("server: " + serverStatus);
		lines.add("avatar: " + avatarMode + "  target: " + shortId(targetPlayerId)
				+ (controllingSeparate ? "  CONTROLLED" : ""));
		lines.add("frames decoded/pending/active: " + lastDecodedFrameId() + "/"
				+ (pending.isEmpty() ? "-" : pending.values().stream().reduce((a, b) -> b).orElseThrow().snapshot().frameId())
				+ "/" + (active == null ? "-" : active.frameId()));
		if (active != null) {
			long age = System.currentTimeMillis() - displayedReceivedMs;
			lines.add("calibration: " + shortId(active.calibrationId()) + "  age: " + age + " ms");
			if (holdingLastScan || age > 2000) lines.add("STALE scan: holding last usable frame (" + age + " ms old)");
			if (latestDecoded != null) {
				for (String warning : latestDecoded.header().quality().warnings()) lines.add("WARNING: " + warning);
				if (latestDecoded.header().quality().validColliderCount() < 3)
					lines.add("Body tracking incomplete; available cloud shown");
			}
			lines.add("points/landmarks/colliders: " + active.stageCloud().count() + "/" + active.landmarks().size()
					+ "/" + active.validColliderCount() + "  contacts: " + contactCount);
		}
		lines.add("arm swings: " + (config.armSwingEnabled ? String.format(Locale.ROOT, "L %s  R %s  trigger %.2f m/s",
				speedLabel(swings.leftSpeed()), speedLabel(swings.rightSpeed()), config.armSwingSpeedMps) : "disabled"));
		lines.add("last swing: " + lastSwing);
		if (config.mouseMovementEnabled) lines.add("Mouse: hold buttons 4/5 to turn left/right");
		lines.add(String.format(Locale.ROOT, "capture target: %.0f FPS", config.liveRateHz));
		var anchor = normalization.estimate();
		if (anchor != null) lines.add("anchor: dense body " + anchor.bodyPoints() + "/" + anchor.totalPoints()
				+ " pts; cyan cross = player feet");
		lines.add("last probe: " + lastProbe);
		return lines;
	}

	private String speedLabel(double speed) {
		return System.currentTimeMillis() - lastReceivedMs > 500 || !Double.isFinite(speed)
				? "untracked / warming up" : String.format(Locale.ROOT, "%.2f m/s", speed);
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
