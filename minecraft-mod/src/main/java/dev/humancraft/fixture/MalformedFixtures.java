package dev.humancraft.fixture;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dev.humancraft.contract.CharacterFrameEncoder;
import dev.humancraft.contract.Hmc1Envelope;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.model.CharacterFrame;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Deterministic malformed messages, each paired with the stable rejection code the decoder must produce.
 * Every entry derives from the golden neutral frame so the only difference is the injected fault.
 */
public final class MalformedFixtures {
	public record Case(String name, byte[] bytes, String expectedCode) {}

	private MalformedFixtures() {}

	public static Map<String, Case> all() {
		CharacterFrame golden = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 42, Mode.SNAPSHOT, 64);
		byte[] good = CharacterFrameEncoder.encode(golden);
		String headerJson = golden.header().toJson().toString();
		byte[] payload = payloadOf(good);

		Map<String, Case> cases = new LinkedHashMap<>();

		byte[] wrongMagic = good.clone();
		wrongMagic[0] = 'X';
		cases.put("wrong_magic", new Case("wrong_magic", wrongMagic, ProtocolException.INVALID_MESSAGE));

		byte[] badVersion = good.clone();
		badVersion[4] = 2;
		cases.put("unsupported_envelope_version", new Case("unsupported_envelope_version", badVersion, ProtocolException.UNSUPPORTED_VERSION));

		byte[] wrongType = good.clone();
		wrongType[6] = 1;
		cases.put("rgbd_message_type", new Case("rgbd_message_type", wrongType, ProtocolException.INVALID_MESSAGE));

		cases.put("truncated_payload", new Case("truncated_payload", Arrays.copyOf(good, good.length - 7), ProtocolException.INVALID_MESSAGE));
		cases.put("short_header", new Case("short_header", Arrays.copyOf(good, 12), ProtocolException.INVALID_MESSAGE));

		byte[] oversized = good.clone();
		ByteBuffer.wrap(oversized).order(ByteOrder.LITTLE_ENDIAN).putInt(12, ProtocolLimits.MAX_CHARACTER_PAYLOAD_BYTES + 1);
		cases.put("payload_over_limit", new Case("payload_over_limit", oversized, ProtocolException.FRAME_TOO_LARGE));

		// Header claims two buffers that overlap (second starts inside the first).
		JsonObject overlapping = golden.header().toJson();
		JsonArray buffers = new JsonArray();
		JsonObject first = new JsonObject();
		first.addProperty("name", "points");
		first.addProperty("encoding", "xyzrgba16_le");
		first.addProperty("offset", 0);
		first.addProperty("length", payload.length);
		JsonArray shape = new JsonArray();
		shape.add(payload.length / 16);
		first.add("shape", shape);
		JsonObject second = new JsonObject();
		second.addProperty("name", "extra");
		second.addProperty("encoding", "uint8");
		second.addProperty("offset", 16);
		second.addProperty("length", 16);
		JsonArray shape2 = new JsonArray();
		shape2.add(16);
		second.add("shape", shape2);
		buffers.add(first);
		buffers.add(second);
		overlapping.add("buffers", buffers);
		cases.put("overlapping_buffers", new Case("overlapping_buffers", envelope(overlapping.toString(), payload), ProtocolException.INVALID_BUFFER_RANGE));

		// Points descriptor ends after the payload.
		JsonObject pastEnd = golden.header().toJson();
		pastEnd.getAsJsonArray("buffers").get(0).getAsJsonObject().addProperty("length", payload.length + 16);
		pastEnd.getAsJsonArray("buffers").get(0).getAsJsonObject().getAsJsonArray("shape").set(0, new com.google.gson.JsonPrimitive(payload.length / 16 + 1));
		pastEnd.getAsJsonObject("quality").addProperty("point_count", payload.length / 16 + 1);
		cases.put("buffer_past_payload", new Case("buffer_past_payload", envelope(pastEnd.toString(), payload), ProtocolException.INVALID_BUFFER_RANGE));

		// Invalid UTF-8 inside the JSON header (lone continuation byte).
		byte[] headerBytes = headerJson.getBytes(java.nio.charset.StandardCharsets.UTF_8);
		byte[] badUtf8 = headerBytes.clone();
		badUtf8[2] = (byte) 0xFF;
		cases.put("invalid_utf8_header", new Case("invalid_utf8_header", envelopeRaw(badUtf8, payload), ProtocolException.INVALID_MESSAGE));

		// JSON header with NaN token (lenient parsers accept it; we must not).
		String nanJson = headerJson.replaceFirst("\"pair_skew_ms\":[-0-9.eE]+", "\"pair_skew_ms\":NaN");
		cases.put("nan_in_header", new Case("nan_in_header", envelope(nanJson, payload), ProtocolException.INVALID_MESSAGE));

