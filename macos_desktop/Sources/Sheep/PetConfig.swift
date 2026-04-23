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

    /// Pet name from term-pet's profile.yaml. Parsed with a minimal YAML-unaware
    /// regex — profile.yaml's `name:` field is always flat scalar. Falls back to
    /// "Pet" if the file can't be read or the field is missing.
    static let petName: String = readPetName()

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
