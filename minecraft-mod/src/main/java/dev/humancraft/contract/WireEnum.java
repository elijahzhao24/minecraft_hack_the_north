package dev.humancraft.contract;

/** Closed protocol v1 enum with a stable snake_case wire spelling. Unknown values are rejected. */
public interface WireEnum {
	String wireName();

	static <E extends Enum<E> & WireEnum> E fromWire(Class<E> type, String value) {
		for (E constant : type.getEnumConstants()) {
			if (constant.wireName().equals(value)) {
				return constant;
			}
		}
		throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
				"unknown " + type.getSimpleName() + " value '" + value + "'");
	}
}
