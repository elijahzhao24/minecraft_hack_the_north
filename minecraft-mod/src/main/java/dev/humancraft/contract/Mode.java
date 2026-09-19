package dev.humancraft.contract;

public enum Mode implements WireEnum {
	SNAPSHOT("snapshot"),
	LIVE("live");

	private final String wire;

	Mode(String wire) {
		this.wire = wire;
	}

	@Override
	public String wireName() {
		return wire;
	}
}
