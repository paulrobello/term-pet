import AppKit

/// The 10 sprite frames, using term-pet's layout convention.
enum SpriteFrame: Int, CaseIterable {
    case idleA   = 0
    case idleB   = 1
    case blinkA  = 2
    case blinkB  = 3
    case excited = 4
    case sleep   = 5
    case walkA   = 6
    case walkB   = 7
    case fall    = 8
    case stunned = 9

    /// Bundled fallback filename (no pet-name prefix).
    var bundledName: String { "frame_\(rawValue)" }
}

enum Sprites {
    /// Fixed 64x64 canvas. Source art is scaled so its width = 64; height
    /// follows aspect ratio. Any height overflow past 64 is clipped at the
    /// top (heads of very tall sprites would be cut). Center-bottom anchored.
    static let size = NSSize(width: 64, height: 64)

    /// Override → bundled → blank canvas. All returned images are the same
    /// `size`, with source scaled so max(w,h) matches and anchored
    /// center-bottom.
    static func sheep(_ frame: SpriteFrame) -> NSImage {
        if let img = rawImage(frame) {
            return compose(sourceImage: img)
        }
        return NSImage(size: size) // blank, transparent
    }

    // MARK: - Loading

    private static func rawImage(_ frame: SpriteFrame) -> NSImage? {
        if let url = PetConfig.overrideURL(for: frame),
           let img = NSImage(contentsOf: url) {
            return img
        }
        if let url = Bundle.module.url(forResource: frame.bundledName, withExtension: "png"),
           let img = NSImage(contentsOf: url) {
            return img
        }
        return nil
    }

    // MARK: - Compose

    /// Returns a `size`-sized canvas with the source image scaled so its
    /// width equals `size.width` and drawn bottom-anchored.
    private static func compose(sourceImage: NSImage) -> NSImage {
        let src = sourceImage.size
        let canvas = NSImage(size: size)
        guard src.width > 0, src.height > 0 else { return canvas }

        let scale = size.width / src.width
        let w = size.width
        let h = src.height * scale
        let rect = CGRect(x: 0, y: 0, width: w, height: h)

        canvas.lockFocusFlipped(false)
        sourceImage.draw(in: rect,
                         from: .zero,
                         operation: .sourceOver,
                         fraction: 1.0)
        canvas.unlockFocus()
        return canvas
    }
}
