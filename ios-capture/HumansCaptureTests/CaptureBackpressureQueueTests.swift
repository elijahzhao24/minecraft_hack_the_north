import XCTest
@testable import HumansCapture

final class CaptureBackpressureQueueTests: XCTestCase {
    func testLiveFrameReplacesOnlyPreviousLiveFrame() {
        var queue = CaptureBackpressureQueue<String>(maximumSnapshots: 2)
        _ = queue.enqueueSnapshot("snapshot")
        _ = queue.enqueueLive("live-1")
        guard case .replacedLive(let replaced) = queue.enqueueLive("live-2") else {
            return XCTFail("Expected a live-frame replacement")
        }
        XCTAssertEqual(replaced, "live-1")
        XCTAssertEqual(queue.popFirst(), "snapshot")
        XCTAssertEqual(queue.popFirst(), "live-2")
    }

    func testSnapshotCapacityRejectsWithoutReplacingSnapshots() {
        var queue = CaptureBackpressureQueue<Int>(maximumSnapshots: 2)
        _ = queue.enqueueSnapshot(1)
        _ = queue.enqueueSnapshot(2)
        guard case .snapshotQueueFull = queue.enqueueSnapshot(3) else {
            return XCTFail("Expected the snapshot queue to be full")
        }
        XCTAssertEqual(queue.popFirst(), 1)
        XCTAssertEqual(queue.popFirst(), 2)
    }
}
