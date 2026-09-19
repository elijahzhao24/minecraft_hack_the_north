package dev.humancraft.contract;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.List;

public record FrameQuality(boolean valid, long pointCount, long validLandmarkCount, long validColliderCount,
		List<String> warnings) {
	public FrameQuality {
		warnings = List.copyOf(warnings);
	}

	public static FrameQuality parse(StrictJson.Obj o) {
		FrameQuality q = new FrameQuality(
				o.bool("valid"),
				o.counter("point_count"),
				o.counter("valid_landmark_count"),
				o.counter("valid_collider_count"),
				o.stringList("warnings"));
		o.finish();
		return q;
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("valid", valid);
		o.addProperty("point_count", pointCount);
		o.addProperty("valid_landmark_count", validLandmarkCount);
		o.addProperty("valid_collider_count", validColliderCount);
		JsonArray w = new JsonArray();
		warnings.forEach(w::add);
		o.add("warnings", w);
		return o;
	}
}
