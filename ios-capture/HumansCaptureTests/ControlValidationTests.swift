import XCTest
@testable import HumansCapture

final class ControlValidationTests: XCTestCase {
    func testRejectsUnknownControlFields() {
        let data = Data(#"{"type":"clock_ping","protocol_version":1,"request_id":"00000000-0000-4000-8000-000000000001","backend_send_time_s":1,"typo":true}"#.utf8)
        XCTAssertThrowsError(try CaptureSocket.requireOnlyKeys(
            ["type", "protocol_version", "request_id", "backend_send_time_s"],
            in: data
        ))
    }

    func testAcceptsExactControlFields() throws {
        let data = Data(#"{"type":"clock_ping","protocol_version":1,"request_id":"00000000-0000-4000-8000-000000000001","backend_send_time_s":1}"#.utf8)
        XCTAssertNoThrow(try CaptureSocket.requireOnlyKeys(
            ["type", "protocol_version", "request_id", "backend_send_time_s"],
            in: data
        ))
    }
}
