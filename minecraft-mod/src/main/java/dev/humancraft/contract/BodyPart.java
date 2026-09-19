package dev.humancraft.contract;

/** Stable body part enum used in results and UI. Hands and feet are distinct from forearms and shins. */
public enum BodyPart implements WireEnum {
	HEAD("head"),
	TORSO("torso"),
	PELVIS("pelvis"),
	LEFT_UPPER_ARM("left_upper_arm"),
	RIGHT_UPPER_ARM("right_upper_arm"),
	LEFT_FOREARM("left_forearm"),
	RIGHT_FOREARM("right_forearm"),
	LEFT_HAND("left_hand"),
	RIGHT_HAND("right_hand"),
	LEFT_THIGH("left_thigh"),
	RIGHT_THIGH("right_thigh"),
	LEFT_SHIN("left_shin"),
	RIGHT_SHIN("right_shin"),
	LEFT_FOOT("left_foot"),
	RIGHT_FOOT("right_foot");

	private final String wire;

	BodyPart(String wire) {
		this.wire = wire;
	}

	@Override
	public String wireName() {
		return wire;
	}

	public boolean isHand() {
		return this == LEFT_HAND || this == RIGHT_HAND;
	}

	public boolean isFoot() {
		return this == LEFT_FOOT || this == RIGHT_FOOT;
	}

	/** Parts whose contact with blocks is reported. */
	public boolean isContactPart() {
		return isHand() || isFoot();
	}
}
