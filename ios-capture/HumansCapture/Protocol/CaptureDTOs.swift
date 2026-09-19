import Foundation
import simd

@propertyWrapper
struct LowercaseUUID: Codable, Equatable, Sendable {
    var wrappedValue: UUID

    init(wrappedValue: UUID) {
        self.wrappedValue = wrappedValue
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let raw = try container.decode(String.self)
        guard let value = UUID(uuidString: raw) else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected a canonical UUID string.")
        }
        wrappedValue = value
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(wrappedValue.uuidString.lowercased())
    }
}

@propertyWrapper
struct OptionalLowercaseUUID: Codable, Equatable, Sendable {
    var wrappedValue: UUID?

    init(wrappedValue: UUID?) {
        self.wrappedValue = wrappedValue
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        guard !container.decodeNil() else {
            wrappedValue = nil
            return
        }
        let raw = try container.decode(String.self)
        guard let value = UUID(uuidString: raw) else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected a canonical UUID string or null.")
        }
        wrappedValue = value
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        if let wrappedValue {
            try container.encode(wrappedValue.uuidString.lowercased())
        } else {
            try container.encodeNil()
        }
    }
}

enum HMCProtocol {
    static let version: UInt16 = 1
    static let rgbdMessageType: UInt16 = 1
    static let maximumHeaderBytes = 65_536
    static let maximumRGBDPayloadBytes = 16 * 1_024 * 1_024
    static let maximumRasterDimension = 8_192
}

enum ImageOrientation: String, Codable, CaseIterable, Sendable {
    case landscapeRight = "landscape_right"
    case landscapeLeft = "landscape_left"
    case portrait
    case portraitUpsideDown = "portrait_upside_down"
}

enum CaptureMode: String, Codable, Sendable {
    case snapshot
    case live
}

enum CaptureTrackingState: String, Codable, Sendable {
    case normal
    case limitedInitializing = "limited_initializing"
    case limitedExcessiveMotion = "limited_excessive_motion"
    case limitedInsufficientFeatures = "limited_insufficient_features"
    case limitedRelocalizing = "limited_relocalizing"
    case notAvailable = "not_available"
}

struct BufferDescriptor: Codable, Equatable, Sendable {
    let name: String
    let encoding: String
    let offset: UInt32
    let length: UInt32
    let shape: [UInt32]?
}

struct RGBMetadata: Codable, Equatable, Sendable {
    let width: UInt32
    let height: UInt32
    let intrinsicsRowMajor: [Double]

    enum CodingKeys: String, CodingKey {
        case width, height
        case intrinsicsRowMajor = "intrinsics_row_major"
    }
}

struct DepthMetadata: Codable, Equatable, Sendable {
    let width: UInt32
    let height: UInt32
    let unit: String
    let confidenceEncoding: String?

    enum CodingKeys: String, CodingKey {
        case width, height, unit
        case confidenceEncoding = "confidence_encoding"
    }
}

struct RGBDepthMapping: Codable, Equatable, Sendable {
    let method: String
    let rgbCrop: [UInt32]?
    let depthCrop: [UInt32]?

    enum CodingKeys: String, CodingKey {
        case method
        case rgbCrop = "rgb_crop"
        case depthCrop = "depth_crop"
    }

    static let normalizedUncropped = RGBDepthMapping(
        method: "normalized_uncropped_scale",
        rgbCrop: nil,
        depthCrop: nil
    )
}

struct TraceContext: Codable, Equatable, Sendable {
    let sentryTrace: String?
    let baggage: String?

    enum CodingKeys: String, CodingKey {
        case sentryTrace = "sentry_trace"
        case baggage
    }
}

struct RGBDFrameHeader: Codable, Equatable, Sendable {
    let schema: String
    let schemaVersion: UInt16
    let deviceID: String
    @LowercaseUUID var sessionID: UUID
    @LowercaseUUID var captureID: UUID
    let sequence: UInt64
    let captureTimestampSeconds: Double
    let imageOrientation: ImageOrientation
    let mirrored: Bool
    let trackingState: CaptureTrackingState
    let rgb: RGBMetadata
    let depth: DepthMetadata
    let rgbDepthMapping: RGBDepthMapping
    let arkitWorldFromCameraRowMajor: [Double]
    let trace: TraceContext?
    let buffers: [BufferDescriptor]

    enum CodingKeys: String, CodingKey {
        case schema
        case schemaVersion = "schema_version"
        case deviceID = "device_id"
        case sessionID = "session_id"
        case captureID = "capture_id"
        case sequence
        case captureTimestampSeconds = "capture_timestamp_s"
        case imageOrientation = "image_orientation"
        case mirrored
        case trackingState = "tracking_state"
        case rgb, depth
        case rgbDepthMapping = "rgb_depth_mapping"
        case arkitWorldFromCameraRowMajor = "T_arkit_world_from_camera_row_major"
        case trace, buffers
    }
}

