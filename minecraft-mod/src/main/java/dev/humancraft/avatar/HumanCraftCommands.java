package dev.humancraft.avatar;

import com.mojang.brigadier.CommandDispatcher;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;

public final class HumanCraftCommands {
	private HumanCraftCommands() {}

	public static void register(AvatarService avatars) {
		CommandRegistrationCallback.EVENT.register((dispatcher, registry, environment) -> build(dispatcher, avatars));
	}

	private static void build(CommandDispatcher<CommandSourceStack> dispatcher, AvatarService avatars) {
		dispatcher.register(Commands.literal("humancraft")
				.then(Commands.literal("mode")
						.then(Commands.literal("self").executes(ctx -> run(ctx.getSource(), p -> avatars.self(p))))
						.then(Commands.literal("separate").executes(ctx -> run(ctx.getSource(), p -> avatars.spawnSeparate(p)))))
				.then(Commands.literal("spawn").executes(ctx -> run(ctx.getSource(), p -> avatars.spawnSeparate(p))))
				.then(Commands.literal("despawn").executes(ctx -> run(ctx.getSource(), p -> avatars.despawn(p))))
				.then(Commands.literal("control").executes(ctx -> run(ctx.getSource(), p -> avatars.control(p, true))))
				.then(Commands.literal("release").executes(ctx -> run(ctx.getSource(), p -> avatars.control(p, false))))
				.then(Commands.literal("calibrate").executes(ctx -> run(ctx.getSource(), p -> avatars.recalibrate(p))))
				.then(Commands.literal("debug")
						.then(Commands.literal("on").executes(ctx -> debug(ctx.getSource(), true)))
						.then(Commands.literal("off").executes(ctx -> debug(ctx.getSource(), false)))));
	}

	private static int run(CommandSourceStack source, java.util.function.Consumer<ServerPlayer> action) {
		try {
			action.accept(source.getPlayerOrException());
			return 1;
		} catch (Exception e) {
			source.sendFailure(Component.literal("HumanCraft: " + e.getMessage()));
			return 0;
		}
	}

	private static int debug(CommandSourceStack source, boolean enabled) {
		try {
			ServerPlayer player = source.getPlayerOrException();
			if (net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking.canSend(player, dev.humancraft.network.HumanCraftPayloads.DebugState.TYPE)) {
				net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking.send(player, new dev.humancraft.network.HumanCraftPayloads.DebugState(enabled));
			}
			return 1;
		} catch (Exception e) {
			source.sendFailure(Component.literal("HumanCraft: " + e.getMessage()));
			return 0;
		}
	}
}
