package dev.humancraft.client.mixin;

import dev.humancraft.client.input.MouseMovement;
import net.minecraft.client.player.Input;
import net.minecraft.client.player.KeyboardInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(KeyboardInput.class)
public abstract class KeyboardInputMixin extends Input {
	@Inject(method = "tick", at = @At("TAIL"))
	private void humancraft$mouseMovement(boolean slowDown, float slowDownFactor, CallbackInfo ci) {
		MouseMovement.apply(this);
	}
}
