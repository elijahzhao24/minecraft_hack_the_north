package dev.humancraft.contract;

import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.util.Optional;

/** Sentry distributed tracing headers propagated from the backend. Both may be absent. */
public record TraceContext(Optional<String> sentryTrace, Optional<String> baggage) {
	public static final TraceContext EMPTY = new TraceContext(Optional.empty(), Optional.empty());

	public static TraceContext parse(StrictJson.Obj o) {
		TraceContext t = new TraceContext(o.optionalString("sentry_trace"), o.optionalString("baggage"));
		o.finish();
		return t;
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.add("sentry_trace", sentryTrace.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
		o.add("baggage", baggage.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
		return o;
	}
}
