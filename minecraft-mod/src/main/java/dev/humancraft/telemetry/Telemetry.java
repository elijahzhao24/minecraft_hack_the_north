package dev.humancraft.telemetry;

import dev.humancraft.HumanCraft;
import dev.humancraft.config.HumanCraftConfig;
import dev.humancraft.contract.ProtocolException;
import dev.humancraft.contract.TraceContext;
import io.sentry.ISpan;
import io.sentry.ITransaction;
import io.sentry.Sentry;
import io.sentry.SentryLevel;
import io.sentry.SpanStatus;
import io.sentry.TransactionContext;
import io.sentry.TransactionOptions;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Supplier;

/**
 * Thin, always-safe wrapper around the Sentry Java SDK. When no DSN is configured every call is a cheap
 * no-op, so gameplay never depends on telemetry. The DSN is read from the environment/config only and is
 * never written back to disk.
 */
public final class Telemetry {
	private static volatile boolean enabled;
	private static volatile String release = "humancraft@dev";
	private static long aggregateStartedNanos = System.nanoTime();
	private static long aggregateFrames;
	private static long aggregatePoints;
	private static long aggregateBytes;

	private Telemetry() {}

	public static boolean enabled() {
		return enabled;
	}

	public static synchronized void init(HumanCraftConfig config, String modVersion, Map<String, String> tags) {
		release = "humancraft@" + modVersion;
		String dsn = config.sentry.dsn == null ? "" : config.sentry.dsn.trim();
		try {
			Sentry.init(options -> {
				// Enables SENTRY_PROPERTIES_FILE / sentry.properties. Explicit
				// HUMANCRAFT_* configuration still takes precedence when supplied.
				options.setEnableExternalConfiguration(true);
				if (!dsn.isEmpty()) {
					options.setDsn(dsn);
					options.setEnvironment(config.sentry.environment);
					options.setTracesSampleRate(config.sentry.tracesSampleRate);
					options.getLogs().setEnabled(config.sentry.logs);
					options.setDebug(config.sentry.debug);
					options.setSendDefaultPii(false);
				}
				options.setRelease(release);
				options.setAttachStacktrace(true);
				options.setEnableUncaughtExceptionHandler(true);
				options.setShutdownTimeoutMillis(2000);
				options.setTag("mod", "humancraft");
				tags.forEach(options::setTag);
			});
			enabled = Sentry.isEnabled();
			HumanCraft.LOGGER.info("Sentry enabled (release={}, environment={})", release, config.sentry.environment);
			Sentry.logger().info("HumanCraft started release=%s", release);
			if (enabled && Boolean.parseBoolean(System.getenv("HUMANCRAFT_SENTRY_VERIFY"))) {
				verifyInstallation();
			}
		} catch (RuntimeException e) {
			enabled = false;
			HumanCraft.LOGGER.warn("Sentry initialisation failed; continuing without telemetry", e);
		}
	}

	/** Opt-in smoke event and metrics; never runs during normal gameplay. */
	private static void verifyInstallation() {
		try {
			throw new Exception("HumanCraft Sentry installation test");
		} catch (Exception error) {
			Sentry.captureException(error);
		}
		Sentry.metrics().count("humancraft.sentry_verify", 1.0);
		Sentry.metrics().gauge("humancraft.queue_size", 0.0);
		Sentry.metrics().distribution("humancraft.response_time", 150.0);
		Sentry.logger().info("HumanCraft Sentry verification event and metrics sent");
	}

	public static synchronized void shutdown() {
		if (enabled) {
			try {
				Sentry.close();
			} catch (RuntimeException e) {
				HumanCraft.LOGGER.debug("Sentry close failed", e);
			}
			enabled = false;
		}
	}

	// ---- errors ------------------------------------------------------------------------------

	public static void captureException(Throwable t, String category, Map<String, String> tags) {
		HumanCraft.LOGGER.error("[{}] {}", category, t.toString());
		if (!enabled) {
			return;
		}
		try {
			Sentry.captureException(t, scope -> {
				scope.setTag("category", category);
				tags.forEach(scope::setTag);
				if (t instanceof ProtocolException pe) {
					scope.setTag("protocol_code", pe.code());
				}
			});
		} catch (RuntimeException e) {
			HumanCraft.LOGGER.debug("Sentry capture failed", e);
		}
	}

	public static void captureException(Throwable t, String category) {
		captureException(t, category, Map.of());
	}

