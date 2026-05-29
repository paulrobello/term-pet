import Foundation
import Darwin

/// Unix-domain socket server. Reads newline-delimited JSON objects from the
/// peer (term-pet) and dispatches them on the main queue.
///
/// Expected event shape:
///   { "state": "idle"|"reacting"|"sleeping",  // optional
///     "comment": "text" }                      // optional; "" or null clears
///
/// One connected client at a time. If a second client connects, the existing
/// one is dropped.
final class CommentBus {
    struct Event {
        let state: RemoteState?
        let comment: String?   // nil or empty means "clear any current bubble"
    }

    enum RemoteState: String {
        case idle
        case reacting
        case sleeping
    }

    typealias Handler = (Event) -> Void

    private let socketPath: String
    private let handler: Handler

    private var serverFD: Int32 = -1
    private var clientFD: Int32 = -1
    private var acceptSource: DispatchSourceRead?
    private var readSource: DispatchSourceRead?
    private var inBuffer = Data()

    init(socketPath: String, handler: @escaping Handler) {
        self.socketPath = socketPath
        self.handler = handler
    }

    deinit { stop() }

    func start() {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            logErr("socket() failed: \(errno)")
            return
        }

        // Ensure the parent directory exists.
        let dir = (socketPath as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(
            atPath: dir, withIntermediateDirectories: true
        )

        // Nuke any leftover socket file from a previous crash.
        unlink(socketPath)

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        socketPath.withCString { cstr in
            withUnsafeMutableBytes(of: &addr.sun_path) { buf in
                let dst = buf.bindMemory(to: CChar.self).baseAddress!
                let len = min(strlen(cstr), size_t(buf.count - 1))
                memcpy(dst, cstr, len)
                dst[Int(len)] = 0
            }
        }
        let size = socklen_t(MemoryLayout<sockaddr_un>.size)
        let bindResult = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                bind(fd, sa, size)
            }
        }
        guard bindResult == 0 else {
            logErr("bind(\(socketPath)) failed: \(errno)")
            close(fd)
            return
        }
        guard listen(fd, 1) == 0 else {
            logErr("listen() failed: \(errno)")
            close(fd)
            return
        }

        serverFD = fd
        let source = DispatchSource.makeReadSource(
            fileDescriptor: fd, queue: .main
        )
        source.setEventHandler { [weak self] in self?.acceptConnection() }
        source.resume()
        acceptSource = source
    }

    func stop() {
        acceptSource?.cancel(); acceptSource = nil
        readSource?.cancel(); readSource = nil
        if clientFD >= 0 { close(clientFD); clientFD = -1 }
        if serverFD >= 0 { close(serverFD); serverFD = -1 }
        unlink(socketPath)
    }

    private func acceptConnection() {
        var addr = sockaddr_un()
        var len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let fd = withUnsafeMutablePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                accept(serverFD, sa, &len)
            }
        }
        guard fd >= 0 else { return }

        // Drop any prior client.
        readSource?.cancel()
        if clientFD >= 0 { close(clientFD) }

        clientFD = fd
        inBuffer.removeAll(keepingCapacity: true)

        let rs = DispatchSource.makeReadSource(fileDescriptor: fd, queue: .main)
        rs.setEventHandler { [weak self] in self?.readAvailable() }
        rs.setCancelHandler { [weak self] in
            guard let self = self else { return }
            if self.clientFD >= 0 {
                close(self.clientFD)
                self.clientFD = -1
            }
        }
        rs.resume()
        readSource = rs
    }

    private func readAvailable() {
        var buf = [UInt8](repeating: 0, count: 4096)
        let n = buf.withUnsafeMutableBufferPointer { ptr -> Int in
            read(clientFD, ptr.baseAddress, ptr.count)
        }
        if n <= 0 {
            readSource?.cancel()
            readSource = nil
            return
        }
        inBuffer.append(buf, count: n)
        drainLines()
    }

    private func drainLines() {
        while let nl = inBuffer.firstIndex(of: 0x0a) {
            let lineData = inBuffer.subdata(in: inBuffer.startIndex..<nl)
            inBuffer.removeSubrange(inBuffer.startIndex...nl)
            if lineData.isEmpty { continue }
            decode(line: lineData)
        }
    }

    private func decode(line: Data) {
        guard
            let any = try? JSONSerialization.jsonObject(with: line),
            let obj = any as? [String: Any]
        else {
            logErr("bad json: \(String(data: line, encoding: .utf8) ?? "?")")
            return
        }
        var state: RemoteState? = nil
        if let s = obj["state"] as? String { state = RemoteState(rawValue: s) }
        let comment: String?
        if let c = obj["comment"] as? String { comment = c }
        else if obj["comment"] is NSNull { comment = nil }
        else { comment = nil }
        handler(Event(state: state, comment: comment))
    }

    private func logErr(_ msg: String) {
        FileHandle.standardError.write(Data("CommentBus: \(msg)\n".utf8))
    }
}
