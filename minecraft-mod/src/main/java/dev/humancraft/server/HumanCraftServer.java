package dev.humancraft.server;

import dev.humancraft.HumanCraft;
import dev.humancraft.avatar.AvatarService;
import dev.humancraft.avatar.HumanCraftCommands;
import dev.humancraft.avatar.ScannedServerPlayer;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.SwingArc;
import dev.humancraft.network.HumanCraftPayloads;
import dev.humancraft.network.ScanTransfer;
import dev.humancraft.network.SharedScanCodec;
import dev.humancraft.network.WireCollider;
import dev.humancraft.telemetry.Telemetry;
import io.sentry.SentryLevel;
import net.fabricmc.fabric.api.entity.event.v1.ServerEntityWorldChangeEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.InteractionHand;

import java.util.HashMap;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.OptionalDouble;
import java.util.UUID;

/**
 * Logical-server side of HumanCraft. Runs inside the integrated single-player server (there is no dedicated
 * server); all handlers execute on the server thread, which is the only thread that touches
 * {@link SnapshotStore}.
 */
public final class HumanCraftServer {
	/** Contact queries run every other tick (10 Hz) and heartbeat once a second. */
	private static final int CONTACT_INTERVAL_TICKS = 2;
	private static final long CONTACT_HEARTBEAT_MS = 1000;

	private static HumanCraftServer instance;

	private final HumanCraftConfig config;
	private final SnapshotStore store;
	private final AvatarService avatars = new AvatarService();
	private final Map<UUID, ContactService.Emitter> emitters = new HashMap<>();
	private final Map<UUID, ArmSwingGate> swingGates = new HashMap<>();
	private record AcceptedInstall(HumanCraftPayloads.InstallSnapshot payload, String dimension, long acceptedAtMillis) {}
	private record CachedVisual(long publication, UUID owner, UUID target, String dimension,
			List<HumanCraftPayloads.SharedScanChunk> chunks) {}
	private final Map<UUID, ArrayDeque<AcceptedInstall>> acceptedInstalls = new HashMap<>();
	private final Map<UUID, ScanTransfer> incomingScans = new HashMap<>();
	private final Map<UUID, CachedVisual> sharedVisuals = new HashMap<>();
	private final Map<UUID, Long> lastVisualPublish = new HashMap<>();
	private static final int MAX_VISUAL_UPLOAD_BYTES_PER_SECOND = 6 * 1024 * 1024;
	private static final class UploadWindow { long startedAt; int bytes; }
	private final Map<UUID, UploadWindow> visualUploadWindows = new HashMap<>();
	private long publicationSequence;
	private MinecraftServer runningServer;
	private int tick;

	private HumanCraftServer(HumanCraftConfig config) {
		this.config = config;
		this.store = new SnapshotStore(config.liveFrameTtlMs);
	}

	public static synchronized void init(HumanCraftConfig config) {
		if (instance != null) {
			return;
		}
		instance = new HumanCraftServer(config);
		HumanCraftPayloads.register();
		instance.registerHandlers();
		HumanCraftCommands.register(instance.avatars);
	}

	/** Test/diagnostic access to the authoritative store. */
	public SnapshotStore store() {
		return store;
	}

