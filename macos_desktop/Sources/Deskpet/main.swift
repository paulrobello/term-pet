import AppKit
import Darwin

/// Context passed in from the launching process (tpet or standalone user).
/// Defaults cover the standalone case where no flags are supplied.
struct PetContext {
    var socketPath: String
    var session: String
    var pwd: String
    var artDir: String?
    var profilePath: String?
    var configDir: String?
}

private func parseArgs(_ argv: [String]) -> PetContext {
    let defaultSocket = PetConfig.directoryURL
        .appendingPathComponent("display.sock", isDirectory: false)
        .path

    var socketPath = defaultSocket
    var session: String? = nil
    var pwd = FileManager.default.currentDirectoryPath
    var artDir: String? = nil
    var profilePath: String? = nil
    var configDir: String? = nil

    var i = 1
    while i < argv.count {
        let arg = argv[i]
        let hasValue = i + 1 < argv.count
        switch arg {
        case "--socket":
            if hasValue { socketPath = argv[i + 1]; i += 2 } else { i += 1 }
        case "--session":
            if hasValue { session = argv[i + 1]; i += 2 } else { i += 1 }
        case "--pwd":
            if hasValue { pwd = argv[i + 1]; i += 2 } else { i += 1 }
        case "--art-dir":
            if hasValue { artDir = argv[i + 1]; i += 2 } else { i += 1 }
        case "--profile":
            if hasValue { profilePath = argv[i + 1]; i += 2 } else { i += 1 }
        case "--config-dir":
            if hasValue { configDir = argv[i + 1]; i += 2 } else { i += 1 }
        default:
            i += 1
        }
    }

    let label = session ?? (pwd as NSString).lastPathComponent
    return PetContext(
        socketPath: socketPath,
        session: label.isEmpty ? "deskpet" : label,
        pwd: pwd,
        artDir: artDir,
        profilePath: profilePath,
        configDir: configDir
    )
}

let ctx = parseArgs(CommandLine.arguments)
// Apply path overrides BEFORE ensureSeeded (which reads artURL) and before
// any PetConfig scalar (petName, personality, …) triggers profileContent's
// lazy init.
if let dir = ctx.artDir {
    PetConfig.artURL = URL(fileURLWithPath: dir, isDirectory: true)
}
if let path = ctx.profilePath {
    PetConfig.profileURL = URL(fileURLWithPath: path)
}
PetConfig.ensureSeeded()
PetConfig.socketPath = ctx.socketPath

// Terminate cleanly on SIGTERM. NSApp.terminate exits via `exit()` which
// bypasses Swift deinit, so we unlink the socket file proactively here
// (`unlink` is async-signal-safe). The dispatch to main drives a clean
// NSApp shutdown so windows close.
signal(SIGTERM) { _ in
    PetConfig.socketPath.withCString { cstr in
        _ = unlink(cstr)
    }
    DispatchQueue.main.async { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate(context: ctx)
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
