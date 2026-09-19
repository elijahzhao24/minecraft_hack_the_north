package dev.humancraft.client.render;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.vertex.BufferBuilder;
import com.mojang.blaze3d.vertex.ByteBufferBuilder;
import com.mojang.blaze3d.vertex.DefaultVertexFormat;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexBuffer;
import com.mojang.blaze3d.vertex.VertexFormat;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.LandmarkDto;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import dev.humancraft.model.PointCloud;
import dev.humancraft.model.WorldSnapshot;
import dev.humancraft.telemetry.Telemetry;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderContext;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderEvents;
import net.minecraft.client.renderer.GameRenderer;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Owns three persistent GPU buffers for the acknowledged snapshot. The cloud is uploaded as one batch of
 * depth-tested tiny cubes (the portable fallback for unreliable GL point sizes); skeleton and colliders are
 * separate line batches so either debug layer can be toggled without rebuilding the cloud.
 */
public final class HumanRenderer implements AutoCloseable {
	private static final int VERTEX_BYTES = 16;
	private static final int CIRCLE_SEGMENTS = 20;

	private static final String[][] BODY_EDGES = {
			{"body.left_shoulder", "body.right_shoulder"},
			{"body.left_shoulder", "body.left_elbow"}, {"body.left_elbow", "body.left_wrist"},
			{"body.right_shoulder", "body.right_elbow"}, {"body.right_elbow", "body.right_wrist"},
			{"body.left_shoulder", "body.left_hip"}, {"body.right_shoulder", "body.right_hip"},
			{"body.left_hip", "body.right_hip"}, {"body.left_hip", "body.left_knee"},
			{"body.left_knee", "body.left_ankle"}, {"body.left_ankle", "body.left_heel"},
			{"body.left_heel", "body.left_foot_index"}, {"body.right_hip", "body.right_knee"},
			{"body.right_knee", "body.right_ankle"}, {"body.right_ankle", "body.right_heel"},
			{"body.right_heel", "body.right_foot_index"}, {"body.head_center", "body.nose"}
	};

	private final HumanCraftConfig config;
	private VertexBuffer cloudBuffer;
	private VertexBuffer skeletonBuffer;
	private VertexBuffer colliderBuffer;
	private long uploadedFrame = -1;
	private boolean renderFailed;

	public HumanRenderer(HumanCraftConfig config) {
		this.config = config;
	}

	public void register() {
		WorldRenderEvents.LAST.register(this::render);
	}

	public long uploadedFrame() {
		return uploadedFrame;
	}

	/** Must run on the render thread. Builds all replacement buffers before releasing the active set. */
	public void activate(WorldSnapshot snapshot) {
		RenderSystem.assertOnRenderThread();
		try (Telemetry.Span span = Telemetry.continueTransaction("client.gpu_upload", "hmc.render.upload", snapshot.trace())) {
			long start = System.nanoTime();
			VertexBuffer nextCloud = buildCloud(snapshot);
			VertexBuffer nextSkeleton = buildSkeleton(snapshot);
			VertexBuffer nextColliders = buildColliders(snapshot);
			closeBuffer(cloudBuffer);
			closeBuffer(skeletonBuffer);
			closeBuffer(colliderBuffer);
			cloudBuffer = nextCloud;
			skeletonBuffer = nextSkeleton;
			colliderBuffer = nextColliders;
			uploadedFrame = snapshot.frameId();
			renderFailed = false;
			span.data("frame_id", snapshot.frameId()).data("points", snapshot.stageCloud().count())
					.measurement("cpu_upload_ms", (System.nanoTime() - start) / 1_000_000.0);
		}
	}

	public void clear() {
		if (!RenderSystem.isOnRenderThread()) {
			RenderSystem.recordRenderCall(this::clear);
			return;
		}
		closeBuffer(cloudBuffer);
		closeBuffer(skeletonBuffer);
		closeBuffer(colliderBuffer);
		cloudBuffer = null;
		skeletonBuffer = null;
		colliderBuffer = null;
		uploadedFrame = -1;
	}

