import CoreImage
import CoreVideo
import Foundation
import ImageIO

enum RGBEncodingError: Error, LocalizedError {
    case invalidDimensions
    case jpegEncodingFailed

    var errorDescription: String? {
        switch self {
        case .invalidDimensions: "Captured RGB raster dimensions are invalid."
        case .jpegEncodingFailed: "Core Image could not encode the captured YCbCr raster as JPEG."
        }
    }
}

final class RGBEncoder: @unchecked Sendable {
    private let context = CIContext(options: [
        .cacheIntermediates: true,
        .workingColorSpace: CGColorSpace(name: CGColorSpace.sRGB) as Any
    ])
    private let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    func encodeJPEG(_ pixelBuffer: CVPixelBuffer, quality: Double = 0.85) throws -> Data {
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        guard width > 0, height > 0,
              width <= HMCProtocol.maximumRasterDimension,
              height <= HMCProtocol.maximumRasterDimension else {
            throw RGBEncodingError.invalidDimensions
        }

        // CIImage performs the required full-range bi-planar YCbCr conversion. No EXIF
        // orientation is attached: pixels remain in ARKit's unmirrored sensor raster.
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        let options: [CIImageRepresentationOption: Any] = [
            kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: quality
        ]
        guard let data = context.jpegRepresentation(
            of: image,
            colorSpace: colorSpace,
            options: options
        ) else {
            throw RGBEncodingError.jpegEncodingFailed
        }
        return data
    }
}
