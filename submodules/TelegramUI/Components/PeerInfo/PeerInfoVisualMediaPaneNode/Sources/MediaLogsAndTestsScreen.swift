import Foundation
import UIKit
import Display
import SwiftSignalKit
import Postbox
import TelegramCore
import TelegramPresentationData
import AccountContext
import ComponentFlow
import ViewControllerComponent
import MultilineTextComponent
import ButtonComponent

private func describeResourceStatus(_ status: MediaResourceStatus) -> String {
    switch status {
    case .Local:
        return "Local"
    case let .Remote(progress):
        return String(format: "Remote(%.2f)", progress)
    case let .Fetching(isActive, progress):
        return String(format: "Fetching(active=%@, %.2f)", isActive ? "yes" : "no", progress)
    case let .Paused(progress):
        return String(format: "Paused(%.2f)", progress)
    }
}

private func stickerPackSummary(for file: TelegramMediaFile) -> String {
    for attribute in file.attributes {
        if case let .Sticker(_, packReference, _) = attribute {
            if let packReference {
                return "yes(\(packReference))"
            }
            return "yes(nil)"
        }
    }
    return "no"
}

private func collectGiftMediaFiles(from gift: ProfileGiftsContext.State.StarGift) -> [(String, TelegramMediaFile)] {
    var result: [(String, TelegramMediaFile)] = []
    switch gift.gift {
    case let .unique(uniqueGift):
        for attribute in uniqueGift.attributes {
            switch attribute {
            case let .model(name, file, _, _):
                result.append(("model:\(name)", file))
            case let .pattern(name, file, _):
                result.append(("pattern:\(name)", file))
            case .backdrop, .originalInfo:
                break
            }
        }
    case let .generic(starGift):
        result.append(("cover", starGift.file))
    }
    return result
}

private func inspectMediaFile(account: Account, role: String, file: TelegramMediaFile) -> Signal<String, NoError> {
    let fileId = file.fileId.id
    let resourceId = file.resource.id.stringRepresentation
    let localDataSignal = account.postbox.mediaBox.resourceData(file.resource, attemptSynchronously: true)
    |> take(1)
    |> map { data -> String in
        if data.complete && data.size > 0 {
            return "localData=complete size=\(data.size)"
        } else if data.size > 0 {
            return "localData=partial size=\(data.size)"
        } else {
            return "localData=missing"
        }
    }
    |> timeout(1.5, queue: Queue.mainQueue(), alternate: .single("localData=timeout"))
    
    return combineLatest(
        account.postbox.mediaBox.resourceStatus(file.resource)
        |> take(1)
        |> timeout(1.5, queue: Queue.mainQueue(), alternate: .single(.Remote(progress: 0))),
        localDataSignal
    )
    |> take(1)
    |> map { status, localData in
        var lines: [String] = []
        lines.append("  \(role)")
        lines.append("    fileId=\(fileId) size=\(file.size ?? 0)")
        lines.append("    mime=\(file.mimeType) resource=\(resourceId)")
        lines.append("    stickerPack=\(stickerPackSummary(for: file))")
        lines.append("    status=\(describeResourceStatus(status))")
        lines.append("    \(localData)")
        return lines.joined(separator: "\n")
    }
}

private func inspectGift(account: Account, index: Int, gift: ProfileGiftsContext.State.StarGift) -> Signal<String, NoError> {
    let giftNumber = gift.number.map { "#\($0)" } ?? "n/a"
    let giftLabel: String
    switch gift.gift {
    case let .unique(uniqueGift):
        giftLabel = "unique slug=\(uniqueGift.slug) num=\(uniqueGift.number)"
    case let .generic(starGift):
        giftLabel = "starGift id=\(starGift.id) title=\(starGift.title ?? "nil")"
    }
    
    let mediaFiles = collectGiftMediaFiles(from: gift)
    if mediaFiles.isEmpty {
        return .single("[\(index + 1)] \(giftNumber) \(giftLabel)\n  (no media files in gift payload)")
    }
    
    let fileSignals = mediaFiles.map { role, file in
        inspectMediaFile(account: account, role: role, file: file)
    }
    return combineLatest(fileSignals)
    |> take(1)
    |> map { fileLines in
        var lines = ["[\(index + 1)] \(giftNumber) \(giftLabel)"]
        lines.append(contentsOf: fileLines)
        return lines.joined(separator: "\n")
    }
}

