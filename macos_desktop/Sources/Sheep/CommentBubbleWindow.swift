import AppKit

/// A borderless floating window with a rounded speech-bubble look. Shows
/// short text over the pet for a few seconds, then fades out.
final class CommentBubbleWindow: NSWindow {
    private let label = NSTextField(labelWithString: "")
    private let container = NSVisualEffectView()
    private var hideWorkItem: DispatchWorkItem?

    private let horizontalPad: CGFloat = 10
    private let verticalPad: CGFloat = 6
    private let maxWidth: CGFloat = 260
    private let minWidth: CGFloat = 40

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 120, height: 30),
            styleMask: .borderless,
            backing: .buffered,
            defer: false
        )
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        ignoresMouseEvents = true
        isReleasedWhenClosed = false
        alphaValue = 0

        container.material = .hudWindow
        container.state = .active
        container.wantsLayer = true
        container.layer?.cornerRadius = 10
        container.layer?.masksToBounds = true

        label.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        label.textColor = .labelColor
        label.maximumNumberOfLines = 3
        label.lineBreakMode = .byWordWrapping
        label.cell?.wraps = true
        label.cell?.usesSingleLineMode = false
        label.isEditable = false
        label.isBordered = false
        label.backgroundColor = .clear
        label.translatesAutoresizingMaskIntoConstraints = false

        container.addSubview(label)
        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: container.topAnchor, constant: verticalPad),
            label.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -verticalPad),
            label.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: horizontalPad),
            label.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -horizontalPad),
        ])
        contentView = container
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    func show(text: String, above anchor: NSWindow, duration: TimeInterval = 10.0) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            hide()
            return
        }
        label.stringValue = trimmed
        setContentSize(sizeThatFits(text: trimmed))
        reposition(above: anchor)
        orderFront(nil)
        fadeTo(1.0)

        hideWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.hide() }
        hideWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + duration, execute: work)
    }

    func hide() {
        hideWorkItem?.cancel()
        hideWorkItem = nil
        fadeTo(0.0) { [weak self] in self?.orderOut(nil) }
    }

    /// Anchors the bubble's bottom-left near the pet's top-right corner with a
    /// small overlap so it looks tethered. Flips to top-left if the pet is too
    /// close to the right edge of the screen.
    func reposition(above anchor: NSWindow, overlap: CGFloat = 6) {
        let pet = anchor.frame
        let size = frame.size
        var x = pet.maxX - overlap
        var y = pet.maxY - overlap

        if let vf = NSScreen.main?.visibleFrame {
            if x + size.width > vf.maxX {
                // Flip to top-left of the pet
                x = pet.minX + overlap - size.width
            }
            x = max(vf.minX, min(vf.maxX - size.width, x))
            y = min(vf.maxY - size.height, y)
        }
        setFrameOrigin(NSPoint(x: x, y: y))
    }

    private func sizeThatFits(text: String) -> NSSize {
        let attrs: [NSAttributedString.Key: Any] = [.font: label.font as Any]
        let constraint = NSSize(width: maxWidth - 2 * horizontalPad,
                                height: .greatestFiniteMagnitude)
        let rect = (text as NSString).boundingRect(
            with: constraint,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: attrs
        )
        let w = max(minWidth, min(maxWidth, ceil(rect.width) + 2 * horizontalPad))
        let h = ceil(rect.height) + 2 * verticalPad
        return NSSize(width: w, height: h)
    }

    private func fadeTo(_ target: CGFloat, completion: (() -> Void)? = nil) {
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.18
            self.animator().alphaValue = target
        }, completionHandler: completion)
    }
}
