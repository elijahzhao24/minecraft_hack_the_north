package dev.humancraft.contract;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;

/**
 * Collider discriminated union. Geometry is in the frame of the containing object (stage meters on the
 * wire, world blocks after transform). Invalid colliders keep identity fields and carry no geometry.
 */
public record ColliderDto(
		String id,
		BodyPart bodyPart,
		ColliderType type,
		boolean valid,
		FitSource fitSource,
		Optional<Double> quality,
		Optional<Shape> geometry) {

	public ColliderDto {
		if (id == null || id.isEmpty()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "collider id must be non-empty");
		}
		if (id.getBytes(StandardCharsets.UTF_8).length > ProtocolLimits.MAX_COLLIDER_ID_BYTES) {
			throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "collider id longer than " + ProtocolLimits.MAX_COLLIDER_ID_BYTES + " bytes");
		}
		if (valid) {
			if (geometry.isEmpty()) {
				throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY, "collider '" + id + "' is valid but has no geometry");
			}
			if (!geometry.get().typeName().equals(type.wireName())) {
				throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY,
						"collider '" + id + "' type " + type.wireName() + " does not match geometry " + geometry.get().typeName());
			}
		} else if (geometry.isPresent()) {
			throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY, "collider '" + id + "' is invalid but carries geometry");
		}
		if (quality.isPresent() && !(quality.get() >= 0 && quality.get() <= 1)) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "collider '" + id + "' quality must be within [0,1]");
		}
	}

	/** Largest radius or half extent of the geometry, or 0 for invalid colliders. */
	public double maxSize() {
		if (geometry.isEmpty()) {
			return 0;
		}
		return switch (geometry.get()) {
			case Sphere s -> s.radius();
			case Capsule c -> c.radius();
			case Obb o -> Math.max(o.halfExtents().x(), Math.max(o.halfExtents().y(), o.halfExtents().z()));
		};
	}

	/** Rejects geometry whose radius/half extents exceed {@code maxSize} in the collider's frame. */
	public void requireSizeAtMost(double maxSize) {
		if (maxSize() > maxSize) {
			throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY,
					"collider '" + id + "' size " + maxSize() + " exceeds cap " + maxSize);
		}
	}

	public ColliderDto withGeometry(Optional<Shape> newGeometry) {
		return new ColliderDto(id, bodyPart, type, valid, fitSource, quality, newGeometry);
	}

	public static ColliderDto parse(StrictJson.Obj o) {
		String id = o.string("id");
		BodyPart bodyPart = o.enumValue("body_part", BodyPart.class);
		ColliderType type = o.enumValue("type", ColliderType.class);
		boolean valid = o.bool("valid");
		FitSource fitSource = o.enumValue("fit_source", FitSource.class);
		Optional<Double> quality = o.optionalFinite("quality");

		Optional<Shape> geometry = Optional.empty();
		if (valid) {
			try {
				geometry = Optional.of(switch (type) {
					case SPHERE -> new Sphere(Vector3.of(o.finiteArray("center_stage_m", 3)), o.finiteDouble("radius_m"));
					case CAPSULE -> new Capsule(
							Vector3.of(o.finiteArray("a_stage_m", 3)),
							Vector3.of(o.finiteArray("b_stage_m", 3)),
							o.finiteDouble("radius_m"));
					case OBB -> {
						Vector3 center = Vector3.of(o.finiteArray("center_stage_m", 3));
						double[] axes = o.finiteArray("axes_row_major", 9);
						Vector3 half = Vector3.of(o.finiteArray("half_extents_m", 3));
						yield new Obb(center, List.of(
								new Vector3(axes[0], axes[1], axes[2]),
								new Vector3(axes[3], axes[4], axes[5]),
								new Vector3(axes[6], axes[7], axes[8])), half);
					}
				});
			} catch (IllegalArgumentException e) {
				throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY, "collider '" + id + "': " + e.getMessage(), e);
			}
		} else {
			// Invalid colliders may carry explicit nulls for geometry fields; anything else is rejected.
			for (String key : List.of("geometry", "center_stage_m", "radius_m", "a_stage_m", "b_stage_m", "axes_row_major", "half_extents_m")) {
				if (o.has(key)) {
					throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY, "collider '" + id + "' is invalid but carries " + key);
				}
				o.optionalString(key);
			}
		}
		o.finish();
		return new ColliderDto(id, bodyPart, type, valid, fitSource, quality, geometry);
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("id", id);
		o.addProperty("body_part", bodyPart.wireName());
		o.addProperty("type", type.wireName());
		o.addProperty("valid", valid);
		o.addProperty("fit_source", fitSource.wireName());
		o.add("quality", quality.<JsonElement>map(JsonPrimitive::new).orElse(JsonNull.INSTANCE));
		if (geometry.isPresent()) {
			switch (geometry.get()) {
				case Sphere s -> {
					o.add("center_stage_m", LandmarkDto.vec(s.center()));
					o.addProperty("radius_m", s.radius());
				}
				case Capsule c -> {
					o.add("a_stage_m", LandmarkDto.vec(c.a()));
					o.add("b_stage_m", LandmarkDto.vec(c.b()));
					o.addProperty("radius_m", c.radius());
				}
				case Obb b -> {
					o.add("center_stage_m", LandmarkDto.vec(b.center()));
					JsonArray axes = new JsonArray();
					for (Vector3 axis : b.axes()) {
						axes.add(axis.x());
						axes.add(axis.y());
						axes.add(axis.z());
					}
					o.add("axes_row_major", axes);
					o.add("half_extents_m", LandmarkDto.vec(b.halfExtents()));
				}
			}
		}
		return o;
	}
}
