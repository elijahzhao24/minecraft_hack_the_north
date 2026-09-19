package dev.humancraft.fixture;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.BufferDescriptor;
import dev.humancraft.contract.CharacterFrameHeader;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.ColliderType;
import dev.humancraft.contract.FitSource;
import dev.humancraft.contract.FrameQuality;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.contract.LandmarkSource;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.contract.SourceFrameRef;
import dev.humancraft.contract.TraceContext;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.PointCloud;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Random;
import java.util.UUID;

/**
 * Deterministic synthetic person in stage coordinates (meters, +Y up, facing +Z toward the front camera, so
 * the person's left side is at +X). Produces the same landmarks, colliders and colored point cloud on every
 * run so Workflow 4 can be built and tested without phones or the Python backend.
 *
 * <p>The cloud is sampled on the surfaces of the very colliders that are published, which makes "volumes
 * align with the visible surface" testable: every point must lie on or inside its part's collider.
 */
public final class SyntheticHuman {
	public static final UUID SESSION_ID = UUID.fromString("25b981d5-b9a6-4df5-8495-c95c5a9a65d9");
	public static final UUID CALIBRATION_ID = UUID.fromString("f766f462-d405-4c30-89f9-f626da70547b");
	public static final UUID FRONT_SESSION_ID = UUID.fromString("44487d7c-b847-49db-aa37-cf326ad76078");
	public static final UUID SIDE_SESSION_ID = UUID.fromString("18619e93-736f-4f18-afdb-7f3ea369b7d5");
	public static final String FRONT_DEVICE = "front-phone";
	public static final String SIDE_DEVICE = "side-phone";
	public static final int DEFAULT_POINT_COUNT = 6000;
	private static final long CLOUD_SEED = 0x484D4331L; // "HMC1"

	/** Held poses from the brief's acceptance list. */
	public enum Pose {
		NEUTRAL,
		BENT_LEFT_ARM,
		LIFTED_RIGHT_FOOT
	}

	private SyntheticHuman() {}

	public static CharacterFrame frame(Pose pose, long frameId, Mode mode) {
		return frame(pose, frameId, mode, DEFAULT_POINT_COUNT);
	}

	public static CharacterFrame frame(Pose pose, long frameId, Mode mode, int pointCount) {
		Rig rig = rig(pose);
		List<LandmarkDto> landmarks = landmarks(rig);
		List<ColliderDto> colliders = colliders(rig);
		PointCloud cloud = cloud(colliders, pointCount);
		UUID captureId = UUID.nameUUIDFromBytes(("capture-" + frameId).getBytes());
		List<SourceFrameRef> sources = List.of(
				new SourceFrameRef(FRONT_DEVICE, FRONT_SESSION_ID, captureId, 100 + frameId),
				new SourceFrameRef(SIDE_DEVICE, SIDE_SESSION_ID, captureId, 200 + frameId));
		FrameQuality quality = new FrameQuality(
				true,
				cloud.count(),
				landmarks.stream().filter(LandmarkDto::valid).count(),
				colliders.stream().filter(ColliderDto::valid).count(),
				List.of("synthetic_fixture"));
		List<BufferDescriptor> buffers = List.of(new BufferDescriptor(
				"points", BufferDescriptor.ENCODING_POINTS, 0, (long) cloud.count() * ProtocolLimits.POINT_RECORD_BYTES,
				List.of((long) cloud.count())));
		CharacterFrameHeader header = new CharacterFrameHeader(
				SESSION_ID, CALIBRATION_ID, frameId, sources,
				61420.0 + frameId * 0.5, 4.0, mode, quality, landmarks, colliders, TraceContext.EMPTY, buffers);
		return new CharacterFrame(header, cloud);
	}

	// ---------------------------------------------------------------------------------------------
	// Rig: joint positions per pose
	// ---------------------------------------------------------------------------------------------

