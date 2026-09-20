package dev.humancraft.client.controller;

import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.StrictJson;

/** Strict protocol-v1 state received from the FastAPI controller socket. */
public record ControllerState(
		boolean connected,
		int sequence,
		long badgeUptimeMs,
		int buttons,
		int accelX,
		int accelY,
		int accelZ,
		int punchCounter) {
	public static final int A = 1 << 0;
	public static final int B = 1 << 1;
	public static final int HOME = 1 << 2;
	public static final int DOWN = 1 << 3;
	public static final int LEFT = 1 << 4;
	public static final int RIGHT = 1 << 5;
	public static final int UP = 1 << 6;
	public static final int AUX1 = 1 << 7;
	public static final int START = 1 << 8;

	public static ControllerState disconnected() {
		return new ControllerState(false, 0, 0, 0, 0, 0, 0, 0);
	}

	public boolean down(int button) {
		return (buttons & button) != 0;
	}

	public static ControllerState parse(String json) {
		StrictJson.Obj o = new StrictJson.Obj(StrictJson.parseObject(json), "controller");
		if (!"controller_state".equals(o.string("type"))) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "expected controller_state");
		}
		if (o.counter("protocol_version") != 1) {
			throw new ProtocolException(ProtocolException.UNSUPPORTED_VERSION, "unsupported controller protocol");
		}
		boolean connected = o.bool("connected");
		int sequence = bounded(o.counter("sequence"), 0xFFFF, "sequence");
		long uptime = o.counter("badge_uptime_ms");
		int buttons = bounded(o.counter("buttons"), 0x1FF, "buttons");
		double[] accel = o.finiteArray("accel_mg", 3);
		int ax = signed16(accel[0], "accel_mg[0]");
		int ay = signed16(accel[1], "accel_mg[1]");
		int az = signed16(accel[2], "accel_mg[2]");
		int punches = bounded(o.counter("punch_counter"), 0xFFFF, "punch_counter");
		o.finish();
		return new ControllerState(connected, sequence, uptime, buttons, ax, ay, az, punches);
	}

	private static int bounded(long value, int max, String field) {
		if (value > max) throw new ProtocolException(ProtocolException.INVALID_MESSAGE, field + " out of range");
		return (int) value;
	}

	private static int signed16(double value, String field) {
		if (value != Math.rint(value) || value < Short.MIN_VALUE || value > Short.MAX_VALUE) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, field + " must be int16");
		}
		return (int) value;
	}
}
