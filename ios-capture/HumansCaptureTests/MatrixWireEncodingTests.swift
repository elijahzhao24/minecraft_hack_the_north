import simd
import XCTest
@testable import HumansCapture

final class MatrixWireEncodingTests: XCTestCase {
    func testThreeByThreeIsFlattenedAsMathematicalRows() {
        let matrix = simd_float3x3(columns: (
            SIMD3(1, 4, 7),
            SIMD3(2, 5, 8),
            SIMD3(3, 6, 9)
        ))
        XCTAssertEqual(MatrixWireEncoding.rowMajor(matrix), [1, 2, 3, 4, 5, 6, 7, 8, 9])
    }

    func testFourByFourIsFlattenedAsMathematicalRows() {
        let matrix = simd_float4x4(columns: (
            SIMD4(1, 5, 9, 13), SIMD4(2, 6, 10, 14),
            SIMD4(3, 7, 11, 15), SIMD4(4, 8, 12, 16)
        ))
        XCTAssertEqual(MatrixWireEncoding.rowMajor(matrix), (1...16).map(Double.init))
    }
}