	/** Named joints plus per-hand and per-foot local frames. */
	public static final class Rig {
		public final Map<String, Vector3> joints = new LinkedHashMap<>();
		/** Direction the fingers point, per side ("left"/"right"). */
		public final Map<String, Vector3> fingerDirection = new LinkedHashMap<>();
		/** Palm normal (out of the palm), per side. */
		public final Map<String, Vector3> palmNormal = new LinkedHashMap<>();
		/** Foot "up" direction per side (rotates when the foot is lifted/turned). */
		public final Map<String, Vector3> footUp = new LinkedHashMap<>();

		Vector3 j(String name) {
			Vector3 v = joints.get(name);
			if (v == null) {
				throw new IllegalStateException("missing joint " + name);
			}
			return v;
		}

		void set(String name, double x, double y, double z) {
			joints.put(name, new Vector3(x, y, z));
		}

		void set(String name, Vector3 v) {
			joints.put(name, v);
		}
	}

	public static Rig rig(Pose pose) {
		Rig r = new Rig();
		// Head
		r.set("head_center", 0.00, 1.62, 0.02);
		r.set("nose", 0.00, 1.60, 0.13);
		r.set("left_eye_inner", 0.02, 1.64, 0.11);
		r.set("left_eye", 0.035, 1.64, 0.105);
		r.set("left_eye_outer", 0.05, 1.64, 0.095);
		r.set("right_eye_inner", -0.02, 1.64, 0.11);
		r.set("right_eye", -0.035, 1.64, 0.105);
		r.set("right_eye_outer", -0.05, 1.64, 0.095);
		r.set("left_ear", 0.08, 1.62, 0.02);
		r.set("right_ear", -0.08, 1.62, 0.02);
		r.set("mouth_left", 0.025, 1.555, 0.11);
		r.set("mouth_right", -0.025, 1.555, 0.11);
		// Torso
		r.set("left_shoulder", 0.19, 1.45, 0.00);
		r.set("right_shoulder", -0.19, 1.45, 0.00);
		r.set("left_hip", 0.10, 0.93, 0.00);
		r.set("right_hip", -0.10, 0.93, 0.00);
		r.set("pelvis_center", 0.00, 0.95, 0.00);
		// Arms (hanging)
		r.set("left_elbow", 0.25, 1.17, 0.02);
		r.set("left_wrist", 0.28, 0.92, 0.06);
		r.fingerDirection.put("left", new Vector3(0.05, -1.0, 0.1).normalize());
		r.palmNormal.put("left", new Vector3(-1.0, 0.0, 0.2).normalize());
		r.set("right_elbow", -0.25, 1.17, 0.02);
		r.set("right_wrist", -0.28, 0.92, 0.06);
		r.fingerDirection.put("right", new Vector3(-0.05, -1.0, 0.1).normalize());
		r.palmNormal.put("right", new Vector3(1.0, 0.0, 0.2).normalize());
		// Legs
		r.set("left_knee", 0.11, 0.50, 0.01);
		r.set("right_knee", -0.11, 0.50, 0.01);
		r.set("left_ankle", 0.12, 0.09, 0.00);
		r.set("right_ankle", -0.12, 0.09, 0.00);
		r.set("left_heel", 0.12, 0.03, -0.05);
		r.set("right_heel", -0.12, 0.03, -0.05);
		r.set("left_foot_index", 0.13, 0.02, 0.18);
		r.set("right_foot_index", -0.13, 0.02, 0.18);
		r.footUp.put("left", Vector3.UNIT_Y);
		r.footUp.put("right", Vector3.UNIT_Y);

		switch (pose) {
			case NEUTRAL -> {
			}
			case BENT_LEFT_ARM -> {
				// Elbow stays; forearm swings forward and up, hand open with palm facing the front camera.
				r.set("left_elbow", 0.24, 1.18, 0.03);
				r.set("left_wrist", 0.20, 1.32, 0.27);
				r.fingerDirection.put("left", new Vector3(-0.1, 0.9, 0.3).normalize());
				r.palmNormal.put("left", new Vector3(0.0, -0.3, 1.0).normalize());
			}
			case LIFTED_RIGHT_FOOT -> {
				// Right knee bends, foot lifts 25 cm and toes turn outward (to -X) by ~40 degrees.
				r.set("right_knee", -0.13, 0.55, 0.12);
				r.set("right_ankle", -0.14, 0.34, 0.02);
				double yaw = Math.toRadians(-40);
				Vector3 forward = new Vector3(Math.sin(yaw), 0, Math.cos(yaw)); // toes direction, turned outward
				Vector3 ankle = r.j("right_ankle");
				Vector3 heel = ankle.add(forward.scale(-0.05)).add(new Vector3(0, -0.06, 0));
				Vector3 toe = ankle.add(forward.scale(0.18)).add(new Vector3(0, -0.07, 0));
				r.set("right_heel", heel);
				r.set("right_foot_index", toe);
				r.footUp.put("right", Vector3.UNIT_Y);
			}
		}
		return r;
	}

