import AppKit

struct WindowSurface {
    let id: CGWindowID
    let rect: CGRect
    var topY: CGFloat { rect.maxY }
}

enum WindowSurfaces {
    static func current(excluding ourWindowNumber: Int) -> [WindowSurface] {
        let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        guard let list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
            return []
        }
        guard let primaryHeight = NSScreen.screens.first?.frame.height else {
            return []
        }

        var result: [WindowSurface] = []
        for info in list {
            guard let layer = info[kCGWindowLayer as String] as? Int, layer == 0 else { continue }
            guard let number = info[kCGWindowNumber as String] as? Int, number != ourWindowNumber else { continue }
            guard let boundsDict = info[kCGWindowBounds as String] as? NSDictionary else { continue }
            guard let cgRect = CGRect(dictionaryRepresentation: boundsDict) else { continue }
            guard cgRect.width >= 80, cgRect.height >= 20 else { continue }

            let appkitY = primaryHeight - cgRect.origin.y - cgRect.height
            let appkitRect = CGRect(x: cgRect.origin.x,
                                    y: appkitY,
                                    width: cgRect.width,
                                    height: cgRect.height)
            result.append(WindowSurface(id: CGWindowID(number), rect: appkitRect))
        }
        return result
    }
}
