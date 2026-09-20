package dev.humancraft.server;

import dev.humancraft.contract.Mode;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.StageToWorld;
import dev.humancraft.network.HumanCraftPayloads;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class ArmSwingGateTest {
	private final UUID owner = UUID.randomUUID(), session = UUID.randomUUID(), calibration = UUID.randomUUID();
	private ServerSnapshot snapshot(long frame, long now, Mode mode) {
		return new ServerSnapshot(owner, "minecraft:overworld", frame, session, calibration, UUID.randomUUID(),
				mode, new StageToWorld(Vector3.ZERO, 1), List.of(), now, owner, 3, 4);
	}
	private HumanCraftPayloads.ArmSwing event(long frame) {
		return new HumanCraftPayloads.ArmSwing(frame, session, calibration, owner, 3, 4, true);
	}
	@Test void exactLiveBindingIsRequired() {
		var gate = new ArmSwingGate();
		assertFalse(gate.accept(event(2), null, 1000, 500, 500));
		assertFalse(gate.accept(event(2), snapshot(1, 1000, Mode.LIVE), 1000, 500, 500));
		assertFalse(gate.accept(event(2), snapshot(2, 1000, Mode.SNAPSHOT), 1000, 500, 500));
		assertFalse(gate.accept(event(2), snapshot(2, 0, Mode.LIVE), 1000, 500, 500));
		var wrong = List.of(
				new HumanCraftPayloads.ArmSwing(2, UUID.randomUUID(), calibration, owner, 3, 4, true),
				new HumanCraftPayloads.ArmSwing(2, session, UUID.randomUUID(), owner, 3, 4, true),
				new HumanCraftPayloads.ArmSwing(2, session, calibration, UUID.randomUUID(), 3, 4, true),
				new HumanCraftPayloads.ArmSwing(2, session, calibration, owner, 2, 4, true),
				new HumanCraftPayloads.ArmSwing(2, session, calibration, owner, 3, 3, true));
		for (var event : wrong) assertFalse(gate.accept(event, snapshot(2, 1000, Mode.LIVE), 1000, 500, 500));
		assertTrue(gate.accept(event(2), snapshot(2, 1000, Mode.LIVE), 1000, 500, 500));
	}
	@Test void duplicateFramesAndCooldownCannotBeBypassedByReinstall() {
		var gate = new ArmSwingGate();
		assertTrue(gate.accept(event(1), snapshot(1, 1000, Mode.LIVE), 1000, 500, 500));
		assertFalse(gate.accept(event(1), snapshot(1, 1600, Mode.LIVE), 1600, 500, 500));
		assertFalse(gate.accept(event(2), snapshot(2, 1499, Mode.LIVE), 1499, 500, 500));
		assertTrue(gate.accept(event(3), snapshot(3, 1500, Mode.LIVE), 1500, 500, 500));
	}
}
