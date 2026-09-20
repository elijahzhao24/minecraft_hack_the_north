import Foundation
import os
import Sentry

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
    private let sentrySpan: (any Span)?
    private let lock = NSLock()
    private var attributes: [String: Any] = [:]
    private var finished = false

    init(operation: String, captureID: UUID, logger: Logger, sentrySpan: (any Span)?) {
        self.operation = operation
        self.captureID = captureID
        self.logger = logger
        self.sentrySpan = sentrySpan
    }

    func setData(value: Any, key: String) {
        lock.withLock { attributes[key] = value }
        sentrySpan?.setData(value: value, key: key)
    }

    func finish(status: CaptureSpanStatus) {
        let result = lock.withLock { () -> (Bool, [String: Any]) in
            guard !finished else { return (false, [:]) }
            finished = true
            return (true, attributes)
        }
        guard result.0 else { return }
        let durationMS = (ProcessInfo.processInfo.systemUptime - startedAt) * 1_000
        sentrySpan?.finish(status: status == .ok ? .ok : .internalError)
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
    private var transactions: [UUID: any Span] = [:]
    private var lastWarningAt: [String: TimeInterval] = [:]

    private init() {
        guard let dsn = ProcessInfo.processInfo.environment["HMC_SENTRY_DSN"], !dsn.isEmpty else {
            return
        }
        SentrySDK.start { options in
            options.dsn = dsn
            options.environment = ProcessInfo.processInfo.environment["HMC_SENTRY_ENVIRONMENT"] ?? "development"
            options.releaseName = "humans-capture@\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev")"
            let configuredRate = Double(ProcessInfo.processInfo.environment["HMC_SENTRY_TRACES_SAMPLE_RATE"] ?? "0.2") ?? 0.2
            options.tracesSampleRate = NSNumber(value: min(1, max(0, configuredRate)))
            options.sendDefaultPii = false
            options.debug = ProcessInfo.processInfo.environment["HMC_SENTRY_DEBUG"] == "true"
        }
    }

    func beginCapture(captureID: UUID, mode: CaptureMode, attributes: [String: Any]) {
        let transaction = SentrySDK.startTransaction(name: "ios.capture_frame", operation: "hmc.capture")
        transaction.setData(value: captureID.uuidString.lowercased(), key: "frame_id")
        transaction.setData(value: mode.rawValue, key: "capture_mode")
        attributes.forEach { transaction.setData(value: $0.value, key: $0.key) }
        lock.withLock {
            capturesStartedAt[captureID] = ProcessInfo.processInfo.systemUptime
            transactions[captureID] = transaction
        }
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
        let parent = lock.withLock { transactions[captureID] }
        let sentrySpan = parent?.startChild(operation: operation, description: description)
        let span = CaptureDiagnosticSpan(
            operation: operation,
            captureID: captureID,
            logger: localLogger,
            sentrySpan: sentrySpan
        )
        if let description { span.setData(value: description, key: "description") }
        return span
    }

    func finishCapture(captureID: UUID, error: Error? = nil) {
        let result = lock.withLock {
            (capturesStartedAt.removeValue(forKey: captureID), transactions.removeValue(forKey: captureID))
        }
        let startedAt = result.0
        var attributes: [String: Any] = ["capture_id": captureID.uuidString.lowercased()]
        if let startedAt {
            attributes["duration_ms"] = (ProcessInfo.processInfo.systemUptime - startedAt) * 1_000
        }
        if let error {
            attributes["error"] = error.localizedDescription
        }
        attributes["status"] = error == nil ? "ok" : "failed"
        result.1?.finish(status: error == nil ? .ok : .internalError)
        log(.debug, "capture.finished", attributes: attributes)
    }

    func traceContext(captureID: UUID) -> CaptureTraceContext? {
        guard let transaction = lock.withLock({ transactions[captureID] }) else { return nil }
        return CaptureTraceContext(
            sentryTrace: transaction.toTraceHeader().value(),
            baggage: transaction.baggageHttpHeader()
        )
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
        guard level == .warning || level == .error else { return }
        SentrySDK.capture(message: message) { scope in
            scope.setLevel(level == .error ? .error : .warning)
            attributes.forEach { scope.setExtra(value: $0.value, key: $0.key) }
        }
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
