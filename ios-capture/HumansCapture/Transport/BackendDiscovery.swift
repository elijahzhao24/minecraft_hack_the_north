@preconcurrency import Foundation
import Combine

public struct DiscoveredBackend: Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let hostName: String
    public let port: Int
    public let url: URL

    public init(name: String, hostName: String, port: Int) {
        self.id = "\(name)_\(hostName):\(port)"
        self.name = name
        self.hostName = hostName
        self.port = port
        self.url = URL(string: "ws://\(hostName):\(port)/ws/capture")!
    }
}

@MainActor
public final class BackendDiscovery: NSObject, @unchecked Sendable, ObservableObject {
    @Published public private(set) var backends: [DiscoveredBackend] = []
    @Published public private(set) var isSearching = false

    private let browser = NetServiceBrowser()
    private var resolvingServices: [NetService] = []

    public override init() {
        super.init()
        browser.delegate = self
    }

    public func start() {
        guard !isSearching else { return }
        isSearching = true
        browser.searchForServices(ofType: "_hmc._tcp.", inDomain: "local.")
    }

    public func stop() {
        guard isSearching else { return }
        browser.stop()
        for service in resolvingServices {
            service.stop()
        }
        resolvingServices.removeAll()
        isSearching = false
    }
}

extension BackendDiscovery: @preconcurrency NetServiceBrowserDelegate {
    public func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        resolvingServices.append(service)
        service.delegate = self
        service.resolve(withTimeout: 5.0)
    }

    public func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        let name = service.name
        resolvingServices.removeAll { $0 === service }
        backends.removeAll { $0.name == name }
    }

    public func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        isSearching = false
    }

    public func netServiceBrowserDidStopSearch(_ browser: NetServiceBrowser) {
        isSearching = false
    }
}

extension BackendDiscovery: @preconcurrency NetServiceDelegate {
    public func netServiceDidResolveAddress(_ sender: NetService) {
        resolvingServices.removeAll { $0 === sender }
        guard let host = sender.hostName else { return }
        let cleanHost = host.trimmingCharacters(in: CharacterSet(charactersIn: "."))
        let port = sender.port > 0 ? sender.port : 8000
        let discovered = DiscoveredBackend(name: sender.name, hostName: cleanHost, port: port)
        if !backends.contains(where: { $0.id == discovered.id }) {
            backends.append(discovered)
        }
    }

    public func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
        resolvingServices.removeAll { $0 === sender }
    }
}
