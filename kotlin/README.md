# HU Patcher — Kotlin Multiplatform (Stage 1)

This directory is the Kotlin Multiplatform implementation of the HU patcher.
It is intentionally separate from the Python runtime in the repository root:

- Python stays as the **build-time** tool for generating patch payloads from
  `update.bin`;
- Kotlin becomes the runtime for **Android APK** and **desktop** applications.

## Stage 0 scope

- KMP project skeleton: `core`, `ui`, `androidApp`, `desktopApp`;
- Compose Multiplatform empty window on desktop;
- Android application shell with the same `App()` composable;
- GitHub Actions CI skeleton.

## Stage 1 scope

- shared JVM SSH layer in `core/src/jvmSharedMain` used by Android and desktop;
- sshj transport with password and public-key auth;
- configurable connect/command/upload timeouts;
- host-key logging and optional SHA256 pinning;
- Ed25519 key generation with an OpenSSH `.pub` line;
- legacy SCP upload (no SFTP) and a `cat >` fallback;
- `spike` JVM CLI for real-HU measurements;
- Android instrumentation test for keygen and root/SCP validation.

The private key is stored as unencrypted PKCS#8 PEM for sshj. The `.pub` file is
a normal OpenSSH line usable by `bootstrap/v2/install.sh`. The public key is
always derived from the private key; a stale or mismatched `.pub` file is
rejected.

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

## SSH spike

Generate the patcher's own key (default `id_ed25519` in the output directory):

```sh
./gradlew :spike:run --args="keygen --out ~/.ssh --name hu_patcher_ed25519"
```

Run the spike against a real head unit:

```sh
./gradlew :spike:run --args="run \
  --host 192.168.1.100 \
  --root-key ~/.ssh/hu_patcher_ed25519 \
  --upload ../patches/C-callfix/svp-callfix \
  --upload ../patches/B-touch-while-driving/svp.svc.engineering.patched"
```

Options worth knowing:

- `--media-password TEXT` — media password (empty by default);
- `--skip-media` — test root only;
- `--upload-mode auto|scp|cat` — `auto` tries SCP and falls back to `cat >`;
- `--fingerprint SHA256:...` — pin the host key;
- `--connect-timeout-ms`, `--command-timeout-ms`, `--upload-timeout-ms`.

The spike logs command exit codes, timings, host-key fingerprints and upload
sha256 verification. It never prints the media password or private key.

Recommended Stage 1 run against a real HU:

1. generate the patch payloads with `sh patches/build.sh` first, then
   `--upload` the 49 KB `patches/C-callfix/svp-callfix` and the 1.2 MB
   `patches/B-touch-while-driving/svp.svc.engineering.patched`;
2. run with `--upload-mode scp` first;
3. run once with `--upload-mode cat` to confirm the fallback;
4. record the host-key SHA256 and use `--fingerprint` afterwards.

## Local integration validation

The Stage 1 spike was verified on desktop JVM against a local Alpine sshd
container with:

- a `media` user and empty password (`PermitEmptyPasswords yes`);
- root public-key auth from the generated key;
- the OpenSSH `scp` binary present on the server.

Result: media/root auth, `id -u`, `boot_id`, 49 KB and 1.2 MB SCP uploads and
sha256 verification all passed; `--fingerprint` accepted the correct key and
rejected a wrong one; with `scp` removed from the server, `auto` fell back to
`cat >` successfully.

Real-HU validation is still required. Expected delays: a unit without Fast
Login can take ~25 s to open an SSH session, so keep the default command
timeout and do not treat a slow probe as a wrong key.

## Android instrumentation test

The test class is
`androidApp/src/androidTest/.../SshSpikeInstrumentedTest.kt`.

It always checks Ed25519 keygen on Android. To also test root/SCP against a HU
(device on the same network), pass the instrumentation arguments:

```sh
adb shell am instrument -w \
  -e huHost 192.168.1.100 \
  -e huPort 22 \
  -e huRootKeyPemBase64 "$(base64 -w0 "$HOME/.ssh/hu_patcher_ed25519")" \
  -e huTestUpload 1 \
  com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
```

On Windows PowerShell:

```powershell
$key = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes("$HOME\.ssh\hu_patcher_ed25519"))
adb shell am instrument -w `
  -e huHost 192.168.1.100 `
  -e huRootKeyPemBase64 $key `
  -e huTestUpload 1 `
  com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
```

Without `huHost`/`huRootKeyPemBase64`, the network test is reported as skipped
and only keygen runs.

## Layout

```text
core/        shared Kotlin core (KMP: Android + desktop JVM)
  src/commonMain/       platform-neutral models and tests
  src/jvmSharedMain/    SSH, SCP, keygen, hashing (Android + desktop)
  src/desktopTest/      JVM-only tests
ui/          shared Compose Multiplatform UI
androidApp/  Android application host + instrumentation test
desktopApp/  desktop JVM host
spike/       Stage 1 JVM CLI (not shipped in the app)
assets/      patch/bootstrap assets (filled in Stage 2/4)
tools/       placeholders for build-time scripts
```

## Next stages

1. SSH/keygen spike with sshj. **Implemented; real-HU run pending.**
2. Patch manifest/state model.
3. SSH transport and SCP upload hardening.
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
