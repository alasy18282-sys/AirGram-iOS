// toncenter.* lives in the fork schema, not in upstream 13.2. The constructors
// below are the ones the server answers with (handlers/stubs.py):
//   toncenter.getStreamingUrl#cdaf63c7 = toncenter.StreamingUrl;
//   toncenter.performApiRequest#8d7bdd61 flags:# endpoint:string
//       query:flags.1?string payload:flags.2?string = toncenter.ApiResponse;
//   toncenter.streamingUrl#19887601 url:string expires:int = toncenter.StreamingUrl;
//   toncenter.apiResponse#ac8dfe19 response:DataJSON = toncenter.ApiResponse;

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

public extension Api.toncenter {
    enum DataJSON: TypeConstructorDescription {
        public class Cons_dataJSON: TypeConstructorDescription {
            public var data: String
            public init(data: String) {
                self.data = data
            }
            public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
                return ("dataJSON", [("data", ConstructorParameterDescription(self.data))])
            }
        }
        case dataJSON(Cons_dataJSON)

        public func serialize(_ buffer: Buffer, _ boxed: Swift.Bool) {
            switch self {
            case .dataJSON(let _data):
                if boxed {
                    buffer.appendInt32(2104790276)
                }
                serializeString(_data.data, buffer: buffer, boxed: false)
                break
            }
        }

        public func descriptionFields() -> (String, [(String, ConstructorParameterDescription)]) {
            switch self {
            case .dataJSON(let _data):
                return ("dataJSON", [("data", ConstructorParameterDescription(_data.data))])
            }
        }

        public static func parse_dataJSON(_ reader: BufferReader) -> DataJSON? {
            var _1: String?
            _1 = parseString(reader)
            if let _1 = _1 {
                return Api.toncenter.DataJSON.dataJSON(Cons_dataJSON(data: _1))
            }
            else {
                return nil
            }
        }
    }
}

public extension Api.toncenter {
    enum ApiResponse: TypeConstructorDescription {
        public class Cons_apiResponse: TypeConstructorDescription {
            public var response: DataJSON
            public init(response: DataJSON) {
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
            var _1: DataJSON?
            if let signature = reader.readInt32(), signature == 2104790276 {
                _1 = Api.toncenter.DataJSON.parse_dataJSON(reader)
            }
            if let _1 = _1 {
                return Api.toncenter.ApiResponse.apiResponse(Cons_apiResponse(response: _1))
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
