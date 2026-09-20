package dev.humancraft.client.controller;

import dev.humancraft.contract.ProtocolException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ControllerStateTest {
	private static final String VALID = """
			{"type":"controller_state","protocol_version":1,"connected":true,"sequence":9,
			 "badge_uptime_ms":1234,"buttons":65,"accel_mg":[12,-20,1001],"punch_counter":2}
			""";

	@Test
	void parsesStrictControllerState() {
		ControllerState state = ControllerState.parse(VALID);
		assertTrue(state.connected());
		assertEquals(9, state.sequence());
		assertEquals(65, state.buttons());
		assertEquals(-20, state.accelY());
		assertEquals(2, state.punchCounter());
		assertTrue(state.down(ControllerState.A));
		assertTrue(state.down(ControllerState.UP));
	}

	@Test
	void rejectsUnknownFieldsAndOutOfRangeButtons() {
		assertThrows(ProtocolException.class, () -> ControllerState.parse(VALID.replace("}", ",\"extra\":1}")));
		assertThrows(ProtocolException.class, () -> ControllerState.parse(VALID.replace("\"buttons\":65", "\"buttons\":999")));
	}
}