	// ---------------------------------------------------------------------------------------------
	// Landmarks
	// ---------------------------------------------------------------------------------------------

	/** MediaPipe's 33 pose landmark names, in index order. */
	public static final List<String> BODY_POSE_NAMES = List.of(
			"nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
			"left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder", "right_shoulder", "left_elbow",
			"right_elbow", "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index", "right_index",
			"left_thumb", "right_thumb", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
			"left_heel", "right_heel", "left_foot_index", "right_foot_index");

	/** MediaPipe's 21 hand landmark names, in index order. */
	public static final List<String> HAND_NAMES = List.of(
			"wrist", "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip",
			"index_mcp", "index_pip", "index_dip", "index_tip",
			"middle_mcp", "middle_pip", "middle_dip", "middle_tip",
			"ring_mcp", "ring_pip", "ring_dip", "ring_tip",
			"pinky_mcp", "pinky_pip", "pinky_dip", "pinky_tip");

	/** Every canonical landmark name in the order the fixture emits them. */
	public static List<String> canonicalLandmarkNames() {
		List<String> names = new ArrayList<>();
		BODY_POSE_NAMES.forEach(n -> names.add("body." + n));
		names.add("body.pelvis_center");
		names.add("body.head_center");
		for (String side : List.of("left", "right")) {
			HAND_NAMES.forEach(n -> names.add("hand." + side + "." + n));
		}
		return List.copyOf(names);
	}

	public static List<LandmarkDto> landmarks(Rig rig) {
		Map<String, Vector3> hands = new LinkedHashMap<>();
		for (String side : List.of("left", "right")) {
			hands.putAll(handLandmarks(rig, side));
		}
		// Pose landmarks 17-22 are the coarse hand tips; derive them from the detailed hand model.
		for (String side : List.of("left", "right")) {
			rig.set(side + "_pinky", hands.get("hand." + side + ".pinky_mcp"));
			rig.set(side + "_index", hands.get("hand." + side + ".index_mcp"));
			rig.set(side + "_thumb", hands.get("hand." + side + ".thumb_ip"));
		}

		List<LandmarkDto> out = new ArrayList<>();
		for (String name : canonicalLandmarkNames()) {
			Vector3 p;
			LandmarkSource source;
			List<String> observedBy;
			if (name.startsWith("hand.")) {
				p = hands.get(name);
				source = LandmarkSource.TRIANGULATED;
				observedBy = List.of(FRONT_DEVICE, SIDE_DEVICE);
			} else {
				String joint = name.substring("body.".length());
				p = rig.j(joint);
				source = joint.endsWith("_center") ? LandmarkSource.DERIVED : LandmarkSource.DEPTH_NEIGHBORHOOD;
				observedBy = joint.endsWith("_center") ? List.of() : List.of(FRONT_DEVICE, SIDE_DEVICE);
			}
			double confidence = 0.80 + 0.15 * stableUnit(name);
			out.add(new LandmarkDto(name, Optional.of(p), true, source, Optional.of(round(confidence)),
					Optional.of(round(0.85 + 0.1 * stableUnit(name + "v"))), observedBy,
					source == LandmarkSource.DERIVED ? Optional.empty() : Optional.of(round(1.0 + 2.0 * stableUnit(name + "e")))));
		}
		return out;
	}

