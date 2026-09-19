package dev.humancraft.contract;

public enum ColliderType implements WireEnum {
	SPHERE("sphere"),
	CAPSULE("capsule"),
	OBB("obb");

	private final String wire;

	ColliderType(String wire) {
		this.wire = wire;
	}

	@Override
	public String wireName() {
		return wire;
	}
}
