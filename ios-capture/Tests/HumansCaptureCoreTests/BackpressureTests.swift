import Foundation
import Testing
@testable import HumansCaptureCore

struct BackpressureTests {
    @Test("Processing backlog keeps one active, reserved snapshots, and only the newest live frame")
    func processingBacklogIsBounded() {
        let session = UUID()
        let snapshotID = UUID()
        var backlog = CaptureProcessingBacklog<Int>(sessionID: session, maximumSnapshots: 2)

        let reserved = backlog.reserveSnapshot(snapshotID)
        let first = backlog.submit(work(1, session: session, mode: .live))
        let second = backlog.submit(work(2, session: session, mode: .live))
        let third = backlog.submit(work(3, session: session, mode: .live))
        let fourth = backlog.submit(work(4, id: snapshotID, session: session, mode: .snapshot))
        #expect(reserved)
        #expect(first == .started)
        #expect(second == .queued)
        #expect(third == .replacedLive)
        #expect(fourth == .queued)
        #expect(backlog.retainedValues == [1, 4, 3])

        let afterFirst = backlog.completeActive()
        let afterSecond = backlog.completeActive()
        let afterThird = backlog.completeActive()
        #expect(afterFirst?.value == 4)
        #expect(afterSecond?.value == 3)
        #expect(afterThird == nil)
    }

    @Test("Reset drops queued work and rejects frames from the prior session")
    func processingBacklogFencesSessions() {
        let oldSession = UUID()
        let newSession = UUID()
        var backlog = CaptureProcessingBacklog<Int>(sessionID: oldSession, maximumSnapshots: 1)
        let oldFirst = backlog.submit(work(1, session: oldSession, mode: .live))
        let oldSecond = backlog.submit(work(2, session: oldSession, mode: .live))
        #expect(oldFirst == .started)
        #expect(oldSecond == .queued)

        backlog.reset(sessionID: newSession)

        #expect(backlog.retainedValues.isEmpty)
        let stale = backlog.submit(work(3, session: oldSession, mode: .live))
        let fresh = backlog.submit(work(4, session: newSession, mode: .live))
        #expect(stale == .staleSession)
        #expect(fresh == .started)
    }

    @Test("Failed snapshot is retained while a failed older live frame cannot replace newer live")
    func sendFailureRecoveryPreservesPolicy() {
        let snapshot = pending("00000000-0000-4000-8000-000000000101", mode: .snapshot)
        let live1 = pending("00000000-0000-4000-8000-000000000102", mode: .live)
        let live2 = pending("00000000-0000-4000-8000-000000000103", mode: .live)
        var buffer = CaptureSendBuffer(maximumOutstanding: 3)

        let snapshotAccepted = buffer.enqueue(snapshot)
        let snapshotStarted = buffer.beginNextSend(maximumBytes: 100)
        let live1Accepted = buffer.enqueue(live1)
        let live2Accepted = buffer.enqueue(live2)
        #expect(snapshotAccepted == .accepted)
        #expect(snapshotStarted == .frame(snapshot))
        #expect(live1Accepted == .accepted)
        #expect(live2Accepted == .replacedLive(live1.captureID))
        buffer.failInFlight()
        #expect(buffer.pending.map(\.captureID) == [snapshot.captureID, live2.captureID])

        let snapshotRetried = buffer.beginNextSend(maximumBytes: 100)
        #expect(snapshotRetried == .frame(snapshot))
        buffer.succeedInFlight()
        let liveStarted = buffer.beginNextSend(maximumBytes: 100)
        #expect(liveStarted == .frame(live2))
        let live3 = pending("00000000-0000-4000-8000-000000000104", mode: .live)
        let live3Accepted = buffer.enqueue(live3)
        #expect(live3Accepted == .accepted)
        buffer.failInFlight()
        #expect(buffer.pending.map(\.captureID) == [live3.captureID])
    }

    @Test("Negotiated binary limit rejects a frame before WebSocket send")
    func negotiatedLimitIsEnforced() {
        let snapshot = PendingCapture(
            captureID: UUID(),
            requestID: UUID(),
            mode: .snapshot,
            envelope: Data(repeating: 0, count: 101)
        )
        var buffer = CaptureSendBuffer(maximumOutstanding: 2)
        let accepted = buffer.enqueue(snapshot)
        let started = buffer.beginNextSend(maximumBytes: 100)
        #expect(accepted == .accepted)
        #expect(started == .oversized(snapshot))
        #expect(buffer.outstandingCount == 0)
    }

    private func work(
        _ value: Int,
        id: UUID = UUID(),
        session: UUID,
        mode: CaptureMode
    ) -> CaptureWork<Int> {
        CaptureWork(captureID: id, sessionID: session, mode: mode, value: value)
    }

    private func pending(_ id: String, mode: CaptureMode) -> PendingCapture {
        PendingCapture(
            captureID: UUID(uuidString: id)!,
            requestID: mode == .snapshot ? UUID() : nil,
            mode: mode,
            envelope: Data([1, 2, 3])
        )
    }
}
