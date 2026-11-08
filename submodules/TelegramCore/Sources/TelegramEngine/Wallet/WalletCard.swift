import Foundation
import SwiftSignalKit
import Postbox
import TelegramApi

/// The wallet's customizable card: gift stickers placed over the card background.
public struct WalletCard: Equatable {
    public struct Coordinates: Equatable {
        public let x: CGFloat
        public let y: CGFloat
        public let width: CGFloat
        public let height: CGFloat
        public let rotation: CGFloat
        public let cornerRadius: CGFloat?

        public init(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat, rotation: CGFloat, cornerRadius: CGFloat? = nil) {
            self.x = x
            self.y = y
            self.width = width
            self.height = height
            self.rotation = rotation
            self.cornerRadius = cornerRadius
        }
    }

    public struct InputSticker: Equatable {
        public let coordinates: Coordinates
        public let nftAddress: String

        public init(coordinates: Coordinates, nftAddress: String) {
            self.coordinates = coordinates
            self.nftAddress = nftAddress
        }
    }

    public struct Sticker: Equatable {
        public let coordinates: Coordinates
        public let nft: WalletNftItem

        public init(coordinates: Coordinates, nft: WalletNftItem) {
            self.coordinates = coordinates
            self.nft = nft
        }
    }

    public let address: String
    public let balance: Int64
    public let stickers: [Sticker]
    public let backgroundNft: WalletNftItem?

    public init(address: String, balance: Int64, stickers: [Sticker], backgroundNft: WalletNftItem?) {
        self.address = address
        self.balance = balance
        self.stickers = stickers
        self.backgroundNft = backgroundNft
    }
}

public enum WalletCardError: Error {
    case generic
}

extension TelegramEngine.Wallet {
    /// The server-managed card, if one has been configured. This fork's schema has no
    /// card RPCs, so there is never a server card and the feature stays local-only.
    public func card() -> Signal<WalletCard?, NoError> {
        return .single(nil)
    }

    public func refreshCard() -> Signal<Bool, NoError> {
        return .single(true)
    }

    /// Saving sticker layouts requires a card RPC the fork's schema lacks.
    public func setCardStickers(_ stickers: [WalletCard.InputSticker], backgroundNftAddress: String?) -> Signal<WalletCard, WalletCardError> {
        return .fail(.generic)
    }

    /// Cached address-to-peer resolutions; the caller falls back to its own resolution.
    public func getCachedUserAddressPeers(addresses: [String]) -> Signal<[WalletTransactionPeer], WalletCardError> {
        return .single([])
    }
}