private func giftsForDiagnostics(profileGifts: ProfileGiftsContext) -> Signal<[ProfileGiftsContext.State.StarGift], NoError> {
    if let state = profileGifts.currentState {
        return .single(state.gifts)
    }
    return profileGifts.state
    |> take(1)
    |> map { $0.gifts }
    |> timeout(3.0, queue: Queue.mainQueue(), alternate: .single([]))
}

private func runGiftMediaDiagnostics(context: AccountContext, peerId: PeerId, gifts: [ProfileGiftsContext.State.StarGift]) -> Signal<String, NoError> {
    let account = context.account
    let appVersion = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "unknown"
    let buildNumber = (Bundle.main.infoDictionary?["CFBundleVersion"] as? String) ?? "unknown"
    let bundleId = Bundle.main.bundleIdentifier ?? "unknown"
    let now = ISO8601DateFormatter().string(from: Date())
    
    var header: [String] = []
    header.append("AirGram Media Logs and Tests")
    header.append("time=\(now)")
    header.append("app=\(appVersion) (\(buildNumber)) bundle=\(bundleId)")
    header.append("os=iOS \(UIDevice.current.systemVersion)")
    header.append("peerId=\(peerId.id._internalGetInt64Value())")
    header.append("logToFile=\(Logger.shared.logToFile) logToConsole=\(Logger.shared.logToConsole)")
    header.append("giftsTotal=\(gifts.count)")
    header.append("-----")
    
    let sampleGifts = Array(gifts.prefix(6))
    let giftsReport: Signal<String, NoError>
    if sampleGifts.isEmpty {
        giftsReport = .single("(no gifts loaded — open Gifts tab first, then retry)")
    } else {
        var chain: Signal<String, NoError> = .single("")
        for (index, gift) in sampleGifts.enumerated() {
            chain = chain |> mapToSignal { accumulated -> Signal<String, NoError> in
                return inspectGift(account: account, index: index, gift: gift)
                |> map { giftReport in
                    if accumulated.isEmpty {
                        return giftReport
                    }
                    return accumulated + "\n\n" + giftReport
                }
            }
        }
        giftsReport = chain
    }
    
    let shortLogs = Logger.shared.collectShortLog()
    |> take(1)
    |> timeout(2.0, queue: Queue.mainQueue(), alternate: .single([]))
    |> mapToSignal { events -> Signal<String, NoError> in
        return Logger.shared.collectLogs(prefix: "log-")
        |> take(1)
        |> map { logFiles -> String in
            var lines = ["-----", "Recent GiftMedia / network logs (short):"]
            let filtered = events.filter { _, message in
                message.contains("GiftMedia") || message.contains("upload.getFile") || message.contains("FILE_REFERENCE") || message.contains("LOCATION_INVALID")
            }.suffix(40)
            if filtered.isEmpty {
                lines.append("(no matching short logs yet)")
            } else {
                let formatter = ISO8601DateFormatter()
                for (timestamp, message) in filtered {
                    let time = formatter.string(from: Date(timeIntervalSince1970: timestamp))
                    lines.append("\(time) \(message)")
                }
            }
            if !logFiles.isEmpty, let (_, path) = logFiles.last {
                if let data = try? String(contentsOf: URL(fileURLWithPath: path), encoding: .utf8) {
                    let tail = data.split(separator: "\n").suffix(30).joined(separator: "\n")
                    lines.append("-----")
                    lines.append("File log tail (\(URL(fileURLWithPath: path).lastPathComponent)):")
                    lines.append(String(tail))
                }
            }
            return lines.joined(separator: "\n")
        }
    }
    
    return combineLatest(giftsReport, shortLogs)
    |> take(1)
    |> map { giftsText, logsText in
        (header + [giftsText, logsText]).joined(separator: "\n")
    }
    |> timeout(20.0, queue: Queue.mainQueue(), alternate: .single((header + ["(diagnostics timed out after 20s — try Copy Report and retry with fewer gifts)"]).joined(separator: "\n")))
}

final class MediaLogsAndTestsScreenComponent: Component {
    typealias EnvironmentType = ViewControllerComponentContainer.Environment
    
    let context: AccountContext
    let peerId: PeerId
    let profileGifts: ProfileGiftsContext
    
    init(context: AccountContext, peerId: PeerId, profileGifts: ProfileGiftsContext) {
        self.context = context
        self.peerId = peerId
        self.profileGifts = profileGifts
    }
    
    static func ==(lhs: MediaLogsAndTestsScreenComponent, rhs: MediaLogsAndTestsScreenComponent) -> Bool {
        return true
    }
    