	/** 21 hand landmarks laid out on a flat open hand starting at the wrist. */
	static Map<String, Vector3> handLandmarks(Rig rig, String side) {
		Vector3 wrist = rig.j(side + "_wrist");
		Vector3 f = rig.fingerDirection.get(side);
		Vector3 n = rig.palmNormal.get(side);
		n = n.sub(f.scale(n.dot(f))).normalize();
		Vector3 across = f.cross(n).normalize();
		// Thumb side: for the left hand (palm facing -X in neutral) the thumb is toward the body (-X).
		double thumbSign = side.equals("left") ? -1 : 1;
		if (across.x() * thumbSign < 0) {
			across = across.negate();
		}
		Map<String, Vector3> m = new LinkedHashMap<>();
		String prefix = "hand." + side + ".";
		m.put(prefix + "wrist", wrist);
		// Thumb: angled ~45 degrees between "across" and "forward".
		Vector3 thumbDir = across.scale(0.75).add(f.scale(0.65)).normalize();
		m.put(prefix + "thumb_cmc", wrist.add(thumbDir.scale(0.03)));
		m.put(prefix + "thumb_mcp", wrist.add(thumbDir.scale(0.065)));
		m.put(prefix + "thumb_ip", wrist.add(thumbDir.scale(0.095)));
		m.put(prefix + "thumb_tip", wrist.add(thumbDir.scale(0.12)));
		String[] fingers = {"index", "middle", "ring", "pinky"};
		double[] lateral = {0.03, 0.01, -0.01, -0.03};
		double[] lengths = {0.075, 0.08, 0.075, 0.06};
		for (int i = 0; i < fingers.length; i++) {
			Vector3 mcp = wrist.add(f.scale(0.085)).add(across.scale(lateral[i]));
			double len = lengths[i];
			m.put(prefix + fingers[i] + "_mcp", mcp);
			m.put(prefix + fingers[i] + "_pip", mcp.add(f.scale(len * 0.45)));
			m.put(prefix + fingers[i] + "_dip", mcp.add(f.scale(len * 0.75)));
			m.put(prefix + fingers[i] + "_tip", mcp.add(f.scale(len)));
		}
		return m;
	}

	// ---------------------------------------------------------------------------------------------
	// Colliders
	// ---------------------------------------------------------------------------------------------

	public static List<ColliderDto> colliders(Rig rig) {
		List<ColliderDto> out = new ArrayList<>();
		out.add(observed("head", BodyPart.HEAD, new Sphere(rig.j("head_center"), 0.11), 0.91));

		Vector3 shoulderMid = rig.j("left_shoulder").lerp(rig.j("right_shoulder"), 0.5);
		Vector3 hipMid = rig.j("left_hip").lerp(rig.j("right_hip"), 0.5);
		out.add(observed("torso", BodyPart.TORSO, new Capsule(shoulderMid.add(new Vector3(0, -0.05, 0)), hipMid.add(new Vector3(0, 0.12, 0)), 0.16), 0.88));
		out.add(observed("pelvis", BodyPart.PELVIS, new Capsule(rig.j("left_hip"), rig.j("right_hip"), 0.13), 0.84));

		for (String side : List.of("left", "right")) {
			BodyPart upperArm = side.equals("left") ? BodyPart.LEFT_UPPER_ARM : BodyPart.RIGHT_UPPER_ARM;
			BodyPart forearm = side.equals("left") ? BodyPart.LEFT_FOREARM : BodyPart.RIGHT_FOREARM;
			BodyPart hand = side.equals("left") ? BodyPart.LEFT_HAND : BodyPart.RIGHT_HAND;
			BodyPart thigh = side.equals("left") ? BodyPart.LEFT_THIGH : BodyPart.RIGHT_THIGH;
			BodyPart shin = side.equals("left") ? BodyPart.LEFT_SHIN : BodyPart.RIGHT_SHIN;
			BodyPart foot = side.equals("left") ? BodyPart.LEFT_FOOT : BodyPart.RIGHT_FOOT;

			out.add(observed("arm." + side + ".upper", upperArm, new Capsule(rig.j(side + "_shoulder"), rig.j(side + "_elbow"), 0.05), 0.85));
			out.add(observed("arm." + side + ".forearm", forearm, new Capsule(rig.j(side + "_elbow"), rig.j(side + "_wrist"), 0.042), 0.82));
			out.add(observed("hand." + side, hand, handBox(rig, side), 0.78));
			out.add(observed("leg." + side + ".thigh", thigh, new Capsule(rig.j(side + "_hip"), rig.j(side + "_knee"), 0.08), 0.86));
			out.add(observed("leg." + side + ".shin", shin, new Capsule(rig.j(side + "_knee"), rig.j(side + "_ankle"), 0.055), 0.83));
			out.add(observed("foot." + side, foot, footBox(rig, side), 0.80));
		}
		return out;
	}

