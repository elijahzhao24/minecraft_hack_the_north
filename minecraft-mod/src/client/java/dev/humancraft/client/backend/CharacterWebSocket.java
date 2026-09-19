package dev.humancraft.client.backend;

import dev.humancraft.HumanCraft;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.CharacterFrameDecoder;
import dev.humancraft.contract.ControlMessage;
import dev.humancraft.contract.Mode;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.model.CharacterFrame;
import dev.humancraft.telemetry.Telemetry;
import io.sentry.SentryLevel;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/**
 * Bounded, reconnecting client for the backend character stream. HttpClient callbacks only assemble frames;
 * binary validation runs on a dedicated decode executor and the caller decides how to enter the render thread.
 */
public final class CharacterWebSocket implements AutoCloseable {
	private static final int MAX_CONTROL_BYTES = 64 * 1024;
	private static final long FAILURE_REPORT_INTERVAL_MS = 30_000;

	private final HumanCraftConfig config;
	private final Consumer<CharacterFrame> frameConsumer;
	private final Consumer<String> statusConsumer;
	private final LongSupplier lastFrameId;
	private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(r -> daemon(r, "humancraft-ws"));
	private final ExecutorService decoder = Executors.newSingleThreadExecutor(r -> daemon(r, "humancraft-decode"));
	private final HttpClient client;
	private final AtomicBoolean connectInFlight = new AtomicBoolean();
	private final AtomicBoolean reconnectScheduled = new AtomicBoolean();

	private volatile WebSocket socket;
	private volatile boolean closed;
	private volatile int reconnectAttempt;
	private volatile String status = "disconnected";
	private long lastFailureReportMs;
	private int suppressedFailures;

	public CharacterWebSocket(HumanCraftConfig config, LongSupplier lastFrameId,
			Consumer<CharacterFrame> frameConsumer, Consumer<String> statusConsumer) {
		this.config = config;
		this.lastFrameId = lastFrameId;
		this.frameConsumer = frameConsumer;
		this.statusConsumer = statusConsumer;
		this.client = HttpClient.newBuilder()
				.connectTimeout(Duration.ofSeconds(5))
				.executor(scheduler)
				.build();
	}

	private static Thread daemon(Runnable task, String name) {
		Thread thread = new Thread(task, name);
		thread.setDaemon(true);
		return thread;
	}

	public String status() {
		return status;
	}

	public void start() {
		scheduleConnect(0);
	}

	public void reconnectNow() {
		WebSocket old = socket;
		socket = null;
		if (old != null) {
			old.abort();
		}
		reconnectAttempt = 0;
		reconnectScheduled.set(false);
		setStatus("reconnecting");
		scheduleConnect(0);
	}

	public Optional<UUID> requestCapture(Mode mode) {
		UUID captureId = UUID.randomUUID();
		UUID requestId = UUID.randomUUID();
		if (!send(new ControlMessage.RequestCapture(requestId, captureId, mode))) {
			return Optional.empty();
		}
		return Optional.of(captureId);
	}

	private void scheduleConnect(long delayMs) {
		if (closed || !reconnectScheduled.compareAndSet(false, true)) {
			return;
		}
		scheduler.schedule(() -> {
			reconnectScheduled.set(false);
			connect();
		}, Math.max(0, delayMs), TimeUnit.MILLISECONDS);
	}

	private void connect() {
		if (closed || !connectInFlight.compareAndSet(false, true)) {
			return;
		}
		URI uri;
		try {
			uri = URI.create(config.backendUrl);
			if (!("ws".equalsIgnoreCase(uri.getScheme()) || "wss".equalsIgnoreCase(uri.getScheme()))) {
				throw new IllegalArgumentException("backendUrl must use ws:// or wss://");
			}
		} catch (RuntimeException e) {
			connectInFlight.set(false);
			failAndRetry(e, "invalid backend URL");
			return;
		}

		setStatus("connecting " + uri.getHost() + ":" + effectivePort(uri));
		client.newWebSocketBuilder()
				.connectTimeout(Duration.ofSeconds(5))
				.buildAsync(uri, new Listener())
				.whenComplete((ws, error) -> {
					connectInFlight.set(false);
					if (error != null) {
						failAndRetry(error, "connect failed");
					}
				});
	}

	private static int effectivePort(URI uri) {
		return uri.getPort() >= 0 ? uri.getPort() : ("wss".equalsIgnoreCase(uri.getScheme()) ? 443 : 80);
	}

	private void failAndRetry(Throwable error, String detail) {
		if (closed) {
			return;
		}
		reportConnectionFailure(error, detail);
		long base = Math.min(config.reconnectMaxMs,
				(long) config.reconnectMinMs << Math.min(20, reconnectAttempt++));
		long jitter = Math.max(1, base / 5);
		long delay = Math.min(config.reconnectMaxMs, base + Math.floorMod(System.nanoTime(), jitter));
		setStatus(detail + "; retry in " + delay + " ms");
		scheduleConnect(delay);
	}

	private synchronized void reportConnectionFailure(Throwable error, String detail) {
		long now = System.currentTimeMillis();
		if (lastFailureReportMs == 0 || now - lastFailureReportMs >= FAILURE_REPORT_INTERVAL_MS) {
			Telemetry.captureException(error, "client.websocket", Map.of(
					"detail", detail,
					"suppressed_repeats", Integer.toString(suppressedFailures)));
			lastFailureReportMs = now;
			suppressedFailures = 0;
		} else {
			suppressedFailures++;
			HumanCraft.LOGGER.debug("WebSocket {} (repeat {}): {}", detail, suppressedFailures, error.toString());
		}
	}

