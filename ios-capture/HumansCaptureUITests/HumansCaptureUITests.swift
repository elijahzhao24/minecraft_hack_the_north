import XCTest

final class HumansCaptureUITests: XCTestCase {
    func testMainCaptureControlsAreVisible() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.navigationBars["Humans Capture"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["toggle-ar"].exists)
        XCTAssertTrue(app.buttons["capture-snapshot"].exists)
    }
}
