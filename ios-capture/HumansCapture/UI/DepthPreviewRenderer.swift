import CoreGraphics
import CoreVideo
import Foundation

enum DepthPreviewRenderer {
    static func render(_ pixelBuffer: CVPixelBuffer, rangeGate: DepthRangeGate, near: Float = 0.2, far: Float = 5.0) -> CGImage? {
        guard CVPixelBufferGetPixelFormatType(pixelBuffer) == kCVPixelFormatType_DepthFloat32 else { return nil }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        guard width > 0, height > 0 else { return nil }
        guard CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let stride = CVPixelBufferGetBytesPerRow(pixelBuffer) / MemoryLayout<Float>.size
        var rgba = Data(count: width * height * 4)

        rgba.withUnsafeMutableBytes { destinationRaw in
            guard let destination = destinationRaw.bindMemory(to: UInt8.self).baseAddress else { return }
            for y in 0..<height {
                let source = base.assumingMemoryBound(to: Float.self).advanced(by: y * stride)
                for x in 0..<width {
                    let depth = source[x]
                    let output = destination.advanced(by: (y * width + x) * 4)
                    guard depth >= near, rangeGate.contains(depth, u: x, v: y, maxRange: Double(far)) else {
                        output[0] = 0; output[1] = 0; output[2] = 0; output[3] = 255
                        continue
                    }
                    let t = max(0, min(1, (depth - near) / (far - near)))
                    // Compact blue → cyan → yellow map; nearest pixels are warm.
                    output[0] = UInt8(max(0, min(255, (1 - t) * 510)))
                    output[1] = UInt8(max(0, min(255, (1 - abs(t - 0.5) * 2) * 255)))
                    output[2] = UInt8(max(0, min(255, t * 510)))
                    output[3] = 255
                }
            }
        }

        guard let provider = CGDataProvider(data: rgba as CFData),
              let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        )
    }
}

struct RenderedDepthPreview: @unchecked Sendable {
    let image: CGImage?
}
