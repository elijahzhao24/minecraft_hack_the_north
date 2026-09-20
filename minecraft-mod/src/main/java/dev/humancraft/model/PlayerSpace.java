package dev.humancraft.model;

import dev.humancraft.contract.ColliderDto;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import net.minecraft.world.entity.Entity;

import java.util.List;

/** Converts accepted player-local scan geometry into the target entity's current world pose. */
public final class PlayerSpace {
	private PlayerSpace() {}

	public static Vector3 worldPoint(Vector3 local, Entity player) {
		return worldPoint(local, new Vector3(player.getX(), player.getY(), player.getZ()), player.getYRot());
	}

	public static Vector3 worldPoint(Vector3 local, Vector3 position, float yaw) {
		double radians = Math.toRadians(yaw);
		double cos = Math.cos(radians);
		double sin = Math.sin(radians);
		double x = local.x() * cos - local.z() * sin;
		double z = local.x() * sin + local.z() * cos;
		return new Vector3(position.x() + x, position.y() + local.y(), position.z() + z);
	}

	public static Vector3 worldDirection(Vector3 local, Entity player) {
		double radians = Math.toRadians(player.getYRot());
		double cos = Math.cos(radians);
		double sin = Math.sin(radians);
		return new Vector3(local.x() * cos - local.z() * sin, local.y(), local.x() * sin + local.z() * cos);
	}

	public static Shape worldShape(Shape local, Entity player) {
		return switch (local) {
			case Sphere s -> new Sphere(worldPoint(s.center(), player), s.radius());
			case Capsule c -> new Capsule(worldPoint(c.a(), player), worldPoint(c.b(), player), c.radius());
			case Obb o -> new Obb(worldPoint(o.center(), player),
					o.axes().stream().map(axis -> worldDirection(axis, player)).toList(), o.halfExtents());
		};
	}

	public static List<ColliderDto> worldColliders(List<ColliderDto> local, Entity player) {
		return local.stream().map(c -> c.withGeometry(c.geometry().map(s -> worldShape(s, player)))).toList();
	}
}