	/** Open-hand box: local Z along the fingers, local Y along the palm normal. Distinct from the forearm. */
	static Obb handBox(Rig rig, String side) {
		Vector3 wrist = rig.j(side + "_wrist");
		Vector3 f = rig.fingerDirection.get(side);
		Vector3 center = wrist.add(f.scale(0.095));
		return Obb.fromForwardAndUp(center, f, rig.palmNormal.get(side), new Vector3(0.045, 0.018, 0.095));
	}

	/**
	 * Shoe-sized box from heel to toe, oriented along the observed foot direction. The sole sits ~5 mm below
	 * the heel/toe landmark line so a planted foot actually meets the floor it is standing on.
	 */
	static Obb footBox(Rig rig, String side) {
		Vector3 heel = rig.j(side + "_heel");
		Vector3 toe = rig.j(side + "_foot_index");
		Vector3 forward = toe.sub(heel);
		Vector3 up = rig.footUp.get(side);
		Vector3 center = heel.lerp(toe, 0.5).add(up.scale(0.015));
		double halfLength = forward.length() / 2 + 0.015;
		return Obb.fromForwardAndUp(center, forward, up, new Vector3(0.048, 0.045, halfLength));
	}

	private static ColliderDto observed(String id, BodyPart part, Shape shape, double quality) {
		ColliderType type = switch (shape) {
			case Sphere s -> ColliderType.SPHERE;
			case Capsule c -> ColliderType.CAPSULE;
			case Obb o -> ColliderType.OBB;
		};
		return new ColliderDto(id, part, type, true, FitSource.OBSERVED, Optional.of(quality), Optional.of(shape));
	}

	// ---------------------------------------------------------------------------------------------
	// Point cloud
	// ---------------------------------------------------------------------------------------------

	/** Base color per part: skin, shirt, trousers, shoes. */
	static int[] baseColor(BodyPart part) {
		return switch (part) {
			case HEAD, LEFT_HAND, RIGHT_HAND, LEFT_FOREARM, RIGHT_FOREARM -> new int[] {224, 172, 105};
			case TORSO, PELVIS, LEFT_UPPER_ARM, RIGHT_UPPER_ARM -> new int[] {46, 96, 204};
			case LEFT_THIGH, RIGHT_THIGH, LEFT_SHIN, RIGHT_SHIN -> new int[] {52, 54, 66};
			case LEFT_FOOT, RIGHT_FOOT -> new int[] {28, 24, 24};
		};
	}

	/** Samples {@code pointCount} surface points across all valid colliders, weighted by surface area. */
	public static PointCloud cloud(List<ColliderDto> colliders, int pointCount) {
		Random random = new Random(CLOUD_SEED);
		List<ColliderDto> valid = colliders.stream().filter(ColliderDto::valid).toList();
		double totalArea = valid.stream().mapToDouble(c -> surfaceArea(c.geometry().get())).sum();
		PointCloud.Builder builder = new PointCloud.Builder(pointCount);
		int emitted = 0;
		Vector3 light = new Vector3(0.3, 0.8, 0.55).normalize();
		for (int i = 0; i < valid.size(); i++) {
			ColliderDto c = valid.get(i);
			Shape shape = c.geometry().get();
			int n = i == valid.size() - 1
					? pointCount - emitted
					: (int) Math.round(pointCount * surfaceArea(shape) / totalArea);
			int[] base = baseColor(c.bodyPart());
			for (int k = 0; k < n; k++) {
				Vector3[] pn = sampleSurface(shape, random);
				double shade = 0.7 + 0.3 * Math.max(0, pn[1].dot(light));
				builder.add(pn[0].x(), pn[0].y(), pn[0].z(),
						clamp(base[0] * shade), clamp(base[1] * shade), clamp(base[2] * shade), 255);
			}
			emitted += n;
		}
		return builder.build();
	}

