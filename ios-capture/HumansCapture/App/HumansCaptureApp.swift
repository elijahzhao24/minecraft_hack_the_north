import SwiftUI

@main
struct HumansCaptureApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store = CaptureStore()

    var body: some Scene {
        WindowGroup {
            CaptureView(store: store)
        }
        .onChange(of: scenePhase) { _, phase in
            store.scenePhaseChanged(isActive: phase == .active)
        }
    }
}
