import CryptoKit
import Foundation

struct SavedFixture: Sendable {
    let envelopeURL: URL
    let headerURL: URL
    let checksumURL: URL
}

actor FixtureRecorder {
    private let fileManager: FileManager
    private let rootOverride: URL?

    init(fileManager: FileManager = .default, rootOverride: URL? = nil) {
        self.fileManager = fileManager
        self.rootOverride = rootOverride
    }

    func save(envelope: Data, header: RGBDFrameHeader) throws -> SavedFixture {
        let root = try fixtureDirectory()
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        let stem = "\(header.deviceID)-\(header.captureID.uuidString.lowercased())"
        let envelopeURL = root.appendingPathComponent(stem).appendingPathExtension("hmc")
        let headerURL = root.appendingPathComponent(stem).appendingPathExtension("json")
        let checksumURL = root.appendingPathComponent(stem).appendingPathExtension("sha256")

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let headerData = try encoder.encode(header)
        let digest = SHA256.hash(data: envelope).map { String(format: "%02x", $0) }.joined()

        try envelope.write(to: envelopeURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        try headerData.write(to: headerURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        try Data("\(digest)  \(envelopeURL.lastPathComponent)\n".utf8)
            .write(to: checksumURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])

        return SavedFixture(envelopeURL: envelopeURL, headerURL: headerURL, checksumURL: checksumURL)
    }

    private func fixtureDirectory() throws -> URL {
        if let rootOverride { return rootOverride }
        let applicationSupport = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return applicationSupport
            .appendingPathComponent("HumansCapture", isDirectory: true)
            .appendingPathComponent("Fixtures", isDirectory: true)
    }
}
