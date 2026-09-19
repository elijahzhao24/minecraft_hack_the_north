import Foundation
import simd
import Testing
@testable import HumansCaptureCore

struct ProtocolTests {
    @Test("HMC1 prefix stores exact little-endian version, type, and lengths")
    func envelopePrefixIsLittleEndian() throws {
        let buffers = [
            Data([0xFF, 0xD8, 0xFF]),
            Data(repeating: 0, count: 8),
            Data([2, 1]),
        ]
        let header = try makeHeader(bufferLengths: buffers.map(\.count))

        let packet = try HMCEnvelope.encode(header: header, buffers: buffers)
        let headerLength = packet.count - HMCEnvelope.prefixLength - buffers.reduce(0) { $0 + $1.count }
        let expectedPrefix: [UInt8] = [
            0x48, 0x4D, 0x43, 0x31, // "HMC1"
            0x01, 0x00,             // envelope version 1
            0x01, 0x00,             // RGBD_FRAME message type 1
            UInt8(headerLength & 0xFF),
            UInt8((headerLength >> 8) & 0xFF),
            UInt8((headerLength >> 16) & 0xFF),
            UInt8((headerLength >> 24) & 0xFF),
            0x0D, 0x00, 0x00, 0x00, // 13 payload bytes
        ]

        #expect(Array(packet.prefix(HMCEnvelope.prefixLength)) == expectedPrefix)
    }

    @Test("Matrix serialization follows mathematical rows rather than SIMD storage")
    func matricesAreFlattenedRowMajor() {
        let intrinsics = simd_double3x3(columns: (
            SIMD3(1, 4, 7),
            SIMD3(2, 5, 8),
            SIMD3(3, 6, 9)
        ))
        let transform = simd_double4x4(columns: (
            SIMD4(1, 5, 9, 13),
            SIMD4(2, 6, 10, 14),
            SIMD4(3, 7, 11, 15),
            SIMD4(4, 8, 12, 16)
        ))

        #expect(MatrixWireFormatter.rowMajor(intrinsics) == [1, 2, 3, 4, 5, 6, 7, 8, 9])
        #expect(MatrixWireFormatter.rowMajor(transform) == [
            1, 2, 3, 4,
            5, 6, 7, 8,
            9, 10, 11, 12,
            13, 14, 15, 16,
        ])
    }

    @Test("Closed orientation enum rejects values outside protocol v1")
    func unknownOrientationIsRejected() throws {
        let json = Data(#""diagonal""#.utf8)
        #expect(throws: DecodingError.self) {
            _ = try HMCJSON.decoder().decode(ImageOrientation.self, from: json)
        }
    }

    @Test("Header rejects matrices with the wrong field count")
    func headerRejectsWrongMatrixFieldCounts() throws {
        #expect(throws: ProtocolValidationError.self) {
            _ = try makeHeader(intrinsics: Array(repeating: 1, count: 8))
        }
        #expect(throws: ProtocolValidationError.self) {
            _ = try makeHeader(pose: Array(repeating: 1, count: 15))
        }
    }

    @Test("Header rejects an RGBD payload above the 16 MiB v1 limit")
    func oversizedPayloadIsRejectedBeforeAllocation() throws {
        let tooLarge = HMCProtocolLimits.maximumRGBDPayloadBytes + 1
        #expect(throws: ProtocolValidationError.self) {
            _ = try makeHeader(bufferLengths: [tooLarge, 8, 2])
        }
    }

    @Test("Descriptors must be ordered, contiguous, and end at payload length")
    func nonContiguousDescriptorsAreRejected() throws {
        let descriptors = [
            try BufferDescriptor(name: "rgb", encoding: .jpeg, offset: 0, length: 3),
            try BufferDescriptor(
                name: "depth",
                encoding: .float32LE,
                offset: 4,
                length: 8,
                shape: [1, 2]
            ),
            try BufferDescriptor(
                name: "confidence",
                encoding: .uint8,
                offset: 12,
                length: 2,
                shape: [1, 2]
            ),
        ]

        #expect(throws: ProtocolValidationError.self) {
            _ = try makeHeader(descriptors: descriptors)
        }
    }

    private func makeHeader(
        bufferLengths: [Int] = [3, 8, 2],
        intrinsics: [Double] = [1, 0, 0.5, 0, 1, 0.5, 0, 0, 1],
        pose: [Double] = [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        ],
        descriptors explicitDescriptors: [BufferDescriptor]? = nil
    ) throws -> RGBDFrameHeader {
        let descriptors: [BufferDescriptor]
        if let explicitDescriptors {
            descriptors = explicitDescriptors
        } else {
            let rgbLength = bufferLengths[0]
            let depthLength = bufferLengths[1]
            descriptors = [
                try BufferDescriptor(name: "rgb", encoding: .jpeg, offset: 0, length: rgbLength),
                try BufferDescriptor(
                    name: "depth",
                    encoding: .float32LE,
                    offset: rgbLength,
                    length: depthLength,
                    shape: [1, 2]
                ),
                try BufferDescriptor(
                    name: "confidence",
                    encoding: .uint8,
                    offset: rgbLength + depthLength,
                    length: bufferLengths[2],
                    shape: [1, 2]
                ),
            ]
        }

        return try RGBDFrameHeader(
            deviceID: "front-phone",
            sessionID: UUID(uuidString: "44487d7c-b847-49db-aa37-cf326ad76078")!,
            captureID: UUID(uuidString: "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4")!,
            sequence: 184,
            captureTimestampSeconds: 9_922.107_184,
            imageOrientation: .landscapeRight,
            trackingState: .normal,
            rgb: try RGBMetadata(width: 2, height: 1, intrinsicsRowMajor: intrinsics),
            depth: try DepthMetadata(width: 2, height: 1),
            arkitWorldFromCameraRowMajor: pose,
            buffers: descriptors
        )
    }
}
