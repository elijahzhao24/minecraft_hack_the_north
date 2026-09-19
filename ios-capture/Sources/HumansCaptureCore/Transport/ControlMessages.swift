import Foundation

private let controlProtocolVersion = 1

private func requireVersion(_ value: Int) throws {
    guard value == controlProtocolVersion else {
        throw ProtocolValidationError.invalidBuffer("unsupported control protocol version \(value)")
    }
}

private func canonicalUUID(_ string: String, field: String) throws -> UUID {
    guard string == string.lowercased(), let uuid = UUID(uuidString: string) else {
        throw ProtocolValidationError.invalidBuffer("\(field) must be a lowercase canonical UUID")
    }
    return uuid
}

private func requireFinite(_ value: Double, field: String) throws {
    guard value.isFinite else { throw ProtocolValidationError.nonFinite(field: field) }
}

public struct ClientHello: Encodable, Sendable {
    public let type = "client_hello"
    public let protocolVersion = controlProtocolVersion
    public let deviceID: String
    public let sessionID: UUID
    public let appVersion: String
    public let platform = "ios"
    public let supportsSceneDepth: Bool
    public let imageOrientation: ImageOrientation

    private enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case deviceID = "device_id"
        case sessionID = "session_id"
        case appVersion = "app_version"
        case platform
        case supportsSceneDepth = "supports_scene_depth"
        case imageOrientation = "image_orientation"
    }

    public init(
        deviceID: String,
        sessionID: UUID,
        appVersion: String,
        supportsSceneDepth: Bool,
        imageOrientation: ImageOrientation
    ) {
        self.deviceID = deviceID
        self.sessionID = sessionID
        self.appVersion = appVersion
        self.supportsSceneDepth = supportsSceneDepth
        self.imageOrientation = imageOrientation
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(deviceID, forKey: .deviceID)
        try container.encode(sessionID.uuidString.lowercased(), forKey: .sessionID)
        try container.encode(appVersion, forKey: .appVersion)
        try container.encode(platform, forKey: .platform)
        try container.encode(supportsSceneDepth, forKey: .supportsSceneDepth)
        try container.encode(imageOrientation, forKey: .imageOrientation)
    }
}

public struct ServerHello: Decodable, Sendable {
    public let serverSessionID: UUID
    public let acceptedDeviceID: String
    public let maximumBinaryBytes: Int
    public let clockProbeIntervalSeconds: Double

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case protocolVersion = "protocol_version"
        case serverSessionID = "server_session_id"
        case acceptedDeviceID = "accepted_device_id"
        case maximumBinaryBytes = "max_binary_bytes"
        case clockProbeIntervalSeconds = "clock_probe_interval_s"
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .type) == "server_hello" else {
            throw ProtocolValidationError.invalidBuffer("expected server_hello")
        }
        try requireVersion(container.decode(Int.self, forKey: .protocolVersion))
        serverSessionID = try canonicalUUID(
            container.decode(String.self, forKey: .serverSessionID),
            field: "server_session_id"
        )
        acceptedDeviceID = try container.decode(String.self, forKey: .acceptedDeviceID)
        maximumBinaryBytes = try container.decode(Int.self, forKey: .maximumBinaryBytes)
        clockProbeIntervalSeconds = try container.decode(Double.self, forKey: .clockProbeIntervalSeconds)
        guard maximumBinaryBytes > 0 else {
            throw ProtocolValidationError.integerOutOfRange(field: "max_binary_bytes")
        }
        try requireFinite(clockProbeIntervalSeconds, field: "clock_probe_interval_s")
    }
}

