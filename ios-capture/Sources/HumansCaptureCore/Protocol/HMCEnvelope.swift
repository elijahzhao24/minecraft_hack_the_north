import Foundation

public enum HMCEnvelope {
    public static let prefixLength = 16

    private static let magic: [UInt8] = [0x48, 0x4D, 0x43, 0x31] // ASCII "HMC1"
    private static let envelopeVersion: UInt16 = 1
    private static let rgbdFrameMessageType: UInt16 = 1

    public static func encode(header: RGBDFrameHeader, buffers: [Data]) throws -> Data {
        guard buffers.count == header.buffers.count else {
            throw ProtocolValidationError.invalidBuffer("buffer data count does not match descriptors")
        }
        for (descriptor, bytes) in zip(header.buffers, buffers) {
            guard descriptor.length == UInt32(bytes.count) else {
                throw ProtocolValidationError.invalidBuffer("\(descriptor.name) byte count does not match its descriptor")
            }
        }

        // Header construction performs the contract validation before envelope allocation.
        let headerBytes = try HMCJSON.encoder().encode(header)
        guard headerBytes.count <= HMCProtocolLimits.maximumJSONHeaderBytes else {
            throw ProtocolValidationError.headerTooLarge(
                actual: headerBytes.count,
                maximum: HMCProtocolLimits.maximumJSONHeaderBytes
            )
        }
        let payloadLength = buffers.reduce(0) { $0 + $1.count }
        guard payloadLength <= HMCProtocolLimits.maximumRGBDPayloadBytes else {
            throw ProtocolValidationError.payloadTooLarge(
                actual: payloadLength,
                maximum: HMCProtocolLimits.maximumRGBDPayloadBytes
            )
        }
        guard let headerLength32 = UInt32(exactly: headerBytes.count),
              let payloadLength32 = UInt32(exactly: payloadLength) else {
            throw ProtocolValidationError.integerOutOfRange(field: "envelope lengths")
        }

        var envelope = Data()
        envelope.reserveCapacity(prefixLength + headerBytes.count + payloadLength)
        envelope.append(contentsOf: magic)
        envelope.appendLittleEndian(envelopeVersion)
        envelope.appendLittleEndian(rgbdFrameMessageType)
        envelope.appendLittleEndian(headerLength32)
        envelope.appendLittleEndian(payloadLength32)
        envelope.append(headerBytes)
        for buffer in buffers {
            envelope.append(buffer)
        }
        return envelope
    }
}

private extension Data {
    mutating func appendLittleEndian<T: FixedWidthInteger>(_ value: T) {
        var littleEndian = value.littleEndian
        Swift.withUnsafeBytes(of: &littleEndian) { bytes in
            append(contentsOf: bytes)
        }
    }
}
