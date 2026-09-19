package dev.humancraft.server;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProbeResultCode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Ray;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.StageToWorld;
import org.junit.jupiter.api.Test;

import java.util.Optional;
import java.util.OptionalDouble;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ProbeServiceTest {
	static final UUID OWNER = SnapshotStoreTest.OWNER;
	static final StageToWorld T = new StageToWorld(new Vector3(10, 64, -5), 1.0);
	static final ProbeService.ProbeQuery Q = new ProbeService.ProbeQuery(7, 1);

	static ServerSnapshot neutral() {
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, "minecraft:overworld", SnapshotStoreTest.request(1, Mode.SNAPSHOT, SyntheticHuman.SESSION_ID, T), 0);
		return store.active(OWNER, 0).orElseThrow();
	}

	static Vector3 world(double x, double y, double z) {
		return T.point(new Vector3(x, y, z));
	}

	@Test
	void hitsHeadFromTheFrontAndReportsClosestCollider() {
		ServerSnapshot snap = neutral();
		Vector3 headCenter = world(0, 1.62, 0.02);
		Vector3 eye = headCenter.add(new Vector3(0, 0, 3));
		Ray ray = new Ray(eye, headCenter.sub(eye));
		ProbeService.ProbeOutcome out = ProbeService.probe(Q, Optional.of(snap), ray, 4.5, ProbeService.BlockOcclusion.NONE);
		assertEquals(ProbeResultCode.HIT, out.code());
		assertEquals(BodyPart.HEAD, out.bodyPart().orElseThrow());
		assertEquals("head", out.colliderId().orElseThrow());
		assertEquals(3 - 0.11, out.distance(), 1e-6);
		assertEquals(1, out.frameId());
		assertEquals(7, out.requestId());
		assertTrue(out.narrowPhaseTests() <= out.broadPhaseCandidates());
	}

	@Test
	void handAndFootAreReportedSeparatelyFromLimbs() {
		ServerSnapshot snap = neutral();
		ColliderDto leftHand = snap.worldColliders().stream().filter(c -> c.id().equals("hand.left")).findFirst().orElseThrow();
		Vector3 handCenter = center(leftHand);
		// Approach the hand from image-right (+X) so the forearm/torso are not in front of it.
		Vector3 eye = handCenter.add(new Vector3(2, 0, 0));
		ProbeService.ProbeOutcome hand = ProbeService.probe(Q, Optional.of(snap), new Ray(eye, handCenter.sub(eye)), 4.5, ProbeService.BlockOcclusion.NONE);
		assertEquals(ProbeResultCode.HIT, hand.code());
		assertEquals(BodyPart.LEFT_HAND, hand.bodyPart().orElseThrow());

		ColliderDto rightFoot = snap.worldColliders().stream().filter(c -> c.id().equals("foot.right")).findFirst().orElseThrow();
		Vector3 footCenter = center(rightFoot);
		Vector3 footEye = footCenter.add(new Vector3(0, 0.6, 1.5));
		ProbeService.ProbeOutcome foot = ProbeService.probe(Q, Optional.of(snap), new Ray(footEye, footCenter.sub(footEye)), 4.5, ProbeService.BlockOcclusion.NONE);
		assertEquals(ProbeResultCode.HIT, foot.code());
		assertEquals(BodyPart.RIGHT_FOOT, foot.bodyPart().orElseThrow());
	}

	@Test
	void missOutOfReachAndOcclusion() {
		ServerSnapshot snap = neutral();
		Vector3 headCenter = world(0, 1.62, 0.02);

		Ray away = new Ray(headCenter.add(new Vector3(0, 0, 3)), Vector3.UNIT_Z);
		assertEquals(ProbeResultCode.MISS, ProbeService.probe(Q, Optional.of(snap), away, 4.5, ProbeService.BlockOcclusion.NONE).code());

		Vector3 farEye = headCenter.add(new Vector3(0, 0, 10));
		Ray far = new Ray(farEye, headCenter.sub(farEye));
		ProbeService.ProbeOutcome out = ProbeService.probe(Q, Optional.of(snap), far, 4.5, ProbeService.BlockOcclusion.NONE);
		assertEquals(ProbeResultCode.OUT_OF_REACH, out.code());
		assertEquals("head", out.colliderId().orElseThrow());

		Vector3 eye = headCenter.add(new Vector3(0, 0, 3));
		Ray ray = new Ray(eye, headCenter.sub(eye));
		ProbeService.BlockOcclusion wall = (r, max) -> OptionalDouble.of(1.0);
		ProbeService.ProbeOutcome occluded = ProbeService.probe(Q, Optional.of(snap), ray, 4.5, wall);
		assertEquals(ProbeResultCode.BLOCK_OCCLUDED, occluded.code());
		assertEquals(1.0, occluded.distance(), 1e-9);
		assertEquals("head", occluded.colliderId().orElseThrow(), "what would have been hit is still reported");

		ProbeService.BlockOcclusion behind = (r, max) -> OptionalDouble.of(max + 0.5);
		assertEquals(ProbeResultCode.HIT, ProbeService.probe(Q, Optional.of(snap), ray, 4.5, behind).code());
	}

	@Test
	void snapshotAndFrameGuards() {
		ServerSnapshot snap = neutral();
		Ray ray = new Ray(world(0, 1.62, 3), new Vector3(0, 0, -1));
		assertEquals(ProbeResultCode.NO_ACTIVE_SNAPSHOT, ProbeService.probe(Q, Optional.empty(), ray, 4.5, ProbeService.BlockOcclusion.NONE).code());
		ProbeService.ProbeOutcome mismatch = ProbeService.probe(new ProbeService.ProbeQuery(1, 99), Optional.of(snap), ray, 4.5, ProbeService.BlockOcclusion.NONE);
		assertEquals(ProbeResultCode.FRAME_MISMATCH, mismatch.code());
		assertEquals(1, mismatch.frameId(), "tells the client which frame is actually active");
	}

	@Test
	void raycasterPicksNearestAmongOverlappingCandidates() {
		ServerSnapshot snap = neutral();
		// Looking down the torso axis from above the head hits the head first, never the torso.
		Vector3 eye = world(0, 3.0, 0.02);
		Optional<BodyRaycaster.BodyHit> hit = BodyRaycaster.closestHit(snap.worldColliders(), new Ray(eye, new Vector3(0, -1, 0)), 64);
		assertEquals("head", hit.orElseThrow().collider().id());
		assertEquals(3.0 - 1.62 - 0.11, hit.get().distance(), 1e-6);
	}

	private static Vector3 center(ColliderDto c) {
		var b = c.geometry().orElseThrow().bounds();
		return b.min().lerp(b.max(), 0.5);
	}
}
