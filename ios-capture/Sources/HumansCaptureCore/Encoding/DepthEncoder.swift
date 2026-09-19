import CoreVideo
import Foundation

public enum DepthPlaneCopier {
    public static func copyFloat32MetersLE(
        _ source: Data,
        width: Int,
        height: Int,
        bytesPerRow: Int
    ) throws -> Data {
        let elementSize = MemoryLayout<Float32>.size
        try validate(source, width: width, height: height, bytesPerRow: bytesPerRow, elementSize: elementSize)

        var output = Data()
        output.reserveCapacity(width * height * elementSize)
        source.withUnsafeBytes { rawBytes in
            for row in 0..<height {
                let rowOffset = row * bytesPerRow
                for column in 0..<width {
                    let sampleOffset = rowOffset + column * elementSize
                    let bits = rawBytes.loadUnaligned(fromByteOffset: sampleOffset, as: UInt32.self)
                    let value = Float32(bitPattern: UInt32(littleEndian: bits))
                    // Zero is the wire sentinel for non-finite and non-positive depth.
                    let sanitized: Float32 = value.isFinite && value > 0 ? value : 0
                    var littleEndianBits = sanitized.bitPattern.littleEndian
                    Swift.withUnsafeBytes(of: &littleEndianBits) { output.append(contentsOf: $0) }
                }
            }
        }
        return output
    }

    public static func copyConfidence(
        _ source: Data,
        width: Int,
        height: Int,
        bytesPerRow: Int
    ) throws -> Data {
        try validate(source, width: width, height: height, bytesPerRow: bytesPerRow, elementSize: 1)
        var output = Data()
        output.reserveCapacity(width * height)
        for row in 0..<height {
            let rowStart = row * bytesPerRow
            for column in 0..<width {
                let value = source[rowStart + column]
                guard value <= 2 else { throw BufferCopyError.invalidConfidence(value) }
                output.append(value)
            }
        }
        return output
    }

    private static func validate(
        _ source: Data,
        width: Int,
        height: Int,
        bytesPerRow: Int,
        elementSize: Int
    ) throws {
        guard width > 0, height > 0 else { throw BufferCopyError.invalidDimensions }
        let (logicalRowBytes, rowOverflow) = width.multipliedReportingOverflow(by: elementSize)
        guard !rowOverflow, bytesPerRow >= logicalRowBytes else { throw BufferCopyError.invalidRowStride }
        let (requiredBytes, sizeOverflow) = bytesPerRow.multipliedReportingOverflow(by: height)
        guard !sizeOverflow, source.count >= requiredBytes else { throw BufferCopyError.sourceTooShort }
    }
}

public struct EncodedDepth: Sendable, Equatable {
    public let width: Int
    public let height: Int
    public let depthMetersLE: Data
    public let confidence: Data
}

public enum DepthEncoder {
    public static func copy(
        depthPixelBuffer: CVPixelBuffer,
        confidencePixelBuffer: CVPixelBuffer
    ) throws -> EncodedDepth {
        guard CVPixelBufferGetPixelFormatType(depthPixelBuffer) == kCVPixelFormatType_DepthFloat32 else {
            throw BufferCopyError.unsupportedPixelFormat(CVPixelBufferGetPixelFormatType(depthPixelBuffer))
        }
        guard CVPixelBufferGetPixelFormatType(confidencePixelBuffer) == kCVPixelFormatType_OneComponent8 else {
            throw BufferCopyError.unsupportedPixelFormat(CVPixelBufferGetPixelFormatType(confidencePixelBuffer))
        }
        let width = CVPixelBufferGetWidth(depthPixelBuffer)
        let height = CVPixelBufferGetHeight(depthPixelBuffer)
        guard width == CVPixelBufferGetWidth(confidencePixelBuffer),
              height == CVPixelBufferGetHeight(confidencePixelBuffer) else {
            throw BufferCopyError.mismatchedDepthAndConfidence
        }

        CVPixelBufferLockBaseAddress(depthPixelBuffer, .readOnly)
        CVPixelBufferLockBaseAddress(confidencePixelBuffer, .readOnly)
        defer {
            CVPixelBufferUnlockBaseAddress(confidencePixelBuffer, .readOnly)
            CVPixelBufferUnlockBaseAddress(depthPixelBuffer, .readOnly)
        }
        guard let depthBase = CVPixelBufferGetBaseAddress(depthPixelBuffer),
              let confidenceBase = CVPixelBufferGetBaseAddress(confidencePixelBuffer) else {
            throw BufferCopyError.missingBaseAddress
        }
        let depthBytesPerRow = CVPixelBufferGetBytesPerRow(depthPixelBuffer)
        let confidenceBytesPerRow = CVPixelBufferGetBytesPerRow(confidencePixelBuffer)
        let depthSource = Data(bytes: depthBase, count: depthBytesPerRow * height)
        let confidenceSource = Data(bytes: confidenceBase, count: confidenceBytesPerRow * height)

        return EncodedDepth(
            width: width,
            height: height,
            depthMetersLE: try DepthPlaneCopier.copyFloat32MetersLE(
                depthSource,
                width: width,
                height: height,
                bytesPerRow: depthBytesPerRow
            ),
            confidence: try DepthPlaneCopier.copyConfidence(
                confidenceSource,
                width: width,
                height: height,
                bytesPerRow: confidenceBytesPerRow
            )
        )
    }
}
