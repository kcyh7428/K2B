// Narrow macOS 15 ScreenCaptureKit recorder and read-only source-window query.
// Native Mini CUA corroborates source URL/title/opening picture. No microphone.
import Foundation
import ScreenCaptureKit
import CoreGraphics
import AVFoundation
import AppKit
import CryptoKit

enum Failure: Error { case blocked(String) }

struct TargetBinding: Decodable {
    let id: UInt32
    let process_id: Int32
    let app: String
    let title: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct RecordingContext: Decodable {
    let request_id: String
    let canonical_url: String
    let deadline_epoch: Double
    let source_duration: Double
    let package: String
    let recorder_sha256: String
}

@available(macOS 15.0, *)
final class RecordingDelegate: NSObject, SCRecordingOutputDelegate {
    private let lock = NSLock()
    private var failed = false
    private var finished = false
    private var ready: (String, Double)?
    private let marker: URL
    private let context: RecordingContext
    private let binding: [String:Any]
    init(marker: URL, context: RecordingContext, binding: [String:Any]) {
        self.marker = marker; self.context = context; self.binding = binding
    }
    func snapshot() -> (failed: Bool, finished: Bool, ready: (String, Double)?) {
        lock.lock(); defer { lock.unlock() }
        return (failed, finished, ready)
    }
    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        lock.lock(); defer { lock.unlock() }
        guard ready == nil && !failed else { return }
        do {
            let epoch = Date().timeIntervalSince1970
            guard epoch < context.deadline_epoch else { throw Failure.blocked("recording_ready_late") }
            let report: [String:Any] = ["version":1,"request_id":context.request_id,"canonical_url":context.canonical_url,
                "deadline_epoch":context.deadline_epoch,"source_duration":context.source_duration,"package":context.package,
                "recorder_sha256":context.recorder_sha256,"window":binding,"ready_epoch":epoch,
                "ready_uptime":ProcessInfo.processInfo.systemUptime]
            let data = try JSONSerialization.data(withJSONObject:report,options:[.sortedKeys])
            let temporary = marker.deletingLastPathComponent().appendingPathComponent(".recording-started-" + UUID().uuidString)
            defer { try? FileManager.default.removeItem(at:temporary) }
            try data.write(to:temporary,options:.withoutOverwriting)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:temporary.path)
            // moveItem refuses an existing destination, including a symlink.
            try FileManager.default.moveItem(at:temporary,to:marker)
            ready = (SHA256.hash(data:data).map { String(format:"%02x",$0) }.joined(),epoch)
        } catch { failed = true }
    }
    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        lock.lock(); defer { lock.unlock() }; failed = true
    }
    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        lock.lock(); defer { lock.unlock() }; finished = true
    }
}

