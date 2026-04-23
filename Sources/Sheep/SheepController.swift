import AppKit

enum SheepState {
    case idle
    case walking
    case sleeping
    case dragging
    case falling
    case landed
    case reacting
}

final class SheepController {
    private let window: SheepWindow
    private let view: SheepView
    private let bubble = CommentBubbleWindow()
    private var commentBus: CommentBus?

    private var state: SheepState = .idle
    private var facesRight: Bool = true
    private var frameCounter = 0
    private var stateElapsed = 0
    private var stateDuration = 60

    private var velocity = CGPoint.zero
    private var dragOffset = CGPoint.zero

    private var currentSurfaceID: CGWindowID? = nil

    /// Wall-clock deadline for the "awake" period. While `Date() < awakeUntil`
    /// the pet will not fall asleep spontaneously; only walks and idles.
    private var awakeUntil: Date? = nil
    private let awakeDuration: TimeInterval = 10.0

    private let frames: [NSImage]
    private var timer: Timer?

    private let walkSpeed: CGFloat = 1.6
    private let gravity: CGFloat = 1.6
    private let landedDurationFrames = 30
    private let reactingDurationFrames = 120  // 4s at 30 Hz
    private let stunVelocityThreshold: CGFloat = 36

    private let idleSwapEveryFrames = 30
    private let blinkPeriodFrames = 90
    private let blinkLengthFrames = 6
    private let blinkHalfLength = 3

    init() {
        var loaded: [NSImage] = []
        loaded.reserveCapacity(SpriteFrame.allCases.count)
        for frame in SpriteFrame.allCases {
            loaded.append(Sprites.sheep(frame))
        }
        self.frames = loaded

        self.window = SheepWindow(size: Sprites.size)
        self.view = SheepView(frame: NSRect(origin: .zero, size: Sprites.size))
        window.contentView = view

        if let screen = NSScreen.main {
            let vf = screen.visibleFrame
            let x = vf.midX - Sprites.size.width / 2
            let y = vf.minY
            window.setFrameOrigin(NSPoint(x: x, y: y))
        }

        view.onMouseDown = { [weak self] _ in self?.beginDrag() }
        view.onMouseDragged = { [weak self] pt in self?.updateDrag(mouse: pt) }
        view.onMouseUp = { [weak self] _ in self?.endDrag() }

        updateSprite()
        window.orderFront(nil)
    }

    func start() {
        let t = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.tick()
        }
        RunLoop.main.add(t, forMode: .common)
        self.timer = t
        enter(.idle)

