import CoreVideo
import Foundation

struct EncodedRGBDFrame: Sendable {
    let header: RGBDFrameHeader
    let envelope: Data
    let validDepthFraction: Double
    let validPointCount: Int
    let jpegBytes: Int
}

final class FrameEncoder: @unchecked Sendable {
    private let rgbEncoder = RGBEncoder()
    private let diagnostics: CaptureEventLogger

    init(diagnostics: CaptureEventLogger = .shared) {
        self.diagnostics = diagnostics
    }

    func encode(source: CapturedFrameSource, deviceID: String) throws -> EncodedRGBDFrame {
        let captureID = source.intent.captureID
        let rgbSpan = diagnostics.startSpan(captureID: captureID, operation: "capture.convert_rgb")
        let jpeg: Data
        do {
            let quality = source.intent.mode == .live ? 0.65 : 0.85
            jpeg = try rgbEncoder.encodeJPEG(source.rgbPixelBuffer, quality: quality)
            rgbSpan?.setData(value: jpeg.count, key: "encoded_rgb_bytes")
            rgbSpan?.finish(status: .ok)
        } catch {
            rgbSpan?.finish(status: .internalError)
            throw error
        }

        let copySpan = diagnostics.startSpan(
            captureID: captureID,
            operation: "hmc.point_cloud_generation",
            description: "copy valid LiDAR depth samples"
        )
        let encodedDepth: EncodedDepth
        do {
            encodedDepth = try DepthEncoder.encode(
                depth: source.depthPixelBuffer,
                confidence: source.confidencePixelBuffer,
                rangeGate: DepthRangeGate(
                    rgbIntrinsics: source.rgbIntrinsics,
                    rgbWidth: CVPixelBufferGetWidth(source.rgbPixelBuffer),
                    rgbHeight: CVPixelBufferGetHeight(source.rgbPixelBuffer),
                    depthWidth: CVPixelBufferGetWidth(source.depthPixelBuffer),
                    depthHeight: CVPixelBufferGetHeight(source.depthPixelBuffer)
                )
            )
            copySpan?.setData(value: encodedDepth.validFraction, key: "valid_depth_fraction")
            copySpan?.setData(value: encodedDepth.validPointCount, key: "point_count")
            copySpan?.finish(status: .ok)
        } catch {
            copySpan?.finish(status: .internalError)
            throw error
        }

        let rgbWidth = CVPixelBufferGetWidth(source.rgbPixelBuffer)
        let rgbHeight = CVPixelBufferGetHeight(source.rgbPixelBuffer)
        guard (1...HMCProtocol.maximumRasterDimension).contains(rgbWidth),
              (1...HMCProtocol.maximumRasterDimension).contains(rgbHeight) else {
            throw RGBEncodingError.invalidDimensions
        }

        var buffers = [
            HMCPayloadBuffer(name: "rgb", encoding: "jpeg", data: jpeg, shape: nil),
            HMCPayloadBuffer(
                name: "depth",
                encoding: "float32_le",
                data: encodedDepth.depthMetersLittleEndian,
                shape: [UInt32(encodedDepth.height), UInt32(encodedDepth.width)]
            )
        ]
        if let confidence = encodedDepth.confidence {
            buffers.append(HMCPayloadBuffer(
                name: "confidence",
                encoding: "uint8",
                data: confidence,
                shape: [UInt32(encodedDepth.height), UInt32(encodedDepth.width)]
            ))
        }
        let descriptors = try HMCEnvelope.descriptors(for: buffers)
        let header = RGBDFrameHeader(
            schema: "hmc.rgbd_frame",
            schemaVersion: 2,
            deviceID: deviceID,
            sessionID: source.sessionID,
            captureID: captureID,
            sourceFrameID: source.sourceFrameID,
            sequence: source.sequence,
            captureTimestampSeconds: source.captureTimestampSeconds,
            imageOrientation: .portrait,
            mirrored: false,
            trackingState: source.trackingState,
            rgb: RGBMetadata(
                width: UInt32(rgbWidth),
                height: UInt32(rgbHeight),
                intrinsicsRowMajor: MatrixWireEncoding.rowMajor(source.rgbIntrinsics)
            ),
            depth: DepthMetadata(
                width: UInt32(encodedDepth.width),
                height: UInt32(encodedDepth.height),
                unit: "meter",
                confidenceEncoding: encodedDepth.confidence == nil ? nil : "arkit_0_1_2"
            ),
            rgbDepthMapping: .normalizedUncropped,
            arkitWorldFromCameraRowMajor: MatrixWireEncoding.rowMajor(source.arkitWorldFromCamera),
            buffers: descriptors,
            trace: diagnostics.traceContext(captureID: captureID)
        )

        let serializeSpan = diagnostics.startSpan(captureID: captureID, operation: "hmc.serialize")
        do {
            let envelope = try HMCEnvelope.encode(header: header, buffers: buffers)
            serializeSpan?.setData(value: envelope.count, key: "payload_bytes")
            serializeSpan?.finish(status: .ok)
            return EncodedRGBDFrame(
                header: header,
                envelope: envelope,
                validDepthFraction: encodedDepth.validFraction,
                validPointCount: encodedDepth.validPointCount,
                jpegBytes: jpeg.count
            )
        } catch {
            serializeSpan?.finish(status: .internalError)
            throw error
        }
    }
}
