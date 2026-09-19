import Foundation

public struct EncodedRGBDFrame: Sendable {
    public let captureID: UUID
    public let requestID: UUID?
    public let mode: CaptureMode
    public let header: RGBDFrameHeader
    public let envelope: Data
    public let jpegByteCount: Int
    public let encodingDurationSeconds: TimeInterval
    public let validDepthFraction: Double

    public var pendingCapture: PendingCapture {
        PendingCapture(
            captureID: captureID,
            requestID: requestID,
            mode: mode,
            envelope: envelope
        )
    }
}

public actor RGBDFrameAssembler {
    private let rgbEncoder: RGBEncoder
    private let uptime: @Sendable () -> TimeInterval

    public init(
        rgbEncoder: RGBEncoder = RGBEncoder(),
        uptime: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    ) {
        self.rgbEncoder = rgbEncoder
        self.uptime = uptime
    }

    public func assemble(_ captured: CapturedBuffers) throws -> EncodedRGBDFrame {
        let expectedDepthBytes = captured.depthWidth * captured.depthHeight * MemoryLayout<Float32>.size
        let expectedConfidenceBytes = captured.depthWidth * captured.depthHeight
        guard captured.depthMetersLE.count == expectedDepthBytes else {
            throw ProtocolValidationError.invalidBuffer("owned depth bytes do not match declared dimensions")
        }
        guard captured.confidence.count == expectedConfidenceBytes else {
            throw ProtocolValidationError.invalidBuffer("owned confidence bytes do not match declared dimensions")
        }

        let startTime = uptime()
        let jpeg = try rgbEncoder.encodeJPEG(pixelBuffer: captured.rgbPixelBuffer.pixelBuffer)
        let encodingDuration = uptime() - startTime
        let depthOffset = jpeg.count
        let confidenceOffset = depthOffset + captured.depthMetersLE.count
        let descriptors = [
            try BufferDescriptor(name: "rgb", encoding: .jpeg, offset: 0, length: jpeg.count),
            try BufferDescriptor(
                name: "depth",
                encoding: .float32LE,
                offset: depthOffset,
                length: captured.depthMetersLE.count,
                shape: [captured.depthHeight, captured.depthWidth]
            ),
            try BufferDescriptor(
                name: "confidence",
                encoding: .uint8,
                offset: confidenceOffset,
                length: captured.confidence.count,
                shape: [captured.depthHeight, captured.depthWidth]
            ),
        ]
        let header = try RGBDFrameHeader(
            deviceID: captured.deviceID,
            sessionID: captured.sessionID,
            captureID: captured.captureID,
            sequence: captured.sequence,
            captureTimestampSeconds: captured.captureTimestampSeconds,
            imageOrientation: captured.orientation,
            trackingState: captured.trackingState,
            rgb: RGBMetadata(
                width: captured.rgbWidth,
                height: captured.rgbHeight,
                intrinsicsRowMajor: MatrixWireFormatter.rowMajor(captured.rgbIntrinsics)
            ),
            depth: DepthMetadata(width: captured.depthWidth, height: captured.depthHeight),
            arkitWorldFromCameraRowMajor: MatrixWireFormatter.rowMajor(captured.arkitWorldFromCamera),
            buffers: descriptors
        )
        let envelope = try HMCEnvelope.encode(
            header: header,
            buffers: [jpeg, captured.depthMetersLE, captured.confidence]
        )

        return EncodedRGBDFrame(
            captureID: captured.captureID,
            requestID: captured.requestID,
            mode: captured.mode,
            header: header,
            envelope: envelope,
            jpegByteCount: jpeg.count,
            encodingDurationSeconds: max(0, encodingDuration),
            validDepthFraction: Self.validDepthFraction(captured.depthMetersLE)
        )
    }

    private static func validDepthFraction(_ depthBytes: Data) -> Double {
        let sampleCount = depthBytes.count / MemoryLayout<Float32>.size
        guard sampleCount > 0 else { return 0 }
        let validCount = depthBytes.withUnsafeBytes { bytes in
            (0..<sampleCount).reduce(into: 0) { count, index in
                let bits = bytes.loadUnaligned(
                    fromByteOffset: index * MemoryLayout<Float32>.size,
                    as: UInt32.self
                )
                let value = Float32(bitPattern: UInt32(littleEndian: bits))
                if value.isFinite, value > 0 { count += 1 }
            }
        }
        return Double(validCount) / Double(sampleCount)
    }
}
