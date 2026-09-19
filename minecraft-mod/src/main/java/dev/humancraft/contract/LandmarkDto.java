package dev.humancraft.contract;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import dev.humancraft.geometry.Vector3;

import java.util.List;
import java.util.Optional;

/**
 * {@code Landmark3D}. {@code position} is in the frame of the containing object: stage meters when decoded
 * from the wire, world blocks after {@link dev.humancraft.model.StageToWorld}.
 */
public record LandmarkDto(
		String name,
		Optional<Vector3> position,
		boolean valid,
		LandmarkSource source,
		Optional<Double> confidence,
		Optional<Double> visibility,
		List<String> observedBy,
		Optional<Double> reprojectionErrorPx) {

	public LandmarkDto {
		if (name == null || name.isEmpty()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "landmark name must be non-empty");
		}
		observedBy = List.copyOf(observedBy);
		if (valid) {
			if (position.isEmpty()) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "landmark '" + name + "' is valid but has no position");
			}
			if (source == LandmarkSource.UNAVAILABLE) {
				throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "landmark '" + name + "' is valid but source is unavailable");
			}
		} else if (position.isPresent()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "landmark '" + name + "' is invalid but carries a position");
		}
		if (position.isPresent() && !position.get().isFinite()) {
			throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, "landmark '" + name + "' position is not finite");
		}
		for (Optional<Double> metric : List.of(confidence, visibility, reprojectionErrorPx)) {
			if (metric.isPresent() && !Double.isFinite(metric.get())) {
				throw new ProtocolException(ProtocolException.NON_FINITE_GEOMETRY, "landmark '" + name + "' metric is not finite");
			}
		}
	}

	public LandmarkDto withPosition(Optional<Vector3> newPosition) {
		return new LandmarkDto(name, newPosition, valid, source, confidence, visibility, observedBy, reprojectionErrorPx);
	}

	public static LandmarkDto parse(StrictJson.Obj o) {
		String name = o.string("name");
		Optional<Vector3> position = o.optionalFiniteArray("position_stage_m", 3).map(Vector3::of);
		boolean valid = o.bool("valid");
		LandmarkSource source = o.enumValue("source", LandmarkSource.class);
		Optional<Double> confidence = o.optionalFinite("confidence");
		Optional<Double> visibility = o.optionalFinite("visibility");
		List<String> observedBy = o.stringList("observed_by");
		Optional<Double> reprojection = o.optionalFinite("reprojection_error_px");
		o.finish();
		return new LandmarkDto(name, position, valid, source, confidence, visibility, observedBy, reprojection);
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("name", name);
		o.add("position_stage_m", position.<JsonElement>map(LandmarkDto::vec).orElse(JsonNull.INSTANCE));
		o.addProperty("valid", valid);
		o.addProperty("source", source.wireName());
		o.add("confidence", num(confidence));
		o.add("visibility", num(visibility));
		JsonArray observed = new JsonArray();
		observedBy.forEach(observed::add);
		o.add("observed_by", observed);
		o.add("reprojection_error_px", num(reprojectionErrorPx));
		return o;
	}

	static JsonArray vec(Vector3 v) {
		JsonArray a = new JsonArray();
		a.add(v.x());
		a.add(v.y());
		a.add(v.z());
		return a;
	}

	private static JsonElement num(Optional<Double> value) {
		return value.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE);
	}
}
