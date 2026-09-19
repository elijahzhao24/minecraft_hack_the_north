import Foundation

public struct CaptureWork<Value: Sendable>: Sendable {
    public let captureID: UUID
    public let sessionID: UUID
    public let mode: CaptureMode
    public let value: Value

    public init(captureID: UUID, sessionID: UUID, mode: CaptureMode, value: Value) {
        self.captureID = captureID
        self.sessionID = sessionID
        self.mode = mode
        self.value = value
    }
}

public enum CaptureBacklogSubmitResult: Equatable, Sendable {
    case started
    case queued
    case replacedLive
    case staleSession
    case snapshotNotReserved
}

/// Pure state machine behind the asynchronous encoder pipeline.
/// It bounds retained frame memory to one active item, explicitly reserved snapshots,
/// and one replaceable live item.
public struct CaptureProcessingBacklog<Value: Sendable>: Sendable {
    public private(set) var sessionID: UUID
    public let maximumSnapshots: Int
    public private(set) var active: CaptureWork<Value>?
    public private(set) var pendingSnapshots: [CaptureWork<Value>] = []
    public private(set) var pendingLive: CaptureWork<Value>?
    private var snapshotReservations: Set<UUID> = []

    public init(sessionID: UUID, maximumSnapshots: Int) {
        precondition(maximumSnapshots > 0, "snapshot capacity must be positive")
        self.sessionID = sessionID
        self.maximumSnapshots = maximumSnapshots
    }

    public var retainedValues: [Value] {
        [active?.value].compactMap { $0 } + pendingSnapshots.map(\.value) + [pendingLive?.value].compactMap { $0 }
    }

    public mutating func reserveSnapshot(_ captureID: UUID) -> Bool {
        guard !snapshotReservations.contains(captureID),
              snapshotReservations.count < maximumSnapshots else { return false }
        snapshotReservations.insert(captureID)
        return true
    }

    public mutating func cancelSnapshotReservation(_ captureID: UUID) {
        snapshotReservations.remove(captureID)
        pendingSnapshots.removeAll { $0.captureID == captureID }
        if active?.captureID == captureID { active = nil }
    }

    public mutating func submit(_ work: CaptureWork<Value>) -> CaptureBacklogSubmitResult {
        guard work.sessionID == sessionID else { return .staleSession }
        if work.mode == .snapshot, !snapshotReservations.contains(work.captureID) {
            return .snapshotNotReserved
        }
        guard active != nil else {
            active = work
            return .started
        }
        if work.mode == .snapshot {
            pendingSnapshots.append(work)
            return .queued
        }
        let replaced = pendingLive != nil
        pendingLive = work
        return replaced ? .replacedLive : .queued
    }

    /// Marks the current item complete and returns the next item that should start.
    @discardableResult
    public mutating func completeActive() -> CaptureWork<Value>? {
        if let active, active.mode == .snapshot {
            snapshotReservations.remove(active.captureID)
        }
        if !pendingSnapshots.isEmpty {
            active = pendingSnapshots.removeFirst()
        } else if let pendingLive {
            active = pendingLive
            self.pendingLive = nil
        } else {
            active = nil
        }
        return active
    }

    public mutating func reset(sessionID: UUID) {
        self.sessionID = sessionID
        active = nil
        pendingSnapshots.removeAll(keepingCapacity: true)
        pendingLive = nil
        snapshotReservations.removeAll(keepingCapacity: true)
    }
}
