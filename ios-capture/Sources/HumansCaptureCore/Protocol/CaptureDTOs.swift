import Foundation

public enum ImageOrientation: String, Codable, CaseIterable, Sendable {
    case landscapeRight = "landscape_right"
    case landscapeLeft = "landscape_left"
    case portrait
    case portraitUpsideDown = "portrait_upside_down"
}

public enum TrackingState: String, Codable, CaseIterable, Sendable {
    case normal
    case limitedInitializing = "limited_initializing"
    case limitedExcessiveMotion = "limited_excessive_motion"
    case limitedInsufficientFeatures = "limited_insufficient_features"
    case limitedRelocalizing = "limited_relocalizing"
    case notAvailable = "not_available"
}

public enum CaptureMode: String, Codable, Sendable {
    case snapshot
    case live
}

public enum BufferEncoding: String, Codable, Sendable {
    case jpeg
    case float32LE = "float32_le"
    case uint8
}

public struct BufferDescriptor: Codable, Equatable, Sendable {
    public let name: String
    public let encoding: BufferEncoding
    public let offset: UInt32
    public let length: UInt32
    public let shape: [UInt32]?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case name, encoding, offset, length, shape
    }

    public init(
        name: String,
        encoding: BufferEncoding,
        offset: Int,
        length: Int,
        shape: [Int]? = nil
    ) throws {
        guard !name.isEmpty else { throw ProtocolValidationError.emptyField("buffer.name") }
        guard let offset = UInt32(exactly: offset) else {
            throw ProtocolValidationError.integerOutOfRange(field: "buffer.offset")
        }
        guard let length = UInt32(exactly: length) else {
            throw ProtocolValidationError.integerOutOfRange(field: "buffer.length")
        }
        let convertedShape = try shape?.map { value -> UInt32 in
            guard value > 0, let converted = UInt32(exactly: value) else {
                throw ProtocolValidationError.invalidBuffer("buffer.shape values must be positive uint32")
            }
            return converted
        }
        if encoding == .jpeg, convertedShape != nil {
            throw ProtocolValidationError.invalidBuffer("JPEG descriptors must omit shape")
        }
        if encoding != .jpeg, convertedShape?.count != 2 {
            throw ProtocolValidationError.invalidFieldCount(
                field: "buffer.shape",
                expected: 2,
                actual: convertedShape?.count ?? 0
            )
        }

        self.name = name
        self.encoding = encoding
        self.offset = offset
        self.length = length
        self.shape = convertedShape
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            name: container.decode(String.self, forKey: .name),
            encoding: container.decode(BufferEncoding.self, forKey: .encoding),
            offset: Int(container.decode(UInt32.self, forKey: .offset)),
            length: Int(container.decode(UInt32.self, forKey: .length)),
            shape: container.decodeIfPresent([UInt32].self, forKey: .shape)?.map(Int.init)
        )
    }
}

public struct RGBMetadata: Codable, Equatable, Sendable {
    public let width: Int
    public let height: Int
    public let intrinsicsRowMajor: [Double]

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case width, height
        case intrinsicsRowMajor = "intrinsics_row_major"
    }

    public init(width: Int, height: Int, intrinsicsRowMajor: [Double]) throws {
        try validateDimension(width, field: "rgb.width")
        try validateDimension(height, field: "rgb.height")
        guard intrinsicsRowMajor.count == 9 else {
            throw ProtocolValidationError.invalidFieldCount(
                field: "rgb.intrinsics_row_major",
                expected: 9,
                actual: intrinsicsRowMajor.count
            )
        }
        try validateFinite(intrinsicsRowMajor, field: "rgb.intrinsics_row_major")
        self.width = width
        self.height = height
        self.intrinsicsRowMajor = intrinsicsRowMajor
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            width: container.decode(Int.self, forKey: .width),
            height: container.decode(Int.self, forKey: .height),
            intrinsicsRowMajor: container.decode([Double].self, forKey: .intrinsicsRowMajor)
        )
    }
}

