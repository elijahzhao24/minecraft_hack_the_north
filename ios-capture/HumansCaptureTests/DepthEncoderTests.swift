import XCTest
import simd
@testable import HumansCapture

final class DepthEncoderTests: XCTestCase {
    func testCopiesPaddedFloatRowsAndNormalizesInvalidValues() throws {
        let width = 2
        let height = 2
        let rowStride = 16
        var bytes = Data(count: rowStride * height)
        let rows: [[Float]] = [[1.25, .nan], [-2, 3.5]]
        bytes.withUnsafeMutableBytes { raw in
            for y in 0..<height {
                for x in 0..<width {
                    var bits = rows[y][x].bitPattern
                    withUnsafeBytes(of: &bits) { source in
                        raw.baseAddress!.advanced(by: y * rowStride + x * 4).copyMemory(from: source.baseAddress!, byteCount: 4)
                    }
                }
            }
        }

        let result = try bytes.withUnsafeBytes {
            try DepthEncoder.copyFloat32Rows(baseAddress: $0.baseAddress!, width: width, height: height, bytesPerRow: rowStride)
        }
        XCTAssertEqual(result.validCount, 2)
        let values: [Float] = stride(from: 0, to: result.0.count, by: 4).map { offset in
            let bits = result.0.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: offset, as: UInt32.self) }.littleEndian
            return Float(bitPattern: bits)
        }
        XCTAssertEqual(values, [1.25, 0, 0, 3.5])
    }

    func testFiveMeterRadialGateAndEncodedInvalids() throws {
        let k = simd_float3x3(columns: (SIMD3<Float>(100, 0, 0), SIMD3<Float>(0, 100, 0), SIMD3<Float>(100, 100, 1)))
        let gate = DepthRangeGate(rgbIntrinsics: k, rgbWidth: 200, rgbHeight: 200, depthWidth: 100, depthHeight: 100)
        XCTAssertTrue(gate.contains(4.99, u: 50, v: 50))
        XCTAssertTrue(gate.contains(5.00, u: 50, v: 50))
        XCTAssertFalse(gate.contains(5.01, u: 50, v: 50))
        XCTAssertFalse(gate.contains(4.0, u: 0, v: 50)) // 5.66 m along the ray
        XCTAssertTrue(gate.contains(3.0, u: 0, v: 50))
        XCTAssertFalse(gate.contains(.nan, u: 50, v: 50))
        let depths: [Float] = [4.0, 3.0]
        let result = try depths.withUnsafeBufferPointer {
            try DepthEncoder.copyFloat32Rows(baseAddress: UnsafeRawPointer($0.baseAddress!), width: 2, height: 1,
                                               bytesPerRow: 8, rangeGate: gate)
        }
        XCTAssertEqual(result.validCount, 0) // top-left rays exceed 5 m for both values
        XCTAssertEqual(result.0, Data(repeating: 0, count: 8))
    }

    func testRejectsUnknownConfidence() {
        let bytes = Data([0, 1, 3])
        XCTAssertThrowsError(try bytes.withUnsafeBytes {
            try DepthEncoder.copyUInt8Rows(baseAddress: $0.baseAddress!, width: 3, height: 1, bytesPerRow: 3, allowedRange: 0...2)
        })
    }
}
