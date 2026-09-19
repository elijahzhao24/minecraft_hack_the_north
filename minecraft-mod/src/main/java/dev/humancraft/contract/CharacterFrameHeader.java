package dev.humancraft.contract;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/** JSON header of a {@code CHARACTER_FRAME} message (docs/contracts.md §6). Geometry is in stage meters. */
public record CharacterFrameHeader(
		UUID sessionId,
		UUID calibrationId,
		long frameId,
		List<SourceFrameRef> sourceFrames,
		double normalizedCaptureTimeS,
		double pairSkewMs,
		Mode mode,
		FrameQuality quality,
		List<LandmarkDto> landmarks,
		List<ColliderDto> colliders,
		TraceContext trace,
		List<BufferDescriptor> buffers) {

	public CharacterFrameHeader {
		sourceFrames = List.copyOf(sourceFrames);
		landmarks = List.copyOf(landmarks);
		colliders = List.copyOf(colliders);
		buffers = List.copyOf(buffers);
		if (landmarks.size() > ProtocolLimits.MAX_LANDMARKS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "too many landmarks: " + landmarks.size());
		}
		if (colliders.size() > ProtocolLimits.MAX_COLLIDERS) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "too many colliders: " + colliders.size());
		}
		Set<String> ids = new HashSet<>();
		for (ColliderDto c : colliders) {
			if (!ids.add(c.id())) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "duplicate collider id '" + c.id() + "'");
			}
			c.requireSizeAtMost(ProtocolLimits.MAX_COLLIDER_SIZE_M);
		}
		Set<String> names = new HashSet<>();
		for (LandmarkDto l : landmarks) {
			if (!names.add(l.name())) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "duplicate landmark name '" + l.name() + "'");
			}
		}
		if (!Double.isFinite(normalizedCaptureTimeS) || !Double.isFinite(pairSkewMs)) {
			throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, "timestamps must be finite");
		}
	}

	public static CharacterFrameHeader parse(String json) {
		StrictJson.Obj o = new StrictJson.Obj(StrictJson.parseObject(json), "header");
		String schema = o.string("schema");
		if (!ProtocolLimits.CHARACTER_FRAME_SCHEMA.equals(schema)) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unexpected schema '" + schema + "'");
		}
		long schemaVersion = o.counter("schema_version");
		if (schemaVersion != ProtocolLimits.CHARACTER_FRAME_SCHEMA_VERSION) {
			throw new ProtocolException(ProtocolException.UNSUPPORTED_VERSION, "unsupported character_frame schema_version " + schemaVersion);
		}
		UUID sessionId = o.uuid("session_id");
		UUID calibrationId = o.uuid("calibration_id");
		long frameId = o.counter("frame_id");
		List<SourceFrameRef> sources = o.objectList("source_frames", 16, ProtocolException.LIMIT_EXCEEDED).stream()
				.map(SourceFrameRef::parse).toList();
		double captureTime = o.finiteDouble("normalized_capture_time_s");
		double skew = o.finiteDouble("pair_skew_ms");
		Mode mode = o.enumValue("mode", Mode.class);
		FrameQuality quality = FrameQuality.parse(o.object("quality"));
		List<LandmarkDto> landmarks = o.objectList("landmarks", ProtocolLimits.MAX_LANDMARKS, ProtocolException.LIMIT_EXCEEDED).stream()
				.map(LandmarkDto::parse).toList();
		List<ColliderDto> colliders = o.objectList("colliders", ProtocolLimits.MAX_COLLIDERS, ProtocolException.LIMIT_EXCEEDED).stream()
				.map(ColliderDto::parse).toList();
		TraceContext trace = TraceContext.parse(o.object("trace"));
		List<BufferDescriptor> buffers = o.objectList("buffers", 16, ProtocolException.LIMIT_EXCEEDED).stream()
				.map(BufferDescriptor::parse).toList();
		o.finish();
		return new CharacterFrameHeader(sessionId, calibrationId, frameId, sources, captureTime, skew, mode, quality,
				landmarks, colliders, trace, buffers);
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("schema", ProtocolLimits.CHARACTER_FRAME_SCHEMA);
		o.addProperty("schema_version", ProtocolLimits.CHARACTER_FRAME_SCHEMA_VERSION);
		o.addProperty("session_id", sessionId.toString());
		o.addProperty("calibration_id", calibrationId.toString());
		o.addProperty("frame_id", frameId);
		JsonArray sources = new JsonArray();
		sourceFrames.forEach(s -> sources.add(s.toJson()));
		o.add("source_frames", sources);
		o.addProperty("normalized_capture_time_s", normalizedCaptureTimeS);
		o.addProperty("pair_skew_ms", pairSkewMs);
		o.addProperty("mode", mode.wireName());
		o.add("quality", quality.toJson());
		JsonArray lm = new JsonArray();
		landmarks.forEach(l -> lm.add(l.toJson()));
		o.add("landmarks", lm);
		JsonArray cs = new JsonArray();
		colliders.forEach(c -> cs.add(c.toJson()));
		o.add("colliders", cs);
		o.add("trace", trace.toJson());
		JsonArray bufs = new JsonArray();
		for (BufferDescriptor b : buffers) {
			JsonObject bo = new JsonObject();
			bo.addProperty("name", b.name());
			bo.addProperty("encoding", b.encoding());
			bo.addProperty("offset", b.offset());
			bo.addProperty("length", b.length());
			if (b.shape() != null) {
				JsonArray shape = new JsonArray();
				b.shape().forEach(shape::add);
				bo.add("shape", shape);
			}
			bufs.add(bo);
		}
		o.add("buffers", bufs);
		return o;
	}
}
