import AppKit

/// Rarity mirrors term-pet's `Rarity` enum. Controls the tray accent color
/// and the stars shown next to the pet name.
enum Rarity: String {
    case common = "COMMON"
    case uncommon = "UNCOMMON"
    case rare = "RARE"
    case epic = "EPIC"
    case legendary = "LEGENDARY"

    var stars: String {
        switch self {
        case .common:    return "\u{2605}"
        case .uncommon:  return "\u{2605}\u{2605}"
        case .rare:      return "\u{2605}\u{2605}\u{2605}"
        case .epic:      return "\u{2605}\u{2605}\u{2605}\u{2605}"
        case .legendary: return "\u{2605}\u{2605}\u{2605}\u{2605}\u{2605}"
        }
    }

    /// Maps to the closest AppKit system color. The python side uses Rich
    /// color names (dim / green / yellow / medium_purple1 / bright_magenta);
    /// the macOS tray can't render those verbatim, so we pick visually-similar
    /// system colors that also adapt to dark mode.
    var color: NSColor {
        switch self {
        case .common:    return .secondaryLabelColor
        case .uncommon:  return .systemGreen
        case .rare:      return .systemYellow
        case .epic:      return .systemPurple
        case .legendary: return .systemPink
        }
    }
}

enum PetConfig {
    /// Default `~/.config/tpet` — used when the binary is launched standalone
    /// without `--art-dir` / `--profile` overrides.
    static let directoryURL: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".config/tpet", isDirectory: true)
    }()

    /// Overridable by main.swift via `--art-dir`, so a tpet session launched
    /// with `--config-dir ~/.config/tpet-alt/` gets the right sprites.
    static var artURL: URL =
        directoryURL.appendingPathComponent("art", isDirectory: true)

    /// Overridable by main.swift via `--profile`, so the tray picks up the
    /// pet name, bio, backstory, stats, and rarity from the correct file.
    static var profileURL: URL =
        directoryURL.appendingPathComponent("profile.yaml", isDirectory: false)

    /// Socket path for the CommentBus. Injected from main.swift via `--socket`
    /// flag; defaults to ~/.config/tpet/display.sock for backwards compat when
    /// the binary is launched standalone.
    static var socketPath: String =
        directoryURL.appendingPathComponent("display.sock", isDirectory: false).path

    /// Cached profile contents — we parse several fields out of the same
    /// file, so reading it once avoids repeated I/O during static init.
    private static let profileContent: String = {
        (try? String(contentsOf: profileURL, encoding: .utf8)) ?? ""
    }()

    /// Pet name from term-pet's profile.yaml. Falls back to "Pet".
    static let petName: String = {
        let name = readScalar(key: "name")
        return name.isEmpty ? "Pet" : name
    }()

    /// Short personality summary ("bio"). May be empty if profile.yaml is
    /// missing or the field isn't present.
    static let personality: String = readScalar(key: "personality")

    /// Origin story. May be empty if unavailable.
    static let backstory: String = readScalar(key: "backstory")

    /// Pet rarity tier. Defaults to `.common` when unset or unparseable.
    static let rarity: Rarity = {
        let raw = readScalar(key: "rarity").uppercased()
        return Rarity(rawValue: raw) ?? .common
    }()

    /// Stat list in YAML order. Empty when the profile lacks stats.
    static let stats: [(name: String, value: Int)] = readStats()

    // MARK: - YAML scalar parsing

    /// Reads a top-level YAML scalar and decodes it per its style. Supports
    /// plain, double-quoted (with `\<newline>` line continuations and
    /// standard escape sequences), single-quoted, and literal/folded block
    /// scalars. This is enough for the fields term-pet writes via PyYAML.
    private static func readScalar(key: String) -> String {
        guard !profileContent.isEmpty else { return "" }
        let lines = profileContent.components(separatedBy: "\n")
        let prefix = "\(key):"
        guard let startIdx = lines.firstIndex(where: { line -> Bool in
            guard line.hasPrefix(prefix) else { return false }
            if line.count == prefix.count { return true }
            // Avoid false matches where `key` is a prefix of a longer key
            // (e.g. "name" vs "name_long"). The char right after must be
            // whitespace, since YAML requires a space after `:` for scalars.
            let next = line[line.index(line.startIndex, offsetBy: prefix.count)]
            return next == " " || next == "\t"
        }) else { return "" }

        var block = String(lines[startIdx].dropFirst(prefix.count))
        var i = startIdx + 1
        while i < lines.count {
            let line = lines[i]
            if line.isEmpty {
                // Blank lines are part of the block (paragraph separators
                // in block scalars). Keep them in case this is a literal
                // scalar; plain-scalar decoding also handles them.
                block += "\n"
                i += 1
                continue
            }
            guard let first = line.first, first == " " || first == "\t" else { break }
            block += "\n" + line
            i += 1
        }

        return decodeScalar(block)
    }

    /// Dispatches to the correct decoder based on the scalar's first
    /// non-whitespace character.
    private static func decodeScalar(_ raw: String) -> String {
        let leading = raw.drop(while: { $0 == " " || $0 == "\t" })
        guard let first = leading.first else { return "" }
        switch first {
        case "\"": return decodeDoubleQuoted(String(leading))
        case "'":  return decodeSingleQuoted(String(leading))
        case "|":  return decodeBlock(raw, keepNewlines: true)
        case ">":  return decodeBlock(raw, keepNewlines: false)
        default:   return foldPlain(raw)
        }
    }

    /// Plain-style folded: join indented continuation lines with single
    /// spaces, treat blank lines as paragraph breaks. This matches PyYAML's
    /// default wrap style for long plain strings.
    private static func foldPlain(_ raw: String) -> String {
        let lines = raw.components(separatedBy: "\n")
        var out = ""
        var sawContent = false
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                if sawContent, !out.hasSuffix("\n") {
                    out += "\n"
                }
                continue
            }
            if !out.isEmpty, !out.hasSuffix("\n") {
                out += " "
            }
            out += trimmed
            sawContent = true
        }
        return out
    }

    /// Literal (`|`) or folded (`>`) block scalar decoder. `keepNewlines`
    /// preserves single newlines between indented lines; otherwise single
    /// newlines fold to spaces (blanks still produce newlines).
    private static func decodeBlock(_ raw: String, keepNewlines: Bool) -> String {
        var lines = raw.components(separatedBy: "\n")
        // First "line" is the indicator line (e.g. `|`, `|-`, `>+`). Drop it.
        if !lines.isEmpty { lines.removeFirst() }

        // Strip the common leading indent so the body aligns to column 0.
        let indents: [Int] = lines.compactMap { line in
            if line.trimmingCharacters(in: .whitespaces).isEmpty { return nil }
            return line.prefix(while: { $0 == " " || $0 == "\t" }).count
        }
        let indent = indents.min() ?? 0

        var out = ""
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                if !out.hasSuffix("\n") { out += "\n" }
                continue
            }
            let body = indent < line.count
                ? String(line.dropFirst(indent))
                : line
            if out.isEmpty || out.hasSuffix("\n") {
                out += body
            } else if keepNewlines {
                out += "\n" + body
            } else {
                out += " " + body
            }
        }
        while out.hasSuffix("\n") { out.removeLast() }
        return out
    }

    /// Single-quoted scalar decoder. The only escape is `''` → `'`.
    /// Continuation lines fold to spaces.
    private static func decodeSingleQuoted(_ input: String) -> String {
        var s = input
        if s.hasPrefix("'") { s.removeFirst() }
        if s.hasSuffix("'") { s.removeLast() }
        // Fold newlines (+ indent) into spaces.
        let folded = s
            .components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .joined(separator: " ")
        return folded.replacingOccurrences(of: "''", with: "'")
    }

    /// Double-quoted scalar decoder. Handles:
    /// - `\<newline>` line continuations (elide; also elide leading indent
    ///   of the continuation line)
    /// - Standard escapes: `\n`, `\t`, `\r`, `\\`, `\"`, `\/`, `\0`,
    ///   `\a`, `\b`, `\f`, `\v`, `\e`, `\ `, `\N`, `\_`, `\L`, `\P`
    /// - Unicode escapes: `\xXX`, `\uXXXX`, `\UXXXXXXXX`
    /// Enough for what PyYAML emits.
    private static func decodeDoubleQuoted(_ input: String) -> String {
        var s = input
        if s.hasPrefix("\"") { s.removeFirst() }
        if s.hasSuffix("\"") { s.removeLast() }
        let chars = Array(s)
        var out = ""
        var i = 0
        while i < chars.count {
            let c = chars[i]
            if c == "\\", i + 1 < chars.count {
                let n = chars[i + 1]
                switch n {
                case "\n":
                    // Line continuation: consume the backslash + newline and
                    // any leading indentation on the next line.
                    i += 2
                    while i < chars.count, chars[i] == " " || chars[i] == "\t" {
                        i += 1
                    }
                case "n":  out.append("\n"); i += 2
                case "t":  out.append("\t"); i += 2
                case "r":  out.append("\r"); i += 2
                case "\\": out.append("\\"); i += 2
                case "\"": out.append("\""); i += 2
                case "/":  out.append("/");  i += 2
                case "0":  out.append("\0"); i += 2
                case "a":  out.append("\u{07}"); i += 2
                case "b":  out.append("\u{08}"); i += 2
                case "f":  out.append("\u{0C}"); i += 2
                case "v":  out.append("\u{0B}"); i += 2
                case "e":  out.append("\u{1B}"); i += 2
                case " ":  out.append(" ");  i += 2
                case "\t": out.append("\t"); i += 2
                case "N":  out.append("\u{85}");   i += 2
                case "_":  out.append("\u{A0}");   i += 2
                case "L":  out.append("\u{2028}"); i += 2
                case "P":  out.append("\u{2029}"); i += 2
                case "x":  i = appendHex(chars, from: i + 2, width: 2, into: &out)
                case "u":  i = appendHex(chars, from: i + 2, width: 4, into: &out)
                case "U":  i = appendHex(chars, from: i + 2, width: 8, into: &out)
                default:
                    out.append(n); i += 2
                }
            } else {
                out.append(c); i += 1
            }
        }
        return out
    }

    /// Reads `width` hex digits starting at `from`, appends the matching
    /// Unicode scalar (if valid), and returns the index past the digits.
    private static func appendHex(
        _ chars: [Character],
        from start: Int,
        width: Int,
        into out: inout String
    ) -> Int {
        let end = start + width
        guard end <= chars.count else { return chars.count }
        let hex = String(chars[start..<end])
        if let scalar = UInt32(hex, radix: 16), let u = Unicode.Scalar(scalar) {
            out.unicodeScalars.append(u)
        }
        return end
    }

    // MARK: - Stats

    /// Reads the `stats:` mapping as an ordered `(name, value)` list.
    /// Order matches the YAML file so the tray rows render in the same
    /// sequence term-pet uses everywhere else.
    private static func readStats() -> [(name: String, value: Int)] {
        guard !profileContent.isEmpty else { return [] }
        let lines = profileContent.components(separatedBy: "\n")
        guard let startIdx = lines.firstIndex(where: { $0.hasPrefix("stats:") })
        else { return [] }

        var out: [(String, Int)] = []
        var i = startIdx + 1
        while i < lines.count {
            let line = lines[i]
            if line.isEmpty { i += 1; continue }
            guard let first = line.first, first == " " || first == "\t" else { break }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if let colon = trimmed.firstIndex(of: ":") {
                let key = String(trimmed[..<colon])
                    .trimmingCharacters(in: .whitespaces)
                let valStr = String(trimmed[trimmed.index(after: colon)...])
                    .trimmingCharacters(in: .whitespaces)
                if let val = Int(valStr) {
                    out.append((key, val))
                }
            }
            i += 1
        }
        return out
    }

    // MARK: - Art seeding

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
