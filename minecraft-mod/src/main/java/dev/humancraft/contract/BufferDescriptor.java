package dev.humancraft.contract;

import java.util.List;

/** One tightly packed binary buffer inside an HMC1 payload. */
public record BufferDescriptor(String name, String encoding, long offset, long length, List<Long> shape) {
	public static final String ENCODING_POINTS = "xyzrgba16_le";
	private static final List<String> KNOWN_ENCODINGS = List.of("jpeg", "float32_le", "uint8", ENCODING_POINTS);

	public BufferDescriptor {
		if (name == null || name.isEmpty()) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "buffer name must be non-empty");
		}
		if (!KNOWN_ENCODINGS.contains(encoding)) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "unknown buffer encoding '" + encoding + "'");
		}
		if (offset < 0 || offset > 0xFFFF_FFFFL || length < 0 || length > 0xFFFF_FFFFL) {
			throw new ProtocolException(ProtocolException.INVALID_BUFFER_RANGE, "buffer '" + name + "' offset/length out of uint32 range");
		}
		shape = shape == null ? null : List.copyOf(shape);
		if (shape != null) {
			for (long dim : shape) {
				if (dim <= 0) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "buffer '" + name + "' shape dims must be positive");
				}
			}
		} else if (!encoding.equals("jpeg")) {
			throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "buffer '" + name + "' requires a shape");
		}
	}

	public long end() {
		return offset + length;
	}

	public static BufferDescriptor parse(StrictJson.Obj o) {
		String name = o.string("name");
		String encoding = o.string("encoding");
		long offset = o.counter("offset");
		long length = o.counter("length");
		List<Long> shape = null;
		if (o.has("shape")) {
			shape = o.counterList("shape");
		} else {
			o.optionalCounter("shape");
		}
		o.finish();
		return new BufferDescriptor(name, encoding, offset, length, shape);
	}
}
