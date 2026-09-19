import XCTest
@testable import HumansCapture

final class HMCEnvelopeTests: XCTestCase {
    func testFixedHeaderIsHMC1AndLittleEndian() throws {
        let rgb = Data([0xff, 0xd8, 0xff, 0xd9])
        let depth = Data(repeating: 0, count: 16)
        let buffers = [
            HMCPayloadBuffer(name: "rgb", encoding: "jpeg", data: rgb, shape: nil),
            HMCPayloadBuffer(name: "depth", encoding: "float32_le", data: depth, shape: [2, 2])
        ]
        let header = try fixtureHeader(buffers: buffers)
        let envelope = try HMCEnvelope.encode(header: header, buffers: buffers)

        XCTAssertEqual(Array(envelope.prefix(8)), [0x48, 0x4d, 0x43, 0x31, 0x01, 0x00, 0x01, 0x00])
        let headerLength = envelope.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: 8, as: UInt32.self) }.littleEndian
        let payloadLength = envelope.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: 12, as: UInt32.self) }.littleEndian
        XCTAssertGreaterThan(headerLength, 0)
        XCTAssertEqual(payloadLength, 20)
        XCTAssertEqual(envelope.count, 16 + Int(headerLength) + Int(payloadLength))
    }

    func testRejectsDescriptorMismatch() throws {
        let buffers = [HMCPayloadBuffer(name: "rgb", encoding: "jpeg", data: Data([1]), shape: nil)]
        var header = try fixtureHeader(buffers: buffers)
        header = RGBDFrameHeader(
            schema: header.schema,
            schemaVersion: header.schemaVersion,
            deviceID: header.deviceID,
            sessionID: header.sessionID,
            captureID: header.captureID,
            sequence: header.sequence,
            captureTimestampSeconds: header.captureTimestampSeconds,
            imageOrientation: header.imageOrientation,
            mirrored: header.mirrored,
            trackingState: header.trackingState,
            rgb: header.rgb,
            depth: header.depth,
            rgbDepthMapping: header.rgbDepthMapping,
            arkitWorldFromCameraRowMajor: header.arkitWorldFromCameraRowMajor,
            buffers: [BufferDescriptor(name: "rgb", encoding: "jpeg", offset: 4, length: 1, shape: nil)]
        )
        XCTAssertThrowsError(try HMCEnvelope.encode(header: header, buffers: buffers))
    }

    func testUUIDsAreEncodedAsLowercaseCanonicalStrings() throws {
        let buffers = [HMCPayloadBuffer(name: "rgb", encoding: "jpeg", data: Data([1]), shape: nil)]
        let header = RGBDFrameHeader(
            schema: "hmc.rgbd_frame",
            schemaVersion: 1,
            deviceID: "test-phone",
            sessionID: UUID(uuidString: "ABCDEF00-0000-4000-8000-000000000001")!,
            captureID: UUID(uuidString: "ABCDEF00-0000-4000-8000-000000000002")!,
            sequence: 1,
            captureTimestampSeconds: 1,
            imageOrientation: .landscapeRight,
            mirrored: false,
            trackingState: .normal,
            rgb: RGBMetadata(width: 1, height: 1, intrinsicsRowMajor: [1, 0, 0, 0, 1, 0, 0, 0, 1]),
            depth: DepthMetadata(width: 1, height: 1, unit: "meter", confidenceEncoding: nil),
            rgbDepthMapping: .normalizedUncropped,
            arkitWorldFromCameraRowMajor: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            buffers: try HMCEnvelope.descriptors(for: buffers)
        )
        let encoded = try JSONEncoder().encode(header)
        let json = try XCTUnwrap(String(data: encoded, encoding: .utf8))
        XCTAssertTrue(json.contains("abcdef00-0000-4000-8000-000000000001"))
        XCTAssertFalse(json.contains("ABCDEF00"))
    }

    func testRejectsNonFiniteHeaderGeometry() throws {
        let buffers = [HMCPayloadBuffer(name: "rgb", encoding: "jpeg", data: Data([1]), shape: nil)]
        let valid = try fixtureHeader(buffers: buffers)
        let invalid = RGBDFrameHeader(
            schema: valid.schema,
            schemaVersion: valid.schemaVersion,
            deviceID: valid.deviceID,
            sessionID: valid.sessionID,
            captureID: valid.captureID,
            sequence: valid.sequence,
            captureTimestampSeconds: valid.captureTimestampSeconds,
            imageOrientation: valid.imageOrientation,
            mirrored: valid.mirrored,
            trackingState: valid.trackingState,
            rgb: RGBMetadata(width: 2, height: 2, intrinsicsRowMajor: [.nan, 0, 1, 0, 1, 1, 0, 0, 1]),
            depth: valid.depth,
            rgbDepthMapping: valid.rgbDepthMapping,
            arkitWorldFromCameraRowMajor: valid.arkitWorldFromCameraRowMajor,
            buffers: valid.buffers
        )
        XCTAssertThrowsError(try HMCEnvelope.encode(header: invalid, buffers: buffers))
    }

    private func fixtureHeader(buffers: [HMCPayloadBuffer]) throws -> RGBDFrameHeader {
        RGBDFrameHeader(
            schema: "hmc.rgbd_frame",
            schemaVersion: 1,
            deviceID: "test-phone",
            sessionID: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!,
            captureID: UUID(uuidString: "00000000-0000-4000-8000-000000000002")!,
            sequence: 7,
            captureTimestampSeconds: 12.5,
            imageOrientation: .landscapeRight,
            mirrored: false,
            trackingState: .normal,
            rgb: RGBMetadata(width: 2, height: 2, intrinsicsRowMajor: [1, 0, 1, 0, 1, 1, 0, 0, 1]),
            depth: DepthMetadata(width: 2, height: 2, unit: "meter", confidenceEncoding: nil),
            rgbDepthMapping: .normalizedUncropped,
            arkitWorldFromCameraRowMajor: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            buffers: try HMCEnvelope.descriptors(for: buffers)
        )
    }
}
