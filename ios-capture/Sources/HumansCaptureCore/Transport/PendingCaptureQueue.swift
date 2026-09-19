import Foundation

public struct PendingCapture: Equatable, Sendable {
    public let captureID: UUID
    public let requestID: UUID?
    public let mode: CaptureMode
    public let envelope: Data

    public init(captureID: UUID, requestID: UUID?, mode: CaptureMode, envelope: Data) {
        self.captureID = captureID
        self.requestID = requestID
        self.mode = mode
        self.envelope = envelope
    }
}

public enum CaptureEnqueueResult: Equatable, Sendable {
    case accepted
    case replacedLive(UUID)
    case droppedLiveQueueFull
    case rejectedSnapshotQueueFull
}

public struct PendingCaptureQueue: Sendable {
    public private(set) var pending: [PendingCapture] = []
    public let capacity: Int

    public init(capacity: Int = 2) {
        // Two pending entries allow one explicit snapshot to coexist with the newest live frame.
        precondition(capacity > 0, "capture queue capacity must be positive")
        self.capacity = capacity
    }

    @discardableResult
    public mutating func enqueue(_ capture: PendingCapture) -> CaptureEnqueueResult {
        if capture.mode == .live,
           let oldLiveIndex = pending.firstIndex(where: { $0.mode == .live }) {
            let replacedID = pending[oldLiveIndex].captureID
            pending[oldLiveIndex] = capture
            return .replacedLive(replacedID)
        }

        if pending.count < capacity {
            pending.append(capture)
            return .accepted
        }

        if capture.mode == .snapshot,
           let liveIndex = pending.firstIndex(where: { $0.mode == .live }) {
            let replacedID = pending[liveIndex].captureID
            pending[liveIndex] = capture
            return .replacedLive(replacedID)
        }
        return capture.mode == .snapshot ? .rejectedSnapshotQueueFull : .droppedLiveQueueFull
    }

    public mutating func dequeue() -> PendingCapture? {
        guard !pending.isEmpty else { return nil }
        return pending.removeFirst()
    }

    public mutating func removeAll() {
        pending.removeAll(keepingCapacity: true)
    }
}
