# HU Patcher — Kotlin Multiplatform (Stage 0)

This directory is the Kotlin Multiplatform implementation of the HU patcher.
It is intentionally separate from the Python runtime in the repository root:

- Python stays as the **build-time** tool for generating patch payloads from
  `update.bin`;
- Kotlin becomes the runtime for **Android APK** and **desktop** applications.

## Stage 0 scope

- KMP project skeleton: `core`, `ui`, `androidApp`, `desktopApp`;
- Compose Multiplatform empty window on desktop;
- Android application shell with the same `App()` composable;
- version catalog with Kotlin, Compose Multiplatform, sshj, BouncyCastle,
  kotlinx.serialization and coroutines;
- GitHub Actions CI skeleton;
- no patch logic, SSH or UI screens yet.

## Prerequisites

- JDK 21;
- Android Studio with Android SDK 35 + Build-Tools 35;
- WiX Toolset 3.14 for Windows EXE/MSI packaging;
- Gradle Wrapper is included, so a separate Gradle install is not required.

On Windows:

```powershell
.\gradlew.bat :desktopApp:run
.\gradlew.bat :androidApp:assembleDebug
```

On Linux/macOS:

```sh
./gradlew :desktopApp:run
./gradlew :androidApp:assembleDebug
```

Tests:

```sh
./gradlew :core:allTests
```

Desktop packaging (current OS):

```sh
./gradlew :desktopApp:packageDistributionForCurrentOS
```

Windows `.exe`/`.msi` packaging requires WiX 3.14 in `PATH`.

## Layout

```text
core/        shared Kotlin core (KMP: Android + desktop JVM)
ui/          shared Compose Multiplatform UI
androidApp/  Android application host
desktopApp/  desktop JVM host
assets/      patch/bootstrap assets (filled in Stage 2/4)
tools/       placeholders for build-time scripts
```

## Next stages

1. SSH/keygen spike with sshj.
2. Patch manifest/state model.
3. SSH transport and SCP upload.
4. Remote apply/rollback/update scripts.
5. Status/versioning.
6. Operations and manual reboot.
7. Bootstrap.
8. Logging.
9. Desktop UI.
10. Android UI + foreground service.
11. Packaging.
12. Parity/mock tests.
13. Real HU integration.
14. Hardening.
15. Release.
