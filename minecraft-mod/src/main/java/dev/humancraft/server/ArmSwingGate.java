package dev.humancraft.server;

import dev.humancraft.contract.Mode;
import dev.humancraft.network.HumanCraftPayloads;
import java.util.UUID;

/** Per-observer authoritative freshness, duplicate and cooldown gate. */
public final class ArmSwingGate {
	private UUID session;
	private long lastFrame = -1, lastSwing = -1;
	public boolean accept(HumanCraftPayloads.ArmSwing event, ServerSnapshot active, long now, long ttl, long cooldown) {
		if (active == null || active.mode() != Mode.LIVE || active.isExpired(now, ttl)
				|| event.frameId() != active.frameId() || !event.sessionId().equals(active.sessionId())
				|| !event.calibrationId().equals(active.calibrationId())
				|| !event.targetPlayerId().equals(active.targetPlayerId())
				|| event.bindingGeneration() != active.bindingGeneration()
				|| event.normalizationRevision() != active.normalizationRevision()
				|| (event.sessionId().equals(session) && event.frameId() <= lastFrame)
				|| (lastSwing >= 0 && now - lastSwing < Math.max(500, cooldown))) return false;
		session = event.sessionId(); lastFrame = event.frameId(); lastSwing = now;
		return true;
	}
}
