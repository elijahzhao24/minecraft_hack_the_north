import XCTest
@testable import HumansCapture

final class HumansCaptureTests: XCTestCase {
    func testInitialStoreIsDisconnected() async {
        let store = await CaptureStore()
        let state = await store.socketState
        XCTAssertEqual(state, .disconnected)
    }
}
