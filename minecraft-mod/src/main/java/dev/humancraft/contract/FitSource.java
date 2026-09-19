package dev.humancraft.contract;

public enum FitSource implements WireEnum {
	OBSERVED("observed"),
	SUBJECT_DEFAULT("subject_default"),
	GLOBAL_DEFAULT("global_default"),
	DISABLED("disabled");

	private final String wire;

	FitSource(String wire) {
		this.wire = wire;
	}

	@Override
	public String wireName() {
		return wire;
	}
}