	private void registerHandlers() {
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.InstallSnapshot.TYPE, (payload, ctx) -> onInstall(payload, ctx.player()));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ClearSnapshot.TYPE, (payload, ctx) -> onClear(ctx.player()));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ProbeRequest.TYPE, (payload, ctx) -> onProbe(payload, ctx.player()));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ControlInput.TYPE,
				(payload, ctx) -> avatars.acceptIntent(ctx.player(), payload.toIntent(System.currentTimeMillis())));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.AnatomyAttack.TYPE,
				(payload, ctx) -> onAnatomyAttack(payload, ctx.player()));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ArmSwing.TYPE,
				(payload, ctx) -> onArmSwing(payload, ctx.player()));
		ServerPlayNetworking.registerGlobalReceiver(HumanCraftPayloads.ScanChunk.TYPE,
				(payload, ctx) -> onScanChunk(payload, ctx.player()));

		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
			runningServer = server;
			if (!(handler.getPlayer() instanceof ScannedServerPlayer)) {
				avatars.sendState(handler.getPlayer());
				replaySharedVisuals(handler.getPlayer());
			}
		});
		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
			if (handler.getPlayer() instanceof ScannedServerPlayer) return;
			removeSharedVisual(handler.getPlayer().getUUID());
			forget(handler.getPlayer().getUUID(), "disconnect");
			avatars.forget(handler.getPlayer());
		});
		ServerEntityWorldChangeEvents.AFTER_PLAYER_CHANGE_WORLD.register((player, from, to) -> {
			removeSharedVisual(player.getUUID());
			if (store.clear(player.getUUID())) {
				emitters.remove(player.getUUID());
				Telemetry.log(SentryLevel.INFO, "snapshot cleared for %s: changed world %s -> %s", player.getUUID(),
						from.dimension().location(), to.dimension().location());
				ServerPlayNetworking.send(player, new HumanCraftPayloads.SnapshotAck(-1, true, "cleared", "world changed", -1, 0));
			}
		});
		ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
			store.forgetAll();
			emitters.clear();
			swingGates.clear();
			acceptedInstalls.clear();
			incomingScans.clear();
			sharedVisuals.clear();
			lastVisualPublish.clear();
			visualUploadWindows.clear();
			runningServer = null;
		});
		ServerTickEvents.END_SERVER_TICK.register(this::onTick);
	}

	private void forget(UUID player, String reason) {
		store.forget(player);
		acceptedInstalls.remove(player);
		incomingScans.remove(player);
		lastVisualPublish.remove(player);
		visualUploadWindows.remove(player);
		swingGates.remove(player);
		emitters.remove(player);
		HumanCraft.LOGGER.debug("Forgot snapshot state for {} ({})", player, reason);
	}

	// ---- install / clear ---------------------------------------------------------------------

	private void onInstall(HumanCraftPayloads.InstallSnapshot payload, ServerPlayer player) {
		long now = System.currentTimeMillis();
		UUID owner = player.getUUID();
		AvatarService.Binding binding = avatars.binding(player);
		String dimension = player.serverLevel().dimension().location().toString();
		try (Telemetry.Span span = Telemetry.transaction("server.install_snapshot", "hmc.install")) {
			span.data("frame_id", payload.frameId()).data("collider_count", payload.colliders().size())
					.tag("mode", payload.mode()).tag("dimension", dimension);
			InstallOutcome outcome;
			int validColliders = 0;
			try {
				InstallRequest request = Telemetry.timed(span, "hmc.decode", "payload->InstallRequest", payload::toRequest);
				if (!request.targetPlayerId().equals(binding.targetId())
						|| request.bindingGeneration() != binding.generation()
						|| request.normalizationRevision() != binding.normalizationRevision()) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "snapshot binding is stale or targets the wrong player");
				}
				ServerPlayer target = player.server.getPlayerList().getPlayer(binding.targetId());
				if (target == null || target.serverLevel() != player.serverLevel()) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "target player is unavailable");
				}
				outcome = Telemetry.timed(span, "hmc.install.validate_transform", "SnapshotStore.install",
						() -> store.install(owner, dimension, request, now));
			} catch (ProtocolException | IllegalArgumentException e) {
				outcome = InstallOutcome.rejected(InstallOutcome.REJECTED_INVALID, e.getMessage(), store.activeFrameId(owner, now));
				Telemetry.captureException(e, "server.install.invalid", Map.of("frame_id", Long.toString(payload.frameId())));
			}
			if (outcome.accepted()) {
				validColliders = store.active(owner, now).map(s -> (int) s.validColliderCount()).orElse(0);
				ContactService.Emitter emitter = emitters.computeIfAbsent(owner, k -> new ContactService.Emitter(CONTACT_HEARTBEAT_MS));
				emitter.reset();
				ArrayDeque<AcceptedInstall> history = acceptedInstalls.computeIfAbsent(owner, ignored -> new ArrayDeque<>());
				history.addLast(new AcceptedInstall(payload, dimension, now));
				while (history.size() > 32) history.removeFirst();
				history.removeIf(item -> now - item.acceptedAtMillis() > 2_000);
			}
			span.tag("result", outcome.code());
			Telemetry.log(outcome.accepted() ? SentryLevel.INFO : SentryLevel.WARNING,
					"install frame=%d owner=%s result=%s %s", payload.frameId(), owner, outcome.code(), outcome.detail());
			ServerPlayNetworking.send(player, HumanCraftPayloads.SnapshotAck.of(payload.frameId(), outcome, validColliders,
					payload.fusionId(), payload.bindingGeneration(), payload.normalizationRevision()));
		}
	}

	private void onClear(ServerPlayer player) {
		boolean had = store.clear(player.getUUID());
		emitters.remove(player.getUUID());
		acceptedInstalls.remove(player.getUUID());
		incomingScans.remove(player.getUUID());
		removeSharedVisual(player.getUUID());
		Telemetry.breadcrumb("hmc.server", "clear snapshot had=" + had);
		ServerPlayNetworking.send(player, new HumanCraftPayloads.SnapshotAck(-1, true, "cleared", had ? "cleared active snapshot" : "nothing active", -1, 0));
	}

	private void onScanChunk(HumanCraftPayloads.ScanChunk chunk, ServerPlayer owner) {
		UUID ownerId = owner.getUUID();
		long now = System.currentTimeMillis();
		try {
			UploadWindow window = visualUploadWindows.computeIfAbsent(ownerId, ignored -> new UploadWindow());
			if (window.startedAt == 0 || now - window.startedAt >= 1_000) { window.startedAt = now; window.bytes = 0; }
			window.bytes = Math.addExact(window.bytes, chunk.data().length);
			if (window.bytes > MAX_VISUAL_UPLOAD_BYTES_PER_SECOND) {
				throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "shared scan upload rate exceeded");
			}
			ScanTransfer transfer = incomingScans.get(ownerId);
			if (transfer == null || transfer.expired(now) || !transfer.transferId().equals(chunk.transferId())) {
				transfer = new ScanTransfer(chunk, now);
				incomingScans.put(ownerId, transfer);
			}
			Optional<byte[]> complete = transfer.accept(chunk);
			if (complete.isEmpty()) return;
			incomingScans.remove(ownerId);
			var frame = SharedScanCodec.decode(complete.get());
			AcceptedInstall accepted = matchingInstall(ownerId, chunk, now).orElseThrow(() ->
					new ProtocolException(ProtocolException.INVALID_MESSAGE, "shared scan has no matching accepted install"));
			if (frame.frameId() != chunk.frameId() || !frame.header().sessionId().equals(chunk.sessionId())
					|| !frame.header().calibrationId().equals(chunk.calibrationId())
					|| !Objects.equals(frame.header().fusionId(), chunk.fusionId())
					|| !sameColliders(frame.header().colliders().stream().map(WireCollider::of).toList(), accepted.payload().colliders())) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "shared scan frame identity or geometry metadata differs from install");
			}
			AvatarService.Binding binding = avatars.binding(owner);
			if (!binding.targetId().equals(chunk.targetPlayerId()) || binding.generation() != chunk.bindingGeneration()
					|| binding.normalizationRevision() != chunk.normalizationRevision()) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "shared scan binding is obsolete");
			}
			ServerPlayer target = owner.server.getPlayerList().getPlayer(chunk.targetPlayerId());
			if (target == null || target.serverLevel() != owner.serverLevel()) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "shared scan target is unavailable");
			}
			if (now - lastVisualPublish.getOrDefault(ownerId, 0L) < 100) return;
			lastVisualPublish.put(ownerId, now);
			long publication = ++publicationSequence;
			List<byte[]> parts = SharedScanCodec.chunks(complete.get());
			List<HumanCraftPayloads.SharedScanChunk> relayed = new ArrayList<>(parts.size());
			for (int i = 0; i < parts.size(); i++) {
				HumanCraftPayloads.ScanChunk part = new HumanCraftPayloads.ScanChunk(chunk.transferId(), chunk.frameId(), chunk.sessionId(),
						chunk.calibrationId(), chunk.fusionId(), chunk.targetPlayerId(), chunk.bindingGeneration(),
						chunk.normalizationRevision(), chunk.anchorX(), chunk.anchorY(), chunk.anchorZ(), chunk.blocksPerMeter(),
						i, parts.size(), complete.get().length, parts.get(i));
				relayed.add(new HumanCraftPayloads.SharedScanChunk(publication, ownerId, part));
			}
			CachedVisual cached = new CachedVisual(publication, ownerId, chunk.targetPlayerId(), accepted.dimension(), List.copyOf(relayed));
			sharedVisuals.put(ownerId, cached);
			broadcast(owner, cached);
		} catch (RuntimeException e) {
			incomingScans.remove(ownerId);
			HumanCraft.LOGGER.warn("Rejected shared scan chunk from {}: {}", ownerId, e.toString());
		}
	}

	private static boolean sameColliders(List<WireCollider> a, List<WireCollider> b) {
		if (a.size() != b.size()) return false;
		for (int i = 0; i < a.size(); i++) {
			WireCollider x = a.get(i), y = b.get(i);
			if (!x.id().equals(y.id()) || !x.bodyPart().equals(y.bodyPart()) || !x.type().equals(y.type())
					|| x.valid() != y.valid() || !x.fitSource().equals(y.fitSource())
					|| Float.compare(x.quality(), y.quality()) != 0 || !java.util.Arrays.equals(x.numbers(), y.numbers())) return false;
		}
		return true;
	}

	private Optional<AcceptedInstall> matchingInstall(UUID owner, HumanCraftPayloads.ScanChunk chunk, long now) {
		ArrayDeque<AcceptedInstall> history = acceptedInstalls.get(owner);
		if (history == null) return Optional.empty();
		history.removeIf(item -> now - item.acceptedAtMillis() > 2_000);
		return history.stream().filter(item -> {
			var p = item.payload();
			return p.frameId() == chunk.frameId() && p.sessionId().equals(chunk.sessionId())
					&& p.calibrationId().equals(chunk.calibrationId()) && Objects.equals(p.fusionId(), chunk.fusionId())
					&& p.targetPlayerId().equals(chunk.targetPlayerId()) && p.bindingGeneration() == chunk.bindingGeneration()
					&& p.normalizationRevision() == chunk.normalizationRevision()
					&& Double.compare(p.anchorX(), chunk.anchorX()) == 0 && Double.compare(p.anchorY(), chunk.anchorY()) == 0
					&& Double.compare(p.anchorZ(), chunk.anchorZ()) == 0 && Double.compare(p.blocksPerMeter(), chunk.blocksPerMeter()) == 0;
		}).reduce((a, b) -> b);
	}

	private void broadcast(ServerPlayer owner, CachedVisual visual) {
		for (ServerPlayer viewer : owner.server.getPlayerList().getPlayers()) {
			if (viewer == owner || !viewer.serverLevel().dimension().location().toString().equals(visual.dimension())) continue;
			if (!ServerPlayNetworking.canSend(viewer, HumanCraftPayloads.SharedScanChunk.TYPE)) continue;
			for (var chunk : visual.chunks()) ServerPlayNetworking.send(viewer, chunk);
		}
	}

	private void replaySharedVisuals(ServerPlayer viewer) {
		String dimension = viewer.serverLevel().dimension().location().toString();
		for (CachedVisual visual : sharedVisuals.values()) {
			if (visual.owner().equals(viewer.getUUID()) || !visual.dimension().equals(dimension)
					|| !ServerPlayNetworking.canSend(viewer, HumanCraftPayloads.SharedScanChunk.TYPE)) continue;
			for (var chunk : visual.chunks()) ServerPlayNetworking.send(viewer, chunk);
		}
	}

	private void removeSharedVisual(UUID ownerId) {
		CachedVisual visual = sharedVisuals.remove(ownerId);
		if (visual == null) return;
		long publication = ++publicationSequence;
		HumanCraftPayloads.SharedScanRemoved removed = new HumanCraftPayloads.SharedScanRemoved(publication, ownerId, visual.target());
		if (runningServer == null) return;
		for (ServerPlayer viewer : runningServer.getPlayerList().getPlayers()) {
			if (ServerPlayNetworking.canSend(viewer, HumanCraftPayloads.SharedScanRemoved.TYPE)) {
				ServerPlayNetworking.send(viewer, removed);
			}
		}
	}

	private void onArmSwing(HumanCraftPayloads.ArmSwing payload, ServerPlayer observer) {
		if (!config.armSwingEnabled || !observer.isAlive() || observer.isSpectator()) return;
		var binding = avatars.binding(observer);
		if (!payload.targetPlayerId().equals(binding.targetId()) || payload.bindingGeneration() != binding.generation()
				|| payload.normalizationRevision() != binding.normalizationRevision()) return;
		ServerPlayer actor = observer.server.getPlayerList().getPlayer(binding.targetId());
		if (actor == null || !actor.isAlive() || actor.isSpectator() || actor.serverLevel() != observer.serverLevel()) return;
		long now = System.currentTimeMillis();
		ServerSnapshot active = store.active(observer.getUUID(), now).orElse(null);
		if (active == null || !active.dimension().equals(actor.serverLevel().dimension().location().toString())) return;
		if (!swingGates.computeIfAbsent(observer.getUUID(), ignored -> new ArmSwingGate())
				.accept(payload, active, now, config.liveFrameTtlMs, config.armSwingCooldownMs)) return;
		actor.swing(payload.left() ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND);
		Vector3 origin = new Vector3(actor.getX(), actor.getY(), actor.getZ());
		int hits = 0;
		for (LivingEntity target : actor.serverLevel().getEntitiesOfClass(LivingEntity.class,
				actor.getBoundingBox().inflate(SwingArc.REACH))) {
			if (target == actor || target == observer || !target.isAlive() || target.isSpectator()
					|| !target.isAttackable() || actor.isAlliedTo(target)) continue;
			if (target instanceof ServerPlayer player && !actor.canHarmPlayer(player)) continue;
			if (!SwingArc.contains(origin, actor.getYRot(), new Vector3(target.getX(), target.getY(), target.getZ()))
					|| !actor.hasLineOfSight(target)) continue;
			// A plain fist hit for every target; weapon damage and sweeping enchantments are deliberately not applied.
			if (target.hurt(actor.damageSources().playerAttack(actor), 1.0f)) {
				double yaw = Math.toRadians(actor.getYRot());
				target.knockback(0.4, Math.sin(yaw), -Math.cos(yaw));
				hits++;
			}
		}
		HumanCraft.LOGGER.debug("Arm swing frame={} actor={} hand={} hits={}", payload.frameId(), actor.getUUID(),
				payload.left() ? "left" : "right", hits);
	}

	// ---- probe -------------------------------------------------------------------------------

	private void onProbe(HumanCraftPayloads.ProbeRequest payload, ServerPlayer player) {
		long now = System.currentTimeMillis();
		ServerLevel level = player.serverLevel();
		String dimension = level.dimension().location().toString();
		try (Telemetry.Span span = Telemetry.transaction("server.probe", "hmc.probe")) {
			Optional<ServerSnapshot> active = resolvedSnapshot(player, now).filter(s -> s.dimension().equals(dimension));
			Vec3 eye = player.getEyePosition();
			Vec3 look = player.getViewVector(1.0f);
			Ray ray = new Ray(new Vector3(eye.x, eye.y, eye.z), new Vector3(look.x, look.y, look.z));
			double reach = config.probeReachBlocks > 0 ? config.probeReachBlocks : player.blockInteractionRange();

			ProbeService.BlockOcclusion occlusion = (r, maxDistance) -> {
				try (Telemetry.Span s = span.child("hmc.probe.block_occlusion", "Level.clip")) {
					Vec3 to = eye.add(look.scale(maxDistance));
					BlockHitResult hit = level.clip(new ClipContext(eye, to, ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE, player));
					if (hit.getType() != HitResult.Type.BLOCK) {
						return OptionalDouble.empty();
					}
					return OptionalDouble.of(hit.getLocation().distanceTo(eye));
				}
			};

			ProbeService.ProbeOutcome outcome;
			try (Telemetry.Span s = span.child("hmc.probe.body", "broad+narrow phase")) {
				outcome = ProbeService.probe(payload.toQuery(), active, ray, reach, occlusion);
				s.data("broad_candidates", outcome.broadPhaseCandidates()).data("narrow_tests", outcome.narrowPhaseTests());
			}
			span.tag("result", outcome.code().wireName()).data("distance", outcome.distance())
					.data("collider_id", outcome.colliderId().orElse(""));
			ServerPlayNetworking.send(player, HumanCraftPayloads.ProbeResult.of(outcome));
		} catch (RuntimeException e) {
			Telemetry.captureException(e, "server.probe");
			ServerPlayNetworking.send(player, new HumanCraftPayloads.ProbeResult(payload.requestId(), "MISS", payload.expectedFrameId(), "", "", -1, false, 0, 0, 0));
		}
	}

	private void onAnatomyAttack(HumanCraftPayloads.AnatomyAttack payload, ServerPlayer attacker) {
		Optional<ServerSnapshot> active = resolvedSnapshot(attacker, System.currentTimeMillis());
		if (active.isEmpty()) return;
		ServerPlayer target = attacker.server.getPlayerList().getPlayer(active.get().targetPlayerId());
		if (target == null || target == attacker || !target.isAlive()) return;
		double reach = attacker.entityInteractionRange();
		Vec3 eye = attacker.getEyePosition();
		Vec3 look = attacker.getViewVector(1.0f);
		Ray ray = new Ray(new Vector3(eye.x, eye.y, eye.z), new Vector3(look.x, look.y, look.z));
		Optional<BodyRaycaster.BodyHit> anatomy = BodyRaycaster.closestHit(active.get().worldColliders(), ray, reach);
		if (anatomy.isEmpty()) return;
		double distance = anatomy.get().distance();
		BlockHitResult block = attacker.serverLevel().clip(new ClipContext(eye, eye.add(look.scale(reach)),
				ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE, attacker));
		if (block.getType() == HitResult.Type.BLOCK && block.getLocation().distanceTo(eye) < distance) return;
		EntityHitResult ordinary = ProjectileUtil.getEntityHitResult(attacker, eye, eye.add(look.scale(reach)),
				new AABB(eye, eye.add(look.scale(reach))).inflate(1.0),
				entity -> entity.isPickable() && entity != attacker, reach * reach);
		if (ordinary != null && ordinary.getEntity() != target && ordinary.getLocation().distanceTo(eye) < distance) return;
		attacker.attack(target);
	}

	public Optional<EntityHitResult> projectileAnatomyHit(net.minecraft.world.entity.projectile.Projectile projectile, Vec3 start, Vec3 end,
			EntityHitResult ordinary) {
		double segmentLength = start.distanceTo(end);
		if (segmentLength < 1e-9) return Optional.ofNullable(ordinary);
		Ray ray = new Ray(new Vector3(start.x, start.y, start.z),
				new Vector3(end.x - start.x, end.y - start.y, end.z - start.z));
		double best = ordinary == null ? segmentLength : ordinary.getLocation().distanceTo(start);
		EntityHitResult result = ordinary;
		long now = System.currentTimeMillis();
		for (ServerSnapshot snapshot : store.activeSnapshots(now)) {
			if (projectile.getServer() == null) continue;
			ServerPlayer target = projectile.getServer().getPlayerList().getPlayer(snapshot.targetPlayerId());
			if (target == null || target.level() != projectile.level() || target == projectile.getOwner()) continue;
			ServerSnapshot resolved = snapshot.resolved(target);
			Optional<BodyRaycaster.BodyHit> hit = BodyRaycaster.closestHit(resolved.worldColliders(), ray, Math.min(best, segmentLength));
			if (hit.isPresent() && hit.get().distance() < best) {
				best = hit.get().distance();
				Vector3 p = hit.get().point();
				result = new EntityHitResult(target, new Vec3(p.x(), p.y(), p.z()));
			}
		}
		return Optional.ofNullable(result);
	}

	public static Optional<HumanCraftServer> instance() {
		return Optional.ofNullable(instance);
	}

	// ---- contacts ----------------------------------------------------------------------------

	private void onTick(MinecraftServer server) {
		runningServer = server;
		avatars.tick(server);
		List<UUID> obsoleteVisuals = new ArrayList<>();
		for (CachedVisual visual : sharedVisuals.values()) {
			ServerPlayer owner = server.getPlayerList().getPlayer(visual.owner());
			ServerPlayer target = server.getPlayerList().getPlayer(visual.target());
			if (owner == null || target == null || target.serverLevel() != owner.serverLevel()
					|| !owner.serverLevel().dimension().location().toString().equals(visual.dimension())
					|| !avatars.binding(owner).targetId().equals(visual.target())) obsoleteVisuals.add(visual.owner());
		}
		obsoleteVisuals.forEach(this::removeSharedVisual);
		tick++;
		if (tick % CONTACT_INTERVAL_TICKS != 0 || store.size() == 0) {
			return;
		}
		long now = System.currentTimeMillis();
		for (ServerPlayer player : server.getPlayerList().getPlayers()) {
			Optional<ServerSnapshot> active = resolvedSnapshot(player, now);
			if (active.isEmpty()) {
				continue;
			}
			ServerLevel level = player.serverLevel();
			if (!active.get().dimension().equals(level.dimension().location().toString())) {
				continue;
			}
			try (Telemetry.Span span = Telemetry.transaction("server.contact_query", "hmc.contact")) {
				BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
				ContactService.ContactState state = ContactService.computeWithShapes(active.get(), (x, y, z) -> {
					pos.set(x, y, z);
					if (!level.isLoaded(pos)) return java.util.List.of();
					return level.getBlockState(pos).getCollisionShape(level, pos).toAabbs().stream()
							.map(box -> new dev.humancraft.geometry.Aabb(
									new Vector3(box.minX + x, box.minY + y, box.minZ + z),
									new Vector3(box.maxX + x, box.maxY + y, box.maxZ + z)))
							.toList();
				});
				span.data("cells_tested", state.cellsTested()).data("contacts", state.contacts().size());
				ContactService.Emitter emitter = emitters.computeIfAbsent(player.getUUID(), k -> new ContactService.Emitter(CONTACT_HEARTBEAT_MS));
				if (emitter.shouldEmit(state, now)) {
					ServerPlayNetworking.send(player, HumanCraftPayloads.ContactState.of(state));
				}
			} catch (RuntimeException e) {
				Telemetry.captureException(e, "server.contact");
			}
		}
	}

	private Optional<ServerSnapshot> resolvedSnapshot(ServerPlayer observer, long now) {
		return store.active(observer.getUUID(), now).flatMap(snapshot -> {
			AvatarService.Binding binding = avatars.binding(observer);
			if (!snapshot.targetPlayerId().equals(binding.targetId())
					|| snapshot.bindingGeneration() != binding.generation()
					|| snapshot.normalizationRevision() != binding.normalizationRevision()) {
				return Optional.empty();
			}
			ServerPlayer target = observer.server.getPlayerList().getPlayer(snapshot.targetPlayerId());
			if (target == null || target.serverLevel() != observer.serverLevel() || !target.isAlive()) {
				return Optional.empty();
			}
			return Optional.of(snapshot.resolved(target));
		});
	}
}