public struct DepthMetadata: Codable, Equatable, Sendable {
    public let width: Int
    public let height: Int
    public let unit = "meter"
    public let confidenceEncoding = "arkit_0_1_2"

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case width, height, unit
        case confidenceEncoding = "confidence_encoding"
    }

    public init(width: Int, height: Int) throws {
        try validateDimension(width, field: "depth.width")
        try validateDimension(height, field: "depth.height")
        self.width = width
        self.height = height
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decodedUnit = try container.decode(String.self, forKey: .unit)
        let decodedConfidence = try container.decode(String.self, forKey: .confidenceEncoding)
        guard decodedUnit == "meter" else {
            throw ProtocolValidationError.invalidBuffer("depth.unit must be meter")
        }
        guard decodedConfidence == "arkit_0_1_2" else {
            throw ProtocolValidationError.invalidBuffer("unsupported confidence encoding")
        }
        try self.init(
            width: container.decode(Int.self, forKey: .width),
            height: container.decode(Int.self, forKey: .height)
        )
    }
}

public struct RGBDFrameHeader: Codable, Equatable, Sendable {
    public let schema = "hmc.rgbd_frame"
    public let schemaVersion = 1
    public let deviceID: String
    public let sessionID: UUID
    public let captureID: UUID
    public let sequence: UInt64
    public let captureTimestampSeconds: Double
    public let imageOrientation: ImageOrientation
    public let trackingState: TrackingState
    public let rgb: RGBMetadata
    public let depth: DepthMetadata
    public let arkitWorldFromCameraRowMajor: [Double]
    public let buffers: [BufferDescriptor]

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case schema
        case schemaVersion = "schema_version"
        case deviceID = "device_id"
        case sessionID = "session_id"
        case captureID = "capture_id"
        case sequence
        case captureTimestampSeconds = "capture_timestamp_s"
        case imageOrientation = "image_orientation"
        case trackingState = "tracking_state"
        case rgb, depth
        case arkitWorldFromCameraRowMajor = "T_arkit_world_from_camera_row_major"
        case buffers
    }

    public init(
        deviceID: String,
        sessionID: UUID,
        captureID: UUID,
        sequence: UInt64,
        captureTimestampSeconds: Double,
        imageOrientation: ImageOrientation,
        trackingState: TrackingState,
        rgb: RGBMetadata,
        depth: DepthMetadata,
        arkitWorldFromCameraRowMajor: [Double],
        buffers: [BufferDescriptor]
    ) throws {
        guard !deviceID.isEmpty else { throw ProtocolValidationError.emptyField("device_id") }
        guard sequence <= HMCProtocolLimits.maximumCounter else {
            throw ProtocolValidationError.integerOutOfRange(field: "sequence")
        }
        guard captureTimestampSeconds.isFinite else {
            throw ProtocolValidationError.nonFinite(field: "capture_timestamp_s")
        }
        guard arkitWorldFromCameraRowMajor.count == 16 else {
            throw ProtocolValidationError.invalidFieldCount(
                field: "T_arkit_world_from_camera_row_major",
                expected: 16,
                actual: arkitWorldFromCameraRowMajor.count
            )
        }
        try validateFinite(
            arkitWorldFromCameraRowMajor,
            field: "T_arkit_world_from_camera_row_major"
        )
        try Self.validateBuffers(buffers, depth: depth)

        self.deviceID = deviceID
        self.sessionID = sessionID
        self.captureID = captureID
        self.sequence = sequence
        self.captureTimestampSeconds = captureTimestampSeconds
        self.imageOrientation = imageOrientation
        self.trackingState = trackingState
        self.rgb = rgb
        self.depth = depth
        self.arkitWorldFromCameraRowMajor = arkitWorldFromCameraRowMajor
        self.buffers = buffers
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .schema) == "hmc.rgbd_frame" else {
            throw ProtocolValidationError.invalidBuffer("unsupported RGBD schema")
        }
        guard try container.decode(Int.self, forKey: .schemaVersion) == 1 else {
            throw ProtocolValidationError.invalidBuffer("unsupported RGBD schema version")
        }
        let sessionString = try container.decode(String.self, forKey: .sessionID)
        let captureString = try container.decode(String.self, forKey: .captureID)
        guard sessionString == sessionString.lowercased(), let sessionID = UUID(uuidString: sessionString) else {
            throw ProtocolValidationError.invalidBuffer("session_id must be a lowercase canonical UUID")
        }
        guard captureString == captureString.lowercased(), let captureID = UUID(uuidString: captureString) else {
            throw ProtocolValidationError.invalidBuffer("capture_id must be a lowercase canonical UUID")
        }
        try self.init(
            deviceID: container.decode(String.self, forKey: .deviceID),
            sessionID: sessionID,
            captureID: captureID,
            sequence: container.decode(UInt64.self, forKey: .sequence),
            captureTimestampSeconds: container.decode(Double.self, forKey: .captureTimestampSeconds),
            imageOrientation: container.decode(ImageOrientation.self, forKey: .imageOrientation),
            trackingState: container.decode(TrackingState.self, forKey: .trackingState),
            rgb: container.decode(RGBMetadata.self, forKey: .rgb),
            depth: container.decode(DepthMetadata.self, forKey: .depth),
            arkitWorldFromCameraRowMajor: container.decode([Double].self, forKey: .arkitWorldFromCameraRowMajor),
            buffers: container.decode([BufferDescriptor].self, forKey: .buffers)
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schema, forKey: .schema)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(deviceID, forKey: .deviceID)
        try container.encode(sessionID.uuidString.lowercased(), forKey: .sessionID)
        try container.encode(captureID.uuidString.lowercased(), forKey: .captureID)
        try container.encode(sequence, forKey: .sequence)
        try container.encode(captureTimestampSeconds, forKey: .captureTimestampSeconds)
        try container.encode(imageOrientation, forKey: .imageOrientation)
        try container.encode(trackingState, forKey: .trackingState)
        try container.encode(rgb, forKey: .rgb)
        try container.encode(depth, forKey: .depth)
        try container.encode(arkitWorldFromCameraRowMajor, forKey: .arkitWorldFromCameraRowMajor)
        try container.encode(buffers, forKey: .buffers)
    }

    private static func validateBuffers(_ buffers: [BufferDescriptor], depth: DepthMetadata) throws {
        let expectedNames = ["rgb", "depth", "confidence"]
        guard buffers.map(\.name) == expectedNames else {
            throw ProtocolValidationError.invalidBuffer("RGBD buffers must be ordered rgb, depth, confidence")
        }
        guard Set(buffers.map(\.name)).count == buffers.count else {
            throw ProtocolValidationError.invalidBuffer("buffer names must be unique")
        }

        var nextOffset: UInt64 = 0
        for descriptor in buffers {
            guard UInt64(descriptor.offset) == nextOffset else {
                throw ProtocolValidationError.invalidBuffer("buffers must be tightly concatenated")
            }
            nextOffset += UInt64(descriptor.length)
        }
        guard nextOffset <= UInt64(HMCProtocolLimits.maximumRGBDPayloadBytes) else {
            throw ProtocolValidationError.payloadTooLarge(
                actual: Int(nextOffset),
                maximum: HMCProtocolLimits.maximumRGBDPayloadBytes
            )
        }

        let pixelCount = depth.width * depth.height
        let depthDescriptor = buffers[1]
        let confidenceDescriptor = buffers[2]
        guard depthDescriptor.encoding == .float32LE,
              depthDescriptor.shape == [UInt32(depth.height), UInt32(depth.width)],
              Int(depthDescriptor.length) == pixelCount * MemoryLayout<Float32>.size else {
            throw ProtocolValidationError.invalidBuffer("depth descriptor does not match the depth raster")
        }
        guard confidenceDescriptor.encoding == .uint8,
              confidenceDescriptor.shape == [UInt32(depth.height), UInt32(depth.width)],
              Int(confidenceDescriptor.length) == pixelCount else {
            throw ProtocolValidationError.invalidBuffer("confidence descriptor does not match the depth raster")
        }
        guard buffers[0].encoding == .jpeg else {
            throw ProtocolValidationError.invalidBuffer("rgb buffer must use JPEG encoding")
        }
    }
}
