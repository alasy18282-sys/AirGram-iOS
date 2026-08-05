import Foundation
import UIKit
import Display
import SwiftSignalKit
import Postbox
import TelegramCore
import AccountContext
import PeerInfoCoverComponent

public struct GiftPatternAppearance: Equatable {
    public var outerColor: UIColor?
    public var innerColor: UIColor?
    public var patternColor: UIColor?
    public var patternFile: TelegramMediaFile?
    public var patternFileId: Int64?
    public var files: [Int64: TelegramMediaFile]
    
    public init(
        outerColor: UIColor? = nil,
        innerColor: UIColor? = nil,
        patternColor: UIColor? = nil,
        patternFile: TelegramMediaFile? = nil,
        patternFileId: Int64? = nil,
        files: [Int64: TelegramMediaFile] = [:]
    ) {
        self.outerColor = outerColor
        self.innerColor = innerColor
        self.patternColor = patternColor
        self.patternFile = patternFile
        self.patternFileId = patternFileId
        self.files = files
    }
    
    public var isVisible: Bool {
        if self.outerColor != nil || self.innerColor != nil {
            return true
        }
        if self.patternFile != nil {
            return true
        }
        if let patternFileId = self.patternFileId, patternFileId != 0 {
            return true
        }
        return false
    }
    
    public var resolvedPatternFileId: Int64? {
        if let patternFile = self.patternFile {
            return patternFile.fileId.id
        }
        if let patternFileId = self.patternFileId, patternFileId != 0 {
            return patternFileId
        }
        return nil
    }
}

public enum GiftPatternRenderer {
    public static func log(_ message: String) {
        GiftTGSRenderer.log("Pattern: \(message)")
    }
    
    public static func appearance(from attributes: [StarGift.UniqueGift.Attribute]) -> GiftPatternAppearance {
        var appearance = GiftPatternAppearance()
        var patternName: String?
        var backdropName: String?
        for attribute in attributes {
            switch attribute {
            case let .pattern(name, file, _):
                patternName = name
                appearance.patternFile = file
                appearance.patternFileId = file.fileId.id
                appearance.files[file.fileId.id] = file
            case let .backdrop(name, _, innerColor, outerColor, patternColor, _, _):
                backdropName = name
                appearance.innerColor = UIColor(rgb: UInt32(bitPattern: innerColor))
                appearance.outerColor = UIColor(rgb: UInt32(bitPattern: outerColor))
                appearance.patternColor = UIColor(rgb: UInt32(bitPattern: patternColor))
            default:
                break
            }
        }
        Self.log("appearance pattern=\(patternName ?? "nil") fileId=\(appearance.patternFileId.map(String.init) ?? "nil") file=\(appearance.patternFile != nil) backdrop=\(backdropName ?? "nil") colors=\(appearance.innerColor != nil)/\(appearance.outerColor != nil)/\(appearance.patternColor != nil)")
        return appearance
    }
    
    public static func appearance(from uniqueGift: StarGift.UniqueGift) -> GiftPatternAppearance {
        return self.appearance(from: uniqueGift.attributes)
    }
    
    public static func prefetchAndReloadPattern(
        account: Account,
        appearance: GiftPatternAppearance,
        disposables: DisposableSet,
        reload: @escaping () -> Void
    ) {
        self.prefetch(account: account, appearance: appearance, disposables: disposables, onReady: reload)
    }
    
    public static func prefetchStatusMedia(
        account: Account,
        emojiStatus: PeerEmojiStatus,
        gifts: [ProfileGiftsContext.State.StarGift],
        localFiles: [Int64: TelegramMediaFile] = [:],
        disposables: DisposableSet,
        onReady: (() -> Void)? = nil
    ) {
        let appearance = self.appearance(for: emojiStatus, gifts: gifts, localFiles: localFiles)
        self.prefetch(account: account, appearance: appearance, disposables: disposables, onReady: onReady)
        if let modelFile = GiftMediaSupport.modelFile(for: emojiStatus, gifts: gifts, localFiles: localFiles) {
            GiftTGSRenderer.prefetch(account: account, file: modelFile, disposables: disposables, onLocal: onReady)
        } else if case let .starGift(_, fileId, _, _, patternFileId, _, _, _, _) = emojiStatus.content {
            var fileIds: [Int64] = [fileId]
            if patternFileId != 0 {
                fileIds.append(patternFileId)
            }
            disposables.add((
                GiftMediaSupport.resolveLocalFiles(postbox: account.postbox, fileIds: fileIds)
                |> deliverOnMainQueue
            ).startStrict(next: { files in
                for file in files.values {
                    GiftTGSRenderer.prefetch(account: account, file: file, disposables: disposables, onLocal: onReady)
                }
            }))
        }
    }
    
    public static func prefetchUniqueGiftMedia(
        account: Account,
        uniqueGift: StarGift.UniqueGift,
        disposables: DisposableSet
    ) {
        for file in GiftMediaSupport.mediaFiles(from: uniqueGift).values {
            GiftTGSRenderer.prefetch(account: account, file: file, disposables: disposables)
        }
    }
    
