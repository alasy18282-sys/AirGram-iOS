import Foundation
import UIKit
import Display
import AsyncDisplayKit
import SwiftSignalKit
import Postbox
import TelegramCore
import TelegramPresentationData
import PresentationDataUtils
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
    
    let sampleGifts = Array(gifts.prefix(12))
    var giftSignals: [Signal<String, NoError>] = []
    
    for (index, gift) in sampleGifts.enumerated() {
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
            giftSignals.append(.single("[\(index + 1)] \(giftNumber) \(giftLabel)\n  (no media files in gift payload)"))
            continue
        }
        
        var fileSignals: [Signal<String, NoError>] = []
        for (role, file) in mediaFiles {
            let fileId = file.fileId.id
            let resourceId = file.resource.id.stringRepresentation
            let statusSignal = account.postbox.mediaBox.resourceStatus(file.resource)
            |> take(1)
            |> map { status -> String in
                var lines: [String] = []
                lines.append("  \(role)")
                lines.append("    fileId=\(fileId) size=\(file.size ?? 0)")
                lines.append("    mime=\(file.mimeType) resource=\(resourceId)")
                lines.append("    stickerPack=\(stickerPackSummary(for: file))")
                lines.append("    status=\(describeResourceStatus(status))")
                return lines.joined(separator: "\n")
            }
            
            let customEmojiFetch = freeMediaFileResourceInteractiveFetched(
                account: account,
                userLocation: .other,
                fileReference: .customEmoji(media: file),
                resource: file.resource
            )
            |> map { _ -> String in "customEmoji=OK" }
            |> `catch` { error -> Signal<String, NoError> in
                return .single("customEmoji=FAIL(\(error))")
            }
            |> timeout(8.0, queue: Queue.mainQueue(), alternate: .single("customEmoji=TIMEOUT"))
            
            let standaloneFetch = freeMediaFileResourceInteractiveFetched(
                account: account,
                userLocation: .other,
                fileReference: .standalone(media: file),
                resource: file.resource
            )
            |> map { _ -> String in "standalone=OK" }
            |> `catch` { error -> Signal<String, NoError> in
                return .single("standalone=FAIL(\(error))")
            }
            |> timeout(8.0, queue: Queue.mainQueue(), alternate: .single("standalone=TIMEOUT"))
            
            fileSignals.append(
                statusSignal
                |> mapToSignal { statusText -> Signal<String, NoError> in
                    return combineLatest(customEmojiFetch, standaloneFetch)
                    |> take(1)
                    |> map { customEmoji, standalone in
                        return statusText + "\n    fetch \(customEmoji), \(standalone)"
                    }
                }
            )
        }
        
        giftSignals.append(
            combineLatest(fileSignals)
            |> take(1)
            |> map { fileLines in
                var lines = ["[\(index + 1)] \(giftNumber) \(giftLabel)"]
                lines.append(contentsOf: fileLines)
                return lines.joined(separator: "\n")
            }
        )
    }
    
    let giftsReport: Signal<String, NoError>
    if giftSignals.isEmpty {
        giftsReport = .single("(no gifts to test)")
    } else {
        giftsReport = combineLatest(giftSignals)
        |> take(1)
        |> map { $0.joined(separator: "\n\n") }
    }
    
    let shortLogs = Logger.shared.collectShortLog()
    |> take(1)
    |> map { events -> String in
        var lines = ["-----", "Recent GiftMedia / network logs:"]
        let filtered = events.filter { _, message in
            message.contains("GiftMedia") || message.contains("upload.getFile") || message.contains("FILE_REFERENCE") || message.contains("LOCATION_INVALID")
        }.suffix(40)
        if filtered.isEmpty {
            lines.append("(no matching short logs — enable Log to File in Debug menu)")
        } else {
            let formatter = ISO8601DateFormatter()
            for (timestamp, message) in filtered {
                let time = formatter.string(from: Date(timeIntervalSince1970: timestamp))
                lines.append("\(time) \(message)")
            }
        }
        return lines.joined(separator: "\n")
    }
    
    return combineLatest(giftsReport, shortLogs)
    |> take(1)
    |> map { giftsText, logsText in
        (header + [giftsText, logsText]).joined(separator: "\n")
    }
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
        private let textView = UITextView()
        private let runButton = ComponentView<Empty>()
        private let copyButton = ComponentView<Empty>()
        
        private var component: MediaLogsAndTestsScreenComponent?
        private var environment: EnvironmentType?
        private var disposable: Disposable?
        private var reportText: String = "Tap Run Tests to diagnose gift media loading."
        
        override init(frame: CGRect) {
            super.init(frame: frame)
            
            self.scrollView.alwaysBounceVertical = true
            self.addSubview(self.scrollView)
            
            self.textView.isEditable = false
            self.textView.font = UIFont.monospacedSystemFont(ofSize: 12.0, weight: .regular)
            self.textView.backgroundColor = .clear
            self.textView.textContainerInset = UIEdgeInsets(top: 12.0, left: 12.0, bottom: 12.0, right: 12.0)
            self.scrollView.addSubview(self.textView)
        }
        
        required init?(coder: NSCoder) {
            fatalError("init(coder:) has not been implemented")
        }
        
        deinit {
            self.disposable?.dispose()
        }
        
        private func updateText() {
            self.textView.text = self.reportText
            self.textView.sizeToFit()
            self.setNeedsLayout()
        }
        
        private func runTests() {
            guard let component = self.component else {
                return
            }
            Logger.shared.log("GiftMedia", "MediaLogsAndTests: runTests peerId=\(component.peerId.id._internalGetInt64Value())")
            self.reportText = "Running tests...\n"
            self.updateText()
            
            self.disposable?.dispose()
            self.disposable = (component.profileGifts.state
            |> take(1)
            |> mapToSignal { state -> Signal<String, NoError> in
                return runGiftMediaDiagnostics(context: component.context, peerId: component.peerId, gifts: state.gifts)
            }
            |> deliverOnMainQueue).start(next: { [weak self] text in
                guard let self else {
                    return
                }
                self.reportText = text
                self.updateText()
                Logger.shared.log("GiftMedia", "MediaLogsAndTests: completed (\(text.count) chars)")
            })
        }
        
        func update(component: MediaLogsAndTestsScreenComponent, availableSize: CGSize, state: EmptyComponentState, environment: Environment<MediaLogsAndTestsScreenComponent.EnvironmentType>, transition: ComponentTransition) -> CGSize {
            self.component = component
            let environmentValue = environment[MediaLogsAndTestsScreenComponent.EnvironmentType.self].value
            self.environment = environmentValue
            
            let theme = environmentValue.theme
            self.backgroundColor = theme.list.plainBackgroundColor
            self.textView.textColor = theme.list.itemPrimaryTextColor
            
            let sideInset: CGFloat = 16.0
            let buttonHeight: CGFloat = 50.0
            let bottomInset = environmentValue.safeInsets.bottom
            
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
                        isEnabled: true,
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
                            Logger.shared.log("GiftMedia", "MediaLogsAndTests: copied report")
                        }
                    )
                ),
                environment: {},
                containerSize: CGSize(width: availableSize.width - sideInset * 2.0, height: buttonHeight)
            )
            
            if let runView = self.runButton.view {
                if runView.superview == nil {
                    self.addSubview(runView)
                }
                transition.setFrame(view: runView, frame: CGRect(x: sideInset, y: availableSize.height - bottomInset - buttonHeight - 8.0 - buttonHeight - 8.0, width: runButtonSize.width, height: runButtonSize.height))
            }
            if let copyView = self.copyButton.view {
                if copyView.superview == nil {
                    self.addSubview(copyView)
                }
                transition.setFrame(view: copyView, frame: CGRect(x: sideInset, y: availableSize.height - bottomInset - buttonHeight - 8.0, width: copyButtonSize.width, height: copyButtonSize.height))
            }
            
            let scrollFrame = CGRect(x: 0.0, y: 0.0, width: availableSize.width, height: availableSize.height - bottomInset - buttonHeight * 2.0 - 24.0)
            transition.setFrame(view: self.scrollView, frame: scrollFrame)
            
            let textWidth = scrollFrame.width - 24.0
            let textSize = self.textView.sizeThatFits(CGSize(width: textWidth, height: .greatestFiniteMagnitude))
            transition.setFrame(view: self.textView, frame: CGRect(x: 12.0, y: 0.0, width: textWidth, height: max(textSize.height, scrollFrame.height)))
            self.scrollView.contentSize = CGSize(width: scrollFrame.width, height: max(textSize.height, scrollFrame.height))
            
            if self.textView.text != self.reportText {
                self.updateText()
            }
            
            if self.reportText == "Tap Run Tests to diagnose gift media loading." {
                self.runTests()
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
        
        Logger.shared.log("GiftMedia", "MediaLogsAndTestsScreen opened peerId=\(peerId.id._internalGetInt64Value())")
    }
    
    required public init(coder aDecoder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    @objc private func closePressed() {
        self.dismiss()
    }
}
