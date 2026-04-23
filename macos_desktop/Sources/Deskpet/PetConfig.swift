import AppKit

enum PetConfig {
    static let directoryURL: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".config/tpet", isDirectory: true)
    }()

    static let artURL: URL =
        directoryURL.appendingPathComponent("art", isDirectory: true)

    static let profileURL: URL =
        directoryURL.appendingPathComponent("profile.yaml", isDirectory: false)

    /// Socket path for the CommentBus. Injected from main.swift via `--socket`
    /// flag; defaults to ~/.config/tpet/display.sock for backwards compat when
    /// the binary is launched standalone.
    static var socketPath: String =
        directoryURL.appendingPathComponent("display.sock", isDirectory: false).path

    /// Pet name from term-pet's profile.yaml. Parsed with a minimal YAML-unaware
    /// regex — profile.yaml's `name:` field is always flat scalar. Falls back to
    /// "Pet" if the file can't be read or the field is missing.
    static let petName: String = readPetName()

    /// Short personality summary ("bio"). May be empty if profile.yaml is
    /// missing or the field isn't present.
    static let personality: String = readFoldedScalar(key: "personality")

    /// Origin story. May be empty if unavailable.
    static let backstory: String = readFoldedScalar(key: "backstory")

    private static func readPetName() -> String {
        guard let content = try? String(contentsOf: profileURL, encoding: .utf8) else {
            return "Pet"
        }
        for rawLine in content.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            guard line.hasPrefix("name:") else { continue }
            var value = String(line.dropFirst("name:".count))
                .trimmingCharacters(in: .whitespaces)
            value = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            if !value.isEmpty { return value }
        }
        return "Pet"
    }

    /// Reads a top-level YAML scalar that may be folded across multiple
    /// whitespace-indented continuation lines (PyYAML's default block style
    /// for long strings). Continuation ends at the next non-indented line.
    private static func readFoldedScalar(key: String) -> String {
        guard let content = try? String(contentsOf: profileURL, encoding: .utf8) else {
            return ""
        }
        let lines = content.components(separatedBy: "\n")
        let prefix = "\(key):"
        guard let startIdx = lines.firstIndex(where: { $0.hasPrefix(prefix) }) else {
            return ""
        }
        var parts: [String] = []
        let head = lines[startIdx]
            .dropFirst(prefix.count)
            .trimmingCharacters(in: .whitespaces)
        if !head.isEmpty { parts.append(String(head)) }
        var i = startIdx + 1
        while i < lines.count {
            let line = lines[i]
            if line.isEmpty { break }
            guard let first = line.first, first.isWhitespace else { break }
            parts.append(line.trimmingCharacters(in: .whitespaces))
            i += 1
        }
        return parts.joined(separator: " ")
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
    }

    /// Term-pet's sprite filename convention: `{petName}_frame_{N}.png`.
    private static func spriteFilename(for frame: SpriteFrame) -> String {
        "\(petName)_frame_\(frame.rawValue).png"
    }

    /// Creates the art dir on first run and copies any missing bundled frames
    /// into place. Existing files are never overwritten, so term-pet's
    /// generated frames (and any user edits) are preserved.
    static func ensureSeeded() {
        let fm = FileManager.default
        do {
            try fm.createDirectory(at: artURL, withIntermediateDirectories: true)
        } catch {
            FileHandle.standardError.write(
                Data("tpet: failed to create \(artURL.path): \(error)\n".utf8)
            )
            return
        }

        for frame in SpriteFrame.allCases {
            let dest = artURL.appendingPathComponent(spriteFilename(for: frame))
            if fm.fileExists(atPath: dest.path) { continue }
            guard let src = Bundle.module.url(forResource: frame.bundledName,
                                              withExtension: "png") else { continue }
            do {
                try fm.copyItem(at: src, to: dest)
            } catch {
                FileHandle.standardError.write(
                    Data("tpet: seed failed for \(dest.lastPathComponent): \(error)\n".utf8)
                )
            }
        }
    }

    /// Returns the override PNG URL for a frame if present.
    static func overrideURL(for frame: SpriteFrame) -> URL? {
        let url = artURL.appendingPathComponent(spriteFilename(for: frame))
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
}
