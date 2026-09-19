package dev.humancraft.server;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.ColliderType;
import dev.humancraft.contract.FitSource;
import dev.humancraft.contract.Mode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.StageToWorld;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SnapshotStoreTest {
	static final UUID OWNER = UUID.fromString("00000000-0000-0000-0000-000000000001");
	static final UUID OTHER_SESSION = UUID.fromString("00000000-0000-0000-0000-0000000000aa");
	static final String DIM = "minecraft:overworld";
	static final StageToWorld T = new StageToWorld(new Vector3(10, 64, -5), 1.0);

	static InstallRequest request(long frameId, Mode mode) {
		return request(frameId, mode, SyntheticHuman.SESSION_ID, T);
	}

	static InstallRequest request(long frameId, Mode mode, UUID session, StageToWorld transform) {
		CharacterFrame frame = SyntheticHuman.frame(SyntheticHuman.Pose.NEUTRAL, frameId, mode, 64);
		return new InstallRequest(frameId, session, SyntheticHuman.CALIBRATION_ID, mode, transform,
				frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count());
	}

	@Test
	void firstValidInstallBecomesActiveInWorldSpace() {
		SnapshotStore store = new SnapshotStore(500);
		InstallOutcome out = store.install(OWNER, DIM, request(1, Mode.SNAPSHOT), 1000);
		assertTrue(out.accepted());
		assertEquals(1, out.activeFrameId().orElseThrow());
		ServerSnapshot snap = store.active(OWNER, 1000).orElseThrow();
		assertEquals(DIM, snap.dimension());
		ColliderDto head = snap.worldColliders().stream().filter(c -> c.id().equals("head")).findFirst().orElseThrow();
		Sphere s = (Sphere) head.geometry().orElseThrow();
		assertEquals(10.0, s.center().x(), 1e-9);
		assertEquals(64 + 1.62, s.center().y(), 1e-9);
		assertEquals(-5 + 0.02, s.center().z(), 1e-9);
		assertEquals(15, snap.validColliderCount());
	}

	@Test
	void invalidInstallPreservesPreviousSnapshot() {
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, DIM, request(1, Mode.SNAPSHOT), 1000);
		InstallRequest bad = request(2, Mode.SNAPSHOT, SyntheticHuman.SESSION_ID, new StageToWorld(new Vector3(0, 64, 0), 50.0));
		InstallOutcome out = store.install(OWNER, DIM, bad, 1001);
		assertFalse(out.accepted());
		assertEquals(InstallOutcome.REJECTED_INVALID, out.code());
		assertEquals(1, out.activeFrameId().orElseThrow(), "previous frame stays active");
		assertEquals(1, store.active(OWNER, 1002).orElseThrow().frameId());
	}

	@Test
	void limitViolationsAreReportedAsLimitExceeded() {
		SnapshotStore store = new SnapshotStore(500);
		InstallRequest r = request(1, Mode.SNAPSHOT);
		InstallRequest tooMany = new InstallRequest(1, r.sessionId(), r.calibrationId(), r.mode(), r.transform(), r.stageColliders(),
				r.landmarkCount(), 100_001);
		InstallOutcome out = store.install(OWNER, DIM, tooMany, 0);
		assertEquals(InstallOutcome.REJECTED_LIMIT, out.code());
		assertTrue(store.active(OWNER, 0).isEmpty());
	}

	@Test
	void activeFrameCanBeReinstalledForAtomicPlacementChangesButOlderFramesAreRejected() {
		SnapshotStore store = new SnapshotStore(500);
		assertTrue(store.install(OWNER, DIM, request(5, Mode.SNAPSHOT), 0).accepted());
		StageToWorld moved = new StageToWorld(new Vector3(12, 65, -4), 1.5);
		InstallOutcome same = store.install(OWNER, DIM, request(5, Mode.SNAPSHOT, SyntheticHuman.SESSION_ID, moved), 1);
		InstallOutcome older = store.install(OWNER, DIM, request(4, Mode.SNAPSHOT), 2);
		assertTrue(same.accepted());
		assertEquals(moved, store.active(OWNER, 2).orElseThrow().transform());
		assertEquals(InstallOutcome.REJECTED_STALE, older.code());
		assertEquals(5, store.active(OWNER, 3).orElseThrow().frameId());
		assertTrue(store.install(OWNER, DIM, request(6, Mode.SNAPSHOT), 4).accepted());
	}

	@Test
	void newSessionResetsMonotonicity() {
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, DIM, request(100, Mode.SNAPSHOT), 0);
		InstallOutcome out = store.install(OWNER, DIM, request(1, Mode.SNAPSHOT, OTHER_SESSION, T), 1);
		assertTrue(out.accepted());
		assertEquals(OTHER_SESSION, store.active(OWNER, 2).orElseThrow().sessionId());
	}

	@Test
	void clearKeepsCursorButForgetDropsIt() {
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, DIM, request(7, Mode.SNAPSHOT), 0);
		assertTrue(store.clear(OWNER));
		assertTrue(store.active(OWNER, 1).isEmpty());
		// Once cleared or expired, replaying the same frame cannot resurrect stale interaction.
		assertEquals(InstallOutcome.REJECTED_STALE, store.install(OWNER, DIM, request(7, Mode.SNAPSHOT), 2).code());
		store.forget(OWNER);
		assertTrue(store.install(OWNER, DIM, request(7, Mode.SNAPSHOT), 3).accepted());
	}

	@Test
	void liveFramesExpireButSnapshotsPersist() {
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, DIM, request(1, Mode.LIVE), 1000);
		assertTrue(store.active(OWNER, 1400).isPresent());
		assertTrue(store.active(OWNER, 1501).isEmpty(), "live frame past TTL is not interactive");
		assertTrue(store.active(OWNER, 1400).isEmpty(), "expired frame is dropped, not resurrected");

		store.install(OWNER, DIM, request(2, Mode.SNAPSHOT), 2000);
		assertTrue(store.active(OWNER, 2000 + 60L * 60 * 1000).isPresent());
	}

	@Test
	void perPlayerIsolation() {
		UUID other = UUID.fromString("00000000-0000-0000-0000-000000000002");
		SnapshotStore store = new SnapshotStore(500);
		store.install(OWNER, DIM, request(1, Mode.SNAPSHOT), 0);
		assertTrue(store.active(other, 0).isEmpty());
		assertTrue(store.install(other, DIM, request(1, Mode.SNAPSHOT), 0).accepted());
		store.clear(other);
		assertTrue(store.active(OWNER, 0).isPresent());
	}

	@Test
	void geometryAndTransformValidation() {
		InstallRequest r = request(1, Mode.SNAPSHOT);
		List<ColliderDto> dup = new ArrayList<>(r.stageColliders());
		dup.add(dup.get(0));
		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(with(r, dup)));

		List<ColliderDto> huge = List.of(new ColliderDto("big", BodyPart.HEAD, ColliderType.SPHERE, true, FitSource.OBSERVED, Optional.empty(),
				Optional.of(new Sphere(Vector3.ZERO, 1.5))));
		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(with(r, huge)));

		List<ColliderDto> far = List.of(new ColliderDto("far", BodyPart.HEAD, ColliderType.SPHERE, true, FitSource.OBSERVED, Optional.empty(),
				Optional.of(new Sphere(new Vector3(500, 0, 0), 0.1))));
		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(with(r, far)));

		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(new InstallRequest(-1, r.sessionId(), r.calibrationId(),
				r.mode(), r.transform(), r.stageColliders(), r.landmarkCount(), r.pointCount())));
		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(new InstallRequest(1, r.sessionId(), r.calibrationId(),
				r.mode(), new StageToWorld(new Vector3(0, 10_000, 0), 1), r.stageColliders(), r.landmarkCount(), r.pointCount())));
		assertThrows(RuntimeException.class, () -> SnapshotStore.validate(new InstallRequest(1, r.sessionId(), r.calibrationId(),
				r.mode(), new StageToWorld(Vector3.ZERO, 0.01), r.stageColliders(), r.landmarkCount(), r.pointCount())));
	}

	private static InstallRequest with(InstallRequest r, List<ColliderDto> colliders) {
		return new InstallRequest(r.frameId(), r.sessionId(), r.calibrationId(), r.mode(), r.transform(), colliders, r.landmarkCount(), r.pointCount());
	}
}
