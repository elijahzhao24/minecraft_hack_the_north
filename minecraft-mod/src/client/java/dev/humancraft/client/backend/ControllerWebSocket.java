package dev.humancraft.client.backend;

import com.google.gson.JsonObject;
import dev.humancraft.HumanCraft;
import dev.humancraft.client.controller.ControllerState;
import dev.humancraft.config.HumanCraftConfig;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** Small reconnecting text-only client for /ws/controller. */
public final class ControllerWebSocket implements AutoCloseable {
	private static final int MAX_MESSAGE_CHARS = 4096;
	private final HumanCraftConfig config;
	private final Consumer<ControllerState> stateConsumer;
	private final Consumer<String> statusConsumer;
	private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor(r -> {
		Thread thread = new Thread(r, "humancraft-controller-ws");
		thread.setDaemon(true);
		return thread;
	});
	private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).executor(executor).build();
	private final AtomicBoolean scheduled = new AtomicBoolean();
	private volatile WebSocket socket;
	private volatile boolean closed;
	private int attempt;

	public ControllerWebSocket(HumanCraftConfig config, Consumer<ControllerState> stateConsumer,
			Consumer<String> statusConsumer) {
		this.config = config;
		this.stateConsumer = stateConsumer;
		this.statusConsumer = statusConsumer;
	}

	public void start() {
		schedule(0);
	}

	private void schedule(long delayMs) {
		if (closed || !scheduled.compareAndSet(false, true)) return;
		executor.schedule(() -> {
			scheduled.set(false);
			connect();
		}, delayMs, TimeUnit.MILLISECONDS);
	}

	private void connect() {
		if (closed) return;
		try {
			URI uri = URI.create(config.controllerUrl);
			if (!"ws".equalsIgnoreCase(uri.getScheme()) && !"wss".equalsIgnoreCase(uri.getScheme())) {
				throw new IllegalArgumentException("controllerUrl must use ws or wss");
			}
			statusConsumer.accept("connecting");
			client.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5)).buildAsync(uri, new Listener())
					.whenComplete((ignored, error) -> { if (error != null) retry(error); });
		} catch (RuntimeException error) {
			retry(error);
		}
	}

	private void retry(Throwable error) {
		if (closed) return;
		stateConsumer.accept(ControllerState.disconnected());
		long base = Math.min(config.reconnectMaxMs, (long) config.reconnectMinMs << Math.min(20, attempt++));
		statusConsumer.accept("disconnected; retrying");
		HumanCraft.LOGGER.debug("Controller WebSocket unavailable: {}", error.toString());
		schedule(base);
	}

	private final class Listener implements WebSocket.Listener {
		private final StringBuilder text = new StringBuilder();

		@Override
		public void onOpen(WebSocket webSocket) {
			socket = webSocket;
			attempt = 0;
			JsonObject hello = new JsonObject();
			hello.addProperty("type", "controller_hello");
			hello.addProperty("protocol_version", 1);
			hello.addProperty("client_id", config.clientId);
			webSocket.sendText(hello.toString(), true);
			statusConsumer.accept("connected; waiting for badge");
			webSocket.request(1);
		}

		@Override
		public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
			try {
				if (text.length() + data.length() > MAX_MESSAGE_CHARS) throw new IllegalArgumentException("controller message too large");
				text.append(data);
				if (last) {
					ControllerState state = ControllerState.parse(text.toString());
					text.setLength(0);
					stateConsumer.accept(state);
					statusConsumer.accept(state.connected() ? "badge connected" : "waiting for badge");
				}
			} catch (RuntimeException error) {
				text.setLength(0);
				HumanCraft.LOGGER.warn("Rejected controller state: {}", error.toString());
			}
			webSocket.request(1);
			return null;
		}

		@Override
		public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
			if (socket == webSocket) socket = null;
			retry(new IllegalStateException("controller socket closed " + statusCode));
			return null;
		}

		@Override
		public void onError(WebSocket webSocket, Throwable error) {
			if (socket == webSocket) socket = null;
			retry(error);
		}
	}

	@Override
	public void close() {
		closed = true;
		WebSocket current = socket;
		socket = null;
		if (current != null) current.abort();
		stateConsumer.accept(ControllerState.disconnected());
		executor.shutdownNow();
	}
}