    public static func appearance(from bundle: GiftMediaBundle) -> GiftPatternAppearance {
        var appearance = GiftPatternAppearance(files: bundle.files)
        if let outerColor = bundle.outerColor {
            appearance.outerColor = UIColor(rgb: UInt32(bitPattern: outerColor))
        }
        if let innerColor = bundle.innerColor {
            appearance.innerColor = UIColor(rgb: UInt32(bitPattern: innerColor))
        }
        if let patternColor = bundle.patternColor {
            appearance.patternColor = UIColor(rgb: UInt32(bitPattern: patternColor))
        }
        appearance.patternFile = bundle.patternFile
        appearance.patternFileId = bundle.patternFileId
        if let patternFile = bundle.patternFile {
            appearance.files[patternFile.fileId.id] = patternFile
        }
        return appearance
    }
    
    public static func appearance(
        for emojiStatus: PeerEmojiStatus,
        gifts: [ProfileGiftsContext.State.StarGift],
        localFiles: [Int64: TelegramMediaFile] = [:]
    ) -> GiftPatternAppearance {
        let bundle = GiftMediaSupport.mediaBundle(for: emojiStatus, gifts: gifts)
        var appearance = self.appearance(from: bundle)
        var files = GiftMediaSupport.combinedMediaFiles(for: emojiStatus, gifts: gifts, localFiles: localFiles)
        for (fileId, file) in appearance.files where files[fileId] == nil {
            files[fileId] = file
        }
        appearance.files = files
        if appearance.patternFile == nil, let patternFileId = appearance.patternFileId {
            appearance.patternFile = files[patternFileId]
        }
        if appearance.outerColor == nil, appearance.innerColor == nil, appearance.patternColor == nil {
            if case let .starGift(_, _, _, _, _, innerColor, outerColor, patternColor, _) = emojiStatus.content {
                appearance.innerColor = UIColor(rgb: UInt32(bitPattern: innerColor))
                appearance.outerColor = UIColor(rgb: UInt32(bitPattern: outerColor))
                appearance.patternColor = UIColor(rgb: UInt32(bitPattern: patternColor))
            }
        }
        return appearance
    }
    
    public static func prefetch(
        account: Account,
        appearance: GiftPatternAppearance,
        disposables: DisposableSet,
        onReady: (() -> Void)? = nil
    ) {
        if let patternFile = appearance.patternFile ?? appearance.resolvedPatternFileId.flatMap({ appearance.files[$0] }) {
            Self.log("prefetch pattern fileId=\(patternFile.fileId.id)")
            GiftTGSRenderer.prefetch(account: account, file: patternFile, disposables: disposables, onLocal: onReady)
            return
        }
        guard let patternFileId = appearance.resolvedPatternFileId, patternFileId != 0 else {
            Self.log("prefetch skipped: no pattern fileId")
            return
        }
        Self.log("prefetch resolve pattern fileId=\(patternFileId)")
        disposables.add((
            GiftMediaSupport.resolveLocalFiles(postbox: account.postbox, fileIds: [patternFileId])
            |> deliverOnMainQueue
        ).startStrict(next: { files in
            guard let file = files[patternFileId] else {
                Self.log("prefetch resolve failed fileId=\(patternFileId)")
                return
            }
            Self.log("prefetch resolved pattern fileId=\(patternFileId)")
            GiftTGSRenderer.prefetch(account: account, file: file, disposables: disposables, onLocal: onReady)
        }))
    }
    
    public static func makeCoverComponent(
        context: AccountContext,
        appearance: GiftPatternAppearance,
        avatarCenter: CGPoint,
        avatarSize: CGSize = CGSize(width: 100.0, height: 100.0),
        avatarScale: CGFloat = 1.0,
        defaultHeight: CGFloat,
        isDark: Bool = false,
        gradientOnTop: Bool = false,
        gradientCenter: CGPoint = CGPoint(x: 0.5, y: 0.5),
        avatarTransitionFraction: CGFloat = 0.0,
        patternTransitionFraction: CGFloat = 1.0,
        patternIconScale: CGFloat = 1.0
    ) -> PeerInfoCoverComponent? {
        guard appearance.isVisible else {
            return nil
        }
        return PeerInfoCoverComponent(
            context: context,
            subject: .custom(
                appearance.outerColor,
                appearance.innerColor,
                appearance.patternColor,
                appearance.resolvedPatternFileId
            ),
            files: appearance.files,
            isDark: isDark,
            avatarCenter: avatarCenter,
            avatarSize: avatarSize,
            avatarScale: avatarScale,
            defaultHeight: defaultHeight,
            gradientOnTop: gradientOnTop,
            gradientCenter: gradientCenter,
            avatarTransitionFraction: avatarTransitionFraction,
            patternTransitionFraction: patternTransitionFraction,
            patternIconScale: patternIconScale
        )
    }
}
