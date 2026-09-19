package dev.humancraft.contract;

public enum LandmarkSource implements WireEnum {
	DEPTH_NEIGHBORHOOD("depth_neighborhood"),
	TRIANGULATED("triangulated"),
	REGISTERED_MODEL_PRIOR("registered_model_prior"),
	DERIVED("derived"),
	UNAVAILABLE("unavailable");

	private final String wire;

	LandmarkSource(String wire) {
		this.wire = wire;
	}

	@Override
	public String wireName() {
		return wire;
	}
}
