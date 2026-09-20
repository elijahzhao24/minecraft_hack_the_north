package dev.humancraft.telemetry;

import io.sentry.SentryLevel;

import java.util.Objects;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/** Focused state machine for cloud/hitbox identity and live-result freshness. */
public final class FrameConsistencyMonitor {
	public record Event(String kind, long frameId, UUID expectedFusionId, UUID actualFusionId,
			String sourceFrameIds, long ageMs, boolean recovery) {}

	private final int mismatchFrames;
	private final LongSupplier monotonicNanos;
	private final Consumer<Event> sink;
	private int mismatchStreak;
	private boolean mismatchWarning;
	private boolean staleWarning;
	private long lastFreshNanos = -1;
	private long lastFrameId = -1;
	private UUID lastFusionId;
	private String lastSourceFrameIds = "";

	public FrameConsistencyMonitor() {
		this(2, System::nanoTime, FrameConsistencyMonitor::emit);
	}

	public FrameConsistencyMonitor(int mismatchFrames) {
		this(mismatchFrames, System::nanoTime, FrameConsistencyMonitor::emit);
	}

	FrameConsistencyMonitor(int mismatchFrames, LongSupplier monotonicNanos, Consumer<Event> sink) {
		this.mismatchFrames = mismatchFrames;
		this.monotonicNanos = monotonicNanos;
		this.sink = sink;
	}

	public void fresh(long frameId, UUID fusionId, String sourceFrameIds) {
		if (staleWarning) {
			sink.accept(new Event("fresh_results_recovered", frameId, fusionId, fusionId, sourceFrameIds, 0, true));
		}
		staleWarning = false;
		lastFreshNanos = monotonicNanos.getAsLong();
		lastFrameId = frameId;
		lastFusionId = fusionId;
		lastSourceFrameIds = sourceFrameIds;
	}

	public void compatible(long frameId, UUID expectedFusionId, UUID actualFusionId, String sourceFrameIds) {
		if (Objects.equals(expectedFusionId, actualFusionId)) {
			if (mismatchWarning) {
				sink.accept(new Event("frame_identity_recovered", frameId, expectedFusionId, actualFusionId,
						sourceFrameIds, 0, true));
			}
			mismatchStreak = 0;
			mismatchWarning = false;
			return;
		}
		mismatchStreak++;
		if (!mismatchWarning && mismatchStreak >= mismatchFrames) {
			sink.accept(new Event("frame_identity_mismatch", frameId, expectedFusionId, actualFusionId,
					sourceFrameIds, 0, false));
			mismatchWarning = true;
		}
	}

	public void checkStale(long staleAfterMs) {
		if (lastFreshNanos < 0 || staleWarning) return;
		long ageMs = Math.max(0, (monotonicNanos.getAsLong() - lastFreshNanos) / 1_000_000);
		if (ageMs >= staleAfterMs) {
			sink.accept(new Event("fresh_results_stale", lastFrameId, lastFusionId, lastFusionId,
					lastSourceFrameIds, ageMs, false));
			staleWarning = true;
		}

	}

	private static void emit(Event event) {
		Telemetry.log(event.recovery ? SentryLevel.INFO : SentryLevel.WARNING,
				"consistency kind=%s frame_id=%d expected_fusion_id=%s actual_fusion_id=%s source_frame_ids=%s age_ms=%d recovery=%s",
				event.kind, event.frameId, event.expectedFusionId, event.actualFusionId, event.sourceFrameIds,
				event.ageMs, event.recovery);
	}
}