public struct ClockPing: Codable, Equatable, Sendable {
    public let type = "clock_ping"
    public let protocolVersion = controlProtocolVersion
    public let requestID: UUID
    public let backendSendTimeSeconds: Double

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case backendSendTimeSeconds = "backend_send_time_s"
    }

    public init(requestID: UUID, backendSendTimeSeconds: Double) throws {
        try requireFinite(backendSendTimeSeconds, field: "backend_send_time_s")
        self.requestID = requestID
        self.backendSendTimeSeconds = backendSendTimeSeconds
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .type) == "clock_ping" else {
            throw ProtocolValidationError.invalidBuffer("expected clock_ping")
        }
        try requireVersion(container.decode(Int.self, forKey: .protocolVersion))
        try self.init(
            requestID: canonicalUUID(container.decode(String.self, forKey: .requestID), field: "request_id"),
            backendSendTimeSeconds: container.decode(Double.self, forKey: .backendSendTimeSeconds)
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(requestID.uuidString.lowercased(), forKey: .requestID)
        try container.encode(backendSendTimeSeconds, forKey: .backendSendTimeSeconds)
    }
}

public struct ClockPong: Encodable, Equatable, Sendable {
    public let type = "clock_pong"
    public let protocolVersion = controlProtocolVersion
    public let requestID: UUID
    public let backendSendTimeSeconds: Double
    public let phoneReceiveTimeSeconds: Double
    public let phoneSendTimeSeconds: Double

    private enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case backendSendTimeSeconds = "backend_send_time_s"
        case phoneReceiveTimeSeconds = "phone_receive_time_s"
        case phoneSendTimeSeconds = "phone_send_time_s"
    }

    public init(
        requestID: UUID,
        backendSendTimeSeconds: Double,
        phoneReceiveTimeSeconds: Double,
        phoneSendTimeSeconds: Double
    ) throws {
        try requireFinite(backendSendTimeSeconds, field: "backend_send_time_s")
        try requireFinite(phoneReceiveTimeSeconds, field: "phone_receive_time_s")
        try requireFinite(phoneSendTimeSeconds, field: "phone_send_time_s")
        guard phoneSendTimeSeconds >= phoneReceiveTimeSeconds else {
            throw ProtocolValidationError.invalidBuffer("phone send time precedes receive time")
        }
        self.requestID = requestID
        self.backendSendTimeSeconds = backendSendTimeSeconds
        self.phoneReceiveTimeSeconds = phoneReceiveTimeSeconds
        self.phoneSendTimeSeconds = phoneSendTimeSeconds
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(requestID.uuidString.lowercased(), forKey: .requestID)
        try container.encode(backendSendTimeSeconds, forKey: .backendSendTimeSeconds)
        try container.encode(phoneReceiveTimeSeconds, forKey: .phoneReceiveTimeSeconds)
        try container.encode(phoneSendTimeSeconds, forKey: .phoneSendTimeSeconds)
    }
}

public enum ClockResponder {
    public static func makePong(
        for ping: ClockPing,
        phoneReceiveTimeSeconds: Double,
        phoneSendTimeSeconds: Double
    ) throws -> ClockPong {
        try ClockPong(
            requestID: ping.requestID,
            backendSendTimeSeconds: ping.backendSendTimeSeconds,
            phoneReceiveTimeSeconds: phoneReceiveTimeSeconds,
            phoneSendTimeSeconds: phoneSendTimeSeconds
        )
    }
}

public struct CaptureRequest: Decodable, Equatable, Sendable {
    public let requestID: UUID
    public let captureID: UUID
    public let mode: CaptureMode
    public let notBeforePhoneTimeSeconds: Double?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case captureID = "capture_id"
        case mode
        case notBeforePhoneTimeSeconds = "not_before_phone_time_s"
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .type) == "capture_request" else {
            throw ProtocolValidationError.invalidBuffer("expected capture_request")
        }
        try requireVersion(container.decode(Int.self, forKey: .protocolVersion))
        requestID = try canonicalUUID(container.decode(String.self, forKey: .requestID), field: "request_id")
        captureID = try canonicalUUID(container.decode(String.self, forKey: .captureID), field: "capture_id")
        mode = try container.decode(CaptureMode.self, forKey: .mode)
        guard container.contains(.notBeforePhoneTimeSeconds) else {
            throw DecodingError.keyNotFound(
                CodingKeys.notBeforePhoneTimeSeconds,
                .init(codingPath: decoder.codingPath, debugDescription: "required nullable field is missing")
            )
        }
        notBeforePhoneTimeSeconds = try container.decodeIfPresent(Double.self, forKey: .notBeforePhoneTimeSeconds)
        if let notBeforePhoneTimeSeconds {
            try requireFinite(notBeforePhoneTimeSeconds, field: "not_before_phone_time_s")
        }
    }
}

