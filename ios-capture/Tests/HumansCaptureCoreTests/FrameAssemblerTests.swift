import CoreImage
import CoreVideo
import Foundation
import simd
import Testing
@testable import HumansCaptureCore

struct FrameAssemblerTests {
    @Test("Owned buffers assemble into a self-consistent RGBD envelope")
    func frameAssembly() async throws {
        let pixelBuffer = try makePixelBuffer()
        let captured = CapturedBuffers(
            deviceID: "front-phone",
            sessionID: UUID(uuidString: "44487d7c-b847-49db-aa37-cf326ad76078")!,
            captureID: UUID(uuidString: "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4")!,
            requestID: nil,
            mode: .live,
            sequence: 7,
            captureTimestampSeconds: 42.5,
            orientation: .landscapeRight,
            trackingState: .normal,
            rgbWidth: 2,
            rgbHeight: 1,
            rgbIntrinsics: matrix_identity_double3x3,
            rgbPixelBuffer: try SendablePixelBufferCopy(copying: pixelBuffer),
            depthWidth: 2,
            depthHeight: 1,
            depthMetersLE: Data([
                0x00, 0x00, 0x80, 0x3F, // 1.0 meter
                0x00, 0x00, 0x00, 0x00, // invalid sentinel
            ]),
            confidence: Data([2, 0]),
            arkitWorldFromCamera: matrix_identity_double4x4
        )
        let encoder = RGBEncoder(context: CIContext(options: [.useSoftwareRenderer: true]))
        let assembler = RGBDFrameAssembler(rgbEncoder: encoder)

        let encoded = try await assembler.assemble(captured)
        let headerLength = encoded.envelope.withUnsafeBytes {
            Int(UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: 8, as: UInt32.self)))
        }
        let headerData = encoded.envelope.subdata(in: 16..<(16 + headerLength))
        let decodedHeader = try HMCJSON.decoder().decode(RGBDFrameHeader.self, from: headerData)

        #expect(decodedHeader == encoded.header)
        #expect(decodedHeader.sequence == 7)
        #expect(decodedHeader.buffers[0].offset == 0)
        #expect(decodedHeader.buffers[1].offset == decodedHeader.buffers[0].length)
        #expect(decodedHeader.buffers[2].offset == decodedHeader.buffers[0].length + 8)
        #expect(encoded.validDepthFraction == 0.5)
        #expect(encoded.jpegByteCount > 0)
        #expect(encoded.encodingDurationSeconds >= 0)
    }

    private func makePixelBuffer() throws -> CVPixelBuffer {
        var pixelBuffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            2,
            1,
            kCVPixelFormatType_32BGRA,
            nil,
            &pixelBuffer
        )
        #expect(status == kCVReturnSuccess)
        let result = try #require(pixelBuffer)
        CVPixelBufferLockBaseAddress(result, [])
        defer { CVPixelBufferUnlockBaseAddress(result, []) }
        let base = try #require(CVPixelBufferGetBaseAddress(result))
        base.copyMemory(from: [UInt8](repeating: 127, count: 8), byteCount: 8)
        return result
    }
}
