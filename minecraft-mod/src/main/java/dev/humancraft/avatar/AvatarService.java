package dev.humancraft.avatar;

import com.mojang.authlib.GameProfile;
import dev.humancraft.network.HumanCraftPayloads;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.GameType;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** Owns one scan binding per observer and the optional internal player lifecycle. */
public final class AvatarService {
	public static final long CONTROL_TIMEOUT_MS = 250;
	private static final String INTERNAL_NAME = "HumanScan";

	public record Binding(UUID observerId, UUID targetId, AvatarMode mode, long generation, long normalizationRevision, boolean controlling) {}

	private final Map<UUID, Binding> bindings = new ConcurrentHashMap<>();
	private final Map<UUID, ScannedServerPlayer> internalPlayers = new ConcurrentHashMap<>();

	public Binding self(ServerPlayer observer) {
		return replace(observer, observer.getUUID(), AvatarMode.SELF, false);
	}

	public Binding spawnSeparate(ServerPlayer observer) {
		despawnInternal(observer);
		MinecraftServer server = observer.server;
		UUID id = UUID.nameUUIDFromBytes(("humancraft:" + observer.getUUID()).getBytes(StandardCharsets.UTF_8));
		GameProfile profile = new GameProfile(id, INTERNAL_NAME);
		ScannedServerPlayer scanned = new ScannedServerPlayer(server, observer.serverLevel(), profile);
		server.getPlayerList().placeNewPlayer(new InternalPlayerConnection(), scanned,
				new CommonListenerCookie(profile, 0, scanned.clientInformation(), false));
		scanned.teleportTo(observer.serverLevel(), observer.getX() + 2.0, observer.getY(), observer.getZ(), observer.getYRot(), 0);
		scanned.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
		scanned.setHealth(20.0f);
		scanned.prepareForSpawn();
		internalPlayers.put(observer.getUUID(), scanned);
		return replace(observer, scanned.getUUID(), AvatarMode.SEPARATE, false);
	}

	public void despawn(ServerPlayer observer) {
		despawnInternal(observer);
		self(observer);
	}

	public Binding control(ServerPlayer observer, boolean enabled) {
		Binding old = binding(observer);
		boolean allowed = enabled && old.mode() == AvatarMode.SEPARATE && target(observer.server, old).isPresent();
		Binding next = new Binding(old.observerId(), old.targetId(), old.mode(), old.generation() + 1, old.normalizationRevision(), allowed);
		bindings.put(observer.getUUID(), next);
		send(observer, next);
		return next;
	}

	public Binding recalibrate(ServerPlayer observer) {
		Binding old = binding(observer);
		Binding next = new Binding(old.observerId(), old.targetId(), old.mode(), old.generation() + 1, old.normalizationRevision() + 1, old.controlling());
		bindings.put(observer.getUUID(), next);
		send(observer, next);
		return next;
	}

	public Binding binding(ServerPlayer observer) {
		return bindings.computeIfAbsent(observer.getUUID(), ignored ->
				new Binding(observer.getUUID(), observer.getUUID(), AvatarMode.SELF, 1, 1, false));
	}

	public void acceptIntent(ServerPlayer observer, ControlIntent intent) {
		Binding binding = binding(observer);
		if (!binding.controlling() || binding.mode() != AvatarMode.SEPARATE) return;
		ScannedServerPlayer player = internalPlayers.get(observer.getUUID());
		if (player != null && player.isAlive() && player.level() == observer.level()) {
			player.accept(intent);
		} else {
			control(observer, false);
		}
	}

	public void forget(ServerPlayer observer) {
		despawnInternal(observer);
		bindings.remove(observer.getUUID());
	}

	public void sendState(ServerPlayer observer) {
		send(observer, binding(observer));
	}

	/** Release control on death/world separation; the command can then spawn a fresh player explicitly. */
	public void tick(MinecraftServer server) {
		for (var entry : java.util.List.copyOf(internalPlayers.entrySet())) {
			ServerPlayer observer = server.getPlayerList().getPlayer(entry.getKey());
			ScannedServerPlayer scanned = entry.getValue();
			if (observer == null) continue;
			if (!scanned.isAlive() || scanned.level() != observer.level()) {
				control(observer, false);
			}
		}
	}

	private Binding replace(ServerPlayer observer, UUID target, AvatarMode mode, boolean controlling) {
		Binding old = bindings.get(observer.getUUID());
		long generation = old == null ? 1 : old.generation() + 1;
		long revision = old == null ? 1 : old.normalizationRevision();
		Binding next = new Binding(observer.getUUID(), target, mode, generation, revision, controlling);
		bindings.put(observer.getUUID(), next);
		send(observer, next);
		return next;
	}

	private void despawnInternal(ServerPlayer observer) {
		ScannedServerPlayer player = internalPlayers.remove(observer.getUUID());
		if (player != null) {
			observer.server.getPlayerList().remove(player);
			player.remove(Entity.RemovalReason.DISCARDED);
		}
	}

	private static java.util.Optional<ServerPlayer> target(MinecraftServer server, Binding binding) {
		return java.util.Optional.ofNullable(server.getPlayerList().getPlayer(binding.targetId()));
	}

	private static void send(ServerPlayer observer, Binding binding) {
		if (ServerPlayNetworking.canSend(observer, HumanCraftPayloads.AvatarState.TYPE)) {
			ServerPlayNetworking.send(observer, HumanCraftPayloads.AvatarState.of(binding));
		}
		observer.sendSystemMessage(Component.literal("HumanCraft avatar: " + binding.mode().name().toLowerCase()
				+ (binding.controlling() ? " (controlled)" : "")));
	}
}