	/** Structured log line (Sentry Logs) mirrored to the game log at the same level. */
	public static void log(SentryLevel level, String message, Object... args) {
		String formatted = String.format(message, args);
		switch (level) {
			case DEBUG -> HumanCraft.LOGGER.debug(formatted);
			case INFO -> HumanCraft.LOGGER.info(formatted);
			case WARNING -> HumanCraft.LOGGER.warn(formatted);
			default -> HumanCraft.LOGGER.error(formatted);
		}
		if (!enabled) {
			return;
		}
		try {
			switch (level) {
				case DEBUG -> Sentry.logger().debug(message, args);
				case INFO -> Sentry.logger().info(message, args);
				case WARNING -> Sentry.logger().warn(message, args);
				default -> Sentry.logger().error(message, args);
			}
		} catch (RuntimeException e) {
			HumanCraft.LOGGER.debug("Sentry log failed", e);
		}
	}

	public static void breadcrumb(String category, String message) {
		if (enabled) {
			Sentry.addBreadcrumb(message, category);
		}
	}

	/** Periodic summary for routine frames; detailed frame traces remain sampled. */
	public static synchronized void routineFrame(long frameId, int pointCount, int payloadSize) {
		aggregateFrames++;
		aggregatePoints += pointCount;
		aggregateBytes += payloadSize;
		long now = System.nanoTime();
		if (now - aggregateStartedNanos < 30_000_000_000L) return;
		log(SentryLevel.INFO, "frame aggregate last_frame_id=%d frames=%d mean_points=%d mean_payload_bytes=%d period_ms=%d",
				frameId, aggregateFrames, aggregatePoints / aggregateFrames, aggregateBytes / aggregateFrames,
				(now - aggregateStartedNanos) / 1_000_000);
		aggregateFrames = aggregatePoints = aggregateBytes = 0;
		aggregateStartedNanos = now;
	}

	// ---- spans -------------------------------------------------------------------------------

	/** A span or transaction that is safe to use whether or not Sentry is on. */
	public static final class Span implements AutoCloseable {
		private final ISpan span;
		private boolean failed;

		private Span(ISpan span) {
			this.span = span;
		}

		public Span data(String key, Object value) {
			if (span != null) {
				span.setData(key, value);
			}
			return this;
		}

		public Span tag(String key, String value) {
			if (span != null) {
				span.setTag(key, value);
			}
			return this;
		}

		public Span measurement(String key, Number value) {
			if (span != null) {
				span.setMeasurement(key, value);
			}
			return this;
		}

		public Span fail(Throwable t) {
			failed = true;
			if (span != null) {
				span.setThrowable(t);
			}
			return this;
		}

		public Span child(String op, String description) {
			return span == null ? NOOP : new Span(span.startChild(op, description));
		}

		/** W3C-style headers to attach to outgoing messages so the backend can join the trace. */
		public TraceContext traceContext() {
			if (span == null) {
				return TraceContext.EMPTY;
			}
			var trace = span.toSentryTrace();
			var baggage = span.toBaggageHeader(List.of());
			return new TraceContext(Optional.ofNullable(trace == null ? null : trace.getValue()),
					Optional.ofNullable(baggage == null ? null : baggage.getValue()));
		}

		@Override
		public void close() {
			if (span != null) {
				span.finish(failed ? SpanStatus.INTERNAL_ERROR : SpanStatus.OK);
			}
		}
	}

	private static final Span NOOP = new Span(null);

	public static Span transaction(String name, String op) {
		if (!enabled) {
			return NOOP;
		}
		try {
			TransactionOptions options = new TransactionOptions();
			options.setBindToScope(false);
			ITransaction tx = Sentry.startTransaction(name, op, options);
			return new Span(tx);
		} catch (RuntimeException e) {
			return NOOP;
		}
	}

	/** Continues a trace started by the backend (sentry-trace / baggage from the frame header). */
	public static Span continueTransaction(String name, String op, TraceContext trace) {
		if (!enabled) {
			return NOOP;
		}
		try {
			if (trace.sentryTrace().isEmpty()) {
				return transaction(name, op);
			}
			TransactionContext context = Sentry.continueTrace(trace.sentryTrace().get(), trace.baggage().map(List::of).orElse(List.of()));
			if (context == null) {
				return transaction(name, op);
			}
			context.setName(name);
			context.setOperation(op);
			TransactionOptions options = new TransactionOptions();
			options.setBindToScope(false);
			return new Span(Sentry.startTransaction(context, options));
		} catch (RuntimeException e) {
			return NOOP;
		}
	}

	public static <T> T timed(Span parent, String op, String description, Supplier<T> body) {
		try (Span s = parent.child(op, description)) {
			try {
				return body.get();
			} catch (RuntimeException e) {
				s.fail(e);
				throw e;
			}
		}
	}
}
