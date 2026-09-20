import CoreVideo
import Foundation
import simd

enum DepthEncodingError: Error, Equatable, LocalizedError {
    case unsupportedPixelFormat(OSType)
    case dimensionsMismatch
    case invalidDimensions
    case lockFailed(CVReturn)
    case invalidRowStride
    case invalidConfidence(UInt8)

    var errorDescription: String? {
        switch self {
        case .unsupportedPixelFormat(let format): "Unsupported depth pixel format: \(format)."
        case .dimensionsMismatch: "Depth and confidence dimensions do not match."
        case .invalidDimensions: "Raster dimensions are invalid."
        case .lockFailed(let status): "Could not lock pixel buffer: \(status)."
        case .invalidRowStride: "Pixel-buffer row stride is smaller than the active row."
        case .invalidConfidence(let value): "Unknown ARKit confidence value: \(value)."
        }
    }
}

struct EncodedDepth: Sendable {
    let width: Int
    let height: Int
    let depthMetersLittleEndian: Data
    let confidence: Data?
    let validFraction: Double
    let validPointCount: Int
}

/// Metric ray-distance filter shared by wire encoding and the depth preview.
struct DepthRangeGate: Sendable {
    let fx: Double
    let fy: Double
    let cx: Double
    let cy: Double

    init(rgbIntrinsics k: simd_float3x3, rgbWidth: Int, rgbHeight: Int, depthWidth: Int, depthHeight: Int) {
        let sx = Double(depthWidth) / Double(rgbWidth)
        let sy = Double(depthHeight) / Double(rgbHeight)
        fx = Double(k[0][0]) * sx
        fy = Double(k[1][1]) * sy
        cx = Double(k[2][0]) * sx
        cy = Double(k[2][1]) * sy
    }

    func contains(_ depth: Float, u: Int, v: Int, maxRange: Double = 5.0) -> Bool {
        guard depth.isFinite, depth > 0, fx.isFinite, fy.isFinite,
              cx.isFinite, cy.isFinite, fx > 0, fy > 0,
              maxRange.isFinite, maxRange > 0, maxRange <= 5.0 else { return false }
        let x = (Double(u) - cx) / fx
        let y = (Double(v) - cy) / fy
        return Double(depth) * Double(depth) * (1 + x * x + y * y) <= maxRange * maxRange
    }
}

enum DepthEncoder {
    static func encode(depth: CVPixelBuffer, confidence: CVPixelBuffer?, rangeGate: DepthRangeGate) throws -> EncodedDepth {
        let width = CVPixelBufferGetWidth(depth)
        let height = CVPixelBufferGetHeight(depth)
        guard width > 0, height > 0,
              width <= HMCProtocol.maximumRasterDimension,
              height <= HMCProtocol.maximumRasterDimension else {
            throw DepthEncodingError.invalidDimensions
        }
        guard CVPixelBufferGetPixelFormatType(depth) == kCVPixelFormatType_DepthFloat32 else {
            throw DepthEncodingError.unsupportedPixelFormat(CVPixelBufferGetPixelFormatType(depth))
        }

        let status = CVPixelBufferLockBaseAddress(depth, .readOnly)
        guard status == kCVReturnSuccess else { throw DepthEncodingError.lockFailed(status) }
        defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(depth) else {
            throw DepthEncodingError.invalidRowStride
        }

        let rowBytes = CVPixelBufferGetBytesPerRow(depth)
        let (depthData, validCount) = try copyFloat32Rows(
            baseAddress: base,
            width: width,
            height: height,
            bytesPerRow: rowBytes,
            rangeGate: rangeGate
        )

        var confidenceData: Data?
        if let confidence {
            guard CVPixelBufferGetWidth(confidence) == width,
                  CVPixelBufferGetHeight(confidence) == height else {
                throw DepthEncodingError.dimensionsMismatch
            }
            let confidenceStatus = CVPixelBufferLockBaseAddress(confidence, .readOnly)
            guard confidenceStatus == kCVReturnSuccess else {
                throw DepthEncodingError.lockFailed(confidenceStatus)
            }
            defer { CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
            guard let confidenceBase = CVPixelBufferGetBaseAddress(confidence) else {
                throw DepthEncodingError.invalidRowStride
            }
            confidenceData = try copyUInt8Rows(
                baseAddress: confidenceBase,
                width: width,
                height: height,
                bytesPerRow: CVPixelBufferGetBytesPerRow(confidence),
                allowedRange: 0...2
            )
        }

        return EncodedDepth(
            width: width,
            height: height,
            depthMetersLittleEndian: depthData,
            confidence: confidenceData,
            validFraction: Double(validCount) / Double(width * height),
            validPointCount: validCount
        )
    }

    static func copyFloat32Rows(
        baseAddress: UnsafeRawPointer,
        width: Int,
        height: Int,
        bytesPerRow: Int,
        rangeGate: DepthRangeGate? = nil
    ) throws -> (Data, validCount: Int) {
        guard width > 0, height > 0 else { throw DepthEncodingError.invalidDimensions }
        let activeBytes = width * MemoryLayout<Float>.size
        guard bytesPerRow >= activeBytes else { throw DepthEncodingError.invalidRowStride }
        var data = Data(capacity: activeBytes * height)
        var validCount = 0
        for row in 0..<height {
            let rowBase = baseAddress.advanced(by: row * bytesPerRow)
                .assumingMemoryBound(to: Float.self)
            for column in 0..<width {
                let value = rowBase[column]
                let normalized: Float
                if value.isFinite && value > 0 && value <= 5.0
                    && (rangeGate?.contains(value, u: column, v: row) ?? true) {
                    normalized = value
                    validCount += 1
                } else {
                    normalized = 0
                }
                var bits = normalized.bitPattern.littleEndian
                Swift.withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
            }
        }
        return (data, validCount)
    }

    static func copyUInt8Rows(
        baseAddress: UnsafeRawPointer,
        width: Int,
        height: Int,
        bytesPerRow: Int,
        allowedRange: ClosedRange<UInt8>? = nil
    ) throws -> Data {
        guard width > 0, height > 0 else { throw DepthEncodingError.invalidDimensions }
        guard bytesPerRow >= width else { throw DepthEncodingError.invalidRowStride }
        var data = Data(capacity: width * height)
        for row in 0..<height {
            let rowBase = baseAddress.advanced(by: row * bytesPerRow)
                .assumingMemoryBound(to: UInt8.self)
            for column in 0..<width {
                let value = rowBase[column]
                if let allowedRange, !allowedRange.contains(value) {
                    throw DepthEncodingError.invalidConfidence(value)
                }
                data.append(value)
            }
        }
        return data
    }
}
