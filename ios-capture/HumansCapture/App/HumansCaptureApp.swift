import SwiftUI
import Sentry


@main
struct HumansCaptureApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store: CaptureStore

    init() {
        SentrySDK.start { options in
            options.dsn = "https://bac69829b21573f6cd6c6a7d0811ea08@o4512111998664704.ingest.us.sentry.io/4512112002924544"

            // Adds IP for users.
            // For more information, visit: https://docs.sentry.io/platforms/apple/data-management/data-collected/
            options.sendDefaultPii = true

            // Set tracesSampleRate to 1.0 to capture 100% of transactions for performance monitoring.
            // We recommend adjusting this value in production.
            options.tracesSampleRate = 1.0

            // Configure profiling. Visit https://docs.sentry.io/platforms/apple/profiling/ to learn more.
            options.configureProfiling = {
                $0.sessionSampleRate = 1.0 // We recommend adjusting this value in production.
                $0.lifecycle = .trace
            }

            // Uncomment the following lines to add more data to your events
            // options.attachScreenshot = true // This adds a screenshot to the error events
            // options.attachViewHierarchy = true // This adds the view hierarchy to the error events

            // Enable experimental logging features
            options.experimental.enableLogs = true
        }
        // Remove the next line after confirming that your Sentry integration is working.
        SentrySDK.capture(message: "This app uses Sentry! :)")

        _store = StateObject(wrappedValue: CaptureStore())
    }

    var body: some Scene {
        WindowGroup {
            CaptureView(store: store)
                .onChange(of: scenePhase) { _, phase in store.handleScenePhase(phase) }
        }
    }
}
