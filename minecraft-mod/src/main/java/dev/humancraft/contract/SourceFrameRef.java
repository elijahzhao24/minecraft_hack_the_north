package dev.humancraft.contract;

import com.google.gson.JsonObject;

import java.util.UUID;

/** Identity of one phone frame that contributed to a character frame. */
public record SourceFrameRef(String deviceId, UUID sessionId, UUID captureId, long sequence, UUID sourceFrameId) {
	public SourceFrameRef(String deviceId, UUID sessionId, UUID captureId, long sequence) {
		this(deviceId, sessionId, captureId, sequence, null);
	}
	public SourceFrameRef {
		if (deviceId == null || deviceId.isEmpty()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "source_frames.device_id must be non-empty");
		}
	}

	public static SourceFrameRef parse(StrictJson.Obj o) {
		String deviceId = o.string("device_id");
		UUID sessionId = o.uuid("session_id");
		UUID captureId = o.has("capture_id") ? o.uuid("capture_id") : null;
		if (captureId == null) {
			o.optionalString("capture_id");
		}
		long sequence = o.counter("sequence");
		UUID sourceFrameId = o.optionalString("source_frame_id").map(SourceFrameRef::uuid).orElse(null);
		o.finish();
		return new SourceFrameRef(deviceId, sessionId, captureId, sequence, sourceFrameId);
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("device_id", deviceId);
		o.addProperty("session_id", sessionId.toString());
		if (captureId == null) {
			o.add("capture_id", com.google.gson.JsonNull.INSTANCE);
		} else {
			o.addProperty("capture_id", captureId.toString());
		}
		o.addProperty("sequence", sequence);
		if (sourceFrameId == null) o.add("source_frame_id", com.google.gson.JsonNull.INSTANCE);
		else o.addProperty("source_frame_id", sourceFrameId.toString());
		return o;
	}

	private static UUID uuid(String value) {
		try {
			UUID parsed = UUID.fromString(value);
			if (!parsed.toString().equals(value)) throw new IllegalArgumentException();
			return parsed;
		} catch (IllegalArgumentException e) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "invalid source_frame_id");
		}
	}
}
