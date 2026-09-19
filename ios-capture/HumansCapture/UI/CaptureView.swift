import SwiftUI

struct CaptureView: View {
    @ObservedObject var store: CaptureStore

    var body: some View {
        NavigationStack {
            HStack(spacing: 12) {
                VStack(spacing: 10) {
                    ARCameraPreview(session: store.captureController.session)
                        .overlay(alignment: .topLeading) { previewLabel("RGB • unmirrored") }
                        .clipShape(RoundedRectangle(cornerRadius: 12))

                    Group {
                        if let image = store.depthPreview {
                            Image(decorative: image, scale: 1)
                                .resizable()
                                .interpolation(.none)
                                .aspectRatio(contentMode: .fit)
                        } else {
                            ContentUnavailableView("Waiting for depth", systemImage: "square.3.layers.3d")
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.black)
                    .overlay(alignment: .topLeading) { previewLabel("Depth • 0.2–5 m") }
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .frame(maxWidth: .infinity)

                Form {
                    Section("Connection") {
                        TextField("Device ID", text: $store.deviceID)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        TextField("ws://host:8000/ws/capture", text: $store.backendURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        HStack {
                            Button(store.isRunning ? "Stop" : "Start") {
                                store.isRunning ? store.stop() : store.start()
                            }
                            Button(store.socketState == .ready ? "Disconnect" : "Connect") {
                                store.socketState == .ready ? store.disconnect() : store.connect()
                            }
                        }
                    }

                    Section("Capture") {
                        Button("Capture snapshot", systemImage: "camera") { store.captureSnapshot() }
                            .buttonStyle(.borderedProminent)
                            .disabled(!store.isRunning)
                        Button(store.liveEnabled ? "Stop live recapture" : "Start live recapture") {
                            store.toggleLive()
                        }
                        .disabled(!store.isRunning)
                        if let fixture = store.lastFixtureURL {
                            ShareLink(item: fixture) {
                                Label("Export last .hmc fixture", systemImage: "square.and.arrow.up")
                            }
                        }
                    }

                    Section("Status") {
                        metric("Connection", String(describing: store.socketState))
                        metric("Tracking", store.trackingState.rawValue)
                        metric("RGB", store.rgbDimensions)
                        metric("Depth", store.depthDimensions)
                        metric("Valid depth", store.validDepthFraction.map { $0.formatted(.percent.precision(.fractionLength(1))) } ?? "—")
                        metric("Queue", "\(store.queueDepth)")
                        metric("Dropped live", "\(store.droppedFrames)")
                        metric("Session", store.sessionID?.uuidString.lowercased() ?? "—")
                        metric("Sequence", store.lastSequence.map(String.init) ?? "—")
                        Text(store.lastStatus).font(.footnote)
                    }

                    Section("Diagnostics") {
                        Button("Emit safe Sentry test error") { store.validateSentry() }
                    }
                }
                .frame(width: 400)
            }
            .padding(12)
            .navigationTitle("Humans Capture")
        }
    }

    private func previewLabel(_ text: String) -> some View {
        Text(text)
            .font(.caption.bold())
            .padding(6)
            .background(.black.opacity(0.65), in: Capsule())
            .foregroundStyle(.white)
            .padding(8)
    }

    private func metric(_ label: String, _ value: String) -> some View {
        LabeledContent(label) {
            Text(value).lineLimit(1).minimumScaleFactor(0.6)
        }
    }
}
