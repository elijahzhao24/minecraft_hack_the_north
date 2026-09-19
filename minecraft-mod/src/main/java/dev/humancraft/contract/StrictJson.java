package dev.humancraft.contract;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import com.google.gson.stream.MalformedJsonException;

import java.io.IOException;
import java.io.StringReader;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Strict JSON parsing for protocol v1: strict UTF-8 (no BOM), no lenient tokens, no NaN/Infinity, no
 * duplicate keys, no trailing content, and unknown-key rejection through {@link Obj}.
 */
public final class StrictJson {
	private static final int MAX_DEPTH = 32;

	private StrictJson() {}

	public static String decodeUtf8(ByteBuffer bytes) {
		if (bytes.remaining() >= 3) {
			int p = bytes.position();
			if ((bytes.get(p) & 0xFF) == 0xEF && (bytes.get(p + 1) & 0xFF) == 0xBB && (bytes.get(p + 2) & 0xFF) == 0xBF) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "JSON header must not start with a BOM");
			}
		}
		try {
			return StandardCharsets.UTF_8.newDecoder()
					.onMalformedInput(CodingErrorAction.REPORT)
					.onUnmappableCharacter(CodingErrorAction.REPORT)
					.decode(bytes.slice())
					.toString();
		} catch (CharacterCodingException e) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "JSON header is not valid UTF-8", e);
		}
	}

	public static JsonObject parseObject(String text) {
		JsonElement element = parse(text);
		if (!element.isJsonObject()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "expected a JSON object");
		}
		return element.getAsJsonObject();
	}

	public static JsonElement parse(String text) {
		try (JsonReader reader = new JsonReader(new StringReader(text))) {
			reader.setLenient(false);
			JsonElement element = readElement(reader, 0);
			if (reader.peek() != JsonToken.END_DOCUMENT) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "trailing content after JSON document");
			}
			return element;
		} catch (MalformedJsonException | IllegalStateException | NumberFormatException e) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "malformed JSON: " + e.getMessage(), e);
		} catch (IOException e) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unreadable JSON", e);
		}
	}

	private static JsonElement readElement(JsonReader reader, int depth) throws IOException {
		if (depth > MAX_DEPTH) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "JSON nesting deeper than " + MAX_DEPTH);
		}
		JsonToken token = reader.peek();
		switch (token) {
			case BEGIN_OBJECT -> {
				reader.beginObject();
				JsonObject object = new JsonObject();
				while (reader.hasNext()) {
					String name = reader.nextName();
					if (object.has(name)) {
						throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "duplicate JSON key '" + name + "'");
					}
					object.add(name, readElement(reader, depth + 1));
				}
				reader.endObject();
				return object;
			}
			case BEGIN_ARRAY -> {
				reader.beginArray();
				JsonArray array = new JsonArray();
				while (reader.hasNext()) {
					array.add(readElement(reader, depth + 1));
				}
				reader.endArray();
				return array;
			}
			case STRING -> {
				return new JsonPrimitive(reader.nextString());
			}
			case NUMBER -> {
				String raw = reader.nextString();
				return new JsonPrimitive(new BigDecimal(raw));
			}
			case BOOLEAN -> {
				return new JsonPrimitive(reader.nextBoolean());
			}
			case NULL -> {
				reader.nextNull();
				return JsonNull.INSTANCE;
			}
			default -> throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unexpected token " + token);
		}
	}

	/** Field accessor that records every key read so leftover (unknown) keys can be rejected. */
	public static final class Obj {
		private final JsonObject object;
		private final String path;
		private final Set<String> seen = new HashSet<>();

		public Obj(JsonObject object, String path) {
			this.object = object;
			this.path = path;
		}

		public static Obj of(JsonElement element, String path) {
			if (element == null || !element.isJsonObject()) {
				throw fail(path, "expected object");
			}
			return new Obj(element.getAsJsonObject(), path);
		}

		private JsonElement require(String key) {
			seen.add(key);
			if (!object.has(key)) {
				throw fail(path + "." + key, "missing required field");
			}
			return object.get(key);
		}

		private JsonElement nullable(String key) {
			seen.add(key);
			JsonElement e = object.get(key);
			return e == null || e.isJsonNull() ? null : e;
		}

		public boolean has(String key) {
			return object.has(key) && !object.get(key).isJsonNull();
		}

		public String string(String key) {
			return asString(require(key), path + "." + key);
		}

		public Optional<String> optionalString(String key) {
			JsonElement e = nullable(key);
			return e == null ? Optional.empty() : Optional.of(asString(e, path + "." + key));
		}

		public boolean bool(String key) {
			JsonElement e = require(key);
			if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) {
				throw fail(path + "." + key, "expected boolean");
			}
			return e.getAsBoolean();
		}

		public long counter(String key) {
			return asCounter(require(key), path + "." + key);
		}

		public Optional<Long> optionalCounter(String key) {
			JsonElement e = nullable(key);
			return e == null ? Optional.empty() : Optional.of(asCounter(e, path + "." + key));
		}

		public double finiteDouble(String key) {
			return asFinite(require(key), path + "." + key);
		}

		public Optional<Double> optionalFinite(String key) {
			JsonElement e = nullable(key);
			return e == null ? Optional.empty() : Optional.of(asFinite(e, path + "." + key));
		}

		public <E extends Enum<E> & WireEnum> E enumValue(String key, Class<E> type) {
			return WireEnum.fromWire(type, string(key));
		}

		public UUID uuid(String key) {
			String s = string(key);
			try {
				UUID id = UUID.fromString(s);
				if (!id.toString().equals(s)) {
					throw fail(path + "." + key, "UUID must be lowercase canonical");
				}
				return id;
			} catch (IllegalArgumentException e) {
				throw fail(path + "." + key, "invalid UUID '" + s + "'");
			}
		}

		public double[] finiteArray(String key, int expectedLength) {
			return asFiniteArray(require(key), path + "." + key, expectedLength);
		}

		public Optional<double[]> optionalFiniteArray(String key, int expectedLength) {
			JsonElement e = nullable(key);
			return e == null ? Optional.empty() : Optional.of(asFiniteArray(e, path + "." + key, expectedLength));
		}

		public List<String> stringList(String key) {
			JsonArray array = asArray(require(key), path + "." + key);
			List<String> out = new ArrayList<>(array.size());
			for (int i = 0; i < array.size(); i++) {
				out.add(asString(array.get(i), path + "." + key + "[" + i + "]"));
			}
			return List.copyOf(out);
		}

		public List<Long> counterList(String key) {
			JsonArray array = asArray(require(key), path + "." + key);
			List<Long> out = new ArrayList<>(array.size());
			for (int i = 0; i < array.size(); i++) {
				out.add(asCounter(array.get(i), path + "." + key + "[" + i + "]"));
			}
			return List.copyOf(out);
		}

		public List<Obj> objectList(String key, int maxSize, String limitCode) {
			JsonArray array = asArray(require(key), path + "." + key);
			if (array.size() > maxSize) {
				throw new ProtocolException(limitCode, path + "." + key + " has " + array.size() + " entries, limit " + maxSize);
			}
			List<Obj> out = new ArrayList<>(array.size());
			for (int i = 0; i < array.size(); i++) {
				out.add(Obj.of(array.get(i), path + "." + key + "[" + i + "]"));
			}
			return out;
		}

		public Obj object(String key) {
			return Obj.of(require(key), path + "." + key);
		}

		/** Rejects any key that was not read. Call after all fields have been consumed. */
		public void finish() {
			for (Map.Entry<String, JsonElement> entry : object.entrySet()) {
				if (!seen.contains(entry.getKey())) {
					throw fail(path + "." + entry.getKey(), "unknown field");
				}
			}
		}

		private static String asString(JsonElement e, String at) {
			if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) {
				throw fail(at, "expected string");
			}
			return e.getAsString();
		}

		private static JsonArray asArray(JsonElement e, String at) {
			if (!e.isJsonArray()) {
				throw fail(at, "expected array");
			}
			return e.getAsJsonArray();
		}

		private static long asCounter(JsonElement e, String at) {
			if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
				throw fail(at, "expected integer");
			}
			BigDecimal value = e.getAsBigDecimal();
			if (value.stripTrailingZeros().scale() > 0) {
				throw fail(at, "expected integer, got " + value);
			}
			if (value.signum() < 0 || value.compareTo(BigDecimal.valueOf(Long.MAX_VALUE)) > 0) {
				throw fail(at, "counter out of range: " + value);
			}
			return value.longValueExact();
		}

		private static double asFinite(JsonElement e, String at) {
			if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
				throw fail(at, "expected number");
			}
			double d = e.getAsDouble();
			if (!Double.isFinite(d)) {
				throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, at + ": non-finite number");
			}
			return d;
		}

		private static double[] asFiniteArray(JsonElement e, String at, int expectedLength) {
			JsonArray array = asArray(e, at);
			if (array.size() != expectedLength) {
				throw fail(at, "expected " + expectedLength + " numbers, got " + array.size());
			}
			double[] out = new double[expectedLength];
			for (int i = 0; i < expectedLength; i++) {
				out[i] = asFinite(array.get(i), at + "[" + i + "]");
			}
			return out;
		}

		private static ProtocolException fail(String at, String why) {
			return new ProtocolException(ProtocolException.INVALID_MESSAGE, at + ": " + why);
		}
	}
}