@main
struct Recorder {
    static func emit(_ report: [String:Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        print(String(decoding:data,as:UTF8.self))
    }

    @MainActor static func main() async {
        do {
            guard #available(macOS 15.0, *) else { throw Failure.blocked("macos_15_required") }
            // A standalone CLI has no Xcode-generated AppKit entry point.
            // Establish its WindowServer connection before constructing filters.
            _ = NSApplication.shared
            guard CGPreflightScreenCaptureAccess() else { throw Failure.blocked("recorder_screen_audio_permission_required") }
            let args = CommandLine.arguments
            let probing = args.count == 3 && args[1] == "probe"
            guard probing || args.count == 6 else { throw Failure.blocked("invalid_recording_arguments") }
            let title = probing ? args[2] : args[3]
            guard !title.isEmpty && title.count <= 2048 else { throw Failure.blocked("invalid_source_title") }
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            // Return only the approved source candidate, never a general window
            // inventory or unrelated Chrome titles. Zero/ambiguity both stop.
            let windows = content.windows.filter {
                $0.owningApplication?.bundleIdentifier == "com.google.Chrome" &&
                ($0.title?.contains(title) ?? false) && $0.frame.width > 320 && $0.frame.height > 200
            }
            guard windows.count == 1, let window = windows.first, let app = window.owningApplication else {
                throw Failure.blocked("verified_chrome_window_not_unique")
            }
            let bounds = window.frame
            let binding: [String:Any] = ["id":window.windowID,"process_id":app.processID,"app":app.bundleIdentifier,
                "title":window.title ?? "","x":bounds.origin.x,"y":bounds.origin.y,"width":bounds.width,"height":bounds.height]
            // Probe the same filter constructor as recording, without a stream.
            let filter = SCContentFilter(desktopIndependentWindow: window)
            if probing {
                try emit(["status":"window_candidate","window":binding])
                return
            }
            guard let seconds = Double(args[2]), seconds.isFinite, seconds > 0, seconds <= 3720,
                  let encoded = args[4].data(using:.utf8), encoded.count <= 4096 else { throw Failure.blocked("invalid_recording_arguments") }
            let expected = try JSONDecoder().decode(TargetBinding.self,from:encoded)
            guard let contextData = args[5].data(using:.utf8), contextData.count <= 4096 else { throw Failure.blocked("invalid_recording_context") }
            let context = try JSONDecoder().decode(RecordingContext.self,from:contextData)
            let executable = URL(fileURLWithPath:args[0]).standardizedFileURL
            let binaryHash = SHA256.hash(data:try Data(contentsOf:executable)).map { String(format:"%02x",$0) }.joined()
            let sourceURL = URL(string:context.canonical_url)
            let host = sourceURL?.host ?? ""
            let maximum = host == "www.facebook.com" ? 1200.0 : 3600.0
            guard UUID(uuidString:context.request_id) != nil,
                  sourceURL?.scheme == "https", ["www.facebook.com","www.youtube.com"].contains(host),
                  context.package.range(of:"^[a-f0-9]{64}$",options:.regularExpression) != nil,
                  context.recorder_sha256 == binaryHash, context.source_duration.isFinite,
                  context.source_duration > 0, context.source_duration <= maximum,
                  seconds == context.source_duration+60, context.deadline_epoch.isFinite,
                  context.deadline_epoch-Date().timeIntervalSince1970 >= context.source_duration+120 else {
                throw Failure.blocked("invalid_recording_context")
            }
            // Recheck the native-corroborated target immediately before capture.
            guard expected.id == window.windowID && expected.process_id == app.processID && expected.app == app.bundleIdentifier &&
                  expected.title == window.title && expected.x == bounds.origin.x && expected.y == bounds.origin.y &&
                  expected.width == bounds.width && expected.height == bounds.height else { throw Failure.blocked("recording_target_changed_or_ambiguous") }
            let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/state/k2b/video-v2")
            let output = URL(fileURLWithPath: args[1]).standardizedFileURL
            guard output.lastPathComponent == "source.mp4", output.path.hasPrefix(root.path + "/"),
                  !FileManager.default.fileExists(atPath: output.path) else { throw Failure.blocked("invalid_output_or_title") }
            var ancestor = output.deletingLastPathComponent()
            while ancestor.path != "/" {
                let values = try ancestor.resourceValues(forKeys: [.isSymbolicLinkKey])
                guard values.isSymbolicLink != true else { throw Failure.blocked("symlink_output") }
                ancestor.deleteLastPathComponent()
            }
            let config = SCStreamConfiguration()
            let scale = min(1280 / bounds.width, 720 / bounds.height, 1)
            config.width = max(2, Int(bounds.width * scale) / 2 * 2)
            config.height = max(2, Int(bounds.height * scale) / 2 * 2)
            config.minimumFrameInterval = CMTime(value: 1, timescale: 25)
            config.capturesAudio = true
            config.captureMicrophone = false
            config.excludesCurrentProcessAudio = true
            config.sampleRate = 48000
            config.channelCount = 2
            config.showsCursor = false
            let stream = SCStream(filter: filter, configuration: config, delegate: nil)
            let recordingConfig = SCRecordingOutputConfiguration()
            recordingConfig.outputURL = output
            recordingConfig.videoCodecType = .h264
            recordingConfig.outputFileType = .mp4
            let delegate = RecordingDelegate(marker:output.deletingLastPathComponent().appendingPathComponent("recording-started.json"),context:context,binding:binding)
            let recording = SCRecordingOutput(configuration: recordingConfig, delegate: delegate)
            try stream.addRecordingOutput(recording)
            try await stream.startCapture()
            // Only the recording-output callback publishes readiness. A returned
            // startCapture call or a UI indicator is not equivalent evidence.
            for _ in 0..<100 {
                let status = delegate.snapshot()
                if status.ready != nil || status.failed { break }
                try await Task.sleep(for:.milliseconds(100))
            }
            guard delegate.snapshot().ready != nil && !delegate.snapshot().failed else {
                try? await stream.stopCapture()
                throw Failure.blocked("recording_ready_callback_missing")
            }
            // Native CUA must start and observe advancing playback within 30s.
            // Keep raw padding; actual source boundaries still need content review.
            try await Task.sleep(for: .seconds(seconds))
            try await stream.stopCapture()
            for _ in 0..<100 {
                if delegate.snapshot().finished || delegate.snapshot().failed { break }
                try await Task.sleep(for: .milliseconds(100))
            }
            guard !delegate.snapshot().failed && delegate.snapshot().finished, let ready = delegate.snapshot().ready else { throw Failure.blocked("recording_did_not_finish") }
            try emit(["method":"playback_recording","system_audio":true,"microphone":false,"requested_recording_seconds":seconds,
                "ready_marker_sha256":ready.0,"ready_epoch":ready.1,"boundary_review_required":true,"source_audio_review_required":true,"window":binding,
                "output_width":config.width,"output_height":config.height,"aspect_policy":"preserve_window_aspect_within_1280x720"])
        } catch {
            let reason: String
            if case Failure.blocked(let code) = error { reason = code } else { reason = "recording_framework_failed" }
            FileHandle.standardError.write(Data("{\"status\":\"blocked\",\"reason\":\"\(reason)\"}\n".utf8))
            exit(2)
        }
    }
}
