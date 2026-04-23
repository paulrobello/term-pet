import AppKit

final class SheepView: NSView {
    var image: NSImage? {
        didSet { needsDisplay = true }
    }
    var mirrored: Bool = false {
        didSet { needsDisplay = true }
    }

    var onMouseDown: ((NSPoint) -> Void)?
    var onMouseDragged: ((NSPoint) -> Void)?
    var onMouseUp: ((NSPoint) -> Void)?

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        guard let image = image,
              let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.saveGState()
        if mirrored {
            ctx.translateBy(x: bounds.width, y: 0)
            ctx.scaleBy(x: -1, y: 1)
        }
        image.draw(in: bounds,
                   from: .zero,
                   operation: .sourceOver,
                   fraction: 1.0)
        ctx.restoreGState()
    }

    override func mouseDown(with event: NSEvent) {
        onMouseDown?(event.locationInWindow)
    }

    override func mouseDragged(with event: NSEvent) {
        onMouseDragged?(NSEvent.mouseLocation)
    }

    override func mouseUp(with event: NSEvent) {
        onMouseUp?(NSEvent.mouseLocation)
    }
}