    final class View: UIView {
        private let scrollView = UIScrollView()
        private let textLabel = UILabel()
        private let bottomContainer = UIView()
        private let runButton = ComponentView<Empty>()
        private let copyButton = ComponentView<Empty>()
        
        private var component: MediaLogsAndTestsScreenComponent?
        private var environment: EnvironmentType?
        private var disposable: Disposable?
        private var reportText: String = "Tap Run Tests to diagnose gift media loading."
        private var didAutoRun = false
        private var isRunning = false
        
        override init(frame: CGRect) {
            super.init(frame: frame)
            
            self.scrollView.alwaysBounceVertical = true
            self.scrollView.showsVerticalScrollIndicator = true
            self.addSubview(self.scrollView)
            
            self.textLabel.numberOfLines = 0
            self.textLabel.font = UIFont.monospacedSystemFont(ofSize: 12.0, weight: .regular)
            self.scrollView.addSubview(self.textLabel)
            
            self.addSubview(self.bottomContainer)
        }
        
        required init?(coder: NSCoder) {
            fatalError("init(coder:) has not been implemented")
        }
        
        deinit {
            self.disposable?.dispose()
        }
        
        private func setReportText(_ text: String) {
            self.reportText = text
            self.textLabel.text = text
            self.setNeedsLayout()
        }
        
        private func runTests() {
            guard let component = self.component, !self.isRunning else {
                return
            }
            self.isRunning = true
            Logger.shared.shortLog("GiftMedia", "MediaLogsAndTests: runTests peerId=\(component.peerId.id._internalGetInt64Value())")
            self.setReportText("Running tests...\n")
            
            self.disposable?.dispose()
            self.disposable = (giftsForDiagnostics(profileGifts: component.profileGifts)
            |> mapToSignal { gifts -> Signal<String, NoError> in
                return runGiftMediaDiagnostics(context: component.context, peerId: component.peerId, gifts: gifts)
            }
            |> deliverOnMainQueue).start(next: { [weak self] text in
                guard let self else {
                    return
                }
                self.isRunning = false
                self.setReportText(text)
                Logger.shared.shortLog("GiftMedia", "MediaLogsAndTests: completed (\(text.count) chars)")
            })
        }
        
        private func layoutContent(availableSize: CGSize, environmentValue: EnvironmentType, theme: PresentationTheme, sideInset: CGFloat, buttonHeight: CGFloat, transition: ComponentTransition) {
            let bottomInset = environmentValue.safeInsets.bottom
            let bottomPanelHeight = bottomInset + buttonHeight * 2.0 + 8.0 + 16.0
            
            transition.setFrame(view: self.bottomContainer, frame: CGRect(x: 0.0, y: availableSize.height - bottomPanelHeight, width: availableSize.width, height: bottomPanelHeight))
            self.bottomContainer.backgroundColor = theme.list.plainBackgroundColor
            
            let runButtonSize = self.runButton.update(
                transition: transition,
                component: AnyComponent(
                    ButtonComponent(
                        background: ButtonComponent.Background(
                            style: .glass,
                            color: theme.list.itemCheckColors.fillColor,
                            foreground: theme.list.itemCheckColors.foregroundColor,
                            pressedColor: theme.list.itemCheckColors.fillColor.withMultipliedAlpha(0.8)
                        ),
                        content: AnyComponentWithIdentity(
                            id: "run",
                            component: AnyComponent(MultilineTextComponent(text: .plain(NSAttributedString(string: "Run Tests", font: Font.semibold(17.0), textColor: theme.list.itemCheckColors.foregroundColor, paragraphAlignment: .center))))
                        ),
                        isEnabled: !self.isRunning,
                        action: { [weak self] in
                            self?.runTests()
                        }
                    )
                ),
                environment: {},
                containerSize: CGSize(width: availableSize.width - sideInset * 2.0, height: buttonHeight)
            )
            
            let copyButtonSize = self.copyButton.update(
                transition: transition,
                component: AnyComponent(
                    ButtonComponent(
                        background: ButtonComponent.Background(
                            style: .glass,
                            color: theme.list.itemBlocksBackgroundColor,
                            foreground: theme.list.itemPrimaryTextColor,
                            pressedColor: theme.list.itemBlocksBackgroundColor.withMultipliedAlpha(0.8)
                        ),
                        content: AnyComponentWithIdentity(
                            id: "copy",
                            component: AnyComponent(MultilineTextComponent(text: .plain(NSAttributedString(string: "Copy Report", font: Font.semibold(17.0), textColor: theme.list.itemPrimaryTextColor, paragraphAlignment: .center))))
                        ),
                        isEnabled: true,
                        action: { [weak self] in
                            guard let self else {
                                return
                            }
                            UIPasteboard.general.string = self.reportText
                            Logger.shared.shortLog("GiftMedia", "MediaLogsAndTests: copied report")
                        }
                    )
                ),
                environment: {},
                containerSize: CGSize(width: availableSize.width - sideInset * 2.0, height: buttonHeight)
            )
            
            if let runView = self.runButton.view {
                if runView.superview == nil {
                    self.bottomContainer.addSubview(runView)
                }
                transition.setFrame(view: runView, frame: CGRect(x: sideInset, y: 8.0, width: runButtonSize.width, height: runButtonSize.height))
            }
            if let copyView = self.copyButton.view {
                if copyView.superview == nil {
                    self.bottomContainer.addSubview(copyView)
                }
                transition.setFrame(view: copyView, frame: CGRect(x: sideInset, y: 8.0 + buttonHeight + 8.0, width: copyButtonSize.width, height: copyButtonSize.height))
            }
            
            let scrollFrame = CGRect(x: 0.0, y: 0.0, width: availableSize.width, height: max(0.0, availableSize.height - bottomPanelHeight))
            transition.setFrame(view: self.scrollView, frame: scrollFrame)
            
            let textWidth = scrollFrame.width - sideInset * 2.0
            let textHeight = self.textLabel.sizeThatFits(CGSize(width: textWidth, height: .greatestFiniteMagnitude)).height
            let contentHeight = max(textHeight + 24.0, scrollFrame.height)
            transition.setFrame(view: self.textLabel, frame: CGRect(x: sideInset, y: 12.0, width: textWidth, height: textHeight))
            self.scrollView.contentSize = CGSize(width: scrollFrame.width, height: contentHeight)
        }
        
