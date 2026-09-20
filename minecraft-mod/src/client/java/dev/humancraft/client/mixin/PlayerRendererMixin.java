package dev.humancraft.client.mixin;

import com.mojang.blaze3d.vertex.PoseStack;
import dev.humancraft.client.render.ScanRenderState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.entity.player.PlayerRenderer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(PlayerRenderer.class)
public abstract class PlayerRendererMixin {
	@Inject(method = "render(Lnet/minecraft/client/player/AbstractClientPlayer;FFLcom/mojang/blaze3d/vertex/PoseStack;Lnet/minecraft/client/renderer/MultiBufferSource;I)V",
			at = @At("HEAD"), cancellable = true)
	private void humancraft$replaceSkin(AbstractClientPlayer player, float yaw, float tickDelta, PoseStack matrices,
			MultiBufferSource vertices, int light, CallbackInfo ci) {
		if (!ScanRenderState.replaces(player.getUUID())) return;
		if (player.isSwimming() || player.isFallFlying()) return;
		Minecraft client = Minecraft.getInstance();
		if (player == client.player && client.options.getCameraType().isFirstPerson()) return;
		ci.cancel();
	}
}
