import Foundation
import Postbox
import SwiftSignalKit

public struct GiftMediaBundle: Equatable {
    public var modelFile: TelegramMediaFile?
    public var patternFile: TelegramMediaFile?
    public var files: [Int64: TelegramMediaFile]
    public var patternFileId: Int64?
    public var innerColor: Int32?
    public var outerColor: Int32?
    public var patternColor: Int32?
    public var textColor: Int32?
    
    public init(
        modelFile: TelegramMediaFile? = nil,
        patternFile: TelegramMediaFile? = nil,
        files: [Int64: TelegramMediaFile] = [:],
        patternFileId: Int64? = nil,
        innerColor: Int32? = nil,
        outerColor: Int32? = nil,
        patternColor: Int32? = nil,
        textColor: Int32? = nil
    ) {
        self.modelFile = modelFile
        self.patternFile = patternFile
        self.files = files
        self.patternFileId = patternFileId
        self.innerColor = innerColor
        self.outerColor = outerColor
        self.patternColor = patternColor
        self.textColor = textColor
    }
}

public enum GiftMediaSupport {
    public static func uniqueGift(in gifts: [ProfileGiftsContext.State.StarGift], matching emojiStatus: PeerEmojiStatus) -> StarGift.UniqueGift? {
        guard case let .starGift(giftId, _, _, slug, _, _, _, _, _) = emojiStatus.content else {
            return nil
        }
        for gift in gifts {
            if case let .unique(uniqueGift) = gift.gift, uniqueGift.id == giftId || uniqueGift.slug == slug {
                return uniqueGift
            }
        }
        return nil
    }
    
    public static func mediaFiles(from uniqueGift: StarGift.UniqueGift) -> [Int64: TelegramMediaFile] {
        var files: [Int64: TelegramMediaFile] = [:]
        for attribute in uniqueGift.attributes {
            switch attribute {
            case let .model(_, file, _, _):
                files[file.fileId.id] = file
            case let .pattern(_, file, _):
                files[file.fileId.id] = file
            case .backdrop, .originalInfo:
                break
            }
        }
        return files
    }
    
    public static func mediaBundle(from uniqueGift: StarGift.UniqueGift) -> GiftMediaBundle {
        var bundle = GiftMediaBundle()
        bundle.files = self.mediaFiles(from: uniqueGift)
        for attribute in uniqueGift.attributes {
            switch attribute {
            case let .model(_, file, _, _):
                bundle.modelFile = file
            case let .pattern(_, file, _):
                bundle.patternFile = file
                bundle.patternFileId = file.fileId.id
            case let .backdrop(_, _, innerColor, outerColor, patternColor, textColor, _):
                bundle.innerColor = innerColor
                bundle.outerColor = outerColor
                bundle.patternColor = patternColor
                bundle.textColor = textColor
            case .originalInfo:
                break
            }
        }
        return bundle
    }
    
    public static func mediaBundle(for emojiStatus: PeerEmojiStatus, gifts: [ProfileGiftsContext.State.StarGift]) -> GiftMediaBundle {
        guard case let .starGift(_, fileId, _, _, patternFileId, innerColor, outerColor, patternColor, textColor) = emojiStatus.content else {
            return GiftMediaBundle()
        }
        if let uniqueGift = self.uniqueGift(in: gifts, matching: emojiStatus) {
            return self.mediaBundle(from: uniqueGift)
        }
        return GiftMediaBundle(
            patternFileId: patternFileId,
            innerColor: innerColor,
            outerColor: outerColor,
            patternColor: patternColor,
            textColor: textColor
        ).withResolvedFileIds(modelFileId: fileId, patternFileId: patternFileId, files: [:])
    }
    
    public static func mediaFiles(for emojiStatus: PeerEmojiStatus, gifts: [ProfileGiftsContext.State.StarGift]) -> [Int64: TelegramMediaFile] {
        return self.mediaBundle(for: emojiStatus, gifts: gifts).files
    }
    
    public static func combinedMediaFiles(
        for emojiStatus: PeerEmojiStatus,
        gifts: [ProfileGiftsContext.State.StarGift],
        localFiles: [Int64: TelegramMediaFile]
    ) -> [Int64: TelegramMediaFile] {
        var files = self.mediaFiles(for: emojiStatus, gifts: gifts)
        for (fileId, file) in localFiles where files[fileId] == nil {
            files[fileId] = file
        }
        return files
    }
    
    public static func modelFile(
        for emojiStatus: PeerEmojiStatus,
        gifts: [ProfileGiftsContext.State.StarGift],
        localFiles: [Int64: TelegramMediaFile] = [:]
    ) -> TelegramMediaFile? {
        let bundle = self.mediaBundle(for: emojiStatus, gifts: gifts)
        if let modelFile = bundle.modelFile {
            return modelFile
        }
        guard case let .starGift(_, fileId, _, _, _, _, _, _, _) = emojiStatus.content else {
            return nil
        }
        return localFiles[fileId]
    }
    
    public static func resolveLocalFiles(postbox: Postbox, emojiStatus: PeerEmojiStatus) -> Signal<[Int64: TelegramMediaFile], NoError> {
        guard case let .starGift(_, fileId, _, _, patternFileId, _, _, _, _) = emojiStatus.content else {
            return .single([:])
        }
        var fileIds: [Int64] = [fileId]
        if patternFileId != 0 {
            fileIds.append(patternFileId)
        }
        return self.resolveLocalFiles(postbox: postbox, fileIds: fileIds)
    }
    
    public static func resolveLocalFiles(postbox: Postbox, fileIds: [Int64]) -> Signal<[Int64: TelegramMediaFile], NoError> {
        return _internal_resolveInlineStickersLocal(postbox: postbox, fileIds: fileIds)
    }
    
    public static func resolveMediaFiles(account: Account, fileIds: [Int64]) -> Signal<[Int64: TelegramMediaFile], NoError> {
        if fileIds.isEmpty {
            return .single([:])
        }
        return self.resolveLocalFiles(postbox: account.postbox, fileIds: fileIds)
        |> mapToSignal { localFiles -> Signal<[Int64: TelegramMediaFile], NoError> in
            let missing = fileIds.filter { localFiles[$0] == nil }
            if missing.isEmpty {
                return .single(localFiles)
            }
            return _internal_resolveInlineStickers(postbox: account.postbox, network: account.network, fileIds: missing)
            |> map { remoteFiles in
                var merged = localFiles
                for (fileId, file) in remoteFiles where merged[fileId] == nil {
                    merged[fileId] = file
                }
                return merged
            }
        }
    }
    
    public static func resolveMediaFiles(account: Account, emojiStatus: PeerEmojiStatus) -> Signal<[Int64: TelegramMediaFile], NoError> {
        return self.resolveMediaFiles(account: account, fileIds: self.fileIds(for: emojiStatus))
    }
    
    public static func fileIds(for emojiStatus: PeerEmojiStatus) -> [Int64] {
        guard case let .starGift(_, fileId, _, _, patternFileId, _, _, _, _) = emojiStatus.content else {
            return []
        }
        if patternFileId != 0 {
            return [fileId, patternFileId]
        }
        return [fileId]
    }
}

private extension GiftMediaBundle {
    func withResolvedFileIds(modelFileId: Int64, patternFileId: Int64, files: [Int64: TelegramMediaFile]) -> GiftMediaBundle {
        var result = self
        result.files = files
        result.modelFile = files[modelFileId]
        if patternFileId != 0 {
            result.patternFile = files[patternFileId]
            result.patternFileId = patternFileId
        }
        return result
    }
}
