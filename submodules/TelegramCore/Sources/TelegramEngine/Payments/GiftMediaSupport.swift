import Foundation
import SwiftSignalKit
import Postbox

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
    
    public static func mediaFiles(for emojiStatus: PeerEmojiStatus, gifts: [ProfileGiftsContext.State.StarGift]) -> [Int64: TelegramMediaFile] {
        guard let uniqueGift = self.uniqueGift(in: gifts, matching: emojiStatus) else {
            return [:]
        }
        return self.mediaFiles(from: uniqueGift)
    }
    
    public static func modelFile(for emojiStatus: PeerEmojiStatus, gifts: [ProfileGiftsContext.State.StarGift]) -> TelegramMediaFile? {
        guard let uniqueGift = self.uniqueGift(in: gifts, matching: emojiStatus) else {
            return nil
        }
        for attribute in uniqueGift.attributes {
            if case let .model(_, file, _, _) = attribute {
                return file
            }
        }
        return nil
    }
    
    public static func prefetchFiles(postbox: Postbox, files: [TelegramMediaFile]) -> Disposable {
        let set = DisposableSet()
        for file in files {
            set.add(freeMediaFileResourceInteractiveFetched(
                postbox: postbox,
                userLocation: .other,
                fileReference: .standalone(media: file),
                resource: file.resource
            ).start())
        }
        return set
    }
}