	private VertexBuffer buildCloud(WorldSnapshot snapshot) {
		PointCloud points = snapshot.stageCloud();
		if (points.count() == 0) {
			return null;
		}
		double half = 0.0015 * config.pointSize * snapshot.transform().blocksPerMeter();
		int capacity = Math.max(1024, points.count() * 24 * VERTEX_BYTES);
		try (ByteBufferBuilder bytes = new ByteBufferBuilder(capacity)) {
			BufferBuilder builder = new BufferBuilder(bytes, VertexFormat.Mode.QUADS, DefaultVertexFormat.POSITION_COLOR);
			for (int i = 0; i < points.count(); i++) {
				Vector3 p = snapshot.transform().point(points.position(i));
				int r = points.r(i);
				int g = points.g(i);
				int b = points.b(i);
				if (config.sourceColors) {
					// The v1 cloud has no source-mask buffer. This stage-side split is explicitly a registration aid.
					boolean leftStageHalf = points.x(i) >= 0;
					r = leftStageHalf ? 40 : 230;
					g = leftStageHalf ? 210 : 50;
					b = leftStageHalf ? 240 : 210;
				}
				cube(builder, p, half, r, g, b, points.a(i));
			}
			return upload(builder);
		}
	}

	private VertexBuffer buildSkeleton(WorldSnapshot snapshot) {
		Map<String, Vector3> positions = new HashMap<>();
		for (LandmarkDto landmark : snapshot.landmarks()) {
			if (landmark.valid() && landmark.position().isPresent()) {
				positions.put(landmark.name(), landmark.position().get());
			}
		}
		try (ByteBufferBuilder bytes = new ByteBufferBuilder(Math.max(4096, positions.size() * 12 * VERTEX_BYTES))) {
			BufferBuilder builder = new BufferBuilder(bytes, VertexFormat.Mode.DEBUG_LINES, DefaultVertexFormat.POSITION_COLOR);
			for (String[] edge : BODY_EDGES) {
				lineIfPresent(builder, positions, edge[0], edge[1], 80, 255, 100, 255);
			}
			for (String side : List.of("left", "right")) {
				String prefix = "hand." + side + ".";
				for (String finger : List.of("thumb", "index", "middle", "ring", "pinky")) {
					String[] joints = finger.equals("thumb")
							? new String[] {"wrist", "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip"}
							: new String[] {"wrist", finger + "_mcp", finger + "_pip", finger + "_dip", finger + "_tip"};
					for (int i = 0; i + 1 < joints.length; i++) {
						lineIfPresent(builder, positions, prefix + joints[i], prefix + joints[i + 1], 255, 220, 60, 255);
					}
				}
			}
			return uploadOrNull(builder);
		}
	}

	private VertexBuffer buildColliders(WorldSnapshot snapshot) {
		try (ByteBufferBuilder bytes = new ByteBufferBuilder(Math.max(8192, snapshot.colliders().size() * 256 * VERTEX_BYTES))) {
			BufferBuilder builder = new BufferBuilder(bytes, VertexFormat.Mode.DEBUG_LINES, DefaultVertexFormat.POSITION_COLOR);
			for (ColliderDto collider : snapshot.colliders()) {
				if (!collider.valid() || collider.geometry().isEmpty()) {
					continue;
				}
				int[] color = colliderColor(collider.bodyPart());
				shape(builder, collider.geometry().get(), color[0], color[1], color[2], 255);
			}
			return uploadOrNull(builder);
		}
	}

