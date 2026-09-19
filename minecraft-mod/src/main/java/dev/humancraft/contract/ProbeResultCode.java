package dev.humancraft.contract;

public enum ProbeResultCode implements WireEnum {
	HIT,
	MISS,
	BLOCK_OCCLUDED,
	NO_ACTIVE_SNAPSHOT,
	FRAME_MISMATCH,
	OUT_OF_REACH;

	@Override
	public String wireName() {
		return name();
	}
}