        func update(component: MediaLogsAndTestsScreenComponent, availableSize: CGSize, state: EmptyComponentState, environment: Environment<MediaLogsAndTestsScreenComponent.EnvironmentType>, transition: ComponentTransition) -> CGSize {
            self.component = component
            let environmentValue = environment[MediaLogsAndTestsScreenComponent.EnvironmentType.self].value
            self.environment = environmentValue
            
            let theme = environmentValue.theme
            self.backgroundColor = theme.list.plainBackgroundColor
            self.textLabel.textColor = theme.list.itemPrimaryTextColor
            
            if self.textLabel.text != self.reportText {
                self.textLabel.text = self.reportText
            }
            
            self.layoutContent(
                availableSize: availableSize,
                environmentValue: environmentValue,
                theme: theme,
                sideInset: 16.0,
                buttonHeight: 50.0,
                transition: transition
            )
            
            if !self.didAutoRun {
                self.didAutoRun = true
                Queue.mainQueue().justDispatch {
                    self.runTests()
                }
            }
            
            return availableSize
        }
    }
    
    func makeView() -> View {
        return View()
    }
    
    func update(view: View, availableSize: CGSize, state: EmptyComponentState, environment: Environment<EnvironmentType>, transition: ComponentTransition) -> CGSize {
        return view.update(component: self, availableSize: availableSize, state: state, environment: environment, transition: transition)
    }
}

public final class MediaLogsAndTestsScreen: ViewControllerComponentContainer {
    public init(context: AccountContext, peerId: PeerId, profileGifts: ProfileGiftsContext) {
        super.init(
            context: context,
            component: MediaLogsAndTestsScreenComponent(
                context: context,
                peerId: peerId,
                profileGifts: profileGifts
            ),
            navigationBarAppearance: .default,
            theme: .default,
            updatedPresentationData: nil
        )
        self.title = "Media Logs and Tests"
        self.navigationPresentation = .modal
        
        let presentationData = context.sharedContext.currentPresentationData.with { $0 }
        self.navigationItem.leftBarButtonItem = UIBarButtonItem(title: presentationData.strings.Common_Close, style: .plain, target: self, action: #selector(self.closePressed))
        
        Logger.shared.shortLog("GiftMedia", "MediaLogsAndTestsScreen opened peerId=\(peerId.id._internalGetInt64Value())")
    }
    
    required public init(coder aDecoder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    @objc private func closePressed() {
        self.dismiss()
    }
}
