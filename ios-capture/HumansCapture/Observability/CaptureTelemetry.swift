import Foundation
import os

enum CaptureLogLevel {
    case debug, info, warning, error
}

enum CaptureSpanStatus: String {
    case ok
    case internalError = "internal_error"
}

final class CaptureDiagnosticSpan: @unchecked Sendable {
    private let operation: String
    private let captureID: UUID
    private let startedAt = ProcessInfo.processInfo.systemUptime
    private let logger: Logger
    private let lock = NSLock()
    private var attributes: [String: Any] = [:]
    private var finished = false

    init(operation: String, captureID: UUID, logger: Logger) {
        self.operation = operation
        self.captureID = captureID
        self.logger = logger
    }

    func setData(value: Any, key: String) {
        lock.withLock { attributes[key] = value }
    }

    func finish(status: CaptureSpanStatus) {
        let result = lock.withLock { () -> (Bool, [String: Any]) in
            guard !finished else { return (false, [:]) }
            finished = true
            return (true, attributes)
        }
        guard result.0 else { return }
        let durationMS = (ProcessInfo.processInfo.systemUptime - startedAt) * 1_000
        logger.debug(
            "\(self.operation, privacy: .public) capture_id=\(self.captureID.uuidString.lowercased(), privacy: .public) status=\(status.rawValue, privacy: .public) duration_ms=\(durationMS, privacy: .public) attributes=\(String(describing: result.1), privacy: .private(mask: .hash))"
        )
    }
}

final class CaptureEventLogger: @unchecked Sendable {
    static let shared = CaptureEventLogger()

    private let localLogger = Logger(subsystem: "dev.hmc.HumansCapture", category: "capture")
    private let lock = NSLock()
    private var capturesStartedAt: [UUID: TimeInterval] = [:]
    private var lastWarningAt: [String: TimeInterval] = [:]

    private init() {}

    func beginCapture(captureID: UUID, mode: CaptureMode, attributes: [String: Any]) {
        lock.withLock { capturesStartedAt[captureID] = ProcessInfo.processInfo.systemUptime }
        log(
            .debug,
            "capture.started",
            attributes: attributes.merging([
                "capture_id": captureID.uuidString.lowercased(),
                "capture_mode": mode.rawValue
            ]) { _, new in new }
        )
    }

    func startSpan(captureID: UUID, operation: String, description: String? = nil) -> CaptureDiagnosticSpan? {
        let span = CaptureDiagnosticSpan(operation: operation, captureID: captureID, logger: localLogger)
        if let description { span.setData(value: description, key: "description") }
        return span
    }

    func finishCapture(captureID: UUID, error: Error? = nil) {
        let startedAt = lock.withLock { capturesStartedAt.removeValue(forKey: captureID) }
        var attributes: [String: Any] = ["capture_id": captureID.uuidString.lowercased()]
        if let startedAt {
            attributes["duration_ms"] = (ProcessInfo.processInfo.systemUptime - startedAt) * 1_000
        }
        if let error {
            attributes["error"] = error.localizedDescription
        }
        attributes["status"] = error == nil ? "ok" : "failed"
        log(.debug, "capture.finished", attributes: attributes)
    }

    func log(
        _ level: CaptureLogLevel,
        _ message: String,
        attributes: [String: Any] = [:],
        rateLimitKey: String? = nil,
        minimumInterval: TimeInterval = 10
    ) {
        if let rateLimitKey {
            let now = ProcessInfo.processInfo.systemUptime
            let shouldEmit = lock.withLock { () -> Bool in
                if let last = lastWarningAt[rateLimitKey], now - last < minimumInterval { return false }
                lastWarningAt[rateLimitKey] = now
                return true
            }
            guard shouldEmit else { return }
        }

        localLogger.log(
            level: level.osLogType,
            "\(message, privacy: .public) \(String(describing: attributes), privacy: .private(mask: .hash))"
        )
    }
}

private extension CaptureLogLevel {
    var osLogType: OSLogType {
        switch self {
        case .debug: .debug
        case .info: .info
        case .warning: .default
        case .error: .error
        }
    }
}

private extension NSLock {
    func withLock<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}
