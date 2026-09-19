package dev.humancraft.server;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.fixture.SyntheticHuman;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.StageToWorld;
import org.junit.jupiter.api.Test;

import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ContactServiceTest {
	static final UUID OWNER = SnapshotStoreTest.OWNER;

	/** Fixture standing with the stage floor exactly on top of the block layer y=63 (anchor y = 64). */
	static ServerSnapshot standing(SyntheticHuman.Pose pose) {
		CharacterFrame frame = SyntheticHuman.frame(pose, 1, Mode.SNAPSHOT, 64);
		InstallRequest r = new InstallRequest(1, SyntheticHuman.SESSION_ID, SyntheticHuman.CALIBRATION_ID, Mode.SNAPSHOT,
				new StageToWorld(new Vector3(0.5, 64.0, 0.5), 1.0), frame.header().colliders(), frame.header().landmarks().size(), frame.cloud().count());
		SnapshotStore store = new SnapshotStore(500);
		assertTrue(store.install(OWNER, "minecraft:overworld", r, 0).accepted());
		return store.active(OWNER, 0).orElseThrow();
	}

	static final ContactService.BlockSolidity FLOOR_AT_63 = (x, y, z) -> y <= 63;

	@Test
	void bothFeetTouchTheFloorAndHandsDoNot() {
		ContactService.ContactState state = ContactService.compute(standing(SyntheticHuman.Pose.NEUTRAL), FLOOR_AT_63);
		Set<BodyPart> parts = state.contacts().stream().map(ContactService.Contact::bodyPart).collect(Collectors.toSet());
		assertEquals(Set.of(BodyPart.LEFT_FOOT, BodyPart.RIGHT_FOOT), parts);
		assertTrue(state.contacts().stream().allMatch(c -> c.y() == 63), "only the top floor layer is touched");
		assertTrue(state.cellsTested() > 0);
	}

	@Test
	void liftedFootLosesContactWhilePlantedFootKeepsIt() {
		ContactService.ContactState state = ContactService.compute(standing(SyntheticHuman.Pose.LIFTED_RIGHT_FOOT), FLOOR_AT_63);
		Set<BodyPart> parts = state.contacts().stream().map(ContactService.Contact::bodyPart).collect(Collectors.toSet());
		assertEquals(Set.of(BodyPart.LEFT_FOOT), parts);
	}

	@Test
	void onlyContactPartsAreQueried() {
		ServerSnapshot snap = standing(SyntheticHuman.Pose.NEUTRAL);
		// Everything solid: every hand/foot collider touches something, but torso/limbs are never reported.
		ContactService.ContactState state = ContactService.compute(snap, (x, y, z) -> true);
		Set<String> ids = state.contacts().stream().map(ContactService.Contact::colliderId).collect(Collectors.toSet());
		assertEquals(Set.of("hand.left", "hand.right", "foot.left", "foot.right"), ids);
		for (ColliderDto c : snap.worldColliders()) {
			assertEquals(c.bodyPart().isContactPart(), ids.contains(c.id()), c.id());
		}
	}

	@Test
	void rotatedFootOnlyTouchesCubesItsOrientedBoxReaches() {
		ServerSnapshot snap = standing(SyntheticHuman.Pose.LIFTED_RIGHT_FOOT);
		ColliderDto foot = snap.worldColliders().stream().filter(c -> c.id().equals("foot.right")).findFirst().orElseThrow();
		var bounds = foot.geometry().orElseThrow().bounds();
		// Solid everywhere: the AABB of the turned foot spans more cubes than the OBB actually overlaps.
		ContactService.ContactState state = ContactService.compute(snap, (x, y, z) -> true);
		long footContacts = state.contacts().stream().filter(c -> c.colliderId().equals("foot.right")).count();
		long aabbCells = cells(bounds.min().x(), bounds.max().x()) * cells(bounds.min().y(), bounds.max().y()) * cells(bounds.min().z(), bounds.max().z());
		assertTrue(footContacts > 0);
		assertTrue(footContacts <= aabbCells);
	}

	@Test
	void emitterSendsOnChangeAndHeartbeat() {
		ContactService.Emitter emitter = new ContactService.Emitter(1000);
		ContactService.ContactState a = new ContactService.ContactState(1, java.util.List.of(new ContactService.Contact("foot.left", BodyPart.LEFT_FOOT, 0, 63, 0)), 5);
		ContactService.ContactState aAgain = new ContactService.ContactState(1, a.contacts(), 99);
		ContactService.ContactState b = ContactService.ContactState.empty(1);

		assertTrue(emitter.shouldEmit(a, 0), "first state always emits");
		assertFalse(emitter.shouldEmit(aAgain, 100), "identical contacts (ignoring instrumentation) are suppressed");
		assertTrue(emitter.shouldEmit(b, 200), "change emits");
		assertFalse(emitter.shouldEmit(b, 900));
		assertTrue(emitter.shouldEmit(b, 1200), "heartbeat after 1 s");
		assertFalse(emitter.shouldEmit(b, 1300));
		emitter.reset();
		assertTrue(emitter.shouldEmit(b, 1301), "reset forces a fresh emission");
	}

	private static long cells(double min, double max) {
		return (long) Math.floor(max) - (long) Math.floor(min) + 1;
	}
}
