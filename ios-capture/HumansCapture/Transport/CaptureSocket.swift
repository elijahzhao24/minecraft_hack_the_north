import Foundation
import os

enum CaptureSocketState: Equatable, Sendable {
    case disconnected
    case connecting
    case awaitingHello
    case ready
    case failed(String)
}

enum CaptureSocketEvent: Sendable {
    case stateChanged(CaptureSocketState)
    case clockPing(ClockPing, receivedAt: Double)
    case captureRequest(CaptureRequest)
    case acknowledgement(AckMessage)
    case serverError(ErrorMessage)
}

enum CaptureSocketError: Error, LocalizedError {
    case invalidURL
    case notReady
    case protocolViolation(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL: "The backend WebSocket URL is invalid."
        case .notReady: "The backend has not completed the hello handshake."
        case .protocolViolation(let detail): "WebSocket protocol violation: \(detail)"
        }
    }
}

actor CaptureSocket {
    private let logger = Logger(subsystem: "dev.hmc.HumansCapture", category: "socket")
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let urlSession: URLSession
    private var task: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var state: CaptureSocketState = .disconnected
    private var maximumBinaryBytes = HMCProtocol.maximumRGBDPayloadBytes + HMCProtocol.maximumHeaderBytes + 16
    private var connectionGeneration: UInt64 = 0

    var eventHandler: (@Sendable (CaptureSocketEvent) -> Void)?

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 30
        urlSession = URLSession(configuration: configuration)
    }

    func setEventHandler(_ handler: @escaping @Sendable (CaptureSocketEvent) -> Void) {
        eventHandler = handler
    }

    func connect(url: URL, hello: ClientHello) async throws {
        disconnect()
        guard url.scheme == "ws" || url.scheme == "wss" else {
            throw CaptureSocketError.invalidURL
        }
        setState(.connecting)
        let socket = urlSession.webSocketTask(with: url)
        let generation = connectionGeneration
        task = socket
        socket.resume()
        setState(.awaitingHello)
        try await sendJSON(hello)
        receiveTask = Task { [weak self] in await self?.receiveLoop(socket: socket, generation: generation) }
    }

    func disconnect() {
        connectionGeneration &+= 1
        receiveTask?.cancel()
        receiveTask = nil
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        setState(.disconnected)
    }

    func sendFrame(_ data: Data) async throws {
        guard state == .ready, let task else { throw CaptureSocketError.notReady }
        guard data.count <= maximumBinaryBytes else {
            throw CaptureSocketError.protocolViolation("binary frame exceeds server limit")
        }
        try await task.send(.data(data))
    }

    func sendClockPong(_ pong: ClockPong) async throws {
        try await sendJSON(pong)
    }

    func sendAcknowledgement(requestID: UUID, accepted: Bool, code: String, detail: String?) async throws {
        try await sendJSON(AckMessage(
            type: "ack",
            protocolVersion: 1,
            requestID: requestID,
            accepted: accepted,
            code: code,
            detail: detail
        ))
    }

    private func sendJSON<T: Encodable>(_ value: T) async throws {
        guard let task else { throw CaptureSocketError.notReady }
        let data = try encoder.encode(value)
        guard let text = String(data: data, encoding: .utf8) else {
            throw CaptureSocketError.protocolViolation("control JSON is not UTF-8")
        }
        try await task.send(.string(text))
    }

    private func receiveLoop(socket: URLSessionWebSocketTask, generation: UInt64) async {
        while !Task.isCancelled, generation == connectionGeneration {
            do {
                let message = try await socket.receive()
                switch message {
                case .string(let text):
                    try handleControl(Data(text.utf8))
                case .data:
                    throw CaptureSocketError.protocolViolation("phone received an unexpected binary message")
                @unknown default:
                    throw CaptureSocketError.protocolViolation("unknown WebSocket message kind")
                }
            } catch is CancellationError {
                return
            } catch {
                guard generation == connectionGeneration else { return }
                logger.error("Receive loop ended: \(error.localizedDescription, privacy: .public)")
                self.task = nil
                setState(.failed(error.localizedDescription))
                return
            }
        }
    }

    private func handleControl(_ data: Data) throws {
        let messageType = try decoder.decode(IncomingMessageType.self, from: data)
        switch messageType.type {
        case "server_hello":
            try Self.requireOnlyKeys(
                ["type", "protocol_version", "server_session_id", "accepted_device_id", "max_binary_bytes", "clock_probe_interval_s"],
                in: data
            )
            let hello = try decoder.decode(ServerHello.self, from: data)
            guard hello.protocolVersion == 1,
                  !hello.acceptedDeviceID.isEmpty,
                  hello.maxBinaryBytes > HMCEnvelope.fixedHeaderLength,
                  hello.maxBinaryBytes <= HMCProtocol.maximumRGBDPayloadBytes + HMCProtocol.maximumHeaderBytes + HMCEnvelope.fixedHeaderLength,
                  hello.clockProbeIntervalSeconds.isFinite,
                  hello.clockProbeIntervalSeconds > 0 else {
                throw CaptureSocketError.protocolViolation("invalid server hello")
            }
            maximumBinaryBytes = hello.maxBinaryBytes
            setState(.ready)
        case "clock_ping":
            try Self.requireOnlyKeys(["type", "protocol_version", "request_id", "backend_send_time_s"], in: data)
            let receivedAt = ProcessInfo.processInfo.systemUptime
            let ping = try decoder.decode(ClockPing.self, from: data)
            guard ping.protocolVersion == 1, ping.backendSendTimeSeconds.isFinite else {
                throw CaptureSocketError.protocolViolation("invalid clock ping")
            }
            eventHandler?(.clockPing(ping, receivedAt: receivedAt))
        case "capture_request":
            try Self.requireOnlyKeys(
                ["type", "protocol_version", "request_id", "capture_id", "mode", "not_before_phone_time_s"],
                in: data
            )
            let request = try decoder.decode(CaptureRequest.self, from: data)
            guard request.protocolVersion == 1,
                  request.notBeforePhoneTimeSeconds.map(\.isFinite) ?? true else {
                throw CaptureSocketError.protocolViolation("invalid capture request")
            }
            eventHandler?(.captureRequest(request))
        case "ack":
            try Self.requireOnlyKeys(["type", "protocol_version", "request_id", "accepted", "code", "detail"], in: data)
            let acknowledgement = try decoder.decode(AckMessage.self, from: data)
            guard acknowledgement.protocolVersion == 1, !acknowledgement.code.isEmpty else {
                throw CaptureSocketError.protocolViolation("invalid acknowledgement")
            }
            eventHandler?(.acknowledgement(acknowledgement))
        case "error":
            try Self.requireOnlyKeys(["type", "protocol_version", "request_id", "code", "message", "retryable"], in: data)
            let error = try decoder.decode(ErrorMessage.self, from: data)
            guard error.protocolVersion == 1, !error.code.isEmpty else {
                throw CaptureSocketError.protocolViolation("invalid error message")
            }
            eventHandler?(.serverError(error))
        default:
            throw CaptureSocketError.protocolViolation("unknown control type \(messageType.type)")
        }
    }

    static func requireOnlyKeys(_ allowed: Set<String>, in data: Data) throws {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw CaptureSocketError.protocolViolation("control message is not a JSON object")
        }
        let unknown = Set(object.keys).subtracting(allowed)
        guard unknown.isEmpty else {
            throw CaptureSocketError.protocolViolation("unknown control fields: \(unknown.sorted().joined(separator: ", "))")
        }
    }

    private func setState(_ newState: CaptureSocketState) {
        state = newState
        eventHandler?(.stateChanged(newState))
    }
}
