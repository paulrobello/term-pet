// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Deskpet",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "Deskpet",
            path: "Sources/Deskpet",
            resources: [.copy("Resources")]
        )
    ]
)
