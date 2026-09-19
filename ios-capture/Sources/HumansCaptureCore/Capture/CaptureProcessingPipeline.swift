import Foundation

/// Thread-safe executor for the bounded processing state machine.
/// Only the active work item owns a Task; queued frames remain a bounded set of values.
public final class CaptureProcessingPipeline: @unchecked Sendable {
    public typealias Processor = @Sendable (CapturedBuffers) async -> Void

    private let lock = NSLock()
    private let processor: Processor
    private var backlog: CaptureProcessingBacklog<CapturedBuffers>
    private var activeTask: Task<Void, Never>?

    public init(
        sessionID: UUID,
        maximumSnapshots: Int = 2,
        processor: @escaping Processor
    ) {
        self.backlog = CaptureProcessingBacklog(
            sessionID: sessionID,
            maximumSnapshots: maximumSnapshots
        )
        self.processor = processor
    }

    public func reserveSnapshot(_ captureID: UUID) -> Bool {
        lock.withLock { backlog.reserveSnapshot(captureID) }
    }

    public func cancelSnapshotReservation(_ captureID: UUID) {
        lock.withLock { backlog.cancelSnapshotReservation(captureID) }
    }

    @discardableResult
    public func submit(_ buffers: CapturedBuffers) -> CaptureBacklogSubmitResult {
        lock.withLock {
            let result = backlog.submit(CaptureWork(
                captureID: buffers.captureID,
                sessionID: buffers.sessionID,
                mode: buffers.mode,
                value: buffers
            ))
            if result == .started, let active = backlog.active {
                startLocked(active)
            }
            return result
        }
    }

    public func reset(sessionID: UUID) {
        lock.withLock {
            activeTask?.cancel()
            activeTask = nil
            backlog.reset(sessionID: sessionID)
        }
    }

    private func startLocked(_ work: CaptureWork<CapturedBuffers>) {
        activeTask = Task { [weak self, processor] in
            await processor(work.value)
            self?.finished(work)
        }
    }

    private func finished(_ completed: CaptureWork<CapturedBuffers>) {
        lock.withLock {
            guard backlog.active?.captureID == completed.captureID,
                  backlog.active?.sessionID == completed.sessionID else { return }
            activeTask = nil
            if let next = backlog.completeActive() {
                startLocked(next)
            }
        }
    }
}
