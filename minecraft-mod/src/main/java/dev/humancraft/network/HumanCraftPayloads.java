package dev.humancraft.network;

import dev.humancraft.HumanCraft;
import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProbeResultCode;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.contract.WireEnum;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.StageToWorld;
import dev.humancraft.server.ContactService;
import dev.humancraft.server.InstallOutcome;
import dev.humancraft.server.InstallRequest;
import dev.humancraft.server.ProbeService;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Fabric custom payloads between the client and the logical (integrated) server. All ids live under the
 * {@code hmc} namespace mandated by docs/contracts.md. Decoders are total: they never throw on well-formed
 * bytes with bad semantics; validation happens in the handlers.
 */
public final class HumanCraftPayloads {
	public static final String NAMESPACE = "hmc";
	private static final int MAX_STRING = 256;

	private HumanCraftPayloads() {}

	private static ResourceLocation id(String path) {
		return ResourceLocation.fromNamespaceAndPath(NAMESPACE, path);
	}

	// ---- C2S ---------------------------------------------------------------------------------

	/** {@code hmc:install_snapshot} — ask the server to make a frame's colliders interactive. */
	public record InstallSnapshot(
			long frameId,
			UUID sessionId,
			UUID calibrationId,
			String mode,
			double anchorX,
			double anchorY,
			double anchorZ,
			double blocksPerMeter,
			List<WireCollider> colliders,
			int landmarkCount,
			int pointCount) implements CustomPacketPayload {

		public static final Type<InstallSnapshot> TYPE = new Type<>(id("install_snapshot"));
		public static final StreamCodec<FriendlyByteBuf, InstallSnapshot> CODEC = StreamCodec.of(InstallSnapshot::write, InstallSnapshot::read);

		public static InstallSnapshot of(InstallRequest request) {
			List<WireCollider> wire = new ArrayList<>(request.stageColliders().size());
			for (ColliderDto c : request.stageColliders()) {
				wire.add(WireCollider.of(c));
			}
			Vector3 a = request.transform().anchor();
			return new InstallSnapshot(request.frameId(), request.sessionId(), request.calibrationId(), request.mode().wireName(),
					a.x(), a.y(), a.z(), request.transform().blocksPerMeter(), wire, request.landmarkCount(), request.pointCount());
		}

		/** Validates and converts; throws {@link dev.humancraft.contract.ProtocolException} or {@link IllegalArgumentException}. */
		public InstallRequest toRequest() {
			List<ColliderDto> dtos = new ArrayList<>(colliders.size());
			for (WireCollider c : colliders) {
				dtos.add(c.toDto());
			}
			return new InstallRequest(frameId, sessionId, calibrationId, WireEnum.fromWire(Mode.class, mode),
					new StageToWorld(new Vector3(anchorX, anchorY, anchorZ), blocksPerMeter), dtos, landmarkCount, pointCount);
		}

		private static void write(FriendlyByteBuf buf, InstallSnapshot p) {
			buf.writeLong(p.frameId);
			buf.writeUUID(p.sessionId);
			buf.writeUUID(p.calibrationId);
			buf.writeUtf(p.mode, 16);
			buf.writeDouble(p.anchorX);
			buf.writeDouble(p.anchorY);
			buf.writeDouble(p.anchorZ);
			buf.writeDouble(p.blocksPerMeter);
			buf.writeVarInt(p.colliders.size());
			for (WireCollider c : p.colliders) {
				WireCollider.write(buf, c);
			}
			buf.writeVarInt(p.landmarkCount);
			buf.writeVarInt(p.pointCount);
		}

		private static InstallSnapshot read(FriendlyByteBuf buf) {
			long frameId = buf.readLong();
			UUID session = buf.readUUID();
			UUID calibration = buf.readUUID();
			String mode = buf.readUtf(16);
			double ax = buf.readDouble();
			double ay = buf.readDouble();
			double az = buf.readDouble();
			double scale = buf.readDouble();
			int n = buf.readVarInt();
			if (n < 0 || n > ProtocolLimits.MAX_COLLIDERS) {
				throw new IllegalStateException("collider count " + n + " out of range");
			}
			List<WireCollider> colliders = new ArrayList<>(n);
			for (int i = 0; i < n; i++) {
				colliders.add(WireCollider.read(buf));
			}
			int landmarks = buf.readVarInt();
			int points = buf.readVarInt();
			return new InstallSnapshot(frameId, session, calibration, mode, ax, ay, az, scale, colliders, landmarks, points);
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	/** {@code hmc:clear_snapshot} — drop the caller's active snapshot. */
	public record ClearSnapshot() implements CustomPacketPayload {
		public static final Type<ClearSnapshot> TYPE = new Type<>(id("clear_snapshot"));
		public static final StreamCodec<FriendlyByteBuf, ClearSnapshot> CODEC = StreamCodec.of((buf, p) -> {}, buf -> new ClearSnapshot());

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	/** {@code hmc:probe_request} — intent only; the server derives eye, look and reach. */
	public record ProbeRequest(int requestId, long expectedFrameId) implements CustomPacketPayload {
		public static final Type<ProbeRequest> TYPE = new Type<>(id("probe_request"));
		public static final StreamCodec<FriendlyByteBuf, ProbeRequest> CODEC = StreamCodec.of(
				(buf, p) -> {
					buf.writeVarInt(p.requestId);
					buf.writeLong(p.expectedFrameId);
				},
				buf -> new ProbeRequest(buf.readVarInt(), buf.readLong()));

		public ProbeService.ProbeQuery toQuery() {
			return new ProbeService.ProbeQuery(requestId, expectedFrameId);
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	// ---- S2C ---------------------------------------------------------------------------------

	/** {@code hmc:snapshot_ack} — accepted or rejected install; {@code activeFrameId} is -1 when nothing is active. */
	public record SnapshotAck(long frameId, boolean accepted, String code, String detail, long activeFrameId, int validColliders)
			implements CustomPacketPayload {
		public static final Type<SnapshotAck> TYPE = new Type<>(id("snapshot_ack"));
		public static final StreamCodec<FriendlyByteBuf, SnapshotAck> CODEC = StreamCodec.of(
				(buf, p) -> {
					buf.writeLong(p.frameId);
					buf.writeBoolean(p.accepted);
					buf.writeUtf(p.code, 64);
					buf.writeUtf(p.detail, MAX_STRING);
					buf.writeLong(p.activeFrameId);
					buf.writeVarInt(p.validColliders);
				},
				buf -> new SnapshotAck(buf.readLong(), buf.readBoolean(), buf.readUtf(64), buf.readUtf(MAX_STRING), buf.readLong(), buf.readVarInt()));

		public static SnapshotAck of(long frameId, InstallOutcome outcome, int validColliders) {
			String detail = outcome.detail().length() > MAX_STRING ? outcome.detail().substring(0, MAX_STRING) : outcome.detail();
			return new SnapshotAck(frameId, outcome.accepted(), outcome.code(), detail, outcome.activeFrameId().orElse(-1), validColliders);
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	/** {@code hmc:probe_result} — authoritative answer to a probe. */
	public record ProbeResult(
			int requestId,
			String code,
			long frameId,
			String colliderId,
			String bodyPart,
			double distance,
			boolean hasPoint,
			double px,
			double py,
			double pz) implements CustomPacketPayload {

		public static final Type<ProbeResult> TYPE = new Type<>(id("probe_result"));
		public static final StreamCodec<FriendlyByteBuf, ProbeResult> CODEC = StreamCodec.of(ProbeResult::write, ProbeResult::read);

		public static ProbeResult of(ProbeService.ProbeOutcome o) {
			Vector3 p = o.point().orElse(Vector3.ZERO);
			return new ProbeResult(o.requestId(), o.code().wireName(), o.frameId(), o.colliderId().orElse(""),
					o.bodyPart().map(BodyPart::wireName).orElse(""), o.distance(), o.point().isPresent(), p.x(), p.y(), p.z());
		}

		public Optional<ProbeResultCode> resultCode() {
			for (ProbeResultCode c : ProbeResultCode.values()) {
				if (c.wireName().equals(code)) {
					return Optional.of(c);
				}
			}
			return Optional.empty();
		}

		public Optional<BodyPart> part() {
			if (bodyPart.isEmpty()) {
				return Optional.empty();
			}
			for (BodyPart b : BodyPart.values()) {
				if (b.wireName().equals(bodyPart)) {
					return Optional.of(b);
				}
			}
			return Optional.empty();
		}

		public Optional<Vector3> point() {
			return hasPoint ? Optional.of(new Vector3(px, py, pz)) : Optional.empty();
		}

		private static void write(FriendlyByteBuf buf, ProbeResult p) {
			buf.writeVarInt(p.requestId);
			buf.writeUtf(p.code, 32);
			buf.writeLong(p.frameId);
			buf.writeUtf(p.colliderId, ProtocolLimits.MAX_COLLIDER_ID_BYTES);
			buf.writeUtf(p.bodyPart, 32);
			buf.writeDouble(p.distance);
			buf.writeBoolean(p.hasPoint);
			buf.writeDouble(p.px);
			buf.writeDouble(p.py);
			buf.writeDouble(p.pz);
		}

		private static ProbeResult read(FriendlyByteBuf buf) {
			return new ProbeResult(buf.readVarInt(), buf.readUtf(32), buf.readLong(), buf.readUtf(ProtocolLimits.MAX_COLLIDER_ID_BYTES),
					buf.readUtf(32), buf.readDouble(), buf.readBoolean(), buf.readDouble(), buf.readDouble(), buf.readDouble());
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	/** {@code hmc:contact_state} — which hand/foot colliders currently overlap solid blocks. */
	public record ContactState(long frameId, List<Contact> contacts) implements CustomPacketPayload {
		public static final Type<ContactState> TYPE = new Type<>(id("contact_state"));
		public static final int MAX_CONTACTS = 4096;
		public static final StreamCodec<FriendlyByteBuf, ContactState> CODEC = StreamCodec.of(ContactState::write, ContactState::read);

		public record Contact(String colliderId, String bodyPart, int x, int y, int z) {
			public Optional<BodyPart> part() {
				for (BodyPart b : BodyPart.values()) {
					if (b.wireName().equals(bodyPart)) {
						return Optional.of(b);
					}
				}
				return Optional.empty();
			}
		}

		public static ContactState of(ContactService.ContactState state) {
			List<Contact> out = new ArrayList<>(state.contacts().size());
			for (ContactService.Contact c : state.contacts()) {
				if (out.size() >= MAX_CONTACTS) {
					break;
				}
				out.add(new Contact(c.colliderId(), c.bodyPart().wireName(), c.x(), c.y(), c.z()));
			}
			return new ContactState(state.frameId(), out);
		}

		private static void write(FriendlyByteBuf buf, ContactState p) {
			buf.writeLong(p.frameId);
			buf.writeVarInt(p.contacts.size());
			for (Contact c : p.contacts) {
				buf.writeUtf(c.colliderId, ProtocolLimits.MAX_COLLIDER_ID_BYTES);
				buf.writeUtf(c.bodyPart, 32);
				buf.writeVarInt(c.x);
				buf.writeVarInt(c.y);
				buf.writeVarInt(c.z);
			}
		}

		private static ContactState read(FriendlyByteBuf buf) {
			long frameId = buf.readLong();
			int n = buf.readVarInt();
			if (n < 0 || n > MAX_CONTACTS) {
				throw new IllegalStateException("contact count " + n + " out of range");
			}
			List<Contact> contacts = new ArrayList<>(n);
			for (int i = 0; i < n; i++) {
				contacts.add(new Contact(buf.readUtf(ProtocolLimits.MAX_COLLIDER_ID_BYTES), buf.readUtf(32), buf.readVarInt(), buf.readVarInt(), buf.readVarInt()));
			}
			return new ContactState(frameId, contacts);
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	// ---- registration ------------------------------------------------------------------------

	private static boolean registered;

	/** Registers payload types on both sides; idempotent so client and common initializers can both call it. */
	public static synchronized void register() {
		if (registered) {
			return;
		}
		registered = true;
		PayloadTypeRegistry<RegistryFriendlyByteBuf> c2s = PayloadTypeRegistry.playC2S();
		PayloadTypeRegistry<RegistryFriendlyByteBuf> s2c = PayloadTypeRegistry.playS2C();
		c2s.register(InstallSnapshot.TYPE, InstallSnapshot.CODEC);
		c2s.register(ClearSnapshot.TYPE, ClearSnapshot.CODEC);
		c2s.register(ProbeRequest.TYPE, ProbeRequest.CODEC);
		s2c.register(SnapshotAck.TYPE, SnapshotAck.CODEC);
		s2c.register(ProbeResult.TYPE, ProbeResult.CODEC);
		s2c.register(ContactState.TYPE, ContactState.CODEC);
		HumanCraft.LOGGER.debug("Registered {} payload types", 6);
	}
}
