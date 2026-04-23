import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: SheepController?
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = SheepController()
        controller?.start()
        installStatusItem()
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "pawprint.fill",
                                   accessibilityDescription: "Sheep")
        }
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Quit Sheep",
                                action: #selector(quit),
                                keyEquivalent: "q"))
        menu.items.last?.target = self
        item.menu = menu
        statusItem = item
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}
