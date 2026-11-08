// Narrow macOS 15 ScreenCaptureKit recorder and read-only source-window query.
// Native Mini CUA corroborates source URL/title/opening picture. No microphone.
import Foundation
import ScreenCaptureKit
import CoreGraphics
import AVFoundation

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

@available(macOS 15.0, *)
final class RecordingDelegate: NSObject, SCRecordingOutputDelegate {
    var failure: Error?
    var finished = false
    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) { failure = error }
    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) { finished = true }
}

@main
struct Recorder {
    static func emit(_ report: [String:Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        print(String(decoding:data,as:UTF8.self))
    }

    static func main() async {
        do {
            guard #available(macOS 15.0, *) else { throw Failure.blocked("macos_15_required") }
            guard CGPreflightScreenCaptureAccess() else { throw Failure.blocked("recorder_screen_audio_permission_required") }
            let args = CommandLine.arguments
            let probing = args.count == 3 && args[1] == "probe"
            guard probing || args.count == 5 else { throw Failure.blocked("invalid_recording_arguments") }
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
            if probing {
                try emit(["status":"window_candidate","window":binding])
                return
            }
            guard let seconds = Double(args[2]), seconds.isFinite, seconds > 0, seconds <= 3720,
                  let encoded = args[4].data(using:.utf8), encoded.count <= 4096 else { throw Failure.blocked("invalid_recording_arguments") }
            let expected = try JSONDecoder().decode(TargetBinding.self,from:encoded)
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
            let filter = SCContentFilter(desktopIndependentWindow: window)
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
            let delegate = RecordingDelegate()
            let recording = SCRecordingOutput(configuration: recordingConfig, delegate: delegate)
            try stream.addRecordingOutput(recording)
            try await stream.startCapture()
            // Native CUA starts playback after the recording indicator appears.
            // Padding remains explicit and needs actual source-boundary review.
            try await Task.sleep(for: .seconds(seconds))
            try await stream.stopCapture()
            for _ in 0..<100 {
                if delegate.finished || delegate.failure != nil { break }
                try await Task.sleep(for: .milliseconds(100))
            }
            guard delegate.failure == nil && delegate.finished else { throw Failure.blocked("recording_did_not_finish") }
            try emit(["method":"playback_recording","system_audio":true,"microphone":false,"requested_recording_seconds":seconds,
                "boundary_review_required":true,"source_audio_review_required":true,"window":binding,
                "output_width":config.width,"output_height":config.height,"aspect_policy":"preserve_window_aspect_within_1280x720"])
        } catch {
            let reason: String
            if case Failure.blocked(let code) = error { reason = code } else { reason = "recording_framework_failed" }
            FileHandle.standardError.write(Data("{\"status\":\"blocked\",\"reason\":\"\(reason)\"}\n".utf8))
            exit(2)
        }
    }
}
