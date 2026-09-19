// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "HumansCapture",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "HumansCaptureCore", targets: ["HumansCaptureCore"]),
    ],
    targets: [
        .target(name: "HumansCaptureCore"),
        .testTarget(
            name: "HumansCaptureCoreTests",
            dependencies: ["HumansCaptureCore"]
        ),
    ]
)
