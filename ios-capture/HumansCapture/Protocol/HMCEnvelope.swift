import Foundation

enum HMCEnvelopeError: Error, Equatable, LocalizedError {
    case emptyBufferName
    case duplicateBufferName(String)
    case invalidShape(String)
    case headerTooLarge(Int)
    case payloadTooLarge(Int)
    case integerOverflow
    case nonContiguousOffset(String)
    case invalidHeader(String)

    var errorDescription: String? {
        switch self {
        case .emptyBufferName: "A buffer name cannot be empty."
        case .duplicateBufferName(let name): "Duplicate buffer name: \(name)."
        case .invalidShape(let name): "Invalid shape for buffer: \(name)."
        case .headerTooLarge(let count): "JSON header is \(count) bytes; maximum is \(HMCProtocol.maximumHeaderBytes)."
        case .payloadTooLarge(let count): "Payload is \(count) bytes; maximum is \(HMCProtocol.maximumRGBDPayloadBytes)."
        case .integerOverflow: "An envelope length does not fit uint32."
        case .nonContiguousOffset(let name): "Buffer \(name) is not tightly packed."
        case .invalidHeader(let detail): "Invalid RGBD header: \(detail)."
        }
    }
}

struct HMCPayloadBuffer: Sendable {
    let name: String
    let encoding: String
    let data: Data
    let shape: [UInt32]?
}

enum HMCEnvelope {
    static let fixedHeaderLength = 16

    static func descriptors(for buffers: [HMCPayloadBuffer]) throws -> [BufferDescriptor] {
        var names = Set<String>()
        var offset: UInt64 = 0
        var descriptors: [BufferDescriptor] = []
        descriptors.reserveCapacity(buffers.count)

        for buffer in buffers {
            guard !buffer.name.isEmpty else { throw HMCEnvelopeError.emptyBufferName }
            guard names.insert(buffer.name).inserted else {
                throw HMCEnvelopeError.duplicateBufferName(buffer.name)
            }
            if let shape = buffer.shape, shape.isEmpty || shape.contains(0) {
                throw HMCEnvelopeError.invalidShape(buffer.name)
            }
            guard offset <= UInt32.max, buffer.data.count <= UInt32.max else {
                throw HMCEnvelopeError.integerOverflow
            }
            descriptors.append(BufferDescriptor(
                name: buffer.name,
                encoding: buffer.encoding,
                offset: UInt32(offset),
                length: UInt32(buffer.data.count),
                shape: buffer.shape
            ))
            offset += UInt64(buffer.data.count)
        }
        guard offset <= UInt32.max else { throw HMCEnvelopeError.integerOverflow }
        return descriptors
    }

    static func encode(header: RGBDFrameHeader, buffers: [HMCPayloadBuffer]) throws -> Data {
        try header.validate()
        let expectedDescriptors = try descriptors(for: buffers)
        guard header.buffers == expectedDescriptors else {
            let badName = zip(header.buffers, expectedDescriptors).first(where: { $0 != $1 })?.0.name ?? "buffers"
            throw HMCEnvelopeError.nonContiguousOffset(badName)
        }

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let headerData = try encoder.encode(header)
        let payloadLength = buffers.reduce(into: 0) { $0 += $1.data.count }

        guard headerData.count <= HMCProtocol.maximumHeaderBytes else {
            throw HMCEnvelopeError.headerTooLarge(headerData.count)
        }
        guard payloadLength <= HMCProtocol.maximumRGBDPayloadBytes else {
            throw HMCEnvelopeError.payloadTooLarge(payloadLength)
        }
        guard headerData.count <= UInt32.max, payloadLength <= UInt32.max else {
            throw HMCEnvelopeError.integerOverflow
        }

        var result = Data()
        result.reserveCapacity(fixedHeaderLength + headerData.count + payloadLength)
        result.append(contentsOf: [0x48, 0x4d, 0x43, 0x31]) // HMC1
        result.appendLittleEndian(HMCProtocol.version)
        result.appendLittleEndian(HMCProtocol.rgbdMessageType)
        result.appendLittleEndian(UInt32(headerData.count))
        result.appendLittleEndian(UInt32(payloadLength))
        result.append(headerData)
        buffers.forEach { result.append($0.data) }
        return result
    }
}

extension Data {
    mutating func appendLittleEndian<T: FixedWidthInteger>(_ value: T) {
        var littleEndian = value.littleEndian
        Swift.withUnsafeBytes(of: &littleEndian) { append(contentsOf: $0) }
    }
}
