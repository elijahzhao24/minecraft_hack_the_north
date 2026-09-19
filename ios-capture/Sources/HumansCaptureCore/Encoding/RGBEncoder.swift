import CoreImage
import CoreVideo
import Foundation
import ImageIO
import UniformTypeIdentifiers
import VideoToolbox

public enum RGBEncodingError: Error, Equatable, LocalizedError {
    case invalidQuality
    case imageConversionFailed

    public var errorDescription: String? {
        switch self {
        case .invalidQuality: "JPEG quality must be between zero and one"
        case .imageConversionFailed: "Core Image could not render the captured pixel buffer"
        }
    }
}

public final class RGBEncoder: @unchecked Sendable {
    /// The workflow's initial quality setting; named so tuning never hides a magic number.
    public static let defaultJPEGQuality = 0.85

    private let context: CIContext
    private let colorSpace: CGColorSpace

    public init(
        context: CIContext = CIContext(options: [.cacheIntermediates: true]),
        colorSpace: CGColorSpace = CGColorSpaceCreateDeviceRGB()
    ) {
        self.context = context
        self.colorSpace = colorSpace
    }

    public func encodeJPEG(
        pixelBuffer: CVPixelBuffer,
        quality: Double = RGBEncoder.defaultJPEGQuality
    ) throws -> Data {
        guard (0...1).contains(quality) else { throw RGBEncodingError.invalidQuality }
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        var videoToolboxImage: CGImage?
        let conversionStatus = VTCreateCGImageFromCVPixelBuffer(
            pixelBuffer,
            options: nil,
            imageOut: &videoToolboxImage
        )
        // VideoToolbox handles camera YCbCr directly. Core Image is the fallback for pixel
        // formats VideoToolbox cannot convert on a particular OS release.
        guard let cgImage = conversionStatus == noErr
            ? videoToolboxImage
            : context.createCGImage(image, from: image.extent, format: .RGBA8, colorSpace: colorSpace)
        else {
            throw RGBEncodingError.imageConversionFailed
        }

        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            output,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            throw RGBEncodingError.imageConversionFailed
        }
        // Omitting orientation metadata keeps JPEG coordinates identical to the camera raster.
        CGImageDestinationAddImage(
            destination,
            cgImage,
            [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else {
            throw RGBEncodingError.imageConversionFailed
        }
        return output as Data
    }
}
