import Foundation
import UIKit
import SwiftSignalKit
import TelegramCore

func authorizationLogsTextSignal(maxLines: Int = 160, maxCharacters: Int = 12000) -> Signal<String, NoError> {
    return (Logger.shared.collectShortLog()
    |> take(1)
    |> map { events -> String in
        var lines: [String] = []
        lines.reserveCapacity(maxLines + 8)
        
        let appVersion = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "unknown"
        let buildNumber = (Bundle.main.infoDictionary?["CFBundleVersion"] as? String) ?? "unknown"
        let now = ISO8601DateFormatter().string(from: Date())
        
        lines.append("AirGram iOS authorization logs")
        lines.append("time=\(now)")
        lines.append("app=\(appVersion) (\(buildNumber))")
        lines.append("os=iOS \(UIDevice.current.systemVersion)")
        lines.append("device=\(UIDevice.current.model)")
        lines.append("-----")
        
        let tailEvents = events.suffix(maxLines)
        if tailEvents.isEmpty {
            lines.append("No active short logs collected.")
        } else {
            let formatter = ISO8601DateFormatter()
            for (timestamp, message) in tailEvents {
                let time = formatter.string(from: Date(timeIntervalSince1970: timestamp))
                lines.append("\(time) \(message)")
            }
        }
        
        var text = lines.joined(separator: "\n")
        if text.count > maxCharacters {
            text = String(text.prefix(maxCharacters)) + "\n...[truncated]"
        }
        return text
    })
}

func exportAuthorizationLogsToTelegramWeb(
    openUrl: @escaping (String) -> Void,
    completed: @escaping (String) -> Void
) -> Disposable {
    return (authorizationLogsTextSignal()
    |> deliverOnMainQueue).start(next: { text in
        UIPasteboard.general.string = text
        openUrl("https://web.telegram.org/a/")
        completed(text)
    })
}