struct ClientHello: Codable, Sendable {
    let type = "client_hello"
    let protocolVersion = 1
    let deviceID: String
    @LowercaseUUID var sessionID: UUID
    let appVersion: String
    let platform = "ios"
    let supportsSceneDepth: Bool
    let imageOrientation: ImageOrientation

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case deviceID = "device_id"
        case sessionID = "session_id"
        case appVersion = "app_version"
        case platform
        case supportsSceneDepth = "supports_scene_depth"
        case imageOrientation = "image_orientation"
    }
}

struct ServerHello: Codable, Sendable {
    let type: String
    let protocolVersion: Int
    @LowercaseUUID var serverSessionID: UUID
    let acceptedDeviceID: String
    let maxBinaryBytes: Int
    let clockProbeIntervalSeconds: Double

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case serverSessionID = "server_session_id"
        case acceptedDeviceID = "accepted_device_id"
        case maxBinaryBytes = "max_binary_bytes"
        case clockProbeIntervalSeconds = "clock_probe_interval_s"
    }
}

struct ClockPing: Codable, Sendable {
    let type: String
    let protocolVersion: Int
    @LowercaseUUID var requestID: UUID
    let backendSendTimeSeconds: Double

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case backendSendTimeSeconds = "backend_send_time_s"
    }
}

struct ClockPong: Codable, Sendable {
    let type = "clock_pong"
    let protocolVersion = 1
    @LowercaseUUID var requestID: UUID
    let backendSendTimeSeconds: Double
    let phoneReceiveTimeSeconds: Double
    let phoneSendTimeSeconds: Double

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case backendSendTimeSeconds = "backend_send_time_s"
        case phoneReceiveTimeSeconds = "phone_receive_time_s"
        case phoneSendTimeSeconds = "phone_send_time_s"
    }
}

struct CaptureRequest: Codable, Sendable {
    let type: String
    let protocolVersion: Int
    @LowercaseUUID var requestID: UUID
    @LowercaseUUID var captureID: UUID
    let mode: CaptureMode
    let notBeforePhoneTimeSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case captureID = "capture_id"
        case mode
        case notBeforePhoneTimeSeconds = "not_before_phone_time_s"
    }
}

struct AckMessage: Codable, Sendable {
    let type: String
    let protocolVersion: Int
    @LowercaseUUID var requestID: UUID
    let accepted: Bool
    let code: String
    let detail: String?

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case accepted, code, detail
    }
}

struct ErrorMessage: Codable, Sendable {
    let type: String
    let protocolVersion: Int
    @OptionalLowercaseUUID var requestID: UUID?
    let code: String
    let message: String
    let retryable: Bool

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case code, message, retryable
    }
}

struct IncomingMessageType: Codable {
    let type: String
}

enum MatrixWireEncoding {
    static func rowMajor(_ matrix: simd_float3x3) -> [Double] {
        (0..<3).flatMap { row in
            (0..<3).map { column in Double(matrix[column][row]) }
        }
    }

    static func rowMajor(_ matrix: simd_float4x4) -> [Double] {
        (0..<4).flatMap { row in
            (0..<4).map { column in Double(matrix[column][row]) }
        }
    }
}

extension RGBDFrameHeader {
    func validate() throws {
        guard schema == "hmc.rgbd_frame", schemaVersion == 1 else {
            throw HMCEnvelopeError.invalidHeader("unsupported RGBD schema/version")
        }
        guard !deviceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw HMCEnvelopeError.invalidHeader("device_id is empty")
        }
        guard captureTimestampSeconds.isFinite, captureTimestampSeconds >= 0 else {
            throw HMCEnvelopeError.invalidHeader("capture_timestamp_s is not a non-negative finite value")
        }
        guard imageOrientation == .landscapeRight, mirrored == false else {
            throw HMCEnvelopeError.invalidHeader("v1 capture must be unmirrored landscape_right")
        }
        guard (1...UInt32(HMCProtocol.maximumRasterDimension)).contains(rgb.width),
              (1...UInt32(HMCProtocol.maximumRasterDimension)).contains(rgb.height),
              (1...UInt32(HMCProtocol.maximumRasterDimension)).contains(depth.width),
              (1...UInt32(HMCProtocol.maximumRasterDimension)).contains(depth.height) else {
            throw HMCEnvelopeError.invalidHeader("raster dimensions exceed v1 limits")
        }
        guard rgb.intrinsicsRowMajor.count == 9,
              rgb.intrinsicsRowMajor.allSatisfy(\.isFinite),
              arkitWorldFromCameraRowMajor.count == 16,
              arkitWorldFromCameraRowMajor.allSatisfy(\.isFinite) else {
            throw HMCEnvelopeError.invalidHeader("matrix dimensions or values are invalid")
        }
        guard depth.unit == "meter",
              depth.confidenceEncoding == nil || depth.confidenceEncoding == "arkit_0_1_2",
              rgbDepthMapping == .normalizedUncropped else {
            throw HMCEnvelopeError.invalidHeader("unsupported depth or raster mapping metadata")
        }
    }
}
