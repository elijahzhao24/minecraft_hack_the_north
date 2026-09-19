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
	private WorldSnapshot pending;
	private WorldSnapshot active;
	private long activeSinceMs;
	private boolean joined;
	private String backendStatus = "starting";
	private String serverStatus = "not in world";
	private String lastProbe = "none";
	private int contactCount;
	private UUID pendingCapture;

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

	public long activeFrameId() {
		return active == null ? -1 : active.frameId();
	}

	public Optional<WorldSnapshot> active() {
		return Optional.ofNullable(active);
	}

	public void onJoin(Minecraft client) {
		joined = true;
		serverStatus = "ready";
		if (config.anchorAuto) {
			setAutomaticAnchor(client);
		}
		if (latestDecoded != null) {
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
		if (pendingCapture != null && frame.header().sourceFrames().stream()
				.anyMatch(source -> pendingCapture.equals(source.captureId()))) {
			serverStatus = "capture complete: " + shortId(pendingCapture);
			pendingCapture = null;
		}
		if (joined) {
			install(frame, "decoded");
		}
	}

	private void install(CharacterFrame frame, String reason) {
		StageToWorld transform = transform();
		WorldSnapshot next = transform.snapshot(frame);
		InstallRequest request = new InstallRequest(frame.frameId(), frame.header().sessionId(), frame.header().calibrationId(),
				frame.header().mode(), transform, frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count());
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
			renderer.activate(active);
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

	public void tick() {
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
		setAutomaticAnchor(client);
		persistAndReinstall("anchor reset");
	}

	public void scaleBy(double multiplier) {
		config.blocksPerMeter = Math.max(0.1, Math.min(8.0, config.blocksPerMeter * multiplier));
		persistAndReinstall("scale changed");
	}

	/** Rebuilds GPU buffers after a render-only debug option (for example source-color mode) changes. */
	public void refreshRenderer() {
		if (active != null) {
			renderer.activate(active);
		}
	}

	private void persistAndReinstall(String reason) {
		config.save(FabricLoader.getInstance().getConfigDir());
		if (joined && latestDecoded != null) {
			install(latestDecoded, reason);
		}
	}

	private void setAutomaticAnchor(Minecraft client) {
		if (client.player == null) {
			return;
		}
		Vec3 look = client.player.getLookAngle();
		double length = Math.hypot(look.x, look.z);
		double dx = length > 1e-6 ? look.x / length : 0;
		double dz = length > 1e-6 ? look.z / length : 1;
		config.anchorX = Math.floor(client.player.getX() + dx * 3.0) + 0.5;
		config.anchorY = Math.floor(client.player.getY());
		config.anchorZ = Math.floor(client.player.getZ() + dz * 3.0) + 0.5;
	}

	private StageToWorld transform() {
		return new StageToWorld(new Vector3(config.anchorX, config.anchorY, config.anchorZ), config.blocksPerMeter);
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
