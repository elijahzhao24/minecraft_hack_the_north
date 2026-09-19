package dev.humancraft.network;

import dev.humancraft.contract.BodyPart;
import dev.humancraft.contract.ColliderDto;
import dev.humancraft.contract.ColliderType;
import dev.humancraft.contract.FitSource;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.ProtocolLimits;
import dev.humancraft.geometry.Capsule;
import dev.humancraft.geometry.Obb;
import dev.humancraft.geometry.Shape;
import dev.humancraft.geometry.Sphere;
import dev.humancraft.geometry.Vector3;
import net.minecraft.network.FriendlyByteBuf;

import java.util.List;
import java.util.Optional;

/**
 * Unvalidated packet form of a collider. Decoding never throws so a hostile or buggy client cannot kill the
 * connection with a codec error; {@link #toDto()} performs the validation on the server thread and the
 * failure is reported back as a rejected acknowledgement.
 */
public record WireCollider(
		String id,
		String bodyPart,
		String type,
		boolean valid,
		String fitSource,
		float quality,
		double[] numbers) {

	private static final int MAX_NUMBERS = 15;

	public static WireCollider of(ColliderDto dto) {
		double[] numbers = switch (dto.geometry().orElse(null)) {
			case null -> new double[0];
			case Sphere s -> new double[] {s.center().x(), s.center().y(), s.center().z(), s.radius()};
			case Capsule c -> new double[] {c.a().x(), c.a().y(), c.a().z(), c.b().x(), c.b().y(), c.b().z(), c.radius()};
			case Obb o -> {
				double[] n = new double[15];
				n[0] = o.center().x();
				n[1] = o.center().y();
				n[2] = o.center().z();
				for (int i = 0; i < 3; i++) {
					Vector3 a = o.axes().get(i);
					n[3 + i * 3] = a.x();
					n[4 + i * 3] = a.y();
					n[5 + i * 3] = a.z();
				}
				n[12] = o.halfExtents().x();
				n[13] = o.halfExtents().y();
				n[14] = o.halfExtents().z();
				yield n;
			}
		};
		return new WireCollider(dto.id(), dto.bodyPart().wireName(), dto.type().wireName(), dto.valid(),
				dto.fitSource().wireName(), dto.quality().map(Double::floatValue).orElse(Float.NaN), numbers);
	}

	public ColliderDto toDto() {
		BodyPart part = dev.humancraft.contract.WireEnum.fromWire(BodyPart.class, bodyPart);
		ColliderType colliderType = dev.humancraft.contract.WireEnum.fromWire(ColliderType.class, type);
		FitSource fit = dev.humancraft.contract.WireEnum.fromWire(FitSource.class, fitSource);
		Optional<Double> q = Float.isNaN(quality) ? Optional.empty() : Optional.of((double) quality);
		Optional<Shape> geometry;
		try {
			geometry = switch (numbers.length) {
				case 0 -> Optional.empty();
				case 4 -> Optional.of(new Sphere(new Vector3(numbers[0], numbers[1], numbers[2]), numbers[3]));
				case 7 -> Optional.of(new Capsule(new Vector3(numbers[0], numbers[1], numbers[2]),
						new Vector3(numbers[3], numbers[4], numbers[5]), numbers[6]));
				case 15 -> Optional.of(new Obb(new Vector3(numbers[0], numbers[1], numbers[2]),
						List.of(new Vector3(numbers[3], numbers[4], numbers[5]),
								new Vector3(numbers[6], numbers[7], numbers[8]),
								new Vector3(numbers[9], numbers[10], numbers[11])),
						new Vector3(numbers[12], numbers[13], numbers[14])));
				default -> throw new ProtocolException(ProtocolException.INVALID_MESSAGE,
						"collider '" + id + "' has " + numbers.length + " geometry numbers");
			};
		} catch (IllegalArgumentException e) {
			throw new ProtocolException(ProtocolException.INVALID_COLLIDER_GEOMETRY, "collider '" + id + "': " + e.getMessage(), e);
		}
		return new ColliderDto(id, part, colliderType, valid, fit, q, geometry);
	}

	public static void write(FriendlyByteBuf buf, WireCollider c) {
		buf.writeUtf(c.id, ProtocolLimits.MAX_COLLIDER_ID_BYTES);
		buf.writeUtf(c.bodyPart, 32);
		buf.writeUtf(c.type, 16);
		buf.writeBoolean(c.valid);
		buf.writeUtf(c.fitSource, 32);
		buf.writeFloat(c.quality);
		buf.writeVarInt(c.numbers.length);
		for (double d : c.numbers) {
			buf.writeDouble(d);
		}
	}

	public static WireCollider read(FriendlyByteBuf buf) {
		String id = buf.readUtf(ProtocolLimits.MAX_COLLIDER_ID_BYTES);
		String bodyPart = buf.readUtf(32);
		String type = buf.readUtf(16);
		boolean valid = buf.readBoolean();
		String fit = buf.readUtf(32);
		float quality = buf.readFloat();
		int n = buf.readVarInt();
		if (n < 0 || n > MAX_NUMBERS) {
			throw new IllegalStateException("collider geometry number count " + n + " out of range");
		}
		double[] numbers = new double[n];
		for (int i = 0; i < n; i++) {
			numbers[i] = buf.readDouble();
		}
		return new WireCollider(id, bodyPart, type, valid, fit, quality, numbers);
	}
}