	private void render(WorldRenderContext context) {
		if (uploadedFrame < 0 || context.matrixStack() == null) {
			return;
		}
		try {
			PoseStack matrices = context.matrixStack();
			Vec3 camera = context.camera().getPosition();
			matrices.pushPose();
			matrices.translate(-camera.x, -camera.y, -camera.z);
			RenderSystem.enableDepthTest();
			if (config.showCloud) {
				draw(cloudBuffer, matrices, context);
			}
			if (config.showSkeleton) {
				draw(skeletonBuffer, matrices, context);
			}
			if (config.showColliders) {
				draw(colliderBuffer, matrices, context);
			}
			VertexBuffer.unbind();
			matrices.popPose();
		} catch (RuntimeException e) {
			if (!renderFailed) {
				renderFailed = true;
				Telemetry.captureException(e, "client.renderer.draw");
			}
		}
	}

	private static void draw(VertexBuffer buffer, PoseStack matrices, WorldRenderContext context) {
		if (buffer != null && !buffer.isInvalid()) {
			buffer.bind();
			buffer.drawWithShader(matrices.last().pose(), context.projectionMatrix(), GameRenderer.getPositionColorShader());
		}
	}

	private static VertexBuffer upload(BufferBuilder builder) {
		try (MeshData mesh = builder.buildOrThrow()) {
			VertexBuffer buffer = new VertexBuffer(VertexBuffer.Usage.STATIC);
			buffer.bind();
			buffer.upload(mesh);
			VertexBuffer.unbind();
			return buffer;
		}
	}

	private static VertexBuffer uploadOrNull(BufferBuilder builder) {
		MeshData mesh = builder.build();
		if (mesh == null) {
			return null;
		}
		try (mesh) {
			VertexBuffer buffer = new VertexBuffer(VertexBuffer.Usage.STATIC);
			buffer.bind();
			buffer.upload(mesh);
			VertexBuffer.unbind();
			return buffer;
		}
	}

	private static void cube(BufferBuilder b, Vector3 p, double h, int r, int g, int blue, int a) {
		double x0 = p.x() - h, x1 = p.x() + h;
		double y0 = p.y() - h, y1 = p.y() + h;
		double z0 = p.z() - h, z1 = p.z() + h;
		quad(b, x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0, r, g, blue, a);
		quad(b, x1, y0, z1, x0, y0, z1, x0, y1, z1, x1, y1, z1, r, g, blue, a);
		quad(b, x0, y0, z1, x0, y0, z0, x0, y1, z0, x0, y1, z1, r, g, blue, a);
		quad(b, x1, y0, z0, x1, y0, z1, x1, y1, z1, x1, y1, z0, r, g, blue, a);
		quad(b, x0, y1, z0, x1, y1, z0, x1, y1, z1, x0, y1, z1, r, g, blue, a);
		quad(b, x0, y0, z1, x1, y0, z1, x1, y0, z0, x0, y0, z0, r, g, blue, a);
	}

	private static void quad(BufferBuilder b, double x0, double y0, double z0, double x1, double y1, double z1,
			double x2, double y2, double z2, double x3, double y3, double z3, int r, int g, int blue, int a) {
		vertex(b, new Vector3(x0, y0, z0), r, g, blue, a);
		vertex(b, new Vector3(x1, y1, z1), r, g, blue, a);
		vertex(b, new Vector3(x2, y2, z2), r, g, blue, a);
		vertex(b, new Vector3(x3, y3, z3), r, g, blue, a);
	}

	private static void shape(BufferBuilder builder, Shape shape, int r, int g, int b, int a) {
		switch (shape) {
			case Sphere sphere -> {
				circle(builder, sphere.center(), Vector3.UNIT_X, Vector3.UNIT_Y, sphere.radius(), r, g, b, a);
				circle(builder, sphere.center(), Vector3.UNIT_X, Vector3.UNIT_Z, sphere.radius(), r, g, b, a);
				circle(builder, sphere.center(), Vector3.UNIT_Y, Vector3.UNIT_Z, sphere.radius(), r, g, b, a);
			}
			case Capsule capsule -> capsule(builder, capsule, r, g, b, a);
			case Obb obb -> obb(builder, obb, r, g, b, a);
		}
	}

