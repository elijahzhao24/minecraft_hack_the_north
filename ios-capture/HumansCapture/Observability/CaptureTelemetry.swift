@preconcurrency import Sentry
import Foundation
import os

enum CaptureLogLevel {
    case debug, info, warning, error
}

final class CaptureTelemetry: @unchecked Sendable {
    static let shared = CaptureTelemetry()

    private let localLogger = Logger(subsystem: "dev.hmc.HumansCapture", category: "telemetry")
    private let lock = NSLock()
    private var transactions: [UUID: Span] = [:]
    private var lastWarningAt: [String: TimeInterval] = [:]
    private var configured = false
    private var liveTraceSampleRate = 0.1

    private init() {}

    static func configure(bundle: Bundle = .main, processInfo: ProcessInfo = .processInfo) {
        let info = bundle.infoDictionary ?? [:]
        let environment = processInfo.environment
        let dsn = (environment["SENTRY_DSN"] ?? info["SENTRY_DSN"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let traceRateString = environment["SENTRY_TRACES_SAMPLE_RATE"]
            ?? info["SENTRY_TRACES_SAMPLE_RATE"] as? String
        let liveRateString = environment["SENTRY_LIVE_TRACE_SAMPLE_RATE"]
            ?? info["SENTRY_LIVE_TRACE_SAMPLE_RATE"] as? String
        let traceRate = Self.unitRate(traceRateString) ?? 1.0
        let liveRate = Self.unitRate(liveRateString) ?? 0.1

        shared.lock.withLock {
            shared.liveTraceSampleRate = liveRate
            shared.configured = !(dsn?.isEmpty ?? true)
        }
        guard let dsn, !dsn.isEmpty else {
            shared.localLogger.notice("Sentry disabled because no DSN is configured")
            return
        }

        SentrySDK.start { options in
            options.dsn = dsn
            options.environment = environment["SENTRY_ENVIRONMENT"]
                ?? info["SENTRY_ENVIRONMENT"] as? String
                ?? "development"
            options.releaseName = environment["SENTRY_RELEASE"]
                ?? info["SENTRY_RELEASE"] as? String
                ?? Self.defaultRelease(bundle: bundle)
            options.tracesSampleRate = NSNumber(value: traceRate)
            options.enableLogs = true
            options.enableAutoPerformanceTracing = false
            options.enableNetworkTracking = false
            options.sendDefaultPii = false
            options.attachScreenshot = false
            options.attachViewHierarchy = false
        }
        shared.log(.info, "capture.telemetry.started", attributes: [
            "environment": environment["SENTRY_ENVIRONMENT"] ?? info["SENTRY_ENVIRONMENT"] as? String ?? "development",
            "trace_sample_rate": traceRate,
            "live_trace_sample_rate": liveRate
        ])
    }

    func beginCapture(captureID: UUID, mode: CaptureMode, attributes: [String: Any]) {
        guard isConfigured else { return }
        if mode == .live && Double.random(in: 0...1) > liveTraceSampleRate { return }
        let transaction = SentrySDK.startTransaction(
            name: mode == .snapshot ? "rgbd.snapshot" : "rgbd.live_frame",
            operation: "capture"
        )
        transaction.setTag(value: mode.rawValue, key: "capture_mode")
        transaction.setData(value: captureID.uuidString.lowercased(), key: "capture_id")
        attributes.forEach { transaction.setData(value: $0.value, key: $0.key) }
        lock.withLock { transactions[captureID] = transaction }
    }

    func startSpan(captureID: UUID, operation: String, description: String? = nil) -> Span? {
        let transaction = lock.withLock { transactions[captureID] }
        return transaction?.startChild(operation: operation, description: description)
    }

    func traceContext(captureID: UUID) -> TraceContext? {
        guard let transaction = lock.withLock({ transactions[captureID] }) else { return nil }
        return TraceContext(
            sentryTrace: transaction.toTraceHeader().value(),
            baggage: transaction.baggageHttpHeader()
        )
    }

    func finishCapture(captureID: UUID, error: Error? = nil, captureError: Bool = true) {
        guard let transaction = lock.withLock({ transactions.removeValue(forKey: captureID) }) else { return }
        if let error {
            transaction.setData(value: error.localizedDescription, key: "failure_reason")
            transaction.finish(status: .internalError)
            if captureError { SentrySDK.capture(error: error) }
        } else {
            transaction.finish(status: .ok)
        }
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

        localLogger.log(level: level.osLogType, "\(message, privacy: .public) \(String(describing: attributes), privacy: .private(mask: .hash))")
        guard isConfigured else { return }
        switch level {
        case .debug: SentrySDK.logger.debug(message, attributes: attributes)
        case .info: SentrySDK.logger.info(message, attributes: attributes)
        case .warning: SentrySDK.logger.warn(message, attributes: attributes)
        case .error: SentrySDK.logger.error(message, attributes: attributes)
        }
    }

    func captureValidationError() {
        let error = NSError(
            domain: "dev.hmc.HumansCapture.SentryValidation",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Deliberate non-fatal Sentry validation event"]
        )
        if isConfigured { SentrySDK.capture(error: error) }
        log(.info, "capture.telemetry.validation_error_emitted", attributes: ["test_only": true])
    }

    var isConfigured: Bool { lock.withLock { configured } }

    private static func unitRate(_ value: String?) -> Double? {
        guard let value, let rate = Double(value), (0...1).contains(rate) else { return nil }
        return rate
    }

    private static func defaultRelease(bundle: Bundle) -> String {
        let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "dev.hmc.HumansCapture@\(version)+\(build)"
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