	private void setStatus(String next) {
		status = next;
		statusConsumer.accept(next);
	}

	private boolean send(ControlMessage message) {
		WebSocket ws = socket;
		if (ws == null || ws.isOutputClosed()) {
			setStatus("not connected");
			return false;
		}
		try {
			ws.sendText(ControlMessage.serialize(message), true);
			return true;
		} catch (RuntimeException e) {
			failAndRetry(e, "send failed");
			return false;
		}
	}

	private final class Listener implements WebSocket.Listener {
		private final ByteArrayOutputStream binary = new ByteArrayOutputStream();
		private final StringBuilder text = new StringBuilder();

		@Override
		public void onOpen(WebSocket webSocket) {
			socket = webSocket;
			reconnectAttempt = 0;
			setStatus("connected; awaiting hello");
			long last = lastFrameId.getAsLong();
			send(new ControlMessage.CharacterHello(config.clientId, last >= 0 ? Optional.of(last) : Optional.empty()));
			webSocket.request(1);
		}

		@Override
		public CompletionStage<?> onBinary(WebSocket webSocket, ByteBuffer data, boolean last) {
			try {
				int nextSize = Math.addExact(binary.size(), data.remaining());
				if (nextSize > config.maxBinaryBytes) {
					throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED,
							"binary WebSocket message exceeds " + config.maxBinaryBytes + " bytes");
				}
				byte[] chunk = new byte[data.remaining()];
				data.get(chunk);
				binary.writeBytes(chunk);
				if (last) {
					byte[] complete = binary.toByteArray();
					binary.reset();
					decoder.execute(() -> decode(complete));
				}
			} catch (RuntimeException e) {
				binary.reset();
				Telemetry.captureException(e, "client.websocket.framing");
				webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "invalid binary framing");
			}
			webSocket.request(1);
			return null;
		}

		@Override
		public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
			try {
				if (text.length() + data.length() > MAX_CONTROL_BYTES) {
					throw new ProtocolException(ProtocolException.LIMIT_EXCEEDED, "control message too large");
				}
				text.append(data);
				if (last) {
					String complete = text.toString();
					text.setLength(0);
					handleControl(complete);
				}
			} catch (RuntimeException e) {
				text.setLength(0);
				Telemetry.captureException(e, "client.websocket.control");
				setStatus("malformed backend control");
			}
			webSocket.request(1);
			return null;
		}

		@Override
		public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
			if (socket == webSocket) {
				socket = null;
			}
			if (!closed) {
				failAndRetry(new IllegalStateException("WebSocket closed " + statusCode + ": " + reason), "connection closed");
			}
			return null;
		}

		@Override
		public void onError(WebSocket webSocket, Throwable error) {
			if (socket == webSocket) {
				socket = null;
			}
			failAndRetry(error, "connection error");
		}
	}

	private void decode(byte[] complete) {
		try (Telemetry.Span span = Telemetry.transaction("client.character_decode", "hmc.decode")) {
			span.data("message_bytes", complete.length);
			CharacterFrame frame = CharacterFrameDecoder.decode(ByteBuffer.wrap(complete));
			span.data("frame_id", frame.frameId()).data("point_count", frame.cloud().count())
					.data("collider_count", frame.header().colliders().size());
			send(new ControlMessage.CharacterAck(frame.frameId(), true, "decoded", Optional.empty()));
			frameConsumer.accept(frame);
		} catch (RuntimeException e) {
			Telemetry.captureException(e, "client.frame.malformed");
			setStatus("rejected malformed frame: " + safeMessage(e));
		}
	}

	private void handleControl(String json) {
		ControlMessage message = ControlMessage.parse(json);
		switch (message) {
			case ControlMessage.CharacterServerHello hello -> {
				if (hello.maxBinaryBytes() < 1024) {
					throw new ProtocolException(ProtocolException.INVALID_MESSAGE, "backend max_binary_bytes too small");
				}
				setStatus("streaming");
			}
			case ControlMessage.Ack ack -> setStatus("backend ack: " + ack.code());
			case ControlMessage.Error error -> {
				setStatus("backend error: " + error.code());
				Telemetry.log(SentryLevel.WARNING, "backend error code=%s retryable=%s message=%s",
						error.code(), error.retryable(), error.message());
			}
			default -> HumanCraft.LOGGER.debug("Ignoring backend control type {}", message.type());
		}
	}

	private static String safeMessage(Throwable error) {
		String message = error.getMessage();
		if (message == null || message.isBlank()) {
			return error.getClass().getSimpleName();
		}
		byte[] utf8 = message.getBytes(StandardCharsets.UTF_8);
		return utf8.length <= 180 ? message : new String(utf8, 0, 180, StandardCharsets.UTF_8);
	}

	@Override
	public void close() {
		closed = true;
		WebSocket ws = socket;
		socket = null;
		if (ws != null) {
			try {
				ws.sendClose(WebSocket.NORMAL_CLOSURE, "client stopping");
			} catch (RuntimeException ignored) {
				ws.abort();
			}
		}
		decoder.shutdownNow();
		scheduler.shutdownNow();
		setStatus("stopped");
	}
}
