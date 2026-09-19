import Foundation

struct PipelineStatistics: Sendable {
    var queueDepth = 0
    var droppedLiveFrames: UInt64 = 0
    var sentFrames: UInt64 = 0
    var lastValidDepthFraction: Double?
    var lastFixture: SavedFixture?
}

enum CapturePipelineEvent: Sendable {
    case statistics(PipelineStatistics)
    case snapshotRejected(captureID: UUID, reason: String)
    case frameSent(captureID: UUID, sequence: UInt64)
    case failed(captureID: UUID, message: String)
}

actor CapturePipeline {
    private let encoder: FrameEncoder
    private let socket: CaptureSocket
    private let recorder: FixtureRecorder
    private let diagnostics: CaptureEventLogger
    private var deviceID: String
    private var queue = CaptureBackpressureQueue<CapturedFrameSource>(maximumSnapshots: 2)
    private var draining = false
    private var statistics = PipelineStatistics()
    private var liveSentSinceAggregate: UInt64 = 0
    private var liveDroppedAtLastAggregate: UInt64 = 0
    private var lastLiveAggregateTime = ProcessInfo.processInfo.systemUptime
    private var epoch: UInt64 = 0

    var eventHandler: (@Sendable (CapturePipelineEvent) -> Void)?

    init(
        deviceID: String,
        socket: CaptureSocket,
        encoder: FrameEncoder = FrameEncoder(),
        recorder: FixtureRecorder = FixtureRecorder(),
        diagnostics: CaptureEventLogger = .shared
    ) {
        self.deviceID = deviceID
        self.socket = socket
        self.encoder = encoder
        self.recorder = recorder
        self.diagnostics = diagnostics
    }

    func setEventHandler(_ handler: @escaping @Sendable (CapturePipelineEvent) -> Void) {
        eventHandler = handler
    }

    func updateDeviceID(_ value: String) {
        deviceID = value
    }

    func submit(_ source: CapturedFrameSource) {
        if source.intent.mode == .snapshot {
            guard case .accepted = queue.enqueueSnapshot(source) else {
                diagnostics.log(.warning, "capture.queue.snapshot_rejected", attributes: [
                    "capture_id": source.intent.captureID.uuidString.lowercased(),
                    "queue_depth": statistics.queueDepth
                ])
                eventHandler?(.snapshotRejected(captureID: source.intent.captureID, reason: "snapshot queue is full"))
                diagnostics.finishCapture(
                    captureID: source.intent.captureID,
                    error: PipelineError.snapshotQueueFull
                )
                return
            }
        } else {
            if case .replacedLive(let replaced) = queue.enqueueLive(source) {
                statistics.droppedLiveFrames &+= 1
                diagnostics.finishCapture(
                    captureID: replaced.intent.captureID,
                    error: PipelineError.supersededLiveFrame
                )
                diagnostics.log(
                    .warning,
                    "capture.queue.live_frame_replaced",
                    attributes: ["dropped_frame_count": statistics.droppedLiveFrames],
                    rateLimitKey: "live_frame_replaced"
                )
            }
        }
        publishStatistics()
        guard !draining else { return }
        draining = true
        Task { await drain() }
    }

    func setLive(_ enabled: Bool, rateHz: Double? = nil) async throws {
        try await socket.sendLiveControl(enabled: enabled, rateHz: rateHz)
    }

    func clear() {
        epoch &+= 1
        for source in queue.removeAll() {
            diagnostics.finishCapture(
                captureID: source.intent.captureID,
                error: PipelineError.sessionEnded
            )
        }
        publishStatistics()
    }

    private func drain() async {
        while let source = takeNext() {
            let captureID = source.intent.captureID
            let sourceEpoch = epoch
            do {
                let queueSpan = diagnostics.startSpan(captureID: captureID, operation: "capture.queue_wait")
                queueSpan?.setData(value: statistics.queueDepth, key: "queue_depth")
                queueSpan?.finish(status: .ok)

                let encodeSpan = diagnostics.startSpan(captureID: captureID, operation: "capture.encode")
                let start = ProcessInfo.processInfo.systemUptime
                let encoded = try encoder.encode(source: source, deviceID: deviceID)
                let encodeDurationMS = (ProcessInfo.processInfo.systemUptime - start) * 1_000
                encodeSpan?.setData(value: encodeDurationMS, key: "encode_duration_ms")
                encodeSpan?.finish(status: .ok)
                guard sourceEpoch == epoch else { throw PipelineError.sessionEnded }

                if source.intent.mode == .snapshot {
                    let fixture = try await recorder.save(envelope: encoded.envelope, header: encoded.header)
                    statistics.lastFixture = fixture
                    diagnostics.log(.info, "capture.fixture.saved", attributes: [
                        "device_id": deviceID,
                        "session_id": source.sessionID.uuidString.lowercased(),
                        "sequence": source.sequence,
                        "payload_bytes": encoded.envelope.count
                    ])
                    publishStatistics()
                }
                guard sourceEpoch == epoch else { throw PipelineError.sessionEnded }

                let sendSpan = diagnostics.startSpan(captureID: captureID, operation: "capture.websocket_send")
                let sendStart = ProcessInfo.processInfo.systemUptime
                do {
                    try await socket.sendFrame(encoded.envelope)
                    sendSpan?.setData(
                        value: (ProcessInfo.processInfo.systemUptime - sendStart) * 1_000,
                        key: "send_duration_ms"
                    )
                    sendSpan?.finish(status: .ok)
                } catch {
                    sendSpan?.finish(status: .internalError)
                    throw error
                }

                statistics.sentFrames &+= 1
                statistics.lastValidDepthFraction = encoded.validDepthFraction
                let frameAttributes: [String: Any] = [
                    "device_id": deviceID,
                    "session_id": source.sessionID.uuidString.lowercased(),
                    "sequence": source.sequence,
                    "capture_mode": source.intent.mode.rawValue,
                    "rgb_width": encoded.header.rgb.width,
                    "rgb_height": encoded.header.rgb.height,
                    "depth_width": encoded.header.depth.width,
                    "depth_height": encoded.header.depth.height,
                    "valid_depth_fraction": encoded.validDepthFraction,
                    "encoded_rgb_bytes": encoded.jpegBytes,
                    "payload_bytes": encoded.envelope.count,
                    "dropped_frame_count": statistics.droppedLiveFrames
                ]
                if source.intent.mode == .snapshot {
                    diagnostics.log(.info, "capture.frame.sent", attributes: frameAttributes)
                } else {
                    liveSentSinceAggregate &+= 1
                    emitLiveAggregateIfNeeded(frameAttributes: frameAttributes)
                }
                diagnostics.finishCapture(captureID: captureID)
                eventHandler?(.frameSent(captureID: captureID, sequence: source.sequence))
            } catch {
                diagnostics.log(.error, "capture.frame.failed", attributes: [
                    "device_id": deviceID,
                    "session_id": source.sessionID.uuidString.lowercased(),
                    "sequence": source.sequence,
                    "capture_mode": source.intent.mode.rawValue,
                    "error": error.localizedDescription
                ])
                diagnostics.finishCapture(
                    captureID: captureID,
                    error: error
                )
                eventHandler?(.failed(captureID: captureID, message: error.localizedDescription))
            }
            publishStatistics()
        }
        draining = false
        // A submit can occur between the empty check and this assignment because of actor
        // reentrancy at socket/recorder awaits. Restart if work arrived in that window.
        if !queue.isEmpty {
            draining = true
            Task { await drain() }
        }
    }

    private func takeNext() -> CapturedFrameSource? {
        queue.popFirst()
    }

    private func publishStatistics() {
        statistics.queueDepth = queue.count
        eventHandler?(.statistics(statistics))
    }

    private func emitLiveAggregateIfNeeded(frameAttributes: [String: Any]) {
        let now = ProcessInfo.processInfo.systemUptime
        guard now - lastLiveAggregateTime >= 30 else { return }
        var attributes = frameAttributes
        attributes["aggregate_period_s"] = now - lastLiveAggregateTime
        attributes["frames_sent"] = liveSentSinceAggregate
        attributes["frames_dropped"] = statistics.droppedLiveFrames - liveDroppedAtLastAggregate
        diagnostics.log(.info, "capture.live.aggregate", attributes: attributes)
        liveSentSinceAggregate = 0
        liveDroppedAtLastAggregate = statistics.droppedLiveFrames
        lastLiveAggregateTime = now
    }
}

enum PipelineError: Error, LocalizedError {
    case snapshotQueueFull
    case supersededLiveFrame
    case sessionEnded

    var errorDescription: String? {
        switch self {
        case .snapshotQueueFull: "Snapshot queue is full."
        case .supersededLiveFrame: "Live frame was superseded by a newer frame."
        case .sessionEnded: "Capture session ended before the frame was processed."
        }
    }
}
