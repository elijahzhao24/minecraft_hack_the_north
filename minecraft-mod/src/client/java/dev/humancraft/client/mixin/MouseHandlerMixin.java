package dev.humancraft.client.mixin;

import dev.humancraft.client.input.MouseMovement;
import net.minecraft.client.MouseHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MouseHandler.class)
public abstract class MouseHandlerMixin {
	@Inject(method = "onPress", at = @At("HEAD"), cancellable = true)
	private void humancraft$movementButtons(long window, int button, int action, int modifiers, CallbackInfo ci) {
		if (MouseMovement.consumes(button)) ci.cancel();
	}
}
