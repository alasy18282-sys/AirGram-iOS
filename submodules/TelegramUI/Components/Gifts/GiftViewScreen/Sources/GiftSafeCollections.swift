import Foundation
import UIKit
import Display
import TelegramCore
import TelegramPresentationData
import TextFormat

enum GiftSafeCollections {
    static func value<T>(_ array: [T], at index: Int) -> T? {
        guard !array.isEmpty else {
            return nil
        }
        let clamped = max(0, min(index, array.count - 1))
        return array[clamped]
    }
}

func isValidAttributedRange(_ range: NSRange, in length: Int) -> Bool {
    return range.location != NSNotFound && range.location >= 0 && NSMaxRange(range) <= length
}

func makeUniqueGiftOriginalInfoString(
    format: PresentationStrings.FormattedString,
    font: UIFont,
    textColor: UIColor,
    linkColor: UIColor,
    attributedText: NSAttributedString?,
    senderPeerId: EnginePeer.Id?,
    recipientPeerId: EnginePeer.Id?,
    includeMentions: Bool
) -> NSAttributedString {
    let string = NSMutableAttributedString(string: format.string, font: font, textColor: textColor)
    
    func applyLink(at index: Int, peerId: EnginePeer.Id?) {
        guard index >= 0, index < format.ranges.count else {
            return
        }
        let range = format.ranges[index].range
        guard isValidAttributedRange(range, in: string.length) else {
            return
        }
        string.addAttribute(.foregroundColor, value: linkColor, range: range)
        if includeMentions, let peerId {
            string.addAttribute(NSAttributedString.Key(rawValue: TelegramTextAttributes.PeerMention), value: TelegramPeerMention(peerId: peerId, mention: ""), range: range)
        }
    }
    
    if senderPeerId != nil {
        applyLink(at: 0, peerId: senderPeerId)
        applyLink(at: 1, peerId: recipientPeerId)
    } else {
        applyLink(at: 0, peerId: recipientPeerId)
    }
    
    if let attributedText {
        if let last = format.ranges.last, isValidAttributedRange(last.range, in: string.length) {
            string.replaceCharacters(in: last.range, with: attributedText)
        } else {
            if string.length > 0 {
                string.append(NSAttributedString(string: " ", font: font, textColor: textColor))
            }
            string.append(attributedText)
        }
    }
    
    return string
}
