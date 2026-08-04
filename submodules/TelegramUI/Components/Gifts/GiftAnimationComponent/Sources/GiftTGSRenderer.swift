import Foundation
import UIKit
import Display
import SwiftSignalKit
import Postbox
import TelegramCore
import AccountContext
import AnimatedStickerNode
import TelegramAnimatedStickerNode
import EmojiTextAttachmentView

public enum GiftTGSRenderer {
    public enum PlaybackMode {
        case loop
        case once
        case still
    }
    
    public static func log(_ message: String) {
        Logger.shared.shortLog("GiftMedia", message)
    }
    
    @discardableResult
    public static func prefetch(
        account: Account,
        file: TelegramMediaFile,
        disposables: DisposableSet,
        onLocal: (() -> Void)? = nil
    ) -> Bool {
        let fileId = file.fileId.id
        log("prefetch start fileId=\(fileId) mime=\(file.mimeType)")
        disposables.add(freeMediaFileResourceInteractiveFetched(
            account: account,
            userLocation: .other,
            fileReference: .standalone(media: file),
            resource: file.resource
        ).start(error: { error in
            log("prefetch fail fileId=\(fileId) error=\(error)")
        }, completed: {
            log("prefetch completed fileId=\(fileId)")
        }))
        if let onLocal {
            disposables.add((account.postbox.mediaBox.resourceStatus(file.resource)
            |> filter { status in
                if case .Local = status {
                    return true
                }
                return false
            }
            |> take(1)
            |> deliverOnMainQueue).start(next: { _ in
                log("prefetch local fileId=\(fileId)")
                onLocal()
            }))
        }
        return true
    }
    
    public static func prefetch(
        account: Account,
        files: [TelegramMediaFile],
        disposables: DisposableSet,
        onAnyLocal: (() -> Void)? = nil
    ) {
        for file in files {
            prefetch(account: account, file: file, disposables: disposables, onLocal: onAnyLocal)
        }
    }
    
    @discardableResult
    public static func setup(
        node: DefaultAnimatedStickerNodeImpl,
        account: Account,
        file: TelegramMediaFile,
        size: CGSize,
        playbackMode: PlaybackMode = .once
    ) -> DefaultAnimatedStickerNodeImpl {
        let pathPrefix = account.postbox.mediaBox.shortLivedResourceCachePathPrefix(file.resource.id)
        let mode: AnimatedStickerPlaybackMode
        switch playbackMode {
        case .loop:
            mode = .loop
        case .once:
            mode = .once
        case .still:
            mode = .still(.start)
        }
        node.setup(
            source: AnimatedStickerResourceSource(account: account, resource: file.resource, isVideo: file.isVideoSticker),
            width: Int(size.width * 1.6),
            height: Int(size.height * 1.6),
            playbackMode: mode,
            mode: .direct(cachePathPrefix: pathPrefix)
        )
        node.visibility = true
        node.updateLayout(size: size)
        switch playbackMode {
        case .loop:
            node.playLoop()
        case .once:
            node.playOnce()
        case .still:
            break
        }
        return node
    }
    
    public static func makeNode(
        account: Account,
        file: TelegramMediaFile,
        size: CGSize,
        playbackMode: PlaybackMode = .once
    ) -> DefaultAnimatedStickerNodeImpl {
        let node = DefaultAnimatedStickerNodeImpl()
        node.isUserInteractionEnabled = false
        return setup(node: node, account: account, file: file, size: size, playbackMode: playbackMode)
    }
    
    public static func setupInlineLayer(
        context: AccountContext,
        file: TelegramMediaFile,
        size: CGSize,
        placeholderColor: UIColor,
        loopCount: Int? = 1
    ) -> InlineStickerItemLayer {
        let layer = InlineStickerItemLayer(
            context: .account(context),
            userLocation: .other,
            attemptSynchronousLoad: true,
            emoji: ChatTextInputTextCustomEmojiAttribute(
                interactivelySelectedFromPackId: nil,
                fileId: file.fileId.id,
                file: file
            ),
            file: file,
            cache: context.animationCache,
            renderer: context.animationRenderer,
            unique: true,
            placeholderColor: placeholderColor,
            pointSize: size,
            loopCount: loopCount
        )
        layer.isVisibleForAnimations = true
        layer.playOnce()
        return layer
    }
    
    public static func attachInlineLayer(
        context: AccountContext,
        file: TelegramMediaFile,
        size: CGSize,
        placeholderColor: UIColor,
        to parentLayer: CALayer,
        disposables: DisposableSet,
        loopCount: Int? = 1
    ) -> InlineStickerItemLayer {
        let layer = setupInlineLayer(
            context: context,
            file: file,
            size: size,
            placeholderColor: placeholderColor,
            loopCount: loopCount
        )
        parentLayer.addSublayer(layer)
        prefetch(account: context.account, file: file, disposables: disposables, onLocal: { [weak layer] in
            layer?.reloadAnimation()
        })
        return layer
    }
}