public struct Acknowledgement: Decodable, Equatable, Sendable {
    public let requestID: UUID
    public let accepted: Bool
    public let code: String
    public let detail: String?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case accepted, code, detail
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .type) == "ack" else {
            throw ProtocolValidationError.invalidBuffer("expected ack")
        }
        try requireVersion(container.decode(Int.self, forKey: .protocolVersion))
        requestID = try canonicalUUID(container.decode(String.self, forKey: .requestID), field: "request_id")
        accepted = try container.decode(Bool.self, forKey: .accepted)
        code = try container.decode(String.self, forKey: .code)
        guard container.contains(.detail) else {
            throw DecodingError.keyNotFound(
                CodingKeys.detail,
                .init(codingPath: decoder.codingPath, debugDescription: "required nullable field is missing")
            )
        }
        detail = try container.decodeIfPresent(String.self, forKey: .detail)
    }
}

public struct ClientAcknowledgement: Encodable, Equatable, Sendable {
    public let type = "ack"
    public let protocolVersion = controlProtocolVersion
    public let requestID: UUID
    public let accepted: Bool
    public let code: String
    public let detail: String?

    private enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case accepted, code, detail
    }

    public init(requestID: UUID, accepted: Bool, code: String, detail: String?) {
        self.requestID = requestID
        self.accepted = accepted
        self.code = code
        self.detail = detail
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(requestID.uuidString.lowercased(), forKey: .requestID)
        try container.encode(accepted, forKey: .accepted)
        try container.encode(code, forKey: .code)
        try container.encode(detail, forKey: .detail)
    }
}

public struct ServerErrorMessage: Decodable, Equatable, Sendable {
    public let requestID: UUID?
    public let code: String
    public let message: String
    public let retryable: Bool

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case code, message, retryable
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownFields(allowing: Set(CodingKeys.allCases.map(\.rawValue)))
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .type) == "error" else {
            throw ProtocolValidationError.invalidBuffer("expected error")
        }
        try requireVersion(container.decode(Int.self, forKey: .protocolVersion))
        guard container.contains(.requestID) else {
            throw DecodingError.keyNotFound(
                CodingKeys.requestID,
                .init(codingPath: decoder.codingPath, debugDescription: "required nullable field is missing")
            )
        }
        if let rawRequestID = try container.decodeIfPresent(String.self, forKey: .requestID) {
            requestID = try canonicalUUID(rawRequestID, field: "request_id")
        } else {
            requestID = nil
        }
        code = try container.decode(String.self, forKey: .code)
        message = try container.decode(String.self, forKey: .message)
        retryable = try container.decode(Bool.self, forKey: .retryable)
    }
}

public enum IncomingControlMessage: Sendable {
    case serverHello(ServerHello)
    case clockPing(ClockPing)
    case captureRequest(CaptureRequest)
    case acknowledgement(Acknowledgement)
    case error(ServerErrorMessage)

    public static func decode(_ data: Data) throws -> IncomingControlMessage {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String else {
            throw ProtocolValidationError.invalidBuffer("control message must be a JSON object with type")
        }
        let decoder = HMCJSON.decoder()
        switch type {
        case "server_hello": return .serverHello(try decoder.decode(ServerHello.self, from: data))
        case "clock_ping": return .clockPing(try decoder.decode(ClockPing.self, from: data))
        case "capture_request": return .captureRequest(try decoder.decode(CaptureRequest.self, from: data))
        case "ack": return .acknowledgement(try decoder.decode(Acknowledgement.self, from: data))
        case "error": return .error(try decoder.decode(ServerErrorMessage.self, from: data))
        default: throw ProtocolValidationError.invalidBuffer("unknown control message type \(type)")
        }
    }
}
