import SwiftUI

@main
struct HumansCaptureApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store: CaptureStore

    init() {
        _store = StateObject(wrappedValue: CaptureStore())
    }

    var body: some Scene {
        WindowGroup {
            CaptureView(store: store)
                .onChange(of: scenePhase) { _, phase in store.handleScenePhase(phase) }
        }
    }
}
