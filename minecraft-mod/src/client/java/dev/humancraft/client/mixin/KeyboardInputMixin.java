package dev.humancraft.client.mixin;

import dev.humancraft.client.controller.ExternalController;
import net.minecraft.client.player.KeyboardInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(KeyboardInput.class)
public abstract class KeyboardInputMixin {
	@Inject(method = "tick(ZF)V", at = @At("TAIL"))
	private void humancraft$mergeBadgeInput(boolean slowDown, float multiplier, CallbackInfo ci) {
		ExternalController.applyMovement((KeyboardInput) (Object) this);
	}
}
