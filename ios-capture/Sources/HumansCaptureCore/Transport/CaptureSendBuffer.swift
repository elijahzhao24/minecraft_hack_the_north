import Foundation

public enum CaptureSendStart: Equatable, Sendable {
    case frame(PendingCapture)
    case oversized(PendingCapture)
    case none
}

/// Tracks pending and in-flight sends together so retry never exceeds the bound or
/// replaces a newer live frame with an older failed one.
public struct CaptureSendBuffer: Sendable {
    public let maximumOutstanding: Int
    public private(set) var pending: [PendingCapture] = []
    public private(set) var inFlight: PendingCapture?

    public init(maximumOutstanding: Int = 3) {
        // One in-flight item plus the workflow's two pending slots.
        precondition(maximumOutstanding > 0, "send capacity must be positive")
        self.maximumOutstanding = maximumOutstanding
    }

    public var outstandingCount: Int { pending.count + (inFlight == nil ? 0 : 1) }

    @discardableResult
    public mutating func enqueue(_ capture: PendingCapture) -> CaptureEnqueueResult {
        if capture.mode == .live,
           let liveIndex = pending.firstIndex(where: { $0.mode == .live }) {
            let replacedID = pending[liveIndex].captureID
            pending[liveIndex] = capture
            return .replacedLive(replacedID)
        }
        if outstandingCount < maximumOutstanding {
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

    public mutating func beginNextSend(maximumBytes: Int) -> CaptureSendStart {
        guard inFlight == nil, !pending.isEmpty else { return .none }
        let next = pending.removeFirst()
        guard next.envelope.count <= maximumBytes else { return .oversized(next) }
        inFlight = next
        return .frame(next)
    }

    public mutating func succeedInFlight() {
        inFlight = nil
    }

    public mutating func failInFlight() {
        guard let failed = inFlight else { return }
        inFlight = nil
        if failed.mode == .live, pending.contains(where: { $0.mode == .live }) {
            return
        }
        pending.insert(failed, at: 0)
    }

    public mutating func removeAll() {
        pending.removeAll(keepingCapacity: true)
        inFlight = nil
    }
}
