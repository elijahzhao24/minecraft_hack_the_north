package dev.humancraft.fixture;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dev.humancraft.contract.CharacterFrameEncoder;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.Mode;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.model.PointCloud;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;

/**
 * Writes the frozen fixtures under {@code contracts/fixtures}: one golden {@code .hmc} per pose plus a decoded
 * summary, the malformed set with expected rejection codes, the canonical landmark name list, and SHA-256 sums.
 * Run with {@code ./gradlew writeFixtures}.
 */
public final class FixtureWriter {
	private FixtureWriter() {}

	public static void main(String[] args) throws IOException {
		Path dir = Path.of(args.length > 0 ? args[0] : "../contracts/fixtures");
		Files.createDirectories(dir);
		StringBuilder sums = new StringBuilder();

		for (SyntheticHuman.Pose pose : SyntheticHuman.Pose.values()) {
			String stem = "character_frame_" + pose.name().toLowerCase(Locale.ROOT);
			CharacterFrame frame = SyntheticHuman.frame(pose, 42, Mode.SNAPSHOT);
			byte[] bytes = CharacterFrameEncoder.encode(frame);
			write(dir, stem + ".hmc", bytes, sums);
			write(dir, stem + ".header.json", pretty(frame.header().toJson()).getBytes(StandardCharsets.UTF_8), sums);
			write(dir, stem + ".expected.json", pretty(summary(frame, bytes)).getBytes(StandardCharsets.UTF_8), sums);
		}

		Path malformedDir = dir.resolve("malformed");
		Files.createDirectories(malformedDir);
		JsonObject expectedCodes = new JsonObject();
		for (Map.Entry<String, MalformedFixtures.Case> e : MalformedFixtures.all().entrySet()) {
			write(dir, "malformed/" + e.getKey() + ".hmc", e.getValue().bytes(), sums);
			expectedCodes.addProperty(e.getKey(), e.getValue().expectedCode());
		}
		write(dir, "malformed_expected_codes.json", pretty(expectedCodes).getBytes(StandardCharsets.UTF_8), sums);

		JsonArray names = new JsonArray();
		SyntheticHuman.canonicalLandmarkNames().forEach(names::add);
		JsonObject landmarkNames = new JsonObject();
		landmarkNames.addProperty("schema", "hmc.landmark_names");
		landmarkNames.addProperty("schema_version", 1);
		landmarkNames.add("names", names);
		write(dir, "landmark_names.json", pretty(landmarkNames).getBytes(StandardCharsets.UTF_8), sums);

		Files.writeString(dir.resolve("SHA256SUMS"), sums.toString(), StandardCharsets.UTF_8);
		System.out.println("Wrote fixtures to " + dir.toAbsolutePath());
	}

	/** Decoded values a Python/Swift implementation must reproduce from the same bytes. */
	static JsonObject summary(CharacterFrame frame, byte[] bytes) {
		JsonObject o = new JsonObject();
		o.addProperty("sha256", sha256(bytes));
		o.addProperty("total_bytes", bytes.length);
		o.addProperty("frame_id", frame.frameId());
		o.addProperty("session_id", frame.header().sessionId().toString());
		o.addProperty("calibration_id", frame.header().calibrationId().toString());
		o.addProperty("mode", frame.header().mode().wireName());
		o.addProperty("point_count", frame.cloud().count());
		o.addProperty("landmark_count", frame.header().landmarks().size());
		o.addProperty("collider_count", frame.header().colliders().size());
		PointCloud cloud = frame.cloud();
		JsonArray samples = new JsonArray();
		for (int i : new int[] {0, 1, cloud.count() / 2, cloud.count() - 1}) {
			JsonObject p = new JsonObject();
			p.addProperty("index", i);
			p.addProperty("x", cloud.x(i));
			p.addProperty("y", cloud.y(i));
			p.addProperty("z", cloud.z(i));
			p.addProperty("rgba", String.format("%02x%02x%02x%02x", cloud.r(i), cloud.g(i), cloud.b(i), cloud.a(i)));
			samples.add(p);
		}
		o.add("sample_points", samples);
		var bounds = cloud.bounds();
		JsonArray min = new JsonArray();
		JsonArray max = new JsonArray();
		for (double v : bounds.min().toArray()) {
			min.add(v);
		}
		for (double v : bounds.max().toArray()) {
			max.add(v);
		}
		o.add("cloud_min_stage_m", min);
		o.add("cloud_max_stage_m", max);
		JsonArray colliders = new JsonArray();
		for (ColliderDto c : frame.header().colliders()) {
			JsonObject co = new JsonObject();
			co.addProperty("id", c.id());
			co.addProperty("body_part", c.bodyPart().wireName());
			co.addProperty("type", c.type().wireName());
			co.addProperty("valid", c.valid());
			colliders.add(co);
		}
		o.add("colliders", colliders);
		return o;
	}

	private static void write(Path dir, String relative, byte[] bytes, StringBuilder sums) throws IOException {
		Files.write(dir.resolve(relative), bytes);
		sums.append(sha256(bytes)).append("  ").append(relative).append('\n');
	}

	static String sha256(byte[] bytes) {
		try {
			return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
		} catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}

	private static String pretty(JsonObject o) {
		return new GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(o) + "\n";
	}
}
