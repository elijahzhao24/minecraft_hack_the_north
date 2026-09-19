package dev.humancraft.client.render;

import dev.humancraft.client.state.ClientSnapshotCoordinator;
import dev.humancraft.config.HumanCraftConfig;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;

import java.util.List;

/** Compact runtime evidence: connection, snapshot identity, geometry counts, age, contacts and probe result. */
public final class HumanCraftHud {
	private final HumanCraftConfig config;
	private final ClientSnapshotCoordinator coordinator;

	public HumanCraftHud(HumanCraftConfig config, ClientSnapshotCoordinator coordinator) {
		this.config = config;
		this.coordinator = coordinator;
	}

	public void render(GuiGraphics graphics) {
		Minecraft client = Minecraft.getInstance();
		if (!config.showHud || client.options.hideGui || client.level == null) {
			return;
		}
		List<String> lines = coordinator.hudLines();
		int width = 0;
		for (String line : lines) {
			width = Math.max(width, client.font.width(line));
		}
		int x = 6;
		int y = 6;
		graphics.fill(x - 3, y - 3, x + width + 4, y + lines.size() * 10 + 2, 0x99000000);
		for (String line : lines) {
			graphics.drawString(client.font, line, x, y, 0xFFFFFFFF, true);
			y += 10;
		}
	}
}
