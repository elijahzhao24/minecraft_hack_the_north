import SwiftUI


@main
struct HumansCaptureApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store: CaptureStore

    init() {
        // Sentry is configured once, in CaptureEventLogger (DSN, environment and
        // sample rate come from Info.plist via Config/Base.xcconfig). Touching the
        // singleton here starts the SDK before any capture code runs so early
        // failures are reported too.
        _ = CaptureEventLogger.shared

        _store = StateObject(wrappedValue: CaptureStore())
    }

    var body: some Scene {
        WindowGroup {
            CaptureView(store: store)
                .onChange(of: scenePhase) { _, phase in store.handleScenePhase(phase) }
        }
    }
}
