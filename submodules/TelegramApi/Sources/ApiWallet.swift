// Wallet API namespace grafted from the 229 fork family (schema section identical at 232).

public extension Api.wallet {
    enum EncryptedSecretPhrasePart: TypeConstructorDescription {
        public class Cons_encryptedSecretPhrasePart: TypeConstructorDescription {
            public var data: Buffer
            public init(data: Buffer) {
                self.data = data
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("encryptedSecretPhrasePart", [("data", ConstructorParameterDescription(self.data))])
            }
        }
        case encryptedSecretPhrasePart(Cons_encryptedSecretPhrasePart)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .encryptedSecretPhrasePart(let _data):
                if boxed {
                    buffer.appendInt32(417867063)
                }
                serializeBytes(_data.data, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .encryptedSecretPhrasePart(let _data):
                return ("encryptedSecretPhrasePart", [("data", ConstructorParameterDescription(_data.data))])
            }
        }

        public static func parse_encryptedSecretPhrasePart(_ reader: BufferReader) -> EncryptedSecretPhrasePart? {
            var _1: Buffer?
            _1 = parseBytes(reader)
            let _c1 = _1 != nil
            if _c1 {
                return Api.wallet.EncryptedSecretPhrasePart.encryptedSecretPhrasePart(Cons_encryptedSecretPhrasePart(data: _1!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum ExistingBalance: TypeConstructorDescription {
        public class Cons_existingBalance: TypeConstructorDescription {
            public var flags: Int32
            public var url: String
            public init(flags: Int32, url: String) {
                self.flags = flags
                self.url = url
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("existingBalance", [("flags", ConstructorParameterDescription(self.flags)), ("url", ConstructorParameterDescription(self.url))])
            }
        }
        case existingBalance(Cons_existingBalance)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .existingBalance(let _data):
                if boxed {
                    buffer.appendInt32(-1108800883)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.url, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .existingBalance(let _data):
                return ("existingBalance", [("flags", ConstructorParameterDescription(_data.flags)), ("url", ConstructorParameterDescription(_data.url))])
            }
        }

        public static func parse_existingBalance(_ reader: BufferReader) -> ExistingBalance? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.ExistingBalance.existingBalance(Cons_existingBalance(flags: _1!, url: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum NftAttribute: TypeConstructorDescription {
        public class Cons_nftAttribute: TypeConstructorDescription {
            public var traitType: String
            public var value: String
            public init(traitType: String, value: String) {
                self.traitType = traitType
                self.value = value
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("nftAttribute", [("traitType", ConstructorParameterDescription(self.traitType)), ("value", ConstructorParameterDescription(self.value))])
            }
        }
        case nftAttribute(Cons_nftAttribute)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .nftAttribute(let _data):
                if boxed {
                    buffer.appendInt32(1277096206)
                }
                serializeString(_data.traitType, buffer: buffer, boxed: false)
                serializeString(_data.value, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .nftAttribute(let _data):
                return ("nftAttribute", [("traitType", ConstructorParameterDescription(_data.traitType)), ("value", ConstructorParameterDescription(_data.value))])
            }
        }

        public static func parse_nftAttribute(_ reader: BufferReader) -> NftAttribute? {
            var _1: String?
            _1 = parseString(reader)
            var _2: String?
            _2 = parseString(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.NftAttribute.nftAttribute(Cons_nftAttribute(traitType: _1!, value: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum NftItem: TypeConstructorDescription {
        public class Cons_nftItem: TypeConstructorDescription {
            public var flags: Int32
            public var collectionAddress: String?
            public var address: String
            public var ownerAddress: String
            public var index: String
            public var name: String?
            public var description: String?
            public var image: Api.WebDocument?
            public var imageSmall: Api.WebDocument?
            public var contentUrl: Api.WebDocument?
            public var lottie: Api.WebDocument?
            public var attributes: [Api.wallet.NftAttribute]?
            public var extra: Api.DataJSON?
            public var gift: Api.StarGift?
            public init(flags: Int32, collectionAddress: String?, address: String, ownerAddress: String, index: String, name: String?, description: String?, image: Api.WebDocument?, imageSmall: Api.WebDocument?, contentUrl: Api.WebDocument?, lottie: Api.WebDocument?, attributes: [Api.wallet.NftAttribute]?, extra: Api.DataJSON?, gift: Api.StarGift? = nil) {
                self.flags = flags
                self.collectionAddress = collectionAddress
                self.address = address
                self.ownerAddress = ownerAddress
                self.index = index
                self.name = name
                self.description = description
                self.image = image
                self.imageSmall = imageSmall
                self.contentUrl = contentUrl
                self.lottie = lottie
                self.attributes = attributes
                self.extra = extra
            self.gift = gift
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("nftItem", [("flags", ConstructorParameterDescription(self.flags)), ("collectionAddress", ConstructorParameterDescription(self.collectionAddress)), ("address", ConstructorParameterDescription(self.address)), ("ownerAddress", ConstructorParameterDescription(self.ownerAddress)), ("index", ConstructorParameterDescription(self.index)), ("name", ConstructorParameterDescription(self.name)), ("description", ConstructorParameterDescription(self.description)), ("image", ConstructorParameterDescription(self.image)), ("imageSmall", ConstructorParameterDescription(self.imageSmall)), ("contentUrl", ConstructorParameterDescription(self.contentUrl)), ("lottie", ConstructorParameterDescription(self.lottie)), ("attributes", ConstructorParameterDescription(self.attributes)), ("extra", ConstructorParameterDescription(self.extra))])
            }
        }
        case nftItem(Cons_nftItem)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .nftItem(let _data):
                if boxed {
                    buffer.appendInt32(876739868)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.collectionAddress!, buffer: buffer, boxed: false)
                }
                serializeString(_data.address, buffer: buffer, boxed: false)
                serializeString(_data.ownerAddress, buffer: buffer, boxed: false)
                serializeString(_data.index, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 1) != 0 {
                    serializeString(_data.name!, buffer: buffer, boxed: false)
                }
                if Int(_data.flags) & Int(1 << 2) != 0 {
                    serializeString(_data.description!, buffer: buffer, boxed: false)
                }
                if Int(_data.flags) & Int(1 << 3) != 0 {
                    _data.image!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 4) != 0 {
                    _data.imageSmall!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 5) != 0 {
                    _data.contentUrl!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 6) != 0 {
                    _data.lottie!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 7) != 0 {
                    buffer.appendInt32(481674261)
                    buffer.appendInt32(Int32(_data.attributes!.count))
                    for item in _data.attributes! {
                        item.serialize(buffer, true)
                    }
                }
                if Int(_data.flags) & Int(1 << 8) != 0 {
                    _data.extra!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 9) != 0 {
                    _data.gift!.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .nftItem(let _data):
                return ("nftItem", [("flags", ConstructorParameterDescription(_data.flags)), ("collectionAddress", ConstructorParameterDescription(_data.collectionAddress)), ("address", ConstructorParameterDescription(_data.address)), ("ownerAddress", ConstructorParameterDescription(_data.ownerAddress)), ("index", ConstructorParameterDescription(_data.index)), ("name", ConstructorParameterDescription(_data.name)), ("description", ConstructorParameterDescription(_data.description)), ("image", ConstructorParameterDescription(_data.image)), ("imageSmall", ConstructorParameterDescription(_data.imageSmall)), ("contentUrl", ConstructorParameterDescription(_data.contentUrl)), ("lottie", ConstructorParameterDescription(_data.lottie)), ("attributes", ConstructorParameterDescription(_data.attributes)), ("extra", ConstructorParameterDescription(_data.extra))])
            }
        }

        public static func parse_nftItem(_ reader: BufferReader) -> NftItem? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _2 = parseString(reader)
            }
            var _3: String?
            _3 = parseString(reader)
            var _4: String?
            _4 = parseString(reader)
            var _5: String?
            _5 = parseString(reader)
            var _6: String?
            if Int(_1 ?? 0) & Int(1 << 1) != 0 {
                _6 = parseString(reader)
            }
            var _7: String?
            if Int(_1 ?? 0) & Int(1 << 2) != 0 {
                _7 = parseString(reader)
            }
            var _8: Api.WebDocument?
            if Int(_1 ?? 0) & Int(1 << 3) != 0 {
                if let signature = reader.readInt32() {
                    _8 = Api.parse(reader, signature: signature) as? Api.WebDocument
                }
            }
            var _9: Api.WebDocument?
            if Int(_1 ?? 0) & Int(1 << 4) != 0 {
                if let signature = reader.readInt32() {
                    _9 = Api.parse(reader, signature: signature) as? Api.WebDocument
                }
            }
            var _10: Api.WebDocument?
            if Int(_1 ?? 0) & Int(1 << 5) != 0 {
                if let signature = reader.readInt32() {
                    _10 = Api.parse(reader, signature: signature) as? Api.WebDocument
                }
            }
            var _11: Api.WebDocument?
            if Int(_1 ?? 0) & Int(1 << 6) != 0 {
                if let signature = reader.readInt32() {
                    _11 = Api.parse(reader, signature: signature) as? Api.WebDocument
                }
            }
            var _12: [Api.wallet.NftAttribute]?
            if Int(_1 ?? 0) & Int(1 << 7) != 0 {
                if let _ = reader.readInt32() {
                    _12 = Api.parseVector(reader, elementSignature: 0, elementType: Api.wallet.NftAttribute.self)
                }
            }
            var _13: Api.DataJSON?
            if Int(_1 ?? 0) & Int(1 << 8) != 0 {
                if let signature = reader.readInt32() {
                    _13 = Api.parse(reader, signature: signature) as? Api.DataJSON
                }
            }
            var _14: Api.StarGift?
            if Int(_1 ?? 0) & Int(1 << 9) != 0 {
                if let signature = reader.readInt32() {
                    _14 = Api.parse(reader, signature: signature) as? Api.StarGift
                }
            }
            let _c1 = _1 != nil
            let _c2 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _2 != nil
            let _c3 = _3 != nil
            let _c4 = _4 != nil
            let _c5 = _5 != nil
            let _c6 = (Int(_1 ?? 0) & Int(1 << 1) == 0) || _6 != nil
            let _c7 = (Int(_1 ?? 0) & Int(1 << 2) == 0) || _7 != nil
            let _c8 = (Int(_1 ?? 0) & Int(1 << 3) == 0) || _8 != nil
            let _c9 = (Int(_1 ?? 0) & Int(1 << 4) == 0) || _9 != nil
            let _c10 = (Int(_1 ?? 0) & Int(1 << 5) == 0) || _10 != nil
            let _c11 = (Int(_1 ?? 0) & Int(1 << 6) == 0) || _11 != nil
            let _c12 = (Int(_1 ?? 0) & Int(1 << 7) == 0) || _12 != nil
            let _c13 = (Int(_1 ?? 0) & Int(1 << 8) == 0) || _13 != nil
            let _c14 = (Int(_1 ?? 0) & Int(1 << 9) == 0) || _14 != nil
            if _c1 && _c2 && _c3 && _c4 && _c5 && _c6 && _c7 && _c8 && _c9 && _c10 && _c11 && _c12 && _c13 && _c14 {
                return Api.wallet.NftItem.nftItem(Cons_nftItem(flags: _1!, collectionAddress: _2, address: _3!, ownerAddress: _4!, index: _5!, name: _6, description: _7, image: _8, imageSmall: _9, contentUrl: _10, lottie: _11, attributes: _12, extra: _13, gift: _14))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum NftItems: TypeConstructorDescription {
        public class Cons_nftItems: TypeConstructorDescription {
            public var flags: Int32
            public var items: [Api.wallet.NftItem]
            public var nextOffset: String?
            public init(flags: Int32, items: [Api.wallet.NftItem], nextOffset: String?) {
                self.flags = flags
                self.items = items
                self.nextOffset = nextOffset
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("nftItems", [("flags", ConstructorParameterDescription(self.flags)), ("items", ConstructorParameterDescription(self.items)), ("nextOffset", ConstructorParameterDescription(self.nextOffset))])
            }
        }
        case nftItems(Cons_nftItems)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .nftItems(let _data):
                if boxed {
                    buffer.appendInt32(2035107951)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.items.count))
                for item in _data.items {
                    item.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.nextOffset!, buffer: buffer, boxed: false)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .nftItems(let _data):
                return ("nftItems", [("flags", ConstructorParameterDescription(_data.flags)), ("items", ConstructorParameterDescription(_data.items)), ("nextOffset", ConstructorParameterDescription(_data.nextOffset))])
            }
        }

        public static func parse_nftItems(_ reader: BufferReader) -> NftItems? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: [Api.wallet.NftItem]?
            if let _ = reader.readInt32() {
                _2 = Api.parseVector(reader, elementSignature: 0, elementType: Api.wallet.NftItem.self)
            }
            var _3: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _3 = parseString(reader)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _3 != nil
            if _c1 && _c2 && _c3 {
                return Api.wallet.NftItems.nftItems(Cons_nftItems(flags: _1!, items: _2!, nextOffset: _3))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum SecretPhraseParts: TypeConstructorDescription {
        public class Cons_secretPhraseParts: TypeConstructorDescription {
            public var token: String
            public var dcs: [Int32]
            public init(token: String, dcs: [Int32]) {
                self.token = token
                self.dcs = dcs
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("secretPhraseParts", [("token", ConstructorParameterDescription(self.token)), ("dcs", ConstructorParameterDescription(self.dcs))])
            }
        }
        case secretPhraseParts(Cons_secretPhraseParts)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .secretPhraseParts(let _data):
                if boxed {
                    buffer.appendInt32(-422514943)
                }
                serializeString(_data.token, buffer: buffer, boxed: false)
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.dcs.count))
                for item in _data.dcs {
                    serializeInt32(item, buffer: buffer, boxed: false)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .secretPhraseParts(let _data):
                return ("secretPhraseParts", [("token", ConstructorParameterDescription(_data.token)), ("dcs", ConstructorParameterDescription(_data.dcs))])
            }
        }

        public static func parse_secretPhraseParts(_ reader: BufferReader) -> SecretPhraseParts? {
            var _1: String?
            _1 = parseString(reader)
            var _2: [Int32]?
            if let _ = reader.readInt32() {
                _2 = Api.parseVector(reader, elementSignature: -1471112230, elementType: Int32.self)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.SecretPhraseParts.secretPhraseParts(Cons_secretPhraseParts(token: _1!, dcs: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum ProofChallenge: TypeConstructorDescription {
        public class Cons_proofChallenge: TypeConstructorDescription {
            public var payload: String
            public var expires: Int32
            public var domain: String
            public init(payload: String, expires: Int32, domain: String) {
                self.payload = payload
                self.expires = expires
                self.domain = domain
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("proofChallenge", [("payload", ConstructorParameterDescription(self.payload)), ("expires", ConstructorParameterDescription(self.expires)), ("domain", ConstructorParameterDescription(self.domain))])
            }
        }
        case proofChallenge(Cons_proofChallenge)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .proofChallenge(let _data):
                if boxed {
                    buffer.appendInt32(-1713105145)
                }
                serializeString(_data.payload, buffer: buffer, boxed: false)
                serializeInt32(_data.expires, buffer: buffer, boxed: false)
                serializeString(_data.domain, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .proofChallenge(let _data):
                return ("proofChallenge", [("payload", ConstructorParameterDescription(_data.payload)), ("expires", ConstructorParameterDescription(_data.expires)), ("domain", ConstructorParameterDescription(_data.domain))])
            }
        }

        public static func parse_proofChallenge(_ reader: BufferReader) -> ProofChallenge? {
            var _1: String?
            _1 = parseString(reader)
            var _2: Int32?
            _2 = reader.readInt32()
            var _3: String?
            _3 = parseString(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            if _c1 && _c2 && _c3 {
                return Api.wallet.ProofChallenge.proofChallenge(Cons_proofChallenge(payload: _1!, expires: _2!, domain: _3!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum TonConnectChallenge: TypeConstructorDescription {
        public class Cons_tonConnectChallenge: TypeConstructorDescription {
            public var challenge: Buffer
            public var eventId: Int64
            public init(challenge: Buffer, eventId: Int64) {
                self.challenge = challenge
                self.eventId = eventId
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectChallenge", [("challenge", ConstructorParameterDescription(self.challenge)), ("eventId", ConstructorParameterDescription(self.eventId))])
            }
        }
        case tonConnectChallenge(Cons_tonConnectChallenge)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectChallenge(let _data):
                if boxed {
                    buffer.appendInt32(1271436947)
                }
                serializeBytes(_data.challenge, buffer: buffer, boxed: false)
                serializeInt64(_data.eventId, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectChallenge(let _data):
                return ("tonConnectChallenge", [("challenge", ConstructorParameterDescription(_data.challenge)), ("eventId", ConstructorParameterDescription(_data.eventId))])
            }
        }

        public static func parse_tonConnectChallenge(_ reader: BufferReader) -> TonConnectChallenge? {
            var _1: Buffer?
            _1 = parseBytes(reader)
            var _2: Int64?
            _2 = reader.readInt64()
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.TonConnectChallenge.tonConnectChallenge(Cons_tonConnectChallenge(challenge: _1!, eventId: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum TonConnectPending: TypeConstructorDescription {
        public class Cons_tonConnectPending: TypeConstructorDescription {
            public var session: Api.TonConnectSession
            public var requests: [Api.TonConnectRequest]
            public init(session: Api.TonConnectSession, requests: [Api.TonConnectRequest]) {
                self.session = session
                self.requests = requests
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectPending", [("session", ConstructorParameterDescription(self.session)), ("requests", ConstructorParameterDescription(self.requests))])
            }
        }
        case tonConnectPending(Cons_tonConnectPending)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectPending(let _data):
                if boxed {
                    buffer.appendInt32(-2050952924)
                }
                _data.session.serialize(buffer, true)
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.requests.count))
                for item in _data.requests {
                    item.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectPending(let _data):
                return ("tonConnectPending", [("session", ConstructorParameterDescription(_data.session)), ("requests", ConstructorParameterDescription(_data.requests))])
            }
        }

        public static func parse_tonConnectPending(_ reader: BufferReader) -> TonConnectPending? {
            var _1: Api.TonConnectSession?
            if let signature = reader.readInt32() {
                _1 = Api.parse(reader, signature: signature) as? Api.TonConnectSession
            }
            var _2: [Api.TonConnectRequest]?
            if let _ = reader.readInt32() {
                _2 = Api.parseVector(reader, elementSignature: 0, elementType: Api.TonConnectRequest.self)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.TonConnectPending.tonConnectPending(Cons_tonConnectPending(session: _1!, requests: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum TonConnectSessions: TypeConstructorDescription {
        public class Cons_tonConnectSessions: TypeConstructorDescription {
            public var sessions: [Api.TonConnectSession]
            public init(sessions: [Api.TonConnectSession]) {
                self.sessions = sessions
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectSessions", [("sessions", ConstructorParameterDescription(self.sessions))])
            }
        }
        case tonConnectSessions(Cons_tonConnectSessions)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectSessions(let _data):
                if boxed {
                    buffer.appendInt32(236939414)
                }
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.sessions.count))
                for item in _data.sessions {
                    item.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectSessions(let _data):
                return ("tonConnectSessions", [("sessions", ConstructorParameterDescription(_data.sessions))])
            }
        }

        public static func parse_tonConnectSessions(_ reader: BufferReader) -> TonConnectSessions? {
            var _1: [Api.TonConnectSession]?
            if let _ = reader.readInt32() {
                _1 = Api.parseVector(reader, elementSignature: 0, elementType: Api.TonConnectSession.self)
            }
            let _c1 = _1 != nil
            if _c1 {
                return Api.wallet.TonConnectSessions.tonConnectSessions(Cons_tonConnectSessions(sessions: _1!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum Transactions: TypeConstructorDescription {
        public class Cons_transactions: TypeConstructorDescription {
            public var flags: Int32
            public var balance: Int64
            public var transactions: [Api.WalletTransaction]
            public var nextOffset: String?
            public var chats: [Api.Chat]
            public var users: [Api.User]
            public init(flags: Int32, balance: Int64, transactions: [Api.WalletTransaction], nextOffset: String?, chats: [Api.Chat], users: [Api.User]) {
                self.flags = flags
                self.balance = balance
                self.transactions = transactions
                self.nextOffset = nextOffset
                self.chats = chats
                self.users = users
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("transactions", [("flags", ConstructorParameterDescription(self.flags)), ("balance", ConstructorParameterDescription(self.balance)), ("transactions", ConstructorParameterDescription(self.transactions)), ("nextOffset", ConstructorParameterDescription(self.nextOffset)), ("chats", ConstructorParameterDescription(self.chats)), ("users", ConstructorParameterDescription(self.users))])
            }
        }
        case transactions(Cons_transactions)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .transactions(let _data):
                if boxed {
                    buffer.appendInt32(1126356389)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeInt64(_data.balance, buffer: buffer, boxed: false)
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.transactions.count))
                for item in _data.transactions {
                    item.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.nextOffset!, buffer: buffer, boxed: false)
                }
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.chats.count))
                for item in _data.chats {
                    item.serialize(buffer, true)
                }
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.users.count))
                for item in _data.users {
                    item.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .transactions(let _data):
                return ("transactions", [("flags", ConstructorParameterDescription(_data.flags)), ("balance", ConstructorParameterDescription(_data.balance)), ("transactions", ConstructorParameterDescription(_data.transactions)), ("nextOffset", ConstructorParameterDescription(_data.nextOffset)), ("chats", ConstructorParameterDescription(_data.chats)), ("users", ConstructorParameterDescription(_data.users))])
            }
        }

        public static func parse_transactions(_ reader: BufferReader) -> Transactions? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            _2 = reader.readInt64()
            var _3: [Api.WalletTransaction]?
            if let _ = reader.readInt32() {
                _3 = Api.parseVector(reader, elementSignature: 0, elementType: Api.WalletTransaction.self)
            }
            var _4: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _4 = parseString(reader)
            }
            var _5: [Api.Chat]?
            if let _ = reader.readInt32() {
                _5 = Api.parseVector(reader, elementSignature: 0, elementType: Api.Chat.self)
            }
            var _6: [Api.User]?
            if let _ = reader.readInt32() {
                _6 = Api.parseVector(reader, elementSignature: 0, elementType: Api.User.self)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _4 != nil
            let _c5 = _5 != nil
            let _c6 = _6 != nil
            if _c1 && _c2 && _c3 && _c4 && _c5 && _c6 {
                return Api.wallet.Transactions.transactions(Cons_transactions(flags: _1!, balance: _2!, transactions: _3!, nextOffset: _4, chats: _5!, users: _6!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum UserAddresses: TypeConstructorDescription {
        public class Cons_userAddresses: TypeConstructorDescription {
            public var addresses: [Api.WalletUserAddress]
            public var users: [Api.User]
            public init(addresses: [Api.WalletUserAddress], users: [Api.User]) {
                self.addresses = addresses
                self.users = users
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("userAddresses", [("addresses", ConstructorParameterDescription(self.addresses)), ("users", ConstructorParameterDescription(self.users))])
            }
        }
        case userAddresses(Cons_userAddresses)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .userAddresses(let _data):
                if boxed {
                    buffer.appendInt32(-1836156075)
                }
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.addresses.count))
                for item in _data.addresses {
                    item.serialize(buffer, true)
                }
                buffer.appendInt32(481674261)
                buffer.appendInt32(Int32(_data.users.count))
                for item in _data.users {
                    item.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .userAddresses(let _data):
                return ("userAddresses", [("addresses", ConstructorParameterDescription(_data.addresses)), ("users", ConstructorParameterDescription(_data.users))])
            }
        }

        public static func parse_userAddresses(_ reader: BufferReader) -> UserAddresses? {
            var _1: [Api.WalletUserAddress]?
            if let _ = reader.readInt32() {
                _1 = Api.parseVector(reader, elementSignature: 0, elementType: Api.WalletUserAddress.self)
            }
            var _2: [Api.User]?
            if let _ = reader.readInt32() {
                _2 = Api.parseVector(reader, elementSignature: 0, elementType: Api.User.self)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.UserAddresses.userAddresses(Cons_userAddresses(addresses: _1!, users: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.wallet {
    enum HolderDc: TypeConstructorDescription {
        public class Cons_holderDc: TypeConstructorDescription {
            public var dc: Int32
            public var publicKey: Buffer
            public init(dc: Int32, publicKey: Buffer) {
                self.dc = dc
                self.publicKey = publicKey
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("holderDc", [("dc", ConstructorParameterDescription(self.dc)), ("publicKey", ConstructorParameterDescription(self.publicKey))])
            }
        }
        case holderDc(Cons_holderDc)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .holderDc(let _data):
                if boxed {
                    buffer.appendInt32(-103410961)
                }
                serializeInt32(_data.dc, buffer: buffer, boxed: false)
                serializeBytes(_data.publicKey, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .holderDc(let _data):
                return ("holderDc", [("dc", ConstructorParameterDescription(_data.dc)), ("publicKey", ConstructorParameterDescription(_data.publicKey))])
            }
        }

        public static func parse_holderDc(_ reader: BufferReader) -> HolderDc? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Buffer?
            _2 = parseBytes(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.wallet.HolderDc.holderDc(Cons_holderDc(dc: _1!, publicKey: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum TonConnectSession: TypeConstructorDescription {
        public class Cons_tonConnectSession: TypeConstructorDescription {
            public var flags: Int32
            public var id: Int64
            public var dappClientId: String
            public var clientId: String?
            public var nonce: Buffer
            public var manifest: Api.TonConnectManifest?
            public var manifestError: Int32?
            public var date: Int32
            public init(flags: Int32, id: Int64, dappClientId: String, clientId: String?, nonce: Buffer, manifest: Api.TonConnectManifest?, manifestError: Int32?, date: Int32) {
                self.flags = flags
                self.id = id
                self.dappClientId = dappClientId
                self.clientId = clientId
                self.nonce = nonce
                self.manifest = manifest
                self.manifestError = manifestError
                self.date = date
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectSession", [("flags", ConstructorParameterDescription(self.flags)), ("id", ConstructorParameterDescription(self.id)), ("dappClientId", ConstructorParameterDescription(self.dappClientId)), ("clientId", ConstructorParameterDescription(self.clientId)), ("nonce", ConstructorParameterDescription(self.nonce)), ("manifest", ConstructorParameterDescription(self.manifest)), ("manifestError", ConstructorParameterDescription(self.manifestError)), ("date", ConstructorParameterDescription(self.date))])
            }
        }
        case tonConnectSession(Cons_tonConnectSession)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectSession(let _data):
                if boxed {
                    buffer.appendInt32(308631238)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeInt64(_data.id, buffer: buffer, boxed: false)
                serializeString(_data.dappClientId, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 3) != 0 {
                    serializeString(_data.clientId!, buffer: buffer, boxed: false)
                }
                serializeBytes(_data.nonce, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 4) != 0 {
                    _data.manifest!.serialize(buffer, true)
                }
                if Int(_data.flags) & Int(1 << 5) != 0 {
                    serializeInt32(_data.manifestError!, buffer: buffer, boxed: false)
                }
                serializeInt32(_data.date, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectSession(let _data):
                return ("tonConnectSession", [("flags", ConstructorParameterDescription(_data.flags)), ("id", ConstructorParameterDescription(_data.id)), ("dappClientId", ConstructorParameterDescription(_data.dappClientId)), ("clientId", ConstructorParameterDescription(_data.clientId)), ("nonce", ConstructorParameterDescription(_data.nonce)), ("manifest", ConstructorParameterDescription(_data.manifest)), ("manifestError", ConstructorParameterDescription(_data.manifestError)), ("date", ConstructorParameterDescription(_data.date))])
            }
        }

        public static func parse_tonConnectSession(_ reader: BufferReader) -> TonConnectSession? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            _2 = reader.readInt64()
            var _3: String?
            _3 = parseString(reader)
            var _4: String?
            if Int(_1 ?? 0) & Int(1 << 3) != 0 {
                _4 = parseString(reader)
            }
            var _5: Buffer?
            _5 = parseBytes(reader)
            var _6: Api.TonConnectManifest?
            if Int(_1 ?? 0) & Int(1 << 4) != 0 {
                if let signature = reader.readInt32() {
                    _6 = Api.parse(reader, signature: signature) as? Api.TonConnectManifest
                }
            }
            var _7: Int32?
            if Int(_1 ?? 0) & Int(1 << 5) != 0 {
                _7 = reader.readInt32()
            }
            var _8: Int32?
            _8 = reader.readInt32()
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = (Int(_1 ?? 0) & Int(1 << 3) == 0) || _4 != nil
            let _c5 = _5 != nil
            let _c6 = (Int(_1 ?? 0) & Int(1 << 4) == 0) || _6 != nil
            let _c7 = (Int(_1 ?? 0) & Int(1 << 5) == 0) || _7 != nil
            let _c8 = _8 != nil
            if _c1 && _c2 && _c3 && _c4 && _c5 && _c6 && _c7 && _c8 {
                return Api.TonConnectSession.tonConnectSession(Cons_tonConnectSession(flags: _1!, id: _2!, dappClientId: _3!, clientId: _4, nonce: _5!, manifest: _6, manifestError: _7, date: _8!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum TonConnectRequest: TypeConstructorDescription {
        public class Cons_tonConnectRequest: TypeConstructorDescription {
            public var flags: Int32
            public var sessionId: Int64
            public var msgId: Int32
            public var body: Buffer
            public var expires: Int32
            public var topic: String?
            public var traceId: String?
            public init(flags: Int32, sessionId: Int64, msgId: Int32, body: Buffer, expires: Int32, topic: String?, traceId: String?) {
                self.flags = flags
                self.sessionId = sessionId
                self.msgId = msgId
                self.body = body
                self.expires = expires
                self.topic = topic
                self.traceId = traceId
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectRequest", [("flags", ConstructorParameterDescription(self.flags)), ("sessionId", ConstructorParameterDescription(self.sessionId)), ("msgId", ConstructorParameterDescription(self.msgId)), ("body", ConstructorParameterDescription(self.body)), ("expires", ConstructorParameterDescription(self.expires)), ("topic", ConstructorParameterDescription(self.topic)), ("traceId", ConstructorParameterDescription(self.traceId))])
            }
        }
        case tonConnectRequest(Cons_tonConnectRequest)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectRequest(let _data):
                if boxed {
                    buffer.appendInt32(-1587575533)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeInt64(_data.sessionId, buffer: buffer, boxed: false)
                serializeInt32(_data.msgId, buffer: buffer, boxed: false)
                serializeBytes(_data.body, buffer: buffer, boxed: false)
                serializeInt32(_data.expires, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.topic!, buffer: buffer, boxed: false)
                }
                if Int(_data.flags) & Int(1 << 1) != 0 {
                    serializeString(_data.traceId!, buffer: buffer, boxed: false)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectRequest(let _data):
                return ("tonConnectRequest", [("flags", ConstructorParameterDescription(_data.flags)), ("sessionId", ConstructorParameterDescription(_data.sessionId)), ("msgId", ConstructorParameterDescription(_data.msgId)), ("body", ConstructorParameterDescription(_data.body)), ("expires", ConstructorParameterDescription(_data.expires)), ("topic", ConstructorParameterDescription(_data.topic)), ("traceId", ConstructorParameterDescription(_data.traceId))])
            }
        }

        public static func parse_tonConnectRequest(_ reader: BufferReader) -> TonConnectRequest? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            _2 = reader.readInt64()
            var _3: Int32?
            _3 = reader.readInt32()
            var _4: Buffer?
            _4 = parseBytes(reader)
            var _5: Int32?
            _5 = reader.readInt32()
            var _6: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _6 = parseString(reader)
            }
            var _7: String?
            if Int(_1 ?? 0) & Int(1 << 1) != 0 {
                _7 = parseString(reader)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = _4 != nil
            let _c5 = _5 != nil
            let _c6 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _6 != nil
            let _c7 = (Int(_1 ?? 0) & Int(1 << 1) == 0) || _7 != nil
            if _c1 && _c2 && _c3 && _c4 && _c5 && _c6 && _c7 {
                return Api.TonConnectRequest.tonConnectRequest(Cons_tonConnectRequest(flags: _1!, sessionId: _2!, msgId: _3!, body: _4!, expires: _5!, topic: _6, traceId: _7))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum TonConnectNextEventId: TypeConstructorDescription {
        public class Cons_tonConnectNextEventId: TypeConstructorDescription {
            public var eventId: Int64
            public init(eventId: Int64) {
                self.eventId = eventId
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectNextEventId", [("eventId", ConstructorParameterDescription(self.eventId))])
            }
        }
        case tonConnectNextEventId(Cons_tonConnectNextEventId)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectNextEventId(let _data):
                if boxed {
                    buffer.appendInt32(1478780131)
                }
                serializeInt64(_data.eventId, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectNextEventId(let _data):
                return ("tonConnectNextEventId", [("eventId", ConstructorParameterDescription(_data.eventId))])
            }
        }

        public static func parse_tonConnectNextEventId(_ reader: BufferReader) -> TonConnectNextEventId? {
            var _1: Int64?
            _1 = reader.readInt64()
            let _c1 = _1 != nil
            if _c1 {
                return Api.TonConnectNextEventId.tonConnectNextEventId(Cons_tonConnectNextEventId(eventId: _1!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum WalletState: TypeConstructorDescription {
        public class Cons_walletState: TypeConstructorDescription {
            public var flags: Int32
            public var address: String
            public var publicKey: Buffer
            public var balance: Int64
            public init(flags: Int32, address: String, publicKey: Buffer, balance: Int64) {
                self.flags = flags
                self.address = address
                self.publicKey = publicKey
                self.balance = balance
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletState", [("flags", ConstructorParameterDescription(self.flags)), ("address", ConstructorParameterDescription(self.address)), ("publicKey", ConstructorParameterDescription(self.publicKey)), ("balance", ConstructorParameterDescription(self.balance))])
            }
        }
        public class Cons_walletStateEmpty: TypeConstructorDescription {
            public var flags: Int32
            public init(flags: Int32) {
                self.flags = flags
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletStateEmpty", [("flags", ConstructorParameterDescription(self.flags))])
            }
        }
        case walletState(Cons_walletState)
        case walletStateEmpty(Cons_walletStateEmpty)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .walletState(let _data):
                if boxed {
                    buffer.appendInt32(-1782238101)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.address, buffer: buffer, boxed: false)
                serializeBytes(_data.publicKey, buffer: buffer, boxed: false)
                serializeInt64(_data.balance, buffer: buffer, boxed: false)
                break
            case .walletStateEmpty(let _data):
                if boxed {
                    buffer.appendInt32(-1665551636)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .walletState(let _data):
                return ("walletState", [("flags", ConstructorParameterDescription(_data.flags)), ("address", ConstructorParameterDescription(_data.address)), ("publicKey", ConstructorParameterDescription(_data.publicKey)), ("balance", ConstructorParameterDescription(_data.balance))])
            case .walletStateEmpty(let _data):
                return ("walletStateEmpty", [("flags", ConstructorParameterDescription(_data.flags))])
            }
        }

        public static func parse_walletState(_ reader: BufferReader) -> WalletState? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            var _3: Buffer?
            _3 = parseBytes(reader)
            var _4: Int64?
            _4 = reader.readInt64()
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.WalletState.walletState(Cons_walletState(flags: _1!, address: _2!, publicKey: _3!, balance: _4!))
            }
            else {
                return nil
            }
        }
        public static func parse_walletStateEmpty(_ reader: BufferReader) -> WalletState? {
            var _1: Int32?
            _1 = reader.readInt32()
            let _c1 = _1 != nil
            if _c1 {
                return Api.WalletState.walletStateEmpty(Cons_walletStateEmpty(flags: _1!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum WalletTransaction: TypeConstructorDescription {
        public class Cons_walletTransaction: TypeConstructorDescription {
            public var flags: Int32
            public var id: String
            public var amount: Int64
            public var fee: Int64
            public var date: Int32
            public var peer: Api.WalletTransactionPeer
            public var comment: String?
            public var txHash: String?
            public var nft: Api.wallet.NftItem?
            public init(flags: Int32, id: String, amount: Int64, fee: Int64, date: Int32, peer: Api.WalletTransactionPeer, comment: String?, txHash: String?, nft: Api.wallet.NftItem?) {
                self.flags = flags
                self.id = id
                self.amount = amount
                self.fee = fee
                self.date = date
                self.peer = peer
                self.comment = comment
                self.txHash = txHash
                self.nft = nft
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransaction", [("flags", ConstructorParameterDescription(self.flags)), ("id", ConstructorParameterDescription(self.id)), ("amount", ConstructorParameterDescription(self.amount)), ("fee", ConstructorParameterDescription(self.fee)), ("date", ConstructorParameterDescription(self.date)), ("peer", ConstructorParameterDescription(self.peer)), ("comment", ConstructorParameterDescription(self.comment)), ("txHash", ConstructorParameterDescription(self.txHash)), ("nft", ConstructorParameterDescription(self.nft))])
            }
        }
        case walletTransaction(Cons_walletTransaction)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .walletTransaction(let _data):
                if boxed {
                    buffer.appendInt32(-1792163517)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.id, buffer: buffer, boxed: false)
                serializeInt64(_data.amount, buffer: buffer, boxed: false)
                serializeInt64(_data.fee, buffer: buffer, boxed: false)
                serializeInt32(_data.date, buffer: buffer, boxed: false)
                _data.peer.serialize(buffer, true)
                if Int(_data.flags) & Int(1 << 3) != 0 {
                    serializeString(_data.comment!, buffer: buffer, boxed: false)
                }
                if Int(_data.flags) & Int(1 << 4) != 0 {
                    serializeString(_data.txHash!, buffer: buffer, boxed: false)
                }
                if Int(_data.flags) & Int(1 << 7) != 0 {
                    _data.nft!.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .walletTransaction(let _data):
                return ("walletTransaction", [("flags", ConstructorParameterDescription(_data.flags)), ("id", ConstructorParameterDescription(_data.id)), ("amount", ConstructorParameterDescription(_data.amount)), ("fee", ConstructorParameterDescription(_data.fee)), ("date", ConstructorParameterDescription(_data.date)), ("peer", ConstructorParameterDescription(_data.peer)), ("comment", ConstructorParameterDescription(_data.comment)), ("txHash", ConstructorParameterDescription(_data.txHash)), ("nft", ConstructorParameterDescription(_data.nft))])
            }
        }

        public static func parse_walletTransaction(_ reader: BufferReader) -> WalletTransaction? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            var _3: Int64?
            _3 = reader.readInt64()
            var _4: Int64?
            _4 = reader.readInt64()
            var _5: Int32?
            _5 = reader.readInt32()
            var _6: Api.WalletTransactionPeer?
            if let signature = reader.readInt32() {
                _6 = Api.parse(reader, signature: signature) as? Api.WalletTransactionPeer
            }
            var _7: String?
            if Int(_1 ?? 0) & Int(1 << 3) != 0 {
                _7 = parseString(reader)
            }
            var _8: String?
            if Int(_1 ?? 0) & Int(1 << 4) != 0 {
                _8 = parseString(reader)
            }
            var _9: Api.wallet.NftItem?
            if Int(_1 ?? 0) & Int(1 << 7) != 0 {
                if let signature = reader.readInt32() {
                    _9 = Api.parse(reader, signature: signature) as? Api.wallet.NftItem
                }
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = _4 != nil
            let _c5 = _5 != nil
            let _c6 = _6 != nil
            let _c7 = (Int(_1 ?? 0) & Int(1 << 3) == 0) || _7 != nil
            let _c8 = (Int(_1 ?? 0) & Int(1 << 4) == 0) || _8 != nil
            let _c9 = (Int(_1 ?? 0) & Int(1 << 7) == 0) || _9 != nil
            if _c1 && _c2 && _c3 && _c4 && _c5 && _c6 && _c7 && _c8 && _c9 {
                return Api.WalletTransaction.walletTransaction(Cons_walletTransaction(flags: _1!, id: _2!, amount: _3!, fee: _4!, date: _5!, peer: _6!, comment: _7, txHash: _8, nft: _9))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum WalletUserAddress: TypeConstructorDescription {
        public class Cons_walletUserAddress: TypeConstructorDescription {
            public var flags: Int32
            public var userId: Int64?
            public var address: String
            public var publicKey: Buffer
            public init(flags: Int32, userId: Int64?, address: String, publicKey: Buffer) {
                self.flags = flags
                self.userId = userId
                self.address = address
                self.publicKey = publicKey
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletUserAddress", [("flags", ConstructorParameterDescription(self.flags)), ("userId", ConstructorParameterDescription(self.userId)), ("address", ConstructorParameterDescription(self.address)), ("publicKey", ConstructorParameterDescription(self.publicKey))])
            }
        }
        case walletUserAddress(Cons_walletUserAddress)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .walletUserAddress(let _data):
                if boxed {
                    buffer.appendInt32(-25628980)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeInt64(_data.userId!, buffer: buffer, boxed: false)
                }
                serializeString(_data.address, buffer: buffer, boxed: false)
                serializeBytes(_data.publicKey, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .walletUserAddress(let _data):
                return ("walletUserAddress", [("flags", ConstructorParameterDescription(_data.flags)), ("userId", ConstructorParameterDescription(_data.userId)), ("address", ConstructorParameterDescription(_data.address)), ("publicKey", ConstructorParameterDescription(_data.publicKey))])
            }
        }

        public static func parse_walletUserAddress(_ reader: BufferReader) -> WalletUserAddress? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _2 = reader.readInt64()
            }
            var _3: String?
            _3 = parseString(reader)
            var _4: Buffer?
            _4 = parseBytes(reader)
            let _c1 = _1 != nil
            let _c2 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _2 != nil
            let _c3 = _3 != nil
            let _c4 = _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.WalletUserAddress.walletUserAddress(Cons_walletUserAddress(flags: _1!, userId: _2, address: _3!, publicKey: _4!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum WalletTransactionPeer: TypeConstructorDescription {
        public class Cons_walletTransactionPeerChannel: TypeConstructorDescription {
            public var flags: Int32
            public var channelId: Int64
            public var address: String
            public var domain: String?

            public init(flags: Int32, channelId: Int64, address: String, domain: String?) {
                self.flags = flags
                self.channelId = channelId
                self.address = address
                self.domain = domain
            }

            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransactionPeerChannel", [("flags", ConstructorParameterDescription(self.flags)), ("channelId", ConstructorParameterDescription(self.channelId)), ("address", ConstructorParameterDescription(self.address)), ("domain", ConstructorParameterDescription(self.domain))])
            }
        }
        public class Cons_walletTransactionPeerTelegramGift: TypeConstructorDescription {
            public var flags: Int32

            public init(flags: Int32) {
                self.flags = flags
            }

            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransactionPeerTelegramGift", [("flags", ConstructorParameterDescription(self.flags))])
            }
        }
        public class Cons_walletTransactionPeerAddress: TypeConstructorDescription {
            public var flags: Int32
            public var address: String
            public var domain: String?
            public init(flags: Int32, address: String, domain: String?) {
                self.flags = flags
                self.address = address
                self.domain = domain
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransactionPeerAddress", [("flags", ConstructorParameterDescription(self.flags)), ("address", ConstructorParameterDescription(self.address)), ("domain", ConstructorParameterDescription(self.domain))])
            }
        }
        public class Cons_walletTransactionPeerOnramp: TypeConstructorDescription {
            public var flags: Int32
            public var address: String
            public var domain: String?
            public var providerName: String
            public init(flags: Int32, address: String, domain: String?, providerName: String) {
                self.flags = flags
                self.address = address
                self.domain = domain
                self.providerName = providerName
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransactionPeerOnramp", [("flags", ConstructorParameterDescription(self.flags)), ("address", ConstructorParameterDescription(self.address)), ("domain", ConstructorParameterDescription(self.domain)), ("providerName", ConstructorParameterDescription(self.providerName))])
            }
        }
        public class Cons_walletTransactionPeerUser: TypeConstructorDescription {
            public var flags: Int32
            public var userId: Int64
            public var address: String
            public var domain: String?
            public init(flags: Int32, userId: Int64, address: String, domain: String?) {
                self.flags = flags
                self.userId = userId
                self.address = address
                self.domain = domain
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletTransactionPeerUser", [("flags", ConstructorParameterDescription(self.flags)), ("userId", ConstructorParameterDescription(self.userId)), ("address", ConstructorParameterDescription(self.address)), ("domain", ConstructorParameterDescription(self.domain))])
            }
        }
        case walletTransactionPeerAddress(Cons_walletTransactionPeerAddress)
        case walletTransactionPeerChannel(Cons_walletTransactionPeerChannel)
        case walletTransactionPeerOnramp(Cons_walletTransactionPeerOnramp)
        case walletTransactionPeerTelegramGift(Cons_walletTransactionPeerTelegramGift)
        case walletTransactionPeerUnsupported
        case walletTransactionPeerUser(Cons_walletTransactionPeerUser)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .walletTransactionPeerAddress(let _data):
                if boxed {
                    buffer.appendInt32(103596476)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.address, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.domain!, buffer: buffer, boxed: false)
                }
                break
            case .walletTransactionPeerChannel(let _data):
                if boxed {
                    buffer.appendInt32(1416649777)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeInt64(_data.channelId, buffer: buffer, boxed: false)
                serializeString(_data.address, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.domain!, buffer: buffer, boxed: false)
                }
                break
            case .walletTransactionPeerTelegramGift(let _data):
                if boxed {
                    buffer.appendInt32(-193817447)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                break
            case .walletTransactionPeerOnramp(let _data):
                if boxed {
                    buffer.appendInt32(-443213714)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.address, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.domain!, buffer: buffer, boxed: false)
                }
                serializeString(_data.providerName, buffer: buffer, boxed: false)
                break
            case .walletTransactionPeerUnsupported:
                if boxed {
                    buffer.appendInt32(1921772890)
                }
                break
            case .walletTransactionPeerUser(let _data):
                if boxed {
                    buffer.appendInt32(-722833299)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeInt64(_data.userId, buffer: buffer, boxed: false)
                serializeString(_data.address, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeString(_data.domain!, buffer: buffer, boxed: false)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .walletTransactionPeerAddress(let _data):
                return ("walletTransactionPeerAddress", [("flags", ConstructorParameterDescription(_data.flags)), ("address", ConstructorParameterDescription(_data.address)), ("domain", ConstructorParameterDescription(_data.domain))])
            case .walletTransactionPeerChannel(let _data):
                return ("walletTransactionPeerChannel", [("flags", ConstructorParameterDescription(_data.flags)), ("channelId", ConstructorParameterDescription(_data.channelId)), ("address", ConstructorParameterDescription(_data.address)), ("domain", ConstructorParameterDescription(_data.domain))])
            case .walletTransactionPeerTelegramGift(let _data):
                return ("walletTransactionPeerTelegramGift", [("flags", ConstructorParameterDescription(_data.flags))])
            case .walletTransactionPeerOnramp(let _data):
                return ("walletTransactionPeerOnramp", [("flags", ConstructorParameterDescription(_data.flags)), ("address", ConstructorParameterDescription(_data.address)), ("domain", ConstructorParameterDescription(_data.domain)), ("providerName", ConstructorParameterDescription(_data.providerName))])
            case .walletTransactionPeerUnsupported:
                return ("walletTransactionPeerUnsupported", [])
            case .walletTransactionPeerUser(let _data):
                return ("walletTransactionPeerUser", [("flags", ConstructorParameterDescription(_data.flags)), ("userId", ConstructorParameterDescription(_data.userId)), ("address", ConstructorParameterDescription(_data.address)), ("domain", ConstructorParameterDescription(_data.domain))])
            }
        }

        public static func parse_walletTransactionPeerAddress(_ reader: BufferReader) -> WalletTransactionPeer? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            var _3: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _3 = parseString(reader)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _3 != nil
            if _c1 && _c2 && _c3 {
                return Api.WalletTransactionPeer.walletTransactionPeerAddress(Cons_walletTransactionPeerAddress(flags: _1!, address: _2!, domain: _3))
            }
            else {
                return nil
            }
        }
        public static func parse_walletTransactionPeerOnramp(_ reader: BufferReader) -> WalletTransactionPeer? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            var _3: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _3 = parseString(reader)
            }
            var _4: String?
            _4 = parseString(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _3 != nil
            let _c4 = _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.WalletTransactionPeer.walletTransactionPeerOnramp(Cons_walletTransactionPeerOnramp(flags: _1!, address: _2!, domain: _3, providerName: _4!))
            }
            else {
                return nil
            }
        }
        public static func parse_walletTransactionPeerChannel(_ reader: BufferReader) -> WalletTransactionPeer? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            _2 = reader.readInt64()
            var _3: String?
            _3 = parseString(reader)
            var _4: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _4 = parseString(reader)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.WalletTransactionPeer.walletTransactionPeerChannel(Cons_walletTransactionPeerChannel(flags: _1!, channelId: _2!, address: _3!, domain: _4))
            }
            else {
                return nil
            }
        }
        public static func parse_walletTransactionPeerTelegramGift(_ reader: BufferReader) -> WalletTransactionPeer? {
            var _1: Int32?
            _1 = reader.readInt32()
            let _c1 = _1 != nil
            if _c1 {
                return Api.WalletTransactionPeer.walletTransactionPeerTelegramGift(Cons_walletTransactionPeerTelegramGift(flags: _1!))
            }
            else {
                return nil
            }
        }
        public static func parse_walletTransactionPeerUnsupported(_ reader: BufferReader) -> WalletTransactionPeer? {
            return Api.WalletTransactionPeer.walletTransactionPeerUnsupported
        }
        public static func parse_walletTransactionPeerUser(_ reader: BufferReader) -> WalletTransactionPeer? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Int64?
            _2 = reader.readInt64()
            var _3: String?
            _3 = parseString(reader)
            var _4: String?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _4 = parseString(reader)
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.WalletTransactionPeer.walletTransactionPeerUser(Cons_walletTransactionPeerUser(flags: _1!, userId: _2!, address: _3!, domain: _4))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum InputWalletReplacement: TypeConstructorDescription {
        public class Cons_inputWalletImported: TypeConstructorDescription {
            public var flags: Int32
            public var publicKey: Buffer
            public var anchorPublicKey: Buffer?
            public var proof: Api.WalletOwnershipProof
            public init(flags: Int32, publicKey: Buffer, anchorPublicKey: Buffer?, proof: Api.WalletOwnershipProof) {
                self.flags = flags
                self.publicKey = publicKey
                self.anchorPublicKey = anchorPublicKey
                self.proof = proof
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("inputWalletImported", [("flags", ConstructorParameterDescription(self.flags)), ("publicKey", ConstructorParameterDescription(self.publicKey)), ("anchorPublicKey", ConstructorParameterDescription(self.anchorPublicKey)), ("proof", ConstructorParameterDescription(self.proof))])
            }
        }
        case inputWalletImported(Cons_inputWalletImported)
        case inputWalletNew

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .inputWalletImported(let _data):
                if boxed {
                    buffer.appendInt32(1722182203)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeBytes(_data.publicKey, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    serializeBytes(_data.anchorPublicKey!, buffer: buffer, boxed: false)
                }
                _data.proof.serialize(buffer, true)
                break
            case .inputWalletNew:
                if boxed {
                    buffer.appendInt32(1671708892)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .inputWalletImported(let _data):
                return ("inputWalletImported", [("flags", ConstructorParameterDescription(_data.flags)), ("publicKey", ConstructorParameterDescription(_data.publicKey)), ("anchorPublicKey", ConstructorParameterDescription(_data.anchorPublicKey)), ("proof", ConstructorParameterDescription(_data.proof))])
            case .inputWalletNew:
                return ("inputWalletNew", [])
            }
        }

        public static func parse_inputWalletImported(_ reader: BufferReader) -> InputWalletReplacement? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Buffer?
            _2 = parseBytes(reader)
            var _3: Buffer?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                _3 = parseBytes(reader)
            }
            var _4: Api.WalletOwnershipProof?
            if let signature = reader.readInt32() {
                _4 = Api.parse(reader, signature: signature) as? Api.WalletOwnershipProof
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _3 != nil
            let _c4 = _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.InputWalletReplacement.inputWalletImported(Cons_inputWalletImported(flags: _1!, publicKey: _2!, anchorPublicKey: _3, proof: _4!))
            }
            else {
                return nil
            }
        }
        public static func parse_inputWalletNew(_ reader: BufferReader) -> InputWalletReplacement? {
            return Api.InputWalletReplacement.inputWalletNew
        }
    }
}


public extension Api.functions.wallet {
    static func disableBackup(flags: Int32, password: Api.InputCheckPasswordSRP?, newPublicKey: Buffer?, proof: Api.WalletOwnershipProof?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.WalletState>) {
        let buffer = Buffer()
        buffer.appendInt32(185331930)
        serializeInt32(flags, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            password!.serialize(buffer, true)
        }
        if Int(flags) & Int(1 << 1) != 0 {
            serializeBytes(newPublicKey!, buffer: buffer, boxed: false)
        }
        if Int(flags) & Int(1 << 2) != 0 {
            proof!.serialize(buffer, true)
        }
        return (FunctionDescription(name: "wallet.disableBackup", parameters: [("flags", ConstructorParameterDescription(flags)), ("password", ConstructorParameterDescription(password)), ("newPublicKey", ConstructorParameterDescription(newPublicKey)), ("proof", ConstructorParameterDescription(proof))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.WalletState? in
            let reader = BufferReader(buffer)
            var result: Api.WalletState?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.WalletState
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func enableBackup(flags: Int32, parts: [Buffer], newPublicKey: Buffer?, proof: Api.WalletOwnershipProof?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.WalletState>) {
        let buffer = Buffer()
        buffer.appendInt32(1157678373)
        serializeInt32(flags, buffer: buffer, boxed: false)
        buffer.appendInt32(481674261)
        buffer.appendInt32(Int32(parts.count))
        for item in parts {
            serializeBytes(item, buffer: buffer, boxed: false)
        }
        if Int(flags) & Int(1 << 1) != 0 {
            serializeBytes(newPublicKey!, buffer: buffer, boxed: false)
        }
        if Int(flags) & Int(1 << 2) != 0 {
            proof!.serialize(buffer, true)
        }
        return (FunctionDescription(name: "wallet.enableBackup", parameters: [("flags", ConstructorParameterDescription(flags)), ("parts", ConstructorParameterDescription(parts)), ("newPublicKey", ConstructorParameterDescription(newPublicKey)), ("proof", ConstructorParameterDescription(proof))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.WalletState? in
            let reader = BufferReader(buffer)
            var result: Api.WalletState?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.WalletState
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func exportSecretPhrase(flags: Int32, password: Api.InputCheckPasswordSRP?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.SecretPhraseParts>) {
        let buffer = Buffer()
        buffer.appendInt32(-1871197780)
        serializeInt32(flags, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            password!.serialize(buffer, true)
        }
        return (FunctionDescription(name: "wallet.exportSecretPhrase", parameters: [("flags", ConstructorParameterDescription(flags)), ("password", ConstructorParameterDescription(password))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.SecretPhraseParts? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.SecretPhraseParts?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.SecretPhraseParts
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func fetchEncryptedSecretPhrasePart(token: String, publicKey: Buffer) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.EncryptedSecretPhrasePart>) {
        let buffer = Buffer()
        buffer.appendInt32(-180804145)
        serializeString(token, buffer: buffer, boxed: false)
        serializeBytes(publicKey, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.fetchEncryptedSecretPhrasePart", parameters: [("token", ConstructorParameterDescription(token)), ("publicKey", ConstructorParameterDescription(publicKey))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.EncryptedSecretPhrasePart? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.EncryptedSecretPhrasePart?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.EncryptedSecretPhrasePart
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getBackupHolderDcs() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<[Api.wallet.HolderDc]>) {
        let buffer = Buffer()
        buffer.appendInt32(-780544876)
        return (FunctionDescription(name: "wallet.getBackupHolderDcs", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> [Api.wallet.HolderDc]? in
            let reader = BufferReader(buffer)
            var result: [Api.wallet.HolderDc]?
            if let _ = reader.readInt32() {
                result = Api.parseVector(reader, elementSignature: 0, elementType: Api.wallet.HolderDc.self)
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getExistingWaltBalance() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.ExistingBalance>) {
        let buffer = Buffer()
        buffer.appendInt32(1763656544)
        return (FunctionDescription(name: "wallet.getExistingWaltBalance", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.ExistingBalance? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.ExistingBalance?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.ExistingBalance
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getGaslessInfo() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Updates>) {
        let buffer = Buffer()
        buffer.appendInt32(1949167777)
        return (FunctionDescription(name: "wallet.getGaslessInfo", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Updates? in
            let reader = BufferReader(buffer)
            var result: Api.Updates?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Updates
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getNfts(offset: String, limit: Int32) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.NftItems>) {
        let buffer = Buffer()
        buffer.appendInt32(-1944915036)
        serializeString(offset, buffer: buffer, boxed: false)
        serializeInt32(limit, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.getNfts", parameters: [("offset", ConstructorParameterDescription(offset)), ("limit", ConstructorParameterDescription(limit))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.NftItems? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.NftItems?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.NftItems
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getProofChallenge() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.ProofChallenge>) {
        let buffer = Buffer()
        buffer.appendInt32(539354775)
        return (FunctionDescription(name: "wallet.getProofChallenge", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.ProofChallenge? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.ProofChallenge?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.ProofChallenge
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getState() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.WalletState>) {
        let buffer = Buffer()
        buffer.appendInt32(-1417432262)
        return (FunctionDescription(name: "wallet.getState", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.WalletState? in
            let reader = BufferReader(buffer)
            var result: Api.WalletState?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.WalletState
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getTransactions(flags: Int32, offset: String, limit: Int32) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.Transactions>) {
        let buffer = Buffer()
        buffer.appendInt32(-1467807101)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeString(offset, buffer: buffer, boxed: false)
        serializeInt32(limit, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.getTransactions", parameters: [("flags", ConstructorParameterDescription(flags)), ("offset", ConstructorParameterDescription(offset)), ("limit", ConstructorParameterDescription(limit))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.Transactions? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.Transactions?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.Transactions
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getTransactionsByIDs(id: [String]) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.Transactions>) {
        let buffer = Buffer()
        buffer.appendInt32(-2128811338)
        buffer.appendInt32(481674261)
        buffer.appendInt32(Int32(id.count))
        for item in id {
            serializeString(item, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.getTransactionsByIDs", parameters: [("id", ConstructorParameterDescription(id))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.Transactions? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.Transactions?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.Transactions
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getTransactionsByMsgHash(msgHash: [String]) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.Transactions>) {
        let buffer = Buffer()
        buffer.appendInt32(-1497663139)
        buffer.appendInt32(481674261)
        buffer.appendInt32(Int32(msgHash.count))
        for item in msgHash {
            serializeString(item, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.getTransactionsByMsgHash", parameters: [("msgHash", ConstructorParameterDescription(msgHash))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.Transactions? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.Transactions?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.Transactions
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func getUserAddresses(flags: Int32, id: [Api.InputUser], addresses: [String]) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.UserAddresses>) {
        let buffer = Buffer()
        buffer.appendInt32(1383456733)
        serializeInt32(flags, buffer: buffer, boxed: false)
        buffer.appendInt32(481674261)
        buffer.appendInt32(Int32(id.count))
        for item in id {
            item.serialize(buffer, true)
        }
        buffer.appendInt32(481674261)
        buffer.appendInt32(Int32(addresses.count))
        for item in addresses {
            serializeString(item, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.getUserAddresses", parameters: [("flags", ConstructorParameterDescription(flags)), ("id", ConstructorParameterDescription(id)), ("addresses", ConstructorParameterDescription(addresses))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.UserAddresses? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.UserAddresses?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.UserAddresses
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func replaceWallet(flags: Int32, wallet: Api.InputWalletReplacement, password: Api.InputCheckPasswordSRP?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.WalletState>) {
        let buffer = Buffer()
        buffer.appendInt32(-658034964)
        serializeInt32(flags, buffer: buffer, boxed: false)
        wallet.serialize(buffer, true)
        if Int(flags) & Int(1 << 0) != 0 {
            password!.serialize(buffer, true)
        }
        return (FunctionDescription(name: "wallet.replaceWallet", parameters: [("flags", ConstructorParameterDescription(flags)), ("wallet", ConstructorParameterDescription(wallet)), ("password", ConstructorParameterDescription(password))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.WalletState? in
            let reader = BufferReader(buffer)
            var result: Api.WalletState?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.WalletState
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func sendTransfer(flags: Int32, dataNormal: Buffer, dataGasless: Buffer?, userId: Api.InputUser, randomId: Int64) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Updates>) {
        let buffer = Buffer()
        buffer.appendInt32(-639247902)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeBytes(dataNormal, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            serializeBytes(dataGasless!, buffer: buffer, boxed: false)
        }
        userId.serialize(buffer, true)
        serializeInt64(randomId, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.sendTransfer", parameters: [("flags", ConstructorParameterDescription(flags)), ("dataNormal", ConstructorParameterDescription(dataNormal)), ("dataGasless", ConstructorParameterDescription(dataGasless)), ("userId", ConstructorParameterDescription(userId)), ("randomId", ConstructorParameterDescription(randomId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Updates? in
            let reader = BufferReader(buffer)
            var result: Api.Updates?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Updates
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectClaimRequest(flags: Int32, sessionId: Int64, msgId: Int32, appRequestId: String, challengeAnswer: Buffer?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Bool>) {
        let buffer = Buffer()
        buffer.appendInt32(1723169601)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        serializeInt32(msgId, buffer: buffer, boxed: false)
        serializeString(appRequestId, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            serializeBytes(challengeAnswer!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.tonConnectClaimRequest", parameters: [("flags", ConstructorParameterDescription(flags)), ("sessionId", ConstructorParameterDescription(sessionId)), ("msgId", ConstructorParameterDescription(msgId)), ("appRequestId", ConstructorParameterDescription(appRequestId)), ("challengeAnswer", ConstructorParameterDescription(challengeAnswer))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Bool? in
            let reader = BufferReader(buffer)
            var result: Api.Bool?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Bool
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectCloseSession(flags: Int32, sessionId: Int64, body: Buffer?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Bool>) {
        let buffer = Buffer()
        buffer.appendInt32(-700489266)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            serializeBytes(body!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.tonConnectCloseSession", parameters: [("flags", ConstructorParameterDescription(flags)), ("sessionId", ConstructorParameterDescription(sessionId)), ("body", ConstructorParameterDescription(body))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Bool? in
            let reader = BufferReader(buffer)
            var result: Api.Bool?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Bool
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectCreateSession(dappClientId: String, manifestUrl: String) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.TonConnectSession>) {
        let buffer = Buffer()
        buffer.appendInt32(-862777274)
        serializeString(dappClientId, buffer: buffer, boxed: false)
        serializeString(manifestUrl, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.tonConnectCreateSession", parameters: [("dappClientId", ConstructorParameterDescription(dappClientId)), ("manifestUrl", ConstructorParameterDescription(manifestUrl))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.TonConnectSession? in
            let reader = BufferReader(buffer)
            var result: Api.TonConnectSession?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.TonConnectSession
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectGetPending(flags: Int32, dappClientId: String?, sessionId: Int64?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.TonConnectPending>) {
        let buffer = Buffer()
        buffer.appendInt32(296673381)
        serializeInt32(flags, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            serializeString(dappClientId!, buffer: buffer, boxed: false)
        }
        if Int(flags) & Int(1 << 1) != 0 {
            serializeInt64(sessionId!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.tonConnectGetPending", parameters: [("flags", ConstructorParameterDescription(flags)), ("dappClientId", ConstructorParameterDescription(dappClientId)), ("sessionId", ConstructorParameterDescription(sessionId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.TonConnectPending? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.TonConnectPending?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.TonConnectPending
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectGetSessions() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.TonConnectSessions>) {
        let buffer = Buffer()
        buffer.appendInt32(-1476414098)
        return (FunctionDescription(name: "wallet.tonConnectGetSessions", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.TonConnectSessions? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.TonConnectSessions?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.TonConnectSessions
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectNextEventId(sessionId: Int64) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.TonConnectNextEventId>) {
        let buffer = Buffer()
        buffer.appendInt32(2002612804)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.tonConnectNextEventId", parameters: [("sessionId", ConstructorParameterDescription(sessionId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.TonConnectNextEventId? in
            let reader = BufferReader(buffer)
            var result: Api.TonConnectNextEventId?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.TonConnectNextEventId
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectRegisterKey(sessionId: Int64, clientId: String) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.wallet.TonConnectChallenge>) {
        let buffer = Buffer()
        buffer.appendInt32(864800540)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        serializeString(clientId, buffer: buffer, boxed: false)
        return (FunctionDescription(name: "wallet.tonConnectRegisterKey", parameters: [("sessionId", ConstructorParameterDescription(sessionId)), ("clientId", ConstructorParameterDescription(clientId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.wallet.TonConnectChallenge? in
            let reader = BufferReader(buffer)
            var result: Api.wallet.TonConnectChallenge?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.wallet.TonConnectChallenge
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectSubmitConnectResult(flags: Int32, sessionId: Int64, challengeAnswer: Buffer, body: Buffer, traceId: String?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Bool>) {
        let buffer = Buffer()
        buffer.appendInt32(-1027602567)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        serializeBytes(challengeAnswer, buffer: buffer, boxed: false)
        serializeBytes(body, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 1) != 0 {
            serializeString(traceId!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.tonConnectSubmitConnectResult", parameters: [("flags", ConstructorParameterDescription(flags)), ("sessionId", ConstructorParameterDescription(sessionId)), ("challengeAnswer", ConstructorParameterDescription(challengeAnswer)), ("body", ConstructorParameterDescription(body)), ("traceId", ConstructorParameterDescription(traceId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Bool? in
            let reader = BufferReader(buffer)
            var result: Api.Bool?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Bool
            }
            return result
        })
    }
}
public extension Api.functions.wallet {
    static func tonConnectSubmitResponse(flags: Int32, sessionId: Int64, msgId: Int32, body: Buffer, traceId: String?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.Bool>) {
        let buffer = Buffer()
        buffer.appendInt32(-87655108)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeInt64(sessionId, buffer: buffer, boxed: false)
        serializeInt32(msgId, buffer: buffer, boxed: false)
        serializeBytes(body, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 0) != 0 {
            serializeString(traceId!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "wallet.tonConnectSubmitResponse", parameters: [("flags", ConstructorParameterDescription(flags)), ("sessionId", ConstructorParameterDescription(sessionId)), ("msgId", ConstructorParameterDescription(msgId)), ("body", ConstructorParameterDescription(body)), ("traceId", ConstructorParameterDescription(traceId))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.Bool? in
            let reader = BufferReader(buffer)
            var result: Api.Bool?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.Bool
            }
            return result
        })
    }
}
public extension Api {
    enum WalletOwnershipProof: TypeConstructorDescription {
        public class Cons_walletOwnershipProof: TypeConstructorDescription {
            public var timestamp: Int32
            public var signature: Buffer
            public init(timestamp: Int32, signature: Buffer) {
                self.timestamp = timestamp
                self.signature = signature
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("walletOwnershipProof", [("timestamp", ConstructorParameterDescription(self.timestamp)), ("signature", ConstructorParameterDescription(self.signature))])
            }
        }
        case walletOwnershipProof(Cons_walletOwnershipProof)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .walletOwnershipProof(let _data):
                if boxed {
                    buffer.appendInt32(1622985485)
                }
                serializeInt32(_data.timestamp, buffer: buffer, boxed: false)
                serializeBytes(_data.signature, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .walletOwnershipProof(let _data):
                return ("walletOwnershipProof", [("timestamp", ConstructorParameterDescription(_data.timestamp)), ("signature", ConstructorParameterDescription(_data.signature))])
            }
        }

        public static func parse_walletOwnershipProof(_ reader: BufferReader) -> WalletOwnershipProof? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: Buffer?
            _2 = parseBytes(reader)
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.WalletOwnershipProof.walletOwnershipProof(Cons_walletOwnershipProof(timestamp: _1!, signature: _2!))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api {
    enum TonConnectManifest: TypeConstructorDescription {
        public class Cons_tonConnectManifest: TypeConstructorDescription {
            public var flags: Int32
            public var url: String
            public var name: String
            public var icon: Api.WebDocument?
            public init(flags: Int32, url: String, name: String, icon: Api.WebDocument?) {
                self.flags = flags
                self.url = url
                self.name = name
                self.icon = icon
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("tonConnectManifest", [("flags", ConstructorParameterDescription(self.flags)), ("url", ConstructorParameterDescription(self.url)), ("name", ConstructorParameterDescription(self.name)), ("icon", ConstructorParameterDescription(self.icon))])
            }
        }
        case tonConnectManifest(Cons_tonConnectManifest)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .tonConnectManifest(let _data):
                if boxed {
                    buffer.appendInt32(304255588)
                }
                serializeInt32(_data.flags, buffer: buffer, boxed: false)
                serializeString(_data.url, buffer: buffer, boxed: false)
                serializeString(_data.name, buffer: buffer, boxed: false)
                if Int(_data.flags) & Int(1 << 0) != 0 {
                    _data.icon!.serialize(buffer, true)
                }
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .tonConnectManifest(let _data):
                return ("tonConnectManifest", [("flags", ConstructorParameterDescription(_data.flags)), ("url", ConstructorParameterDescription(_data.url)), ("name", ConstructorParameterDescription(_data.name)), ("icon", ConstructorParameterDescription(_data.icon))])
            }
        }

        public static func parse_tonConnectManifest(_ reader: BufferReader) -> TonConnectManifest? {
            var _1: Int32?
            _1 = reader.readInt32()
            var _2: String?
            _2 = parseString(reader)
            var _3: String?
            _3 = parseString(reader)
            var _4: Api.WebDocument?
            if Int(_1 ?? 0) & Int(1 << 0) != 0 {
                if let signature = reader.readInt32() {
                    _4 = Api.parse(reader, signature: signature) as? Api.WebDocument
                }
            }
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            let _c3 = _3 != nil
            let _c4 = (Int(_1 ?? 0) & Int(1 << 0) == 0) || _4 != nil
            if _c1 && _c2 && _c3 && _c4 {
                return Api.TonConnectManifest.tonConnectManifest(Cons_tonConnectManifest(flags: _1!, url: _2!, name: _3!, icon: _4))
            }
            else {
                return nil
            }
        }
    }
}


public extension Api.functions.toncenter {
    static func getStreamingUrl() -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.toncenter.StreamingUrl>) {
        let buffer = Buffer()
        buffer.appendInt32(-844143673)
        return (FunctionDescription(name: "toncenter.getStreamingUrl", parameters: []), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.toncenter.StreamingUrl? in
            let reader = BufferReader(buffer)
            var result: Api.toncenter.StreamingUrl?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.toncenter.StreamingUrl
            }
            return result
        })
    }
}
public extension Api.functions.toncenter {
    static func performApiRequest(flags: Int32, endpoint: String, query: String?, payload: String?) -> (FunctionDescription, Buffer, DeserializeFunctionResponse<Api.toncenter.ApiResponse>) {
        let buffer = Buffer()
        buffer.appendInt32(-1921262239)
        serializeInt32(flags, buffer: buffer, boxed: false)
        serializeString(endpoint, buffer: buffer, boxed: false)
        if Int(flags) & Int(1 << 1) != 0 {
            serializeString(query!, buffer: buffer, boxed: false)
        }
        if Int(flags) & Int(1 << 2) != 0 {
            serializeString(payload!, buffer: buffer, boxed: false)
        }
        return (FunctionDescription(name: "toncenter.performApiRequest", parameters: [("flags", ConstructorParameterDescription(flags)), ("endpoint", ConstructorParameterDescription(endpoint)), ("query", ConstructorParameterDescription(query)), ("payload", ConstructorParameterDescription(payload))]), buffer, DeserializeFunctionResponse { (buffer: Buffer) -> Api.toncenter.ApiResponse? in
            let reader = BufferReader(buffer)
            var result: Api.toncenter.ApiResponse?
            if let signature = reader.readInt32() {
                result = Api.parse(reader, signature: signature) as? Api.toncenter.ApiResponse
            }
            return result
        })
    }
}

public extension Api.toncenter {
    enum ApiResponse: TypeConstructorDescription {
        public class Cons_apiResponse: TypeConstructorDescription {
            public var response: Api.DataJSON
            public init(response: Api.DataJSON) {
                self.response = response
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("apiResponse", [("response", ConstructorParameterDescription(self.response))])
            }
        }
        case apiResponse(Cons_apiResponse)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .apiResponse(let _data):
                if boxed {
                    buffer.appendInt32(-1399980519)
                }
                _data.response.serialize(buffer, true)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .apiResponse(let _data):
                return ("apiResponse", [("response", ConstructorParameterDescription(_data.response))])
            }
        }

        public static func parse_apiResponse(_ reader: BufferReader) -> ApiResponse? {
            var _1: Api.DataJSON?
            if let signature = reader.readInt32() {
                _1 = Api.parse(reader, signature: signature) as? Api.DataJSON
            }
            let _c1 = _1 != nil
            if _c1 {
                return Api.toncenter.ApiResponse.apiResponse(Cons_apiResponse(response: _1!))
            }
            else {
                return nil
            }
        }
    }
}
public extension Api.toncenter {
    enum StreamingUrl: TypeConstructorDescription {
        public class Cons_streamingUrl: TypeConstructorDescription {
            public var url: String
            public var expires: Int32
            public init(url: String, expires: Int32) {
                self.url = url
                self.expires = expires
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("streamingUrl", [("url", ConstructorParameterDescription(self.url)), ("expires", ConstructorParameterDescription(self.expires))])
            }
        }
        case streamingUrl(Cons_streamingUrl)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .streamingUrl(let _data):
                if boxed {
                    buffer.appendInt32(428373505)
                }
                serializeString(_data.url, buffer: buffer, boxed: false)
                serializeInt32(_data.expires, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .streamingUrl(let _data):
                return ("streamingUrl", [("url", ConstructorParameterDescription(_data.url)), ("expires", ConstructorParameterDescription(_data.expires))])
            }
        }

        public static func parse_streamingUrl(_ reader: BufferReader) -> StreamingUrl? {
            var _1: String?
            _1 = parseString(reader)
            var _2: Int32?
            _2 = reader.readInt32()
            let _c1 = _1 != nil
            let _c2 = _2 != nil
            if _c1 && _c2 {
                return Api.toncenter.StreamingUrl.streamingUrl(Cons_streamingUrl(url: _1!, expires: _2!))
            }
            else {
                return nil
            }
        }
    }
}