	private static void capsule(BufferBuilder builder, Capsule capsule, int r, int g, int b, int alpha) {
		Vector3 axis = capsule.b().sub(capsule.a()).normalize();
		Vector3 helper = Math.abs(axis.y()) < 0.9 ? Vector3.UNIT_Y : Vector3.UNIT_X;
		Vector3 u = axis.cross(helper).normalize();
		Vector3 v = axis.cross(u).normalize();
		circle(builder, capsule.a(), u, v, capsule.radius(), r, g, b, alpha);
		circle(builder, capsule.b(), u, v, capsule.radius(), r, g, b, alpha);
		for (int i = 0; i < 8; i++) {
			double angle = i * Math.PI / 4;
			Vector3 radial = u.scale(Math.cos(angle)).add(v.scale(Math.sin(angle))).scale(capsule.radius());
			line(builder, capsule.a().add(radial), capsule.b().add(radial), r, g, b, alpha);
		}
	}

	private static void obb(BufferBuilder builder, Obb obb, int r, int g, int b, int alpha) {
		Vector3[] corners = new Vector3[8];
		for (int i = 0; i < 8; i++) {
			corners[i] = obb.toWorld(new Vector3(
					(i & 1) == 0 ? -obb.halfExtents().x() : obb.halfExtents().x(),
					(i & 2) == 0 ? -obb.halfExtents().y() : obb.halfExtents().y(),
					(i & 4) == 0 ? -obb.halfExtents().z() : obb.halfExtents().z()));
		}
		for (int i = 0; i < 8; i++) {
			for (int bit : new int[] {1, 2, 4}) {
				if ((i & bit) == 0) {
					line(builder, corners[i], corners[i | bit], r, g, b, alpha);
				}
			}
		}
	}

	private static void circle(BufferBuilder builder, Vector3 center, Vector3 u, Vector3 v, double radius,
			int r, int g, int b, int a) {
		for (int i = 0; i < CIRCLE_SEGMENTS; i++) {
			double first = i * Math.PI * 2 / CIRCLE_SEGMENTS;
			double second = (i + 1) * Math.PI * 2 / CIRCLE_SEGMENTS;
			Vector3 p = center.add(u.scale(Math.cos(first) * radius)).add(v.scale(Math.sin(first) * radius));
			Vector3 q = center.add(u.scale(Math.cos(second) * radius)).add(v.scale(Math.sin(second) * radius));
			line(builder, p, q, r, g, b, a);
		}
	}

	private static void lineIfPresent(BufferBuilder builder, Map<String, Vector3> positions, String a, String b,
			int r, int g, int blue, int alpha) {
		Vector3 p = positions.get(a);
		Vector3 q = positions.get(b);
		if (p != null && q != null) {
			line(builder, p, q, r, g, blue, alpha);
		}
	}

	private static void line(BufferBuilder builder, Vector3 a, Vector3 b, int r, int g, int blue, int alpha) {
		vertex(builder, a, r, g, blue, alpha);
		vertex(builder, b, r, g, blue, alpha);
	}

	private static void vertex(BufferBuilder builder, Vector3 p, int r, int g, int b, int a) {
		builder.addVertex((float) p.x(), (float) p.y(), (float) p.z()).setColor(r, g, b, a);
	}

	private static int[] colliderColor(BodyPart part) {
		return switch (part) {
			case LEFT_HAND, RIGHT_HAND -> new int[] {255, 80, 220};
			case LEFT_FOOT, RIGHT_FOOT -> new int[] {255, 150, 30};
			case HEAD -> new int[] {255, 230, 80};
			case TORSO, PELVIS -> new int[] {50, 180, 255};
			default -> new int[] {100, 255, 140};
		};
	}

	private static void closeBuffer(VertexBuffer buffer) {
		if (buffer != null) {
			buffer.close();
		}
	}

	@Override
	public void close() {
		clear();
	}
}
