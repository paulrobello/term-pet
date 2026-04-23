import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: PetController?
    private var statusItem: NSStatusItem?
    private var gravityItem: NSMenuItem?
    private var walkItem: NSMenuItem?
    private let context: PetContext

    init(context: PetContext) {
        self.context = context
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = PetController()
        controller?.start()
        installStatusItem()
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            button.image = trayIcon()
            button.toolTip = "Deskpet: \(PetConfig.petName) (\(context.session))"
        }

        let menu = NSMenu()

        // Pet name (bold header)
        let header = NSMenuItem()
        header.attributedTitle = NSAttributedString(
            string: PetConfig.petName,
            attributes: [
                .font: NSFont.systemFont(ofSize: NSFont.systemFontSize, weight: .bold),
                .foregroundColor: NSColor.labelColor,
            ]
        )
        header.isEnabled = false
        menu.addItem(header)

        menu.addItem(infoItem(key: "session", value: context.session))
        menu.addItem(infoItem(key: "cwd", value: collapseHome(context.pwd)))

        menu.addItem(NSMenuItem.separator())
        menu.addItem(spriteItem())

        if !PetConfig.personality.isEmpty {
            menu.addItem(NSMenuItem.separator())
            menu.addItem(sectionHeader("Bio"))
            menu.addItem(wrappedBodyItem(PetConfig.personality))
        }

        if !PetConfig.backstory.isEmpty {
            menu.addItem(NSMenuItem.separator())
            menu.addItem(sectionHeader("Backstory"))
            menu.addItem(wrappedBodyItem(PetConfig.backstory))
        }

        menu.addItem(NSMenuItem.separator())

        let gravity = NSMenuItem(
            title: "Gravity",
            action: #selector(toggleGravity(_:)),
            keyEquivalent: ""
        )
        gravity.target = self
        gravity.state = (controller?.gravityEnabled ?? true) ? .on : .off
        menu.addItem(gravity)
        gravityItem = gravity

        let walk = NSMenuItem(
            title: "Walk",
            action: #selector(toggleWalk(_:)),
            keyEquivalent: ""
        )
        walk.target = self
        walk.state = (controller?.walkingEnabled ?? true) ? .on : .off
        menu.addItem(walk)
        walkItem = walk

        menu.addItem(NSMenuItem.separator())

        let quit = NSMenuItem(
            title: "Quit Deskpet",
            action: #selector(quit),
            keyEquivalent: "q"
        )
        quit.target = self
        menu.addItem(quit)

        item.menu = menu
        statusItem = item
    }

    /// Disabled "key:  value" info row. Monospaced so fields align.
    private func infoItem(key: String, value: String) -> NSMenuItem {
        let padded = key.padding(toLength: 8, withPad: " ", startingAt: 0)
        let item = NSMenuItem()
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(
                ofSize: NSFont.smallSystemFontSize,
                weight: .regular
            ),
            .foregroundColor: NSColor.secondaryLabelColor,
        ]
        item.attributedTitle = NSAttributedString(
            string: "\(padded)\(value)",
            attributes: attrs
        )
        item.toolTip = value
        item.isEnabled = false
        return item
    }

    /// Small section title (e.g. "Bio", "Backstory") rendered as a disabled row.
    private func sectionHeader(_ title: String) -> NSMenuItem {
        let item = NSMenuItem()
        item.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .font: NSFont.systemFont(
                    ofSize: NSFont.smallSystemFontSize,
                    weight: .semibold
                ),
                .foregroundColor: NSColor.labelColor,
            ]
        )
        item.isEnabled = false
        return item
    }

    /// Multi-line wrapped body text. NSMenuItem does not wrap attributed
    /// strings on its own, so we host an NSTextField inside a custom view.
    private func wrappedBodyItem(_ text: String, maxWidth: CGFloat = 280) -> NSMenuItem {
        let insetLeft: CGFloat = 20
        let insetRight: CGFloat = 12
        let insetTop: CGFloat = 2
        let insetBottom: CGFloat = 6
        let textWidth = maxWidth - insetLeft - insetRight

        let field = NSTextField(wrappingLabelWithString: text)
        field.font = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)
        field.textColor = .secondaryLabelColor
        field.preferredMaxLayoutWidth = textWidth
        let fitted = field.sizeThatFits(
            NSSize(width: textWidth, height: .greatestFiniteMagnitude)
        )
        field.frame = NSRect(x: insetLeft, y: insetBottom,
                             width: textWidth, height: fitted.height)

        let container = NSView(
            frame: NSRect(x: 0, y: 0, width: maxWidth,
                          height: fitted.height + insetTop + insetBottom)
        )
        container.addSubview(field)

        let item = NSMenuItem()
        item.view = container
        return item
    }

    /// Replaces the $HOME prefix with ~ for friendlier display.
    private func collapseHome(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if path == home { return "~" }
        if path.hasPrefix(home + "/") {
            return "~" + path.dropFirst(home.count)
        }
        return path
    }

    /// Centered idle sprite rendered as a menu item, shown above the Bio
    /// section as a visual header. Uses a custom view so the sprite can be
    /// larger than a standard menu row.
    private func spriteItem(side: CGFloat = 96, rowWidth: CGFloat = 280) -> NSMenuItem {
        let imageView = NSImageView(frame: NSRect(
            x: (rowWidth - side) / 2,
            y: 4,
            width: side,
            height: side
        ))
        imageView.image = Sprites.sheep(.idleA)
        imageView.imageScaling = .scaleProportionallyUpOrDown
        imageView.imageAlignment = .alignCenter

        let container = NSView(frame: NSRect(
            x: 0, y: 0, width: rowWidth, height: side + 8
        ))
        container.addSubview(imageView)

        let item = NSMenuItem()
        item.view = container
        return item
    }

    /// Idle sprite scaled down to menu-bar height. NSStatusBar gives us
    /// ~22pt of vertical space; we target 18pt to leave breathing room.
    /// The sprite canvas is 64×64 with the pet center-bottom anchored, so
    /// scaling uniformly to 18×18 preserves the look.
    private func trayIcon() -> NSImage {
        let source = Sprites.sheep(.idleA)
        let side: CGFloat = 18
        let icon = NSImage(size: NSSize(width: side, height: side))
        icon.lockFocus()
        source.draw(
            in: NSRect(x: 0, y: 0, width: side, height: side),
            from: .zero,
            operation: .sourceOver,
            fraction: 1.0
        )
        icon.unlockFocus()
        icon.isTemplate = false
        return icon
    }

    @objc private func toggleGravity(_ sender: NSMenuItem) {
        guard let controller = controller else { return }
        controller.gravityEnabled.toggle()
        sender.state = controller.gravityEnabled ? .on : .off
    }

    @objc private func toggleWalk(_ sender: NSMenuItem) {
        guard let controller = controller else { return }
        controller.walkingEnabled.toggle()
        sender.state = controller.walkingEnabled ? .on : .off
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}
