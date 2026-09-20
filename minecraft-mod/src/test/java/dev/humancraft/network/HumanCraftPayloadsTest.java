package dev.humancraft.network;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProbeResultCode;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.StageToWorld;
import dev.humancraft.server.ContactService;
import dev.humancraft.server.InstallOutcome;
import dev.humancraft.server.InstallRequest;
import dev.humancraft.server.ProbeService;
import io.netty.buffer.Unpooled;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HumanCraftPayloadsTest {
	@Test void armSwingRoundTripPreservesFrameBindingAndHand() {
		var swing = new HumanCraftPayloads.ArmSwing(45, UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), 3, 7, true);
		assertEquals(swing, roundTrip(HumanCraftPayloads.ArmSwing.CODEC, swing));
	}

	static <T> T roundTrip(StreamCodec<FriendlyByteBuf, T> codec, T value) {
		FriendlyByteBuf buf = new FriendlyByteBuf(Unpooled.buffer());
		codec.encode(buf, value);
		T decoded = codec.decode(buf);
		assertEquals(0, buf.readableBytes(), "decoder consumed the whole payload");
		return decoded;
	}

	static InstallRequest request(SyntheticHuman.Pose pose) {
		CharacterFrame frame = SyntheticHuman.frame(pose, 3, Mode.LIVE, 32);
		return new InstallRequest(3, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID, Mode.LIVE,
				new StageToWorld(new Vector3(1.5, 70, -2.25), 1.25), frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count(),
				UUID.fromString("00000000-0000-0000-0000-000000000123"), 7, 4);
	}

	@Test
	void installSnapshotRoundTripsEveryColliderType() {
		InstallRequest original = request(SyntheticHuman.Pose.LIFTED_RIGHT_FOOT);
		HumanCraftPayloads.InstallSnapshot payload = roundTrip(HumanCraftPayloads.InstallSnapshot.CODEC, HumanCraftPayloads.InstallSnapshot.of(original));
		InstallRequest decoded = payload.toRequest();
		assertEquals(original.frameId(), decoded.frameId());
		assertEquals(original.sessionId(), decoded.sessionId());
		assertEquals(original.calibrationId(), decoded.calibrationId());
		assertEquals(original.mode(), decoded.mode());
		assertEquals(original.transform(), decoded.transform());
		assertEquals(original.landmarkCount(), decoded.landmarkCount());
		assertEquals(original.pointCount(), decoded.pointCount());
		assertEquals(original.targetPlayerId(), decoded.targetPlayerId());
		assertEquals(7, decoded.bindingGeneration());
		assertEquals(4, decoded.normalizationRevision());
		assertEquals(original.stageColliders().size(), decoded.stageColliders().size());
		for (int i = 0; i < original.stageColliders().size(); i++) {
			ColliderDto a = original.stageColliders().get(i);
			ColliderDto b = decoded.stageColliders().get(i);
			assertEquals(a.id(), b.id());
			assertEquals(a.bodyPart(), b.bodyPart());
			assertEquals(a.type(), b.type());
			assertEquals(a.valid(), b.valid());
			assertEquals(a.fitSource(), b.fitSource());
			assertEquals(a.quality().isPresent(), b.quality().isPresent());
			a.quality().ifPresent(q -> assertEquals(q, b.quality().orElseThrow(), 1e-6));
			assertEquals(a.geometry().orElseThrow().bounds().min().x(), b.geometry().orElseThrow().bounds().min().x(), 1e-9);
			assertEquals(a.geometry().orElseThrow().bounds().max().z(), b.geometry().orElseThrow().bounds().max().z(), 1e-9);
		}
	}

	@Test
	void invalidColliderIsRejectedAtConversionNotDecoding() {
		WireCollider bogus = new WireCollider("x", "head", "sphere", true, "observed", Float.NaN, new double[] {0, 0, 0, -1});
		HumanCraftPayloads.InstallSnapshot payload = new HumanCraftPayloads.InstallSnapshot(1, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID,
				"snapshot", 0, 64, 0, 1.0, List.of(bogus), 0, 0);
		HumanCraftPayloads.InstallSnapshot decoded = roundTrip(HumanCraftPayloads.InstallSnapshot.CODEC, payload);
		assertThrows(ProtocolException.class, decoded::toRequest);

		WireCollider unknownPart = new WireCollider("y", "tail", "sphere", true, "observed", 0.5f, new double[] {0, 0, 0, 0.1});
		HumanCraftPayloads.InstallSnapshot bad = new HumanCraftPayloads.InstallSnapshot(1, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID,
				"snapshot", 0, 64, 0, 1.0, List.of(unknownPart), 0, 0);
		assertThrows(ProtocolException.class, () -> roundTrip(HumanCraftPayloads.InstallSnapshot.CODEC, bad).toRequest());

		HumanCraftPayloads.InstallSnapshot badMode = new HumanCraftPayloads.InstallSnapshot(1, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID,
				"replay", 0, 64, 0, 1.0, List.of(), 0, 0);
		assertThrows(ProtocolException.class, () -> roundTrip(HumanCraftPayloads.InstallSnapshot.CODEC, badMode).toRequest());
	}

	@Test
	void ackProbeAndContactRoundTrip() {
		HumanCraftPayloads.SnapshotAck ack = roundTrip(HumanCraftPayloads.SnapshotAck.CODEC,
				HumanCraftPayloads.SnapshotAck.of(9, InstallOutcome.rejected(InstallOutcome.REJECTED_STALE, "frame 9 is not newer than 12", OptionalLong.of(12)), 15, 7, 4));
		assertFalse(ack.accepted());
		assertEquals("stale_frame", ack.code());
		assertEquals(12, ack.activeFrameId());
		assertEquals(9, ack.frameId());
		assertEquals(7, ack.bindingGeneration());
		assertEquals(4, ack.normalizationRevision());

		ProbeService.ProbeOutcome outcome = new ProbeService.ProbeOutcome(4, ProbeResultCode.HIT, 3, Optional.of("hand.left"),
				Optional.of(BodyPart.LEFT_HAND), 2.5, Optional.of(new Vector3(1, 2, 3)), 6, 2);
		HumanCraftPayloads.ProbeResult probe = roundTrip(HumanCraftPayloads.ProbeResult.CODEC, HumanCraftPayloads.ProbeResult.of(outcome));
		assertEquals(ProbeResultCode.HIT, probe.resultCode().orElseThrow());
		assertEquals(BodyPart.LEFT_HAND, probe.part().orElseThrow());
		assertEquals(new Vector3(1, 2, 3), probe.point().orElseThrow());
		assertEquals(2.5, probe.distance());

		HumanCraftPayloads.ProbeResult miss = roundTrip(HumanCraftPayloads.ProbeResult.CODEC,
				HumanCraftPayloads.ProbeResult.of(new ProbeService.ProbeOutcome(5, ProbeResultCode.MISS, 3, Optional.empty(), Optional.empty(), -1, Optional.empty(), 0, 0)));
		assertTrue(miss.part().isEmpty());
		assertTrue(miss.point().isEmpty());

		ContactService.ContactState state = new ContactService.ContactState(3,
				List.of(new ContactService.Contact("foot.left", BodyPart.LEFT_FOOT, -1, 63, 7)), 12);
		HumanCraftPayloads.ContactState contacts = roundTrip(HumanCraftPayloads.ContactState.CODEC, HumanCraftPayloads.ContactState.of(state));
		assertEquals(3, contacts.frameId());
		assertEquals(1, contacts.contacts().size());
		assertEquals(BodyPart.LEFT_FOOT, contacts.contacts().get(0).part().orElseThrow());
		assertEquals(-1, contacts.contacts().get(0).x());

		HumanCraftPayloads.ProbeRequest req = roundTrip(HumanCraftPayloads.ProbeRequest.CODEC, new HumanCraftPayloads.ProbeRequest(77, 3));
		assertEquals(77, req.requestId());
		assertEquals(3, req.toQuery().expectedFrameId());
		roundTrip(HumanCraftPayloads.ClearSnapshot.CODEC, new HumanCraftPayloads.ClearSnapshot());
	}

	@Test
	void payloadIdsUseTheHmcNamespace() {
		assertEquals("hmc:install_snapshot", HumanCraftPayloads.InstallSnapshot.TYPE.id().toString());
		assertEquals("hmc:snapshot_ack", HumanCraftPayloads.SnapshotAck.TYPE.id().toString());
		assertEquals("hmc:probe_request", HumanCraftPayloads.ProbeRequest.TYPE.id().toString());
		assertEquals("hmc:probe_result", HumanCraftPayloads.ProbeResult.TYPE.id().toString());
		assertEquals("hmc:contact_state", HumanCraftPayloads.ContactState.TYPE.id().toString());
	}
}