		// Non-finite point coordinate inside the binary buffer.
		byte[] nanPoint = payload.clone();
		ByteBuffer.wrap(nanPoint).order(ByteOrder.LITTLE_ENDIAN).putFloat(16 * 3 + 4, Float.NaN);
		cases.put("non_finite_point", new Case("non_finite_point", envelope(headerJson, nanPoint), ProtocolException.NON_FINITE_GEOMETRY));

		// Collider with a zero radius.
		JsonObject zeroRadius = golden.header().toJson();
		zeroRadius.getAsJsonArray("colliders").get(0).getAsJsonObject().addProperty("radius_m", 0.0);
		cases.put("zero_radius_collider", new Case("zero_radius_collider", envelope(zeroRadius.toString(), payload), ProtocolException.INVALID_COLLIDER_GEOMETRY));

		// Collider radius above the 1 m cap.
		JsonObject hugeRadius = golden.header().toJson();
		hugeRadius.getAsJsonArray("colliders").get(0).getAsJsonObject().addProperty("radius_m", 1.5);
		cases.put("oversized_collider", new Case("oversized_collider", envelope(hugeRadius.toString(), payload), ProtocolException.INVALID_COLLIDER_GEOMETRY));

		// OBB whose axes are not orthonormal.
		JsonObject skewedObb = golden.header().toJson();
		for (var el : skewedObb.getAsJsonArray("colliders")) {
			JsonObject c = el.getAsJsonObject();
			if (c.get("type").getAsString().equals("obb")) {
				JsonArray axes = new JsonArray();
				for (double v : new double[] {1, 0, 0, 0.5, 1, 0, 0, 0, 1}) {
					axes.add(v);
				}
				c.add("axes_row_major", axes);
				break;
			}
		}
		cases.put("non_orthonormal_obb", new Case("non_orthonormal_obb", envelope(skewedObb.toString(), payload), ProtocolException.INVALID_COLLIDER_GEOMETRY));

		// Unknown field must be rejected in v1.
		JsonObject unknownField = golden.header().toJson();
		unknownField.addProperty("extra_field", true);
		cases.put("unknown_header_field", new Case("unknown_header_field", envelope(unknownField.toString(), payload), ProtocolException.INVALID_MESSAGE));

		// Unknown enum value.
		JsonObject unknownEnum = golden.header().toJson();
		unknownEnum.addProperty("mode", "replay");
		cases.put("unknown_mode_enum", new Case("unknown_mode_enum", envelope(unknownEnum.toString(), payload), ProtocolException.INVALID_MESSAGE));

		// Schema version bump.
		JsonObject futureSchema = golden.header().toJson();
		futureSchema.addProperty("schema_version", 3);
		cases.put("future_schema_version", new Case("future_schema_version", envelope(futureSchema.toString(), payload), ProtocolException.UNSUPPORTED_VERSION));

		// Too many colliders (129 copies with distinct IDs).
		JsonObject tooMany = golden.header().toJson();
		JsonArray colliders = new JsonArray();
		JsonObject template = tooMany.getAsJsonArray("colliders").get(0).getAsJsonObject();
		for (int i = 0; i <= ProtocolLimits.MAX_COLLIDERS; i++) {
			JsonObject copy = template.deepCopy();
			copy.addProperty("id", "dup." + i);
			colliders.add(copy);
		}
		tooMany.add("colliders", colliders);
		cases.put("too_many_colliders", new Case("too_many_colliders", envelope(tooMany.toString(), payload), ProtocolException.LIMIT_EXCEEDED));

		// Point count above the limit (descriptor only; payload length check happens after the limit check).
		JsonObject tooManyPoints = golden.header().toJson();
		JsonObject pts = tooManyPoints.getAsJsonArray("buffers").get(0).getAsJsonObject();
		long n = ProtocolLimits.MAX_POINTS + 1L;
		pts.addProperty("length", n * 16);
		pts.getAsJsonArray("shape").set(0, new com.google.gson.JsonPrimitive(n));
		tooManyPoints.getAsJsonObject("quality").addProperty("point_count", n);
		cases.put("too_many_points", new Case("too_many_points", envelope(tooManyPoints.toString(), payload), ProtocolException.INVALID_BUFFER_RANGE));

		return cases;
	}

	private static byte[] payloadOf(byte[] message) {
		ByteBuffer buf = ByteBuffer.wrap(message).order(ByteOrder.LITTLE_ENDIAN);
		int headerLength = buf.getInt(8);
		return Arrays.copyOfRange(message, 16 + headerLength, message.length);
	}

	private static byte[] envelope(String header, byte[] payload) {
		return Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, header, payload);
	}

	private static byte[] envelopeRaw(byte[] header, byte[] payload) {
		ByteBuffer out = ByteBuffer.allocate(16 + header.length + payload.length).order(ByteOrder.LITTLE_ENDIAN);
		out.put(ProtocolLimits.MAGIC).putShort((short) 1).putShort((short) 2).putInt(header.length).putInt(payload.length);
		out.put(header).put(payload);
		return out.array();
	}
}