	static double surfaceArea(Shape shape) {
		return switch (shape) {
			case Sphere s -> 4 * Math.PI * s.radius() * s.radius();
			case Capsule c -> 2 * Math.PI * c.radius() * c.a().distanceTo(c.b()) + 4 * Math.PI * c.radius() * c.radius();
			case Obb o -> 8 * (o.halfExtents().x() * o.halfExtents().y() + o.halfExtents().y() * o.halfExtents().z()
					+ o.halfExtents().x() * o.halfExtents().z());
		};
	}

	/** Returns {position, outwardNormal} sampled uniformly over the surface. */
	static Vector3[] sampleSurface(Shape shape, Random random) {
		switch (shape) {
			case Sphere s -> {
				Vector3 dir = randomDirection(random);
				return new Vector3[] {s.center().add(dir.scale(s.radius())), dir};
			}
			case Capsule c -> {
				double length = c.a().distanceTo(c.b());
				double sideArea = 2 * Math.PI * c.radius() * length;
				double capArea = 4 * Math.PI * c.radius() * c.radius();
				Vector3 axis = length > 1e-9 ? c.b().sub(c.a()).normalize() : Vector3.UNIT_Y;
				if (random.nextDouble() * (sideArea + capArea) < sideArea) {
					double t = random.nextDouble();
					Vector3 radial = perpendicular(axis, random.nextDouble() * 2 * Math.PI);
					return new Vector3[] {c.a().lerp(c.b(), t).add(radial.scale(c.radius())), radial};
				}
				Vector3 dir = randomDirection(random);
				Vector3 end = dir.dot(axis) >= 0 ? c.b() : c.a();
				return new Vector3[] {end.add(dir.scale(c.radius())), dir};
			}
			case Obb o -> {
				Vector3 h = o.halfExtents();
				double[] faceArea = {h.y() * h.z(), h.x() * h.z(), h.x() * h.y()};
				double total = 2 * (faceArea[0] + faceArea[1] + faceArea[2]);
				double pick = random.nextDouble() * total;
				int axis = 0;
				for (; axis < 2; axis++) {
					if (pick < 2 * faceArea[axis]) {
						break;
					}
					pick -= 2 * faceArea[axis];
				}
				double sign = random.nextBoolean() ? 1 : -1;
				double[] local = new double[3];
				for (int i = 0; i < 3; i++) {
					local[i] = i == axis ? sign * h.component(i) : (random.nextDouble() * 2 - 1) * h.component(i);
				}
				Vector3 normal = o.axes().get(axis).scale(sign);
				return new Vector3[] {o.toWorld(new Vector3(local[0], local[1], local[2])), normal};
			}
		}
	}

	private static Vector3 randomDirection(Random random) {
		double z = random.nextDouble() * 2 - 1;
		double phi = random.nextDouble() * 2 * Math.PI;
		double r = Math.sqrt(Math.max(0, 1 - z * z));
		return new Vector3(r * Math.cos(phi), z, r * Math.sin(phi));
	}

	private static Vector3 perpendicular(Vector3 axis, double angle) {
		Vector3 helper = Math.abs(axis.y()) < 0.9 ? Vector3.UNIT_Y : Vector3.UNIT_X;
		Vector3 u = axis.cross(helper).normalize();
		Vector3 v = axis.cross(u).normalize();
		return u.scale(Math.cos(angle)).add(v.scale(Math.sin(angle)));
	}

	private static int clamp(double v) {
		return (int) Math.max(0, Math.min(255, Math.round(v)));
	}

	/** Deterministic pseudo-random unit value derived from a name (for plausible per-landmark metrics). */
	private static double stableUnit(String name) {
		int h = name.hashCode();
		return ((h & 0x7fffffff) % 1000) / 1000.0;
	}

	private static double round(double v) {
		return Math.round(v * 1000.0) / 1000.0;
	}
}
