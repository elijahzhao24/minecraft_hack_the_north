package dev.humancraft.avatar;

import com.mojang.authlib.GameProfile;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ClientInformation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;

/** A real ServerPlayer with normal health/inventory/physics and server-fed input. */
public final class ScannedServerPlayer extends ServerPlayer {
	private volatile ControlIntent intent = ControlIntent.idle(0);

	public ScannedServerPlayer(MinecraftServer server, ServerLevel level, GameProfile profile) {
		super(server, level, profile, ClientInformation.createDefault());
	}

	public void accept(ControlIntent next) {
		intent = next;
	}

	public void prepareForSpawn() {
		unsetRemoved();
	}

	@Override
	public void tick() {
		ControlIntent current = intent;
		long now = System.currentTimeMillis();
		if (now - current.receivedAtMillis() > AvatarService.CONTROL_TIMEOUT_MS || !isAlive()) {
			current = ControlIntent.idle(now);
			intent = current;
		}
		setYRot(current.yaw());
		setYHeadRot(current.yaw());
		setXRot(current.pitch());
		setShiftKeyDown(current.sneak());
		setSprinting(current.sprint());
		if (current.jump() && onGround()) {
			jumpFromGround();
		}
		travel(new Vec3(current.strafe(), 0, current.forward()));
		super.tick();
		doTick();
		if (server.getTickCount() % 10 == 0) {
			connection.resetPosition();
			serverLevel().getChunkSource().move(this);
		}
	}
}
