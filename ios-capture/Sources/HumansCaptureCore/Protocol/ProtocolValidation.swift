import Foundation

public enum HMCProtocolLimits {
    public static let maximumJSONHeaderBytes = 65_536
    public static let maximumRGBDPayloadBytes = 16 * 1_024 * 1_024
    public static let maximumRasterDimension = 8_192
    public static let maximumCounter = UInt64(Int64.max)
}

public enum ProtocolValidationError: Error, Equatable, LocalizedError {
    case emptyField(String)
    case invalidDimension(field: String, value: Int)
    case invalidFieldCount(field: String, expected: Int, actual: Int)
    case nonFinite(field: String)
    case integerOutOfRange(field: String)
    case invalidBuffer(String)
    case payloadTooLarge(actual: Int, maximum: Int)
    case headerTooLarge(actual: Int, maximum: Int)
    case unknownFields([String])

    public var errorDescription: String? {
        switch self {
        case .emptyField(let field):
            "\(field) must not be empty"
        case .invalidDimension(let field, let value):
            "\(field) has invalid dimension \(value)"
        case .invalidFieldCount(let field, let expected, let actual):
            "\(field) requires \(expected) values, received \(actual)"
        case .nonFinite(let field):
            "\(field) contains a non-finite number"
        case .integerOutOfRange(let field):
            "\(field) is outside protocol v1's integer range"
        case .invalidBuffer(let detail):
            detail
        case .payloadTooLarge(let actual, let maximum):
            "payload is \(actual) bytes; maximum is \(maximum)"
        case .headerTooLarge(let actual, let maximum):
            "JSON header is \(actual) bytes; maximum is \(maximum)"
        case .unknownFields(let fields):
            "unknown protocol fields: \(fields.sorted().joined(separator: ", "))"
        }
    }
}

public enum HMCJSON {
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        // Stable key ordering makes saved envelopes reproducible across runs.
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.nonConformingFloatEncodingStrategy = .throw
        return encoder
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.nonConformingFloatDecodingStrategy = .throw
        return decoder
    }
}

struct AnyCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init?(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }
}

extension Decoder {
    func rejectUnknownFields(allowing allowedFields: Set<String>) throws {
        let container = try self.container(keyedBy: AnyCodingKey.self)
        let unknown = container.allKeys.map(\.stringValue).filter { !allowedFields.contains($0) }
        guard unknown.isEmpty else {
            throw ProtocolValidationError.unknownFields(unknown)
        }
    }
}

func validateDimension(_ value: Int, field: String) throws {
    guard (1...HMCProtocolLimits.maximumRasterDimension).contains(value) else {
        throw ProtocolValidationError.invalidDimension(field: field, value: value)
    }
}

func validateFinite(_ values: [Double], field: String) throws {
    guard values.allSatisfy(\.isFinite) else {
        throw ProtocolValidationError.nonFinite(field: field)
    }
}
