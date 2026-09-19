package dev.humancraft.contract;

import com.google.gson.JsonObject;

import java.util.UUID;

/** Identity of one phone frame that contributed to a character frame. */
public record SourceFrameRef(String deviceId, UUID sessionId, UUID captureId, long sequence) {
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
		o.finish();
		return new SourceFrameRef(deviceId, sessionId, captureId, sequence);
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
		return o;
	}
}
