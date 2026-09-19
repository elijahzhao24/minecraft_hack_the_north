package dev.humancraft.contract;

import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.util.Optional;
import java.util.UUID;

/** Text control messages exchanged over the backend WebSocket (docs/contracts.md §3). */
public sealed interface ControlMessage {
	String type();

	JsonObject toJson();

	/** Minecraft → backend, sent first after connecting. */
	record CharacterHello(String clientId, Optional<Long> lastFrameId) implements ControlMessage {
		@Override
		public String type() {
			return "character_hello";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.addProperty("client_id", clientId);
			o.add("last_frame_id", lastFrameId.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
			return o;
		}
	}

	/** Backend → Minecraft. */
	record CharacterServerHello(UUID serverSessionId, long maxBinaryBytes, Optional<Long> latestFrameId) implements ControlMessage {
		@Override
		public String type() {
			return "character_server_hello";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.addProperty("server_session_id", serverSessionId.toString());
			o.addProperty("max_binary_bytes", maxBinaryBytes);
			o.add("latest_frame_id", latestFrameId.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
			return o;
		}
	}

	/** Minecraft → backend: decode result for one binary frame (not the logical-server ack). */
	record CharacterAck(long frameId, boolean accepted, String code, Optional<String> detail) implements ControlMessage {
		@Override
		public String type() {
			return "character_ack";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.addProperty("frame_id", frameId);
			o.addProperty("accepted", accepted);
			o.addProperty("code", code);
			o.add("detail", detail.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
			return o;
		}
	}

	/** Minecraft → backend: ask both phones to capture. */
	record RequestCapture(UUID requestId, UUID captureId, Mode mode) implements ControlMessage {
		@Override
		public String type() {
			return "request_capture";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.addProperty("request_id", requestId.toString());
			o.addProperty("capture_id", captureId.toString());
			o.addProperty("mode", mode.wireName());
			return o;
		}
	}

	/** Minecraft → backend: start or stop the backend-driven live capture loop. */
	record LiveControl(UUID requestId, boolean enabled, double rateHz) implements ControlMessage {
		@Override
		public String type() {
			return enabled ? "live_start" : "live_stop";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.addProperty("request_id", requestId.toString());
			if (enabled) {
				o.addProperty("rate_hz", rateHz);
			}
			return o;
		}
	}

	/** Backend → Minecraft: generic acknowledgement of a request. */
	record Ack(Optional<UUID> requestId, boolean accepted, String code, Optional<String> detail) implements ControlMessage {
		@Override
		public String type() {
			return "ack";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.add("request_id", requestId.<JsonElement>map(id -> new JsonPrimitive(id.toString())).orElse(JsonNull.INSTANCE));
			o.addProperty("accepted", accepted);
			o.addProperty("code", code);
			o.add("detail", detail.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
			return o;
		}
	}

	/** Backend → Minecraft: error report. */
	record Error(Optional<UUID> requestId, String code, String message, boolean retryable) implements ControlMessage {
		@Override
		public String type() {
			return "error";
		}

		@Override
		public JsonObject toJson() {
			JsonObject o = base(this);
			o.add("request_id", requestId.<JsonElement>map(id -> new JsonPrimitive(id.toString())).orElse(JsonNull.INSTANCE));
			o.addProperty("code", code);
			o.addProperty("message", message);
			o.addProperty("retryable", retryable);
			return o;
		}
	}

	private static JsonObject base(ControlMessage m) {
		JsonObject o = new JsonObject();
		o.addProperty("type", m.type());
		o.addProperty("protocol_version", ProtocolLimits.PROTOCOL_VERSION);
		return o;
	}

	/** Strictly parses any control message the backend may send to Minecraft (or that Minecraft sends). */
	public static ControlMessage parse(String text) {
		StrictJson.Obj o = new StrictJson.Obj(StrictJson.parseObject(text), "control");
		String type = o.string("type");
		long version = o.counter("protocol_version");
		if (version != ProtocolLimits.PROTOCOL_VERSION) {
			throw new ProtocolException(ProtocolException.UNSUPPORTED_VERSION, "unsupported protocol_version " + version);
		}
		ControlMessage message = switch (type) {
			case "character_server_hello" -> new CharacterServerHello(
					o.uuid("server_session_id"), o.counter("max_binary_bytes"), o.optionalCounter("latest_frame_id"));
			case "ack" -> new Ack(optionalUuid(o, "request_id"), o.bool("accepted"), o.string("code"), o.optionalString("detail"));
			case "error" -> new Error(optionalUuid(o, "request_id"), o.string("code"), o.string("message"), o.bool("retryable"));
			case "character_hello" -> new CharacterHello(o.string("client_id"), o.optionalCounter("last_frame_id"));
			case "character_ack" -> new CharacterAck(o.counter("frame_id"), o.bool("accepted"), o.string("code"), o.optionalString("detail"));
			case "request_capture" -> new RequestCapture(o.uuid("request_id"), o.uuid("capture_id"), o.enumValue("mode", Mode.class));
			default -> throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unknown control message type '" + type + "'");
		};
		o.finish();
		return message;
	}

	private static Optional<UUID> optionalUuid(StrictJson.Obj o, String key) {
		return o.has(key) ? Optional.of(o.uuid(key)) : consumeNull(o, key);
	}

	private static Optional<UUID> consumeNull(StrictJson.Obj o, String key) {
		o.optionalString(key);
		return Optional.empty();
	}

	public static String serialize(ControlMessage message) {
		return message.toJson().toString();
	}
}
