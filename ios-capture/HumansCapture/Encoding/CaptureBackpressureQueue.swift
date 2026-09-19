import Foundation

struct CaptureBackpressureQueue<Element> {
    enum EnqueueResult {
        case accepted
        case replacedLive(Element)
        case snapshotQueueFull
    }

    private(set) var snapshots: [Element] = []
    private(set) var live: Element?
    let maximumSnapshots: Int

    init(maximumSnapshots: Int = 2) {
        precondition(maximumSnapshots > 0)
        self.maximumSnapshots = maximumSnapshots
    }

    var count: Int { snapshots.count + (live == nil ? 0 : 1) }
    var isEmpty: Bool { snapshots.isEmpty && live == nil }

    mutating func enqueueSnapshot(_ value: Element) -> EnqueueResult {
        guard snapshots.count < maximumSnapshots else { return .snapshotQueueFull }
        snapshots.append(value)
        return .accepted
    }

    mutating func enqueueLive(_ value: Element) -> EnqueueResult {
        let replaced = live
        live = value
        return replaced.map(EnqueueResult.replacedLive) ?? .accepted
    }

    mutating func popFirst() -> Element? {
        if !snapshots.isEmpty { return snapshots.removeFirst() }
        defer { live = nil }
        return live
    }

    mutating func removeAll() -> [Element] {
        let removed = snapshots + [live].compactMap { $0 }
        snapshots.removeAll()
        live = nil
        return removed
    }
}
