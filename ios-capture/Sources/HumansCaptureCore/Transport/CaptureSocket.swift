import Foundation

public enum CaptureSocketState: Equatable, Sendable {
    case disconnected
    case connecting
    case awaitingServerHello
    case ready
    case reconnecting(attempt: Int)
}

public enum CaptureSocketEvent: Sendable {
    case stateChanged(CaptureSocketState)
    case captureRequested(CaptureRequest)
    case acknowledged(Acknowledgement)
    case serverError(ServerErrorMessage)
    case localError(String)
    case captureRejected(requestID: UUID?, code: String, detail: String)
    case queueChanged(Int)
}

public actor CaptureSocket {
    public nonisolated let events: AsyncStream<CaptureSocketEvent>

    private let eventContinuation: AsyncStream<CaptureSocketEvent>.Continuation
    private let endpoint: URL
    private let deviceID: String
    private let sessionID: UUID
    private let appVersion: String
    private let supportsSceneDepth: Bool
    private let uptime: @Sendable () -> TimeInterval
    private let urlSession: URLSession

    private var socketTask: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var sendBuffer = CaptureSendBuffer()
    private var state: CaptureSocketState = .disconnected
    private var negotiatedMaximumBinaryBytes = HMCProtocolLimits.maximumRGBDPayloadBytes
    private var deliberateDisconnect = false
    private var reconnectAttempt = 0

    /// Reconnects quickly on a demo LAN but never backs off beyond the workflow's five-second cap.
    private static let initialReconnectDelaySeconds = 0.25
    private static let maximumReconnectDelaySeconds = 5.0

    public init(
        endpoint: URL,
        deviceID: String,
        sessionID: UUID,
        appVersion: String,
        supportsSceneDepth: Bool,
        uptime: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        urlSession: URLSession = .shared
    ) {
        let (events, continuation) = AsyncStream.makeStream(of: CaptureSocketEvent.self)
        self.events = events
        self.eventContinuation = continuation
        self.endpoint = endpoint
        self.deviceID = deviceID
        self.sessionID = sessionID
        self.appVersion = appVersion
        self.supportsSceneDepth = supportsSceneDepth
        self.uptime = uptime
        self.urlSession = urlSession
    }

    deinit {
        eventContinuation.finish()
    }

    public func connect() async {
        deliberateDisconnect = false
        reconnectTask?.cancel()
        reconnectTask = nil
        await openSocket()
    }

    public func disconnect() {
        deliberateDisconnect = true
        reconnectTask?.cancel()
        reconnectTask = nil
        receiveTask?.cancel()
        receiveTask = nil
        socketTask?.cancel(with: .goingAway, reason: nil)
        socketTask = nil
        sendBuffer.removeAll()
        negotiatedMaximumBinaryBytes = HMCProtocolLimits.maximumRGBDPayloadBytes
        transition(to: .disconnected)
        eventContinuation.yield(.queueChanged(0))
    }

    @discardableResult
    public func enqueue(_ capture: PendingCapture) async -> CaptureEnqueueResult {
        guard capture.envelope.count <= negotiatedMaximumBinaryBytes else {
            return .rejectedFrameTooLarge(maximumBytes: negotiatedMaximumBinaryBytes)
        }
        let result = sendBuffer.enqueue(capture)
        eventContinuation.yield(.queueChanged(sendBuffer.outstandingCount))
        await drainQueueIfPossible()
        return result
    }

    public func acknowledge(_ acknowledgement: ClientAcknowledgement) async {
        guard let socketTask else {
            eventContinuation.yield(.localError("cannot acknowledge while disconnected"))
            return
        }
        do {
            try await sendJSON(acknowledgement, over: socketTask)
        } catch {
            await connectionFailed(error)
        }
    }

    private func openSocket() async {
        guard socketTask == nil else { return }
        transition(to: reconnectAttempt == 0 ? .connecting : .reconnecting(attempt: reconnectAttempt))
        let task = urlSession.webSocketTask(with: endpoint)
        socketTask = task
        task.resume()
        do {
            let hello = ClientHello(
                deviceID: deviceID,
                sessionID: sessionID,
                appVersion: appVersion,
                supportsSceneDepth: supportsSceneDepth,
                imageOrientation: .landscapeRight
            )
            try await sendJSON(hello, over: task)
            transition(to: .awaitingServerHello)
            receiveTask = Task { [weak self] in
                await self?.receiveLoop(over: task)
            }
        } catch {
            await connectionFailed(error)
        }
    }

    private func receiveLoop(over task: URLSessionWebSocketTask) async {
        do {
            while !Task.isCancelled {
                let message = try await task.receive()
                let receiveTime = uptime()
                switch message {
                case .string(let text):
                    try await handleControl(Data(text.utf8), receiveTime: receiveTime, over: task)
                case .data:
                    throw ProtocolValidationError.invalidBuffer("capture socket received unexpected binary data")
                @unknown default:
                    throw ProtocolValidationError.invalidBuffer("capture socket received unknown WebSocket message")
                }
            }
        } catch is CancellationError {
            return
        } catch {
            await connectionFailed(error)
        }
    }

    private func handleControl(
        _ data: Data,
        receiveTime: TimeInterval,
        over task: URLSessionWebSocketTask
    ) async throws {
        switch try IncomingControlMessage.decode(data) {
        case .serverHello(let hello):
            guard hello.acceptedDeviceID == deviceID else {
                throw ProtocolValidationError.invalidBuffer("server accepted a different device ID")
            }
            negotiatedMaximumBinaryBytes = min(
                hello.maximumBinaryBytes,
                HMCProtocolLimits.maximumRGBDPayloadBytes + HMCEnvelope.prefixLength + HMCProtocolLimits.maximumJSONHeaderBytes
            )
            reconnectAttempt = 0
            transition(to: .ready)
            await drainQueueIfPossible()
        case .clockPing(let ping):
            let pong = try ClockResponder.makePong(
                for: ping,
                phoneReceiveTimeSeconds: receiveTime,
                phoneSendTimeSeconds: uptime()
            )
            try await sendJSON(pong, over: task)
        case .captureRequest(let request):
            eventContinuation.yield(.captureRequested(request))
        case .acknowledgement(let acknowledgement):
            eventContinuation.yield(.acknowledged(acknowledgement))
        case .error(let error):
            eventContinuation.yield(.serverError(error))
        }
    }

    private func drainQueueIfPossible() async {
        guard state == .ready, let task = socketTask else { return }
        let next: PendingCapture
        switch sendBuffer.beginNextSend(maximumBytes: negotiatedMaximumBinaryBytes) {
        case .none:
            return
        case .oversized(let rejected):
            eventContinuation.yield(.captureRejected(
                requestID: rejected.requestID,
                code: "frame_too_large",
                detail: "encoded envelope exceeds negotiated max_binary_bytes \(negotiatedMaximumBinaryBytes)"
            ))
            eventContinuation.yield(.queueChanged(sendBuffer.outstandingCount))
            await drainQueueIfPossible()
            return
        case .frame(let frame):
            next = frame
        }
        eventContinuation.yield(.queueChanged(sendBuffer.outstandingCount))
        do {
            try await task.send(.data(next.envelope))
            sendBuffer.succeedInFlight()
            eventContinuation.yield(.queueChanged(sendBuffer.outstandingCount))
            await drainQueueIfPossible()
        } catch {
            sendBuffer.failInFlight()
            await connectionFailed(error)
        }
    }

    private func sendJSON<T: Encodable>(_ value: T, over task: URLSessionWebSocketTask) async throws {
        let data = try HMCJSON.encoder().encode(value)
        guard let text = String(data: data, encoding: .utf8) else {
            throw ProtocolValidationError.invalidBuffer("JSON encoder produced invalid UTF-8")
        }
        try await task.send(.string(text))
    }

    private func connectionFailed(_ error: Error) async {
        receiveTask?.cancel()
        receiveTask = nil
        socketTask?.cancel(with: .goingAway, reason: nil)
        socketTask = nil
        transition(to: .disconnected)
        eventContinuation.yield(.localError(error.localizedDescription))
        guard !deliberateDisconnect else { return }

        reconnectAttempt += 1
        let exponentialDelay = Self.initialReconnectDelaySeconds * pow(2, Double(reconnectAttempt - 1))
        let delay = min(exponentialDelay, Self.maximumReconnectDelaySeconds)
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            do {
                try await Task.sleep(for: .seconds(delay))
                await self?.openSocket()
            } catch {
                // Cancellation is expected when the user disconnects or manually reconnects.
            }
        }
    }

    private func transition(to newState: CaptureSocketState) {
        state = newState
        eventContinuation.yield(.stateChanged(newState))
    }
}
