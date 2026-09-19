import CoreVideo
import Foundation

public enum BufferCopyError: Error, Equatable, LocalizedError {
    case invalidDimensions
    case invalidRowStride
    case sourceTooShort
    case unsupportedPixelFormat(OSType)
    case allocationFailed(CVReturn)
    case missingBaseAddress
    case invalidConfidence(UInt8)
    case mismatchedDepthAndConfidence

    public var errorDescription: String? {
        switch self {
        case .invalidDimensions: "buffer dimensions must be positive"
        case .invalidRowStride: "bytes-per-row is smaller than a logical row"
        case .sourceTooShort: "source bytes do not contain every declared row"
        case .unsupportedPixelFormat(let format): "unsupported pixel format \(format)"
        case .allocationFailed(let status): "CVPixelBuffer allocation failed with status \(status)"
        case .missingBaseAddress: "CVPixelBuffer has no readable base address"
        case .invalidConfidence(let value): "confidence \(value) is outside ARKit levels 0...2"
        case .mismatchedDepthAndConfidence: "depth and confidence dimensions do not match"
        }
    }
}

/// Owns a newly allocated pixel buffer so ARKit's borrowed frame buffer never crosses actors.
/// CVPixelBuffer is reference-counted but not annotated Sendable; exclusive ownership makes this
/// wrapper safe to transfer as long as callers do not mutate `pixelBuffer` after transfer.
public final class SendablePixelBufferCopy: @unchecked Sendable {
    public let pixelBuffer: CVPixelBuffer

    public init(copying source: CVPixelBuffer) throws {
        let width = CVPixelBufferGetWidth(source)
        let height = CVPixelBufferGetHeight(source)
        let pixelFormat = CVPixelBufferGetPixelFormatType(source)
        var destination: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            width,
            height,
            pixelFormat,
            nil,
            &destination
        )
        guard status == kCVReturnSuccess, let destination else {
            throw BufferCopyError.allocationFailed(status)
        }

        CVPixelBufferLockBaseAddress(source, .readOnly)
        CVPixelBufferLockBaseAddress(destination, [])
        defer {
            CVPixelBufferUnlockBaseAddress(destination, [])
            CVPixelBufferUnlockBaseAddress(source, .readOnly)
        }

        let planeCount = CVPixelBufferGetPlaneCount(source)
        if planeCount == 0 {
            try Self.copyRows(
                source: CVPixelBufferGetBaseAddress(source),
                sourceBytesPerRow: CVPixelBufferGetBytesPerRow(source),
                destination: CVPixelBufferGetBaseAddress(destination),
                destinationBytesPerRow: CVPixelBufferGetBytesPerRow(destination),
                height: height
            )
        } else {
            guard planeCount == CVPixelBufferGetPlaneCount(destination) else {
                throw BufferCopyError.mismatchedDepthAndConfidence
            }
            for plane in 0..<planeCount {
                try Self.copyRows(
                    source: CVPixelBufferGetBaseAddressOfPlane(source, plane),
                    sourceBytesPerRow: CVPixelBufferGetBytesPerRowOfPlane(source, plane),
                    destination: CVPixelBufferGetBaseAddressOfPlane(destination, plane),
                    destinationBytesPerRow: CVPixelBufferGetBytesPerRowOfPlane(destination, plane),
                    height: CVPixelBufferGetHeightOfPlane(source, plane)
                )
            }
        }
        self.pixelBuffer = destination
    }

    private static func copyRows(
        source: UnsafeMutableRawPointer?,
        sourceBytesPerRow: Int,
        destination: UnsafeMutableRawPointer?,
        destinationBytesPerRow: Int,
        height: Int
    ) throws {
        guard let source, let destination else { throw BufferCopyError.missingBaseAddress }
        let bytesToCopy = min(sourceBytesPerRow, destinationBytesPerRow)
        for row in 0..<height {
            destination.advanced(by: row * destinationBytesPerRow).copyMemory(
                from: source.advanced(by: row * sourceBytesPerRow),
                byteCount: bytesToCopy
            )
        }
    }
}
