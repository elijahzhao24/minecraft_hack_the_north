package dev.humancraft.mixin;

import dev.humancraft.server.HumanCraftServer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.function.Predicate;

@Mixin(ProjectileUtil.class)
public abstract class ProjectileUtilMixin {
	@Inject(method = "getEntityHitResult(Lnet/minecraft/world/level/Level;Lnet/minecraft/world/entity/Entity;Lnet/minecraft/world/phys/Vec3;Lnet/minecraft/world/phys/Vec3;Lnet/minecraft/world/phys/AABB;Ljava/util/function/Predicate;F)Lnet/minecraft/world/phys/EntityHitResult;",
			at = @At("RETURN"), cancellable = true)
	private static void humancraft$anatomicalProjectile(Level level, Entity source, Vec3 start, Vec3 end,
			AABB search, Predicate<Entity> predicate, float margin, CallbackInfoReturnable<EntityHitResult> cir) {
		if (level.isClientSide || !(source instanceof Projectile projectile)) return;
		HumanCraftServer.instance().flatMap(server -> server.projectileAnatomyHit(projectile, start, end, cir.getReturnValue()))
				.ifPresent(cir::setReturnValue);
	}
}
