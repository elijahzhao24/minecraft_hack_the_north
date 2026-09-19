package dev.humancraft.server;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.geometry.Aabb;
import dev.humancraft.geometry.Shape;

import java.util.ArrayList;
import java.util.List;

/**
 * Exact hand/foot-vs-block contact. Any block with a non-empty collision shape is treated as a full cube; the narrow phase is the shape's own
 * {@link Shape#overlaps(Aabb)} so a turned foot only touches the cubes its oriented box actually reaches.
 */
public final class ContactService {
	/** Hard cap on cubes examined per collider; at 8 blocks/m a 1 m collider spans at most 17^3 cells. */
	public static final int MAX_CELLS_PER_COLLIDER = 17 * 17 * 17;

	@FunctionalInterface
	public interface BlockSolidity {
		boolean isSolid(int x, int y, int z);
	}

	public record Contact(String colliderId, BodyPart bodyPart, int x, int y, int z) {}

	public record ContactState(long frameId, List<Contact> contacts, int cellsTested) {
		public ContactState {
			contacts = List.copyOf(contacts);
		}

		public static ContactState empty(long frameId) {
			return new ContactState(frameId, List.of(), 0);
		}

		/** Equality that ignores instrumentation so heartbeats can compare states. */
		public boolean sameContacts(ContactState other) {
			return other != null && frameId == other.frameId && contacts.equals(other.contacts);
		}
	}

	private ContactService() {}

	public static ContactState compute(ServerSnapshot snapshot, BlockSolidity solidity) {
		List<Contact> contacts = new ArrayList<>();
		int cells = 0;
		for (ColliderDto collider : snapshot.worldColliders()) {
			if (!collider.valid() || collider.geometry().isEmpty() || !collider.bodyPart().isContactPart()) {
				continue;
			}
			Shape shape = collider.geometry().get();
			Aabb bounds = shape.bounds();
			int minX = (int) Math.floor(bounds.min().x());
			int minY = (int) Math.floor(bounds.min().y());
			int minZ = (int) Math.floor(bounds.min().z());
			int maxX = (int) Math.floor(bounds.max().x());
			int maxY = (int) Math.floor(bounds.max().y());
			int maxZ = (int) Math.floor(bounds.max().z());
			long span = (long) (maxX - minX + 1) * (maxY - minY + 1) * (maxZ - minZ + 1);
			if (span > MAX_CELLS_PER_COLLIDER) {
				continue;
			}
			for (int x = minX; x <= maxX; x++) {
				for (int y = minY; y <= maxY; y++) {
					for (int z = minZ; z <= maxZ; z++) {
						cells++;
						if (!solidity.isSolid(x, y, z)) {
							continue;
						}
						if (shape.overlaps(Aabb.unitCube(x, y, z))) {
							contacts.add(new Contact(collider.id(), collider.bodyPart(), x, y, z));
						}
					}
				}
			}
		}
		return new ContactState(snapshot.frameId(), contacts, cells);
	}

	/** Decides when a state is worth sending: on change, or as a heartbeat every {@code heartbeatMillis}. */
	public static final class Emitter {
		private final long heartbeatMillis;
		private ContactState last;
		private long lastEmitMillis = Long.MIN_VALUE;

		public Emitter(long heartbeatMillis) {
			this.heartbeatMillis = heartbeatMillis;
		}

		public boolean shouldEmit(ContactState state, long nowMillis) {
			boolean changed = last == null || !state.sameContacts(last);
			boolean heartbeat = lastEmitMillis != Long.MIN_VALUE && nowMillis - lastEmitMillis >= heartbeatMillis;
			if (changed || heartbeat) {
				last = state;
				lastEmitMillis = nowMillis;
				return true;
			}
			return false;
		}

		public void reset() {
			last = null;
			lastEmitMillis = Long.MIN_VALUE;
		}
	}
}
