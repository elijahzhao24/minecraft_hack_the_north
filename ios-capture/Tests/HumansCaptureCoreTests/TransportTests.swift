import Foundation
import Testing
@testable import HumansCaptureCore

struct TransportTests {
    @Test("Newest live frame replaces the older live frame without removing a snapshot")
    func latestLiveWinsAndSnapshotIsPreserved() {
        let oldLive = pending("00000000-0000-4000-8000-000000000001", mode: .live)
        let snapshot = pending("00000000-0000-4000-8000-000000000002", mode: .snapshot)
        let newLive = pending("00000000-0000-4000-8000-000000000003", mode: .live)
        var queue = PendingCaptureQueue(capacity: 2)

        #expect(queue.enqueue(oldLive) == .accepted)
        #expect(queue.enqueue(snapshot) == .accepted)
        #expect(queue.enqueue(newLive) == .replacedLive(oldLive.captureID))

        #expect(Set(queue.pending.map(\.captureID)) == Set([snapshot.captureID, newLive.captureID]))
        #expect(queue.pending.contains { $0.captureID == snapshot.captureID && $0.mode == .snapshot })
    }

    @Test("A full snapshot queue explicitly rejects rather than silently replacing a request")
    func snapshotsAreNeverSilentlyReplaced() {
        let first = pending("00000000-0000-4000-8000-000000000011", mode: .snapshot)
        let second = pending("00000000-0000-4000-8000-000000000012", mode: .snapshot)
        let third = pending("00000000-0000-4000-8000-000000000013", mode: .snapshot)
        var queue = PendingCaptureQueue(capacity: 2)

        #expect(queue.enqueue(first) == .accepted)
        #expect(queue.enqueue(second) == .accepted)
        #expect(queue.enqueue(third) == .rejectedSnapshotQueueFull)
        #expect(queue.pending.map(\.captureID) == [first.captureID, second.captureID])
    }

    @Test("Clock pong echoes the ping and uses supplied monotonic receive/send timestamps")
    func clockPongUsesMonotonicTimes() throws {
        let requestID = UUID(uuidString: "456a353c-c2e0-4f95-97c7-68bf38cfef47")!
        let ping = try ClockPing(
            requestID: requestID,
            backendSendTimeSeconds: 61_420.230_115
        )

        let pong = try ClockResponder.makePong(
            for: ping,
            phoneReceiveTimeSeconds: 9_921.620_552,
            phoneSendTimeSeconds: 9_921.620_734
        )

        #expect(pong.requestID == requestID)
        #expect(pong.backendSendTimeSeconds == 61_420.230_115)
        #expect(pong.phoneReceiveTimeSeconds == 9_921.620_552)
        #expect(pong.phoneSendTimeSeconds == 9_921.620_734)
    }

    @Test("Client hello JSON uses contract field names and lowercase UUID")
    func clientHelloEncoding() throws {
        let hello = ClientHello(
            deviceID: "front-phone",
            sessionID: UUID(uuidString: "44487D7C-B847-49DB-AA37-CF326AD76078")!,
            appVersion: "0.1.0",
            supportsSceneDepth: true,
            imageOrientation: .landscapeRight
        )
        let encoded = try HMCJSON.encoder().encode(hello)
        let object = try #require(JSONSerialization.jsonObject(with: encoded) as? [String: Any])

        #expect(object["type"] as? String == "client_hello")
        #expect(object["protocol_version"] as? Int == 1)
        #expect(object["session_id"] as? String == "44487d7c-b847-49db-aa37-cf326ad76078")
        #expect(object["image_orientation"] as? String == "landscape_right")
        #expect(object["platform"] as? String == "ios")
    }

    @Test("Incoming control messages reject unknown fields")
    func unknownControlFieldsAreRejected() {
        let json = Data(#"{"type":"clock_ping","protocol_version":1,"request_id":"456a353c-c2e0-4f95-97c7-68bf38cfef47","backend_send_time_s":61420.0,"typo":true}"#.utf8)

        #expect(throws: ProtocolValidationError.self) {
            _ = try IncomingControlMessage.decode(json)
        }
    }

    @Test("Phone acknowledgement encodes the capture request join key")
    func phoneAcknowledgementEncoding() throws {
        let requestID = UUID(uuidString: "bd36780c-37ac-47ad-8cbc-dba00734859f")!
        let acknowledgement = ClientAcknowledgement(
            requestID: requestID,
            accepted: true,
            code: "capture_queued",
            detail: nil
        )

        let data = try HMCJSON.encoder().encode(acknowledgement)
        let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(object["type"] as? String == "ack")
        #expect(object["request_id"] as? String == requestID.uuidString.lowercased())
        #expect(object["accepted"] as? Bool == true)
        #expect(object["code"] as? String == "capture_queued")
    }

    private func pending(_ id: String, mode: CaptureMode) -> PendingCapture {
        PendingCapture(
            captureID: UUID(uuidString: id)!,
            requestID: mode == .snapshot ? UUID() : nil,
            mode: mode,
            envelope: Data([1, 2, 3])
        )
    }
}
