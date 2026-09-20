package dev.humancraft.network;

import dev.humancraft.HumanCraft;
import dev.humancraft.avatar.AvatarService;
import dev.humancraft.avatar.ControlIntent;
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
			int pointCount,
			UUID fusionId,
			String sourceFrameIds,
			UUID targetPlayerId,
			long bindingGeneration,
			long normalizationRevision) implements CustomPacketPayload {

		public static final Type<InstallSnapshot> TYPE = new Type<>(id("install_snapshot"));
		public static final StreamCodec<FriendlyByteBuf, InstallSnapshot> CODEC = StreamCodec.of(InstallSnapshot::write, InstallSnapshot::read);

		public InstallSnapshot(long frameId, UUID sessionId, UUID calibrationId, String mode,
				double anchorX, double anchorY, double anchorZ, double blocksPerMeter,
				List<WireCollider> colliders, int landmarkCount, int pointCount) {
			this(frameId, sessionId, calibrationId, mode, anchorX, anchorY, anchorZ, blocksPerMeter,
					colliders, landmarkCount, pointCount, null, "", new UUID(0, 0), 0, 0);
		}

		public static InstallSnapshot of(InstallRequest request) {
			List<WireCollider> wire = new ArrayList<>(request.stageColliders().size());
			for (ColliderDto c : request.stageColliders()) {
				wire.add(WireCollider.of(c));
			}
			Vector3 a = request.transform().anchor();
			return new InstallSnapshot(request.frameId(), request.sessionId(), request.calibrationId(), request.mode().wireName(),
					a.x(), a.y(), a.z(), request.transform().blocksPerMeter(), wire, request.landmarkCount(), request.pointCount(),
					request.fusionId(), request.sourceFrameIds(), request.targetPlayerId(), request.bindingGeneration(),
					request.normalizationRevision());
		}

		/** Validates and converts; throws {@link dev.humancraft.contract.ProtocolException} or {@link IllegalArgumentException}. */
		public InstallRequest toRequest() {
			List<ColliderDto> dtos = new ArrayList<>(colliders.size());
			for (WireCollider c : colliders) {
				dtos.add(c.toDto());
			}
			return new InstallRequest(frameId, sessionId, calibrationId, WireEnum.fromWire(Mode.class, mode),
					new StageToWorld(new Vector3(anchorX, anchorY, anchorZ), blocksPerMeter), dtos, landmarkCount, pointCount,
					fusionId, sourceFrameIds, targetPlayerId, bindingGeneration, normalizationRevision);
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
			buf.writeBoolean(p.fusionId != null);
			if (p.fusionId != null) buf.writeUUID(p.fusionId);
			buf.writeUtf(p.sourceFrameIds, 512);
			buf.writeUUID(p.targetPlayerId);
			buf.writeVarLong(p.bindingGeneration);
			buf.writeVarLong(p.normalizationRevision);
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
			UUID fusionId = buf.readBoolean() ? buf.readUUID() : null;
			String sourceFrameIds = buf.readUtf(512);
			return new InstallSnapshot(frameId, session, calibration, mode, ax, ay, az, scale, colliders, landmarks, points,
					fusionId, sourceFrameIds, buf.readUUID(), buf.readVarLong(), buf.readVarLong());
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	public record ControlInput(float strafe, float forward, float yaw, float pitch, boolean jump, boolean sneak, boolean sprint)
			implements CustomPacketPayload {
		public static final Type<ControlInput> TYPE = new Type<>(id("control_input"));
		public static final StreamCodec<FriendlyByteBuf, ControlInput> CODEC = StreamCodec.of(
				(buf, p) -> { buf.writeFloat(p.strafe); buf.writeFloat(p.forward); buf.writeFloat(p.yaw); buf.writeFloat(p.pitch);
					buf.writeBoolean(p.jump); buf.writeBoolean(p.sneak); buf.writeBoolean(p.sprint); },
				buf -> new ControlInput(buf.readFloat(), buf.readFloat(), buf.readFloat(), buf.readFloat(),
						buf.readBoolean(), buf.readBoolean(), buf.readBoolean()));
		public ControlIntent toIntent(long now) { return new ControlIntent(strafe, forward, yaw, pitch, jump, sneak, sprint, now); }
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	/** Sent directly after its InstallSnapshot on the same ordered connection. No client-supplied targets or damage. */
	public record ArmSwing(long frameId, UUID sessionId, UUID calibrationId, UUID targetPlayerId,
			long bindingGeneration, long normalizationRevision, boolean left) implements CustomPacketPayload {
		public static final Type<ArmSwing> TYPE = new Type<>(id("arm_swing"));
		public static final StreamCodec<FriendlyByteBuf, ArmSwing> CODEC = StreamCodec.of((buf, p) -> {
			buf.writeLong(p.frameId); buf.writeUUID(p.sessionId); buf.writeUUID(p.calibrationId);
			buf.writeUUID(p.targetPlayerId); buf.writeVarLong(p.bindingGeneration);
			buf.writeVarLong(p.normalizationRevision); buf.writeBoolean(p.left);
		}, buf -> new ArmSwing(buf.readLong(), buf.readUUID(), buf.readUUID(), buf.readUUID(),
				buf.readVarLong(), buf.readVarLong(), buf.readBoolean()));
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	public record AnatomyAttack(int requestId) implements CustomPacketPayload {
		public static final Type<AnatomyAttack> TYPE = new Type<>(id("anatomy_attack"));
		public static final StreamCodec<FriendlyByteBuf, AnatomyAttack> CODEC = StreamCodec.of(
				(buf, p) -> buf.writeVarInt(p.requestId), buf -> new AnatomyAttack(buf.readVarInt()));
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
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
	public record SnapshotAck(long frameId, boolean accepted, String code, String detail, long activeFrameId, int validColliders,
			UUID fusionId, long bindingGeneration, long normalizationRevision) implements CustomPacketPayload {
		public SnapshotAck(long frameId, boolean accepted, String code, String detail, long activeFrameId, int validColliders) {
			this(frameId, accepted, code, detail, activeFrameId, validColliders, null, 0, 0);
		}

		public static final Type<SnapshotAck> TYPE = new Type<>(id("snapshot_ack"));
		public static final StreamCodec<FriendlyByteBuf, SnapshotAck> CODEC = StreamCodec.of(
				(buf, p) -> {
					buf.writeLong(p.frameId);
					buf.writeBoolean(p.accepted);
					buf.writeUtf(p.code, 64);
					buf.writeUtf(p.detail, MAX_STRING);
					buf.writeLong(p.activeFrameId);
					buf.writeVarInt(p.validColliders);
					buf.writeBoolean(p.fusionId != null);
					if (p.fusionId != null) buf.writeUUID(p.fusionId);
					buf.writeVarLong(p.bindingGeneration);
					buf.writeVarLong(p.normalizationRevision);
				},
				buf -> new SnapshotAck(buf.readLong(), buf.readBoolean(), buf.readUtf(64), buf.readUtf(MAX_STRING), buf.readLong(),
						buf.readVarInt(), buf.readBoolean() ? buf.readUUID() : null, buf.readVarLong(), buf.readVarLong()));

		public static SnapshotAck of(long frameId, InstallOutcome outcome, int validColliders,
				UUID fusionId, long bindingGeneration, long normalizationRevision) {
			String detail = outcome.detail().length() > MAX_STRING ? outcome.detail().substring(0, MAX_STRING) : outcome.detail();
			return new SnapshotAck(frameId, outcome.accepted(), outcome.code(), detail, outcome.activeFrameId().orElse(-1),
					validColliders, fusionId, bindingGeneration, normalizationRevision);
		}

		public static SnapshotAck of(long frameId, InstallOutcome outcome, int validColliders,
				long bindingGeneration, long normalizationRevision) {
			return of(frameId, outcome, validColliders, null, bindingGeneration, normalizationRevision);
		}

		public static SnapshotAck of(long frameId, InstallOutcome outcome, int validColliders, UUID fusionId) {
			return of(frameId, outcome, validColliders, fusionId, 0, 0);
		}

		public static SnapshotAck of(long frameId, InstallOutcome outcome, int validColliders) {
			return of(frameId, outcome, validColliders, null, 0, 0);
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

	public record AvatarState(UUID targetPlayerId, String mode, long bindingGeneration, long normalizationRevision, boolean controlling)
			implements CustomPacketPayload {
		public static final Type<AvatarState> TYPE = new Type<>(id("avatar_state"));
		public static final StreamCodec<FriendlyByteBuf, AvatarState> CODEC = StreamCodec.of(
				(buf, p) -> { buf.writeUUID(p.targetPlayerId); buf.writeUtf(p.mode, 16); buf.writeVarLong(p.bindingGeneration);
					buf.writeVarLong(p.normalizationRevision); buf.writeBoolean(p.controlling); },
				buf -> new AvatarState(buf.readUUID(), buf.readUtf(16), buf.readVarLong(), buf.readVarLong(), buf.readBoolean()));
		public static AvatarState of(AvatarService.Binding b) {
			return new AvatarState(b.targetId(), b.mode().name().toLowerCase(), b.generation(), b.normalizationRevision(), b.controlling());
		}
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	public record DebugState(boolean enabled) implements CustomPacketPayload {
		public static final Type<DebugState> TYPE = new Type<>(id("debug_state"));
		public static final StreamCodec<FriendlyByteBuf, DebugState> CODEC = StreamCodec.of(
				(buf, p) -> buf.writeBoolean(p.enabled), buf -> new DebugState(buf.readBoolean()));
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	public record CameraCalibrationRequest() implements CustomPacketPayload {
		public static final Type<CameraCalibrationRequest> TYPE = new Type<>(id("camera_calibration_request"));
		public static final StreamCodec<FriendlyByteBuf, CameraCalibrationRequest> CODEC = StreamCodec.of(
				(buf, p) -> {}, buf -> new CameraCalibrationRequest());
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	/** One bounded fragment of an accepted scan's render-only HMC1 frame. */
	public record ScanChunk(UUID transferId, long frameId, UUID sessionId, UUID calibrationId, UUID fusionId,
			UUID targetPlayerId, long bindingGeneration, long normalizationRevision,
			double anchorX, double anchorY, double anchorZ, double blocksPerMeter,
			int chunkIndex, int chunkCount, int totalBytes, byte[] data) implements CustomPacketPayload {
		public static final Type<ScanChunk> TYPE = new Type<>(id("scan_chunk"));
		public static final StreamCodec<FriendlyByteBuf, ScanChunk> CODEC = StreamCodec.of(ScanChunk::write, ScanChunk::read);
		private static void write(FriendlyByteBuf b, ScanChunk p) {
			b.writeUUID(p.transferId); b.writeLong(p.frameId); b.writeUUID(p.sessionId); b.writeUUID(p.calibrationId);
			b.writeBoolean(p.fusionId != null); if (p.fusionId != null) b.writeUUID(p.fusionId);
			b.writeUUID(p.targetPlayerId); b.writeVarLong(p.bindingGeneration); b.writeVarLong(p.normalizationRevision);
			b.writeDouble(p.anchorX); b.writeDouble(p.anchorY); b.writeDouble(p.anchorZ); b.writeDouble(p.blocksPerMeter);
			b.writeVarInt(p.chunkIndex); b.writeVarInt(p.chunkCount); b.writeVarInt(p.totalBytes); b.writeByteArray(p.data);
		}
		private static ScanChunk read(FriendlyByteBuf b) {
			UUID transfer = b.readUUID(); long frame = b.readLong(); UUID session = b.readUUID(); UUID calibration = b.readUUID();
			UUID fusion = b.readBoolean() ? b.readUUID() : null; UUID target = b.readUUID(); long generation = b.readVarLong();
			long revision = b.readVarLong(); double x = b.readDouble(), y = b.readDouble(), z = b.readDouble(), scale = b.readDouble();
			return new ScanChunk(transfer, frame, session, calibration, fusion, target, generation, revision, x, y, z, scale,
					b.readVarInt(), b.readVarInt(), b.readVarInt(), b.readByteArray(SharedScanCodec.CHUNK_BYTES));
		}
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	/** Server-authenticated scan fragment relayed to viewing clients. */
	public record SharedScanChunk(long publication, UUID ownerId, ScanChunk chunk) implements CustomPacketPayload {
		public static final Type<SharedScanChunk> TYPE = new Type<>(id("shared_scan_chunk"));
		public static final StreamCodec<FriendlyByteBuf, SharedScanChunk> CODEC = StreamCodec.of(
				(b, p) -> { b.writeVarLong(p.publication); b.writeUUID(p.ownerId); ScanChunk.write(b, p.chunk); },
				b -> new SharedScanChunk(b.readVarLong(), b.readUUID(), ScanChunk.read(b)));
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
	}

	/** Invalidates a published visual; publication ordering prevents late chunks from reviving it. */
	public record SharedScanRemoved(long publication, UUID ownerId, UUID targetPlayerId) implements CustomPacketPayload {
		public static final Type<SharedScanRemoved> TYPE = new Type<>(id("shared_scan_removed"));
		public static final StreamCodec<FriendlyByteBuf, SharedScanRemoved> CODEC = StreamCodec.of(
				(b, p) -> { b.writeVarLong(p.publication); b.writeUUID(p.ownerId); b.writeUUID(p.targetPlayerId); },
				b -> new SharedScanRemoved(b.readVarLong(), b.readUUID(), b.readUUID()));
		@Override public Type<? extends CustomPacketPayload> type() { return TYPE; }
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
		c2s.register(ControlInput.TYPE, ControlInput.CODEC);
		c2s.register(AnatomyAttack.TYPE, AnatomyAttack.CODEC);
		c2s.register(ArmSwing.TYPE, ArmSwing.CODEC);
		c2s.register(ScanChunk.TYPE, ScanChunk.CODEC);
		s2c.register(SnapshotAck.TYPE, SnapshotAck.CODEC);
		s2c.register(ProbeResult.TYPE, ProbeResult.CODEC);
		s2c.register(ContactState.TYPE, ContactState.CODEC);
		s2c.register(AvatarState.TYPE, AvatarState.CODEC);
		s2c.register(DebugState.TYPE, DebugState.CODEC);
		s2c.register(CameraCalibrationRequest.TYPE, CameraCalibrationRequest.CODEC);
		s2c.register(SharedScanChunk.TYPE, SharedScanChunk.CODEC);
		s2c.register(SharedScanRemoved.TYPE, SharedScanRemoved.CODEC);
		HumanCraft.LOGGER.debug("Registered {} payload types", 15);
	}
}
