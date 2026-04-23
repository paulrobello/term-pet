// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Sheep",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "Sheep",
            path: "Sources/Sheep",
            resources: [.copy("Resources")]
        )
    ]
)