        let socketPath = PetConfig.directoryURL
            .appendingPathComponent("display.sock", isDirectory: false)
            .path
        let bus = CommentBus(socketPath: socketPath) { [weak self] event in
            self?.handleRemote(event)
        }
        bus.start()
        self.commentBus = bus
    }

    // MARK: - Tick

    private func tick() {
        frameCounter += 1
        stateElapsed += 1

        let surfaces = WindowSurfaces.current(excluding: window.windowNumber)

        switch state {
        case .idle:
            if !maintainSurface(surfaces: surfaces) {
                enter(.falling)
            } else if stateElapsed >= stateDuration {
                transitionFromIdle()
            }
        case .walking:
            if !maintainSurface(surfaces: surfaces) {
                enter(.falling)
            } else {
                walkStep(surfaces: surfaces)
                if stateElapsed >= stateDuration { enter(.idle) }
            }
        case .sleeping:
            if !maintainSurface(surfaces: surfaces) {
                enter(.falling)
            } else if stateElapsed >= stateDuration {
                enter(.idle)
            }
        case .dragging:
            break
        case .falling:
            fallStep(surfaces: surfaces)
        case .landed:
            if stateElapsed >= landedDurationFrames {
                enter(.idle)
            }
        case .reacting:
            if stateElapsed >= reactingDurationFrames {
                enter(.idle)
            }
        }

        updateSprite()
        repositionBubble()
    }

    // MARK: - Surface helpers

    private func maintainSurface(surfaces: [WindowSurface]) -> Bool {
        guard let id = currentSurfaceID else { return true }
        guard let surface = surfaces.first(where: { $0.id == id }) else {
            currentSurfaceID = nil
            return false
        }
        var origin = window.frame.origin
        origin.y = surface.topY
        let minX = origin.x
        let maxX = origin.x + Sprites.size.width
        if maxX <= surface.rect.minX || minX >= surface.rect.maxX {
            currentSurfaceID = nil
            return false
        }
        window.setFrameOrigin(origin)
        return true
    }

    private func walkStep(surfaces: [WindowSurface]) {
        let dx: CGFloat = facesRight ? walkSpeed : -walkSpeed
        var origin = window.frame.origin
        origin.x += dx

        if let id = currentSurfaceID,
           let surface = surfaces.first(where: { $0.id == id }) {
            origin.y = surface.topY
            let minX = origin.x
            let maxX = origin.x + Sprites.size.width
            if maxX <= surface.rect.minX || minX >= surface.rect.maxX {
                window.setFrameOrigin(origin)
                currentSurfaceID = nil
                enter(.falling)
                return
            }
        } else if let vf = NSScreen.main?.visibleFrame {
            if origin.x < vf.minX {
                origin.x = vf.minX
                facesRight = true
            } else if origin.x + Sprites.size.width > vf.maxX {
                origin.x = vf.maxX - Sprites.size.width
                facesRight = false
            }
        }
        window.setFrameOrigin(origin)
    }

    private func fallStep(surfaces: [WindowSurface]) {
        velocity.y -= gravity

        var origin = window.frame.origin
        let prevY = origin.y
        origin.x += velocity.x
        origin.y += velocity.y

        if let vf = NSScreen.main?.visibleFrame {
            origin.x = max(vf.minX, min(vf.maxX - Sprites.size.width, origin.x))
        }

        let ground = NSScreen.main?.visibleFrame.minY ?? 0
        let centerX = origin.x + Sprites.size.width / 2

        var best: WindowSurface? = nil
        for s in surfaces {
            guard centerX >= s.rect.minX, centerX <= s.rect.maxX else { continue }
            guard s.topY > ground, s.topY <= prevY, s.topY >= origin.y else { continue }
            if let b = best {
                if s.topY > b.topY { best = s }
            } else {
                best = s
            }
        }

        if let landing = best {
            let impact = -velocity.y
            origin.y = landing.topY
            window.setFrameOrigin(origin)
            velocity = .zero
            currentSurfaceID = landing.id
            wake()
            enter(impact >= stunVelocityThreshold ? .landed : .idle)
            return
        }

        if origin.y <= ground {
            let impact = -velocity.y
            origin.y = ground
            window.setFrameOrigin(origin)
            velocity = .zero
            currentSurfaceID = nil
            wake()
            enter(impact >= stunVelocityThreshold ? .landed : .idle)
            return
        }
        window.setFrameOrigin(origin)
    }

    // MARK: - Awake tracking

    private var isAwake: Bool {
        guard let until = awakeUntil else { return false }
        return until > Date()
    }

    private func wake() {
        awakeUntil = Date().addingTimeInterval(awakeDuration)
    }

    // MARK: - State transitions

    private func transitionFromIdle() {
        if isAwake {
            // Awake: never sleep spontaneously. Walk or stay idle.
            if Double.random(in: 0..<1) < 0.6 {
                facesRight = Bool.random()
                enter(.walking)
            } else {
                enter(.idle)
            }
        } else {
            let r = Double.random(in: 0..<1)
            if r < 0.15 {
                enter(.sleeping)
            } else {
                facesRight = Bool.random()
                enter(.walking)
            }
        }
    }

    private func enter(_ new: SheepState) {
        state = new
        stateElapsed = 0
        switch new {
        case .idle:     stateDuration = Int.random(in: 30...120)
        case .walking:  stateDuration = Int.random(in: 90...300)
        case .sleeping: stateDuration = Int.random(in: 120...360)
        case .dragging: stateDuration = Int.max
        case .falling:  stateDuration = Int.max
        case .landed:   stateDuration = landedDurationFrames
        case .reacting: stateDuration = reactingDurationFrames
        }
    }

    private func updateSprite() {
        let frame: SpriteFrame
        switch state {
        case .idle:
            frame = pickIdleFrame()
        case .walking:
            frame = (frameCounter / 6) % 2 == 0 ? .walkA : .walkB
        case .sleeping:
            frame = .sleep
        case .dragging, .reacting:
            frame = .excited
        case .falling:
            frame = .fall
        case .landed:
            frame = .stunned
        }
        view.image = frames[frame.rawValue]
        view.mirrored = !facesRight
    }

    private func pickIdleFrame() -> SpriteFrame {
        let inCycle = frameCounter % blinkPeriodFrames
        if inCycle < blinkLengthFrames {
            return inCycle < blinkHalfLength ? .blinkA : .blinkB
        }
        return (frameCounter / idleSwapEveryFrames) % 2 == 0 ? .idleA : .idleB
    }

    // MARK: - Remote events

    private func handleRemote(_ event: CommentBus.Event) {
        if let s = event.state {
            switch s {
            case .idle:
                if state == .sleeping { enter(.idle) }
            case .reacting:
                enter(.reacting)
            case .sleeping:
                if !isAwake { enter(.sleeping) }
            }
        }
        if let comment = event.comment, !comment.isEmpty {
            bubble.show(text: comment, above: window)
        }
    }

    private func repositionBubble() {
        bubble.reposition(above: window)
    }

    // MARK: - Drag

    private func beginDrag() {
        wake()
        enter(.dragging)
        currentSurfaceID = nil
        let mouse = NSEvent.mouseLocation
        let origin = window.frame.origin
        dragOffset = CGPoint(x: mouse.x - origin.x, y: mouse.y - origin.y)
        velocity = .zero
    }

    private func updateDrag(mouse: NSPoint) {
        let newOrigin = NSPoint(x: mouse.x - dragOffset.x,
                                y: mouse.y - dragOffset.y)
        window.setFrameOrigin(newOrigin)
    }

    private func endDrag() {
        wake()
        velocity = .zero
        enter(.falling)
    }
}
