import CoreGraphics
import CoreImage
import CoreVideo
import Foundation
import ImageIO
import Testing
@testable import HumansCaptureCore

struct EncodingTests {
    @Test("Depth rows are packed tightly and invalid samples become zero")
    func rowStridedDepthCopy() throws {
        // Each two-float row has four padding bytes that must not reach the wire.
        let input = Data([
            0x00, 0x00, 0x80, 0x3F,  // 1.0
            0x00, 0x00, 0xC0, 0x7F,  // NaN -> 0.0
            0xAA, 0xAA, 0xAA, 0xAA,
            0x00, 0x00, 0x80, 0xBF,  // -1.0 -> 0.0
            0x00, 0x00, 0x20, 0x40,  // 2.5
            0xBB, 0xBB, 0xBB, 0xBB,
        ])

        let packed = try DepthPlaneCopier.copyFloat32MetersLE(
            input,
            width: 2,
            height: 2,
            bytesPerRow: 12
        )

        #expect(packed == Data([
            0x00, 0x00, 0x80, 0x3F,
            0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x20, 0x40,
        ]))
    }

    @Test("Confidence rows are tightly packed and accept only ARKit v1 levels")
    func confidenceValidation() throws {
        let input = Data([0, 1, 2, 99, 2, 1, 0, 88])
        let packed = try DepthPlaneCopier.copyConfidence(
            input,
            width: 3,
            height: 2,
            bytesPerRow: 4
        )
        #expect(packed == Data([0, 1, 2, 2, 1, 0]))

        #expect(throws: BufferCopyError.self) {
            _ = try DepthPlaneCopier.copyConfidence(
                Data([0, 3]),
                width: 2,
                height: 1,
                bytesPerRow: 2
            )
        }
    }

    @Test("Pixel-buffer copy owns bytes independently of the source")
    func pixelBufferCopyDoesNotAliasSource() throws {
        let source = try makeBGRAPixelBuffer(width: 2, height: 1)
        try writeBytes([10, 20, 30, 255, 40, 50, 60, 255], to: source)

        let owned = try SendablePixelBufferCopy(copying: source)
        try writeBytes([0, 0, 0, 0, 0, 0, 0, 0], to: source)

        #expect(try readBytes(from: owned.pixelBuffer, count: 8) == [10, 20, 30, 255, 40, 50, 60, 255])
    }

    @Test("JPEG encoding preserves raster dimensions without EXIF rotation")
    func jpegDimensionsAndOrientation() throws {
        let source = try makeBGRAPixelBuffer(width: 2, height: 1)
        try writeBytes([0, 0, 255, 255, 0, 255, 0, 255], to: source)

        // Software rendering keeps this contract test independent of CI GPU availability.
        let encoder = RGBEncoder(context: CIContext(options: [.useSoftwareRenderer: true]))
        let jpeg = try encoder.encodeJPEG(pixelBuffer: source)
        let imageSource = try #require(CGImageSourceCreateWithData(jpeg as CFData, nil))
        let properties = try #require(
            CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [CFString: Any]
        )

        #expect(properties[kCGImagePropertyPixelWidth] as? Int == 2)
        #expect(properties[kCGImagePropertyPixelHeight] as? Int == 1)
        #expect(properties[kCGImagePropertyOrientation] == nil)
    }

    private func makeBGRAPixelBuffer(width: Int, height: Int) throws -> CVPixelBuffer {
        var pixelBuffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            width,
            height,
            kCVPixelFormatType_32BGRA,
            nil,
            &pixelBuffer
        )
        #expect(status == kCVReturnSuccess)
        return try #require(pixelBuffer)
    }

    private func writeBytes(_ bytes: [UInt8], to pixelBuffer: CVPixelBuffer) throws {
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        let baseAddress = try #require(CVPixelBufferGetBaseAddress(pixelBuffer))
        baseAddress.copyMemory(from: bytes, byteCount: bytes.count)
    }

    private func readBytes(from pixelBuffer: CVPixelBuffer, count: Int) throws -> [UInt8] {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        let baseAddress = try #require(CVPixelBufferGetBaseAddress(pixelBuffer))
        return Array(UnsafeBufferPointer(start: baseAddress.assumingMemoryBound(to: UInt8.self), count: count))
    }
}
