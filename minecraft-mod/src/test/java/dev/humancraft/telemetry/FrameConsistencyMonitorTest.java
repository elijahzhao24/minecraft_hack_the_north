package dev.humancraft.telemetry;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FrameConsistencyMonitorTest {
	@Test
	void sustainedMismatchWarnsOnceAndThenRecovers() {
		AtomicLong clock = new AtomicLong();
		List<FrameConsistencyMonitor.Event> events = new ArrayList<>();
		FrameConsistencyMonitor monitor = new FrameConsistencyMonitor(2, clock::get, events::add);
		UUID expected = UUID.randomUUID();
		UUID actual = UUID.randomUUID();

		monitor.compatible(10, expected, actual, "source-a,source-b");
		assertTrue(events.isEmpty());
		monitor.compatible(11, expected, actual, "source-a,source-b");
		monitor.compatible(12, expected, actual, "source-a,source-b");
		assertEquals(1, events.size());
		assertEquals("frame_identity_mismatch", events.getFirst().kind());

		monitor.compatible(13, expected, expected, "source-c,source-d");
		assertEquals(2, events.size());
		assertTrue(events.get(1).recovery());
	}

	@Test
	void staleResultWarnsOnceAndFreshFrameRecordsRecovery() {
		AtomicLong clock = new AtomicLong();
		List<FrameConsistencyMonitor.Event> events = new ArrayList<>();
		FrameConsistencyMonitor monitor = new FrameConsistencyMonitor(2, clock::get, events::add);
		UUID fusion = UUID.randomUUID();
		monitor.fresh(20, fusion, "source-a,source-b");

		clock.set(600_000_000);
		monitor.checkStale(500);
		monitor.checkStale(500);
		assertEquals(1, events.size());
		assertEquals("fresh_results_stale", events.getFirst().kind());
		assertEquals(600, events.getFirst().ageMs());

		monitor.fresh(21, UUID.randomUUID(), "source-c,source-d");
		assertEquals(2, events.size());
		assertEquals("fresh_results_recovered", events.get(1).kind());
	}
}
