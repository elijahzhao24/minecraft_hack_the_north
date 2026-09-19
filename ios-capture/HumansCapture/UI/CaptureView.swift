import HumansCaptureCore
import SwiftUI

struct CaptureView: View {
    @ObservedObject var store: CaptureStore

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if store.calibrationInvalid {
                        Label(
                            "Calibration invalid: the camera raster changed. Restart and recalibrate before capture.",
                            systemImage: "exclamationmark.triangle.fill"
                        )
                        .font(.headline)
                        .foregroundStyle(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.red, in: RoundedRectangle(cornerRadius: 12))
                        .accessibilityIdentifier("calibration-warning")
                    }

                    settings

                    HStack(spacing: 12) {
                        previewCard(title: "RGB") {
                            ARPreviewView(session: store.captureController.session)
                        }
                        previewCard(title: "Depth") {
                            Group {
                                if let image = store.depthPreview {
                                    Image(uiImage: image).resizable().interpolation(.none).scaledToFit()
                                } else {
                                    ContentUnavailableView("No depth frame", systemImage: "square.stack.3d.up.slash")
                                }
                            }
                        }
                    }
                    .frame(height: 300)

                    controls
                    diagnostics
                }
                .padding()
            }
            .navigationTitle("Humans Capture")
        }
    }

    private var settings: some View {
        GroupBox("Connection") {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                GridRow {
                    Text("Device ID")
                    TextField("front-phone", text: $store.deviceID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("device-id")
                }
                GridRow {
                    Text("Backend")
                    TextField("ws://host:8000/ws/capture", text: $store.backendURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("backend-url")
                }
            }
            .textFieldStyle(.roundedBorder)
        }
    }

    private var controls: some View {
        VStack(spacing: 10) {
            HStack {
                Button(store.sessionRunning ? "Stop AR" : "Start AR") {
                    store.sessionRunning ? store.stopSession() : store.startSession()
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("toggle-ar")

                Button(store.socketState == .disconnected ? "Connect" : "Disconnect") {
                    store.socketState == .disconnected ? store.connect() : store.disconnect()
                }
                .buttonStyle(.bordered)
                .disabled(!store.sessionRunning)
                .accessibilityIdentifier("toggle-connection")

                Button("Capture") { store.captureSnapshot() }
                    .buttonStyle(.borderedProminent)
                    .tint(.orange)
                    .disabled(!store.sessionRunning)
                    .accessibilityIdentifier("capture-snapshot")

                Toggle("Live", isOn: $store.liveModeEnabled)
                    .toggleStyle(.switch)
                    .disabled(!store.sessionRunning)
            }
            Text("Snapshot: \(store.snapshotStatus)")
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var diagnostics: some View {
        GroupBox("Diagnostics") {
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 8) {
                diagnosticRow("Socket", String(describing: store.socketState))
                diagnosticRow("Tracking", store.trackingState.rawValue)
                diagnosticRow("RGB", store.rgbDimensions)
                diagnosticRow("Depth", store.depthDimensions)
                diagnosticRow("Sequence", String(store.sequence))
                diagnosticRow("Valid depth", store.validDepthFraction.formatted(.percent.precision(.fractionLength(1))))
                diagnosticRow("Pending sends", String(store.queueDepth))
                diagnosticRow("Last error", store.lastBackendError ?? "None")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func diagnosticRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }

    private func previewCard<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            content()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(.black)
                .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .frame(maxWidth: .infinity)
    }
}
