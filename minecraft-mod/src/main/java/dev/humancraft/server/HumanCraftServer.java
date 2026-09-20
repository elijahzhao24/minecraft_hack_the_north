package dev.humancraft.server;

import dev.humancraft.HumanCraft;
import dev.humancraft.avatar.AvatarService;
import dev.humancraft.avatar.HumanCraftCommands;
import dev.humancraft.avatar.ScannedServerPlayer;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.network.HumanCraftPayloads;
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

import java.util.HashMap;
import java.util.Map;
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

		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
			if (!(handler.getPlayer() instanceof ScannedServerPlayer)) {
				avatars.sendState(handler.getPlayer());
			}
		});
		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
			if (handler.getPlayer() instanceof ScannedServerPlayer) return;
			forget(handler.getPlayer().getUUID(), "disconnect");
			avatars.forget(handler.getPlayer());
		});
		ServerEntityWorldChangeEvents.AFTER_PLAYER_CHANGE_WORLD.register((player, from, to) -> {
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
		});
		ServerTickEvents.END_SERVER_TICK.register(this::onTick);
	}

	private void forget(UUID player, String reason) {
		store.forget(player);
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
		Telemetry.breadcrumb("hmc.server", "clear snapshot had=" + had);
		ServerPlayNetworking.send(player, new HumanCraftPayloads.SnapshotAck(-1, true, "cleared", had ? "cleared active snapshot" : "nothing active", -1, 0));
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
		avatars.tick(server);
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
