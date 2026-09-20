package dev.humancraft.contract;

import dev.humancraft.fixture.MalformedFixtures;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Obb;
import dev.humancraft.model.CharacterFrame;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CharacterFrameDecoderTest {
	static final Path FIXTURES = Path.of(System.getProperty("humancraft.fixtures", "../contracts/fixtures"));

	@Test
	void goldenFixtureRoundTripsThroughEncoderAndDecoder() {
		CharacterFrame original = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 42, Mode.SNAPSHOT);
		byte[] bytes = CharacterFrameEncoder.encode(original);
		CharacterFrame decoded = CharacterFrameDecoder.decode(ByteBuffer.wrap(bytes));

		assertEquals(original.header(), decoded.header());
		assertEquals(original.cloud().count(), decoded.cloud().count());
		for (int i : new int[] {0, 1, 17, 2999, 5999}) {
			assertEquals(original.cloud().x(i), decoded.cloud().x(i));
			assertEquals(original.cloud().y(i), decoded.cloud().y(i));
			assertEquals(original.cloud().z(i), decoded.cloud().z(i));
			assertEquals(original.cloud().r(i), decoded.cloud().r(i));
			assertEquals(original.cloud().a(i), decoded.cloud().a(i));
		}
	}

	@Test
	void goldenFixtureOnDiskMatchesGenerator() throws IOException {
		Path golden = FIXTURES.resolve("character_frame_neutral.hmc");
		assertTrue(Files.exists(golden), "run ./gradlew writeFixtures; missing " + golden.toAbsolutePath());
		byte[] disk = Files.readAllBytes(golden);
		byte[] generated = CharacterFrameEncoder.encode(SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 42, Mode.SNAPSHOT));
		assertArrayEquals(generated, disk, "contracts/fixtures is stale relative to SyntheticHuman");
		CharacterFrame decoded = CharacterFrameDecoder.decode(ByteBuffer.wrap(disk));
		assertEquals(42, decoded.frameId());
		assertEquals(6000, decoded.cloud().count());
		assertEquals(77, decoded.header().landmarks().size());
		assertEquals(15, decoded.header().colliders().size());
	}

	@Test
	void fixtureContainsAllThreeColliderTypesAndSeparateHandsAndFeet() {
		CharacterFrame frame = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 1, Mode.SNAPSHOT);
		Set<ColliderType> types = frame.header().colliders().stream().map(ColliderDto::type).collect(Collectors.toSet());
		assertEquals(Set.of(ColliderType.SPHERE, ColliderType.CAPSULE, ColliderType.OBB), types);
		Set<BodyPart> parts = frame.header().colliders().stream().map(ColliderDto::bodyPart).collect(Collectors.toSet());
		assertEquals(Set.of(BodyPart.values()), parts, "every body part has exactly one collider");
		for (ColliderDto c : frame.header().colliders()) {
			if (c.bodyPart().isHand() || c.bodyPart().isFoot()) {
				assertTrue(c.geometry().orElseThrow() instanceof Obb, c.id() + " must be an oriented box");
			}
		}
		Set<String> names = frame.header().landmarks().stream().map(LandmarkDto::name).collect(Collectors.toSet());
		assertTrue(names.contains("hand.left.index_tip"));
		assertTrue(names.contains("hand.right.thumb_tip"));
		assertTrue(names.contains("body.left_heel"));
		assertTrue(names.contains("body.right_foot_index"));
		assertEquals(77, names.size());
	}

	@Test
	void everyMalformedFixtureIsRejectedWithItsStableCode() {
		Map<String, MalformedFixtures.Case> cases = MalformedFixtures.all();
		assertTrue(cases.size() >= 15);
		for (MalformedFixtures.Case c : cases.values()) {
			ProtocolException e = assertThrows(ProtocolException.class,
					() -> CharacterFrameDecoder.decode(ByteBuffer.wrap(c.bytes())), c.name() + " must be rejected");
			assertEquals(c.expectedCode(), e.code(), c.name() + ": " + e.getMessage());
		}
	}

	@Test
	void malformedFixturesOnDiskMatchGenerator() throws IOException {
		for (MalformedFixtures.Case c : MalformedFixtures.all().values()) {
			Path path = FIXTURES.resolve("malformed").resolve(c.name() + ".hmc");
			assertTrue(Files.exists(path), "missing " + path);
			assertArrayEquals(c.bytes(), Files.readAllBytes(path), c.name() + " on disk is stale");
		}
	}

	@Test
	void decoderNeverAllocatesBeforeValidatingLengths() {
		// Header claims a 4 GiB payload; must fail on the limit check, not attempt allocation.
		byte[] bytes = new byte[16];
		System.arraycopy(ProtocolLimits.MAGIC, 0, bytes, 0, 4);
		bytes[4] = 1;
		bytes[6] = 2;
		bytes[12] = (byte) 0xFF;
		bytes[13] = (byte) 0xFF;
		bytes[14] = (byte) 0xFF;
		bytes[15] = (byte) 0xFF;
		ProtocolException e = assertThrows(ProtocolException.class, () -> Hmc1Envelope.decode(ByteBuffer.wrap(bytes)));
		assertEquals(ProtocolException.FRAME_TOO_LARGE, e.code());
	}

	@Test
	void controlMessagesRoundTripStrictly() {
		String hello = "{\"type\":\"character_server_hello\",\"protocol_version\":1,"
				+ "\"server_session_id\":\"18434a87-0ea1-4088-a120-b44823d1c8a8\",\"max_binary_bytes\":8388608,\"latest_frame_id\":42}";
		ControlMessage m = ControlMessage.parse(hello);
		assertTrue(m instanceof ControlMessage.CharacterServerHello);
		assertEquals(42L, ((ControlMessage.CharacterServerHello) m).latestFrameId().orElseThrow());

		String ack = "{\"type\":\"ack\",\"protocol_version\":1,\"request_id\":null,\"accepted\":true,\"code\":\"capture_queued\",\"detail\":null}";
		assertTrue(ControlMessage.parse(ack) instanceof ControlMessage.Ack);

		String unknownField = "{\"type\":\"ack\",\"protocol_version\":1,\"request_id\":null,\"accepted\":true,\"code\":\"x\",\"detail\":null,\"bogus\":1}";
		assertEquals(ProtocolException.INVALID_MESSAGE, assertThrows(ProtocolException.class, () -> ControlMessage.parse(unknownField)).code());

		String badVersion = "{\"type\":\"ack\",\"protocol_version\":2,\"request_id\":null,\"accepted\":true,\"code\":\"x\",\"detail\":null}";
		assertEquals(ProtocolException.UNSUPPORTED_VERSION, assertThrows(ProtocolException.class, () -> ControlMessage.parse(badVersion)).code());

		ControlMessage.CharacterHello out = new ControlMessage.CharacterHello("demo-laptop", java.util.Optional.of(41L));
		assertEquals(out, ControlMessage.parse(ControlMessage.serialize(out)));
	}

	@Test
	void sourceMasksDecodeAndKeepTheirMeaningAfterScaling() {
		CharacterFrame original = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, 42, Mode.SNAPSHOT, 3);
		var header = original.header().toJson();
		header.getAsJsonArray("colliders").asList().clear();
		header.getAsJsonObject("quality").addProperty("valid_collider_count", 0);
		var descriptor = new com.google.gson.JsonObject();
		descriptor.addProperty("name", "point_sources");
		descriptor.addProperty("encoding", "uint8");
		descriptor.addProperty("offset", 48);
		descriptor.addProperty("length", 3);
		var shape = new com.google.gson.JsonArray();
		shape.add(3);
		descriptor.add("shape", shape);
		header.getAsJsonArray("buffers").add(descriptor);
		byte[] payload = new byte[51];
		original.cloud().data().get(payload, 0, 48);
		payload[48] = 1; payload[49] = 2; payload[50] = 3;
		byte[] bytes = Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, header.toString(), payload);
		var decoded = CharacterFrameDecoder.decode(ByteBuffer.wrap(bytes));
		assertArrayEquals(bytes, CharacterFrameEncoder.encode(decoded));
		assertTrue(decoded.hasRenderableCloud(), "missing anatomy must not hide usable camera points");
		var cloud = decoded.cloud();
		assertEquals(0x28D2F0, cloud.sourceColor(0));
		assertEquals(0xE632D2, cloud.sourceColor(1));
		assertEquals(0xFFE050, cloud.sourceColor(2));
		assertEquals(3, cloud.transform(dev.humancraft.geometry.Vector3.ZERO, 8).sourceMask(2));
		assertEquals(0x888888, original.cloud().sourceColor(0));
		payload[50] = 4;
		byte[] bad = Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, header.toString(), payload);
		assertThrows(ProtocolException.class, () -> CharacterFrameDecoder.decode(ByteBuffer.wrap(bad)));
		descriptor.addProperty("length", 2);
		byte[] shortBuffer = Hmc1Envelope.encode(Hmc1Envelope.MESSAGE_TYPE_CHARACTER_FRAME, header.toString(), payload);
		assertThrows(ProtocolException.class, () -> CharacterFrameDecoder.decode(ByteBuffer.wrap(shortBuffer)));
	}

	@Test
	void strictJsonRejectsDuplicateKeysAndTrailingContent() {
		assertThrows(ProtocolException.class, () -> StrictJson.parseObject("{\"a\":1,\"a\":2}"));
		assertThrows(ProtocolException.class, () -> StrictJson.parseObject("{\"a\":1} x"));
		assertThrows(ProtocolException.class, () -> StrictJson.parseObject("{a:1}"));
		assertThrows(ProtocolException.class, () -> StrictJson.parseObject("{\"a\":NaN}"));
		assertThrows(ProtocolException.class, () -> StrictJson.parseObject("[1]"));
	}
}
