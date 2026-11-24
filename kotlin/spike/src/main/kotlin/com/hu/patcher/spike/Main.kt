package com.hu.patcher.spike

import com.hu.patcher.core.ssh.HashUtils
import com.hu.patcher.core.ssh.HostKeyPolicy
import com.hu.patcher.core.ssh.SshConfig
import com.hu.patcher.core.ssh.SshCredentials
import com.hu.patcher.core.ssh.SshException
import com.hu.patcher.core.ssh.SshKeyGenerator
import com.hu.patcher.core.ssh.SshjTransport
import com.hu.patcher.core.ssh.shellQuote
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.Locale
import kotlin.system.exitProcess

private enum class UploadMode(val label: String) {
    AUTO("auto"),
    SCP("scp"),
    CAT("cat"),
}

private data class RunOptions(
    val host: String,
    val port: Int,
    val mediaPassword: CharArray,
    val skipMedia: Boolean,
    val rootKey: Path,
    val rootPublicKey: Path?,
    val uploads: List<Path>,
    val remoteDir: String,
    val uploadMode: UploadMode,
    val connectTimeout: Duration,
    val commandTimeout: Duration,
    val uploadTimeout: Duration,
    val fingerprint: String?,
)

fun main(args: Array<String>) {
    if (args.isEmpty() || args[0] == "-h" || args[0] == "--help") {
        printUsage()
        return
    }

    val ok = try {
        when (args[0].lowercase(Locale.ROOT)) {
            "keygen" -> keygen(args.drop(1))
            "run" -> runSpike(args.drop(1))
            else -> throw IllegalArgumentException("unknown command: ${args[0]}")
        }
    } catch (e: IllegalArgumentException) {
        System.err.println("ERROR: ${e.message}")
        printUsage()
        false
    } catch (e: Exception) {
        System.err.println("ERROR: ${e.message}")
        false
    }

    if (!ok) exitProcess(1)
}

private fun keygen(args: List<String>): Boolean {
    var outDir = Path.of(".")
    var name = "id_ed25519"
    var comment = "hu-patcher"
    var overwrite = false

    var i = 0
    while (i < args.size) {
        when (args[i]) {
            "--out" -> {
                outDir = userPath(requireValue(args, i, "--out"))
                i += 2
            }
            "--name" -> {
                name = requireValue(args, i, "--name")
                i += 2
            }
            "--comment" -> {
                comment = requireValue(args, i, "--comment")
                i += 2
            }
            "--overwrite" -> {
                overwrite = true
                i++
            }
            "-h", "--help" -> {
                printUsage()
                return true
            }
            else -> throw IllegalArgumentException("unknown keygen option: ${args[i]}")
        }
    }

    val generated = SshKeyGenerator.generateEd25519(
        directory = outDir,
        name = name,
        comment = comment,
        overwrite = overwrite,
    )
    println("private key : ${generated.privateKey.toAbsolutePath()}")
    println("public key  : ${generated.publicKey.toAbsolutePath()}")
    println("fingerprint : ${generated.fingerprint}")
    println("public line : ${generated.publicKeyLine}")
    return true
}

private fun runSpike(args: List<String>): Boolean {
    var host: String? = null
    var port = SshConfig.DEFAULT_PORT
    var mediaPassword = CharArray(0)
    var skipMedia = false
    var rootKey: Path? = null
    var rootPublicKey: Path? = null
    var remoteDir = "/tmp/hu-patcher-spike"
    var uploadMode = UploadMode.AUTO
    var connectTimeout = Duration.ofSeconds(8)
    var commandTimeout = Duration.ofSeconds(120)
    var uploadTimeout = Duration.ofSeconds(180)
    var fingerprint: String? = null
    val uploads = mutableListOf<Path>()

    var i = 0
    while (i < args.size) {
        when (args[i]) {
            "--host" -> {
                host = requireValue(args, i, "--host")
                i += 2
            }
            "--port" -> {
                port = requireValue(args, i, "--port").toInt()
                i += 2
            }
            "--media-password" -> {
                mediaPassword = requireValue(args, i, "--media-password").toCharArray()
                i += 2
            }
            "--skip-media" -> {
                skipMedia = true
                i++
            }
            "--root-key" -> {
                rootKey = userPath(requireValue(args, i, "--root-key"))
                i += 2
            }
            "--root-pub" -> {
                rootPublicKey = userPath(requireValue(args, i, "--root-pub"))
                i += 2
            }
            "--upload" -> {
                uploads.add(userPath(requireValue(args, i, "--upload")))
                i += 2
            }
            "--remote-dir" -> {
                remoteDir = requireValue(args, i, "--remote-dir")
                i += 2
            }
            "--upload-mode" -> {
                val value = requireValue(args, i, "--upload-mode").lowercase(Locale.ROOT)
                uploadMode = UploadMode.entries.firstOrNull { it.label == value }
                    ?: throw IllegalArgumentException(
                        "--upload-mode must be auto, scp or cat"
                    )
                i += 2
            }
            "--connect-timeout-ms" -> {
                connectTimeout = millis(requireValue(args, i, "--connect-timeout-ms"))
                i += 2
            }
            "--command-timeout-ms" -> {
                commandTimeout = millis(requireValue(args, i, "--command-timeout-ms"))
                i += 2
            }
            "--upload-timeout-ms" -> {
                uploadTimeout = millis(requireValue(args, i, "--upload-timeout-ms"))
                i += 2
            }
            "--fingerprint" -> {
                fingerprint = requireValue(args, i, "--fingerprint")
                i += 2
            }
            "-h", "--help" -> {
                printUsage()
                return true
            }
            else -> throw IllegalArgumentException("unknown run option: ${args[i]}")
        }
    }

    val targetHost = host ?: throw IllegalArgumentException("--host is required for run")
    val key = rootKey ?: throw IllegalArgumentException("--root-key is required for run")
    if (!Files.isRegularFile(key)) {
        throw IllegalArgumentException("root key not found: $key")
    }
    val publicKey = rootPublicKey ?: key.resolveSibling(key.fileName.toString() + ".pub")
    if (rootPublicKey != null && !Files.isRegularFile(publicKey)) {
        throw IllegalArgumentException("root public key not found: $publicKey")
    }
    uploads.forEach {
        if (!Files.isRegularFile(it)) {
            throw IllegalArgumentException("upload file not found: $it")
        }
    }

    val config = SshConfig(
        host = targetHost,
        port = port,
        connectTimeout = connectTimeout,
        commandTimeout = commandTimeout,
        uploadTimeout = uploadTimeout,
    )
    val policy = fingerprint?.let { HostKeyPolicy.Pinned(it) } ?: HostKeyPolicy.AcceptAndLog
    var failures = 0

    if (!skipMedia) {
        val ok = testAccount(
            label = "media",
            config = config,
            credentials = SshCredentials.Password(mediaPassword),
            policy = policy,
            uploads = uploads,
            remoteDir = "$remoteDir/media",
            uploadMode = uploadMode,
            readBootId = false,
        )
        if (!ok) failures++
    } else {
        println("[media] skipped")
    }

    val rootOk = testAccount(
        label = "root",
        config = config,
        credentials = SshCredentials.KeyFile(key, publicKey),
        policy = policy,
        uploads = uploads,
        remoteDir = "$remoteDir/root",
        uploadMode = uploadMode,
        readBootId = true,
    )
    if (!rootOk) failures++

    println()
    if (failures == 0) {
        println("SPIKE: OK")
        return true
    }
    println("SPIKE: FAILED ($failures account(s))")
    return false
}

private fun testAccount(
    label: String,
    config: SshConfig,
    credentials: SshCredentials,
    policy: HostKeyPolicy,
    uploads: List<Path>,
    remoteDir: String,
    uploadMode: UploadMode,
    readBootId: Boolean,
): Boolean {
    val transport = SshjTransport(
        config = config,
        username = label,
        credentials = credentials,
        hostKeyPolicy = policy,
        logger = { println("[$label] $it") },
    )
    return try {
        transport.use {
            val connectStarted = System.nanoTime()
            transport.connect()
            println("[$label] connect OK in ${elapsed(connectStarted)}")

            val probeCommand = if (label == "root") "id -u" else "id"
            val probe = transport.exec(probeCommand)
            if (!probe.isSuccess) {
                println("[$label] ERROR: '$probeCommand' rc=${probe.exitStatus}: ${probe.stderr.trim()}")
                return false
            }
            val probeOut = probe.stdout.trim()
            println("[$label] $probeCommand -> $probeOut (${elapsedSeconds(probe.elapsed)})")
            if (label == "root" && probeOut != "0") {
                println("[$label] ERROR: expected uid 0, got '$probeOut'")
                return false
            }

            if (readBootId) {
                val boot = transport.exec("cat /proc/sys/kernel/random/boot_id 2>/dev/null")
                val bootId = boot.stdout.trim()
                if (!boot.isSuccess || bootId.isEmpty()) {
                    println("[$label] ERROR: cannot read boot_id: ${boot.stderr.trim()}")
                    return false
                }
                println("[$label] boot_id -> $bootId")
            }

            if (uploads.isNotEmpty()) {
                val mkdir = transport.exec("mkdir -p ${shellQuote(remoteDir)}")
                if (!mkdir.isSuccess) {
                    println("[$label] ERROR: mkdir $remoteDir failed: ${mkdir.stderr.trim()}")
                    return false
                }
                for (local in uploads) {
                    if (!uploadAndVerify(transport, label, local, remoteDir, uploadMode)) {
                        return false
                    }
                }
            }
            true
        }
    } catch (e: SshException) {
        println("[$label] ERROR: ${e.message}")
        false
    } catch (e: Exception) {
        println("[$label] ERROR: ${e.javaClass.simpleName}: ${e.message}")
        false
    }
}

private fun uploadAndVerify(
    transport: SshjTransport,
    label: String,
    local: Path,
    remoteDir: String,
    mode: UploadMode,
): Boolean {
    val fileName = local.fileName.toString()
    val remotePath = "$remoteDir/$fileName"
    val localHash = HashUtils.sha256File(local)
    val started = System.nanoTime()

    var used = mode
    try {
        when (mode) {
            UploadMode.SCP -> transport.upload(local, remotePath)
            UploadMode.CAT -> transport.uploadViaCat(local, remotePath)
            UploadMode.AUTO -> {
                try {
                    transport.upload(local, remotePath)
                } catch (e: SshException) {
                    println("[$label] SCP failed: ${e.message}")
                    println("[$label] falling back to cat > $remotePath")
                    transport.uploadViaCat(local, remotePath)
                    used = UploadMode.CAT
                }
            }
        }
    } catch (e: SshException) {
        println("[$label] ERROR: upload $fileName failed: ${e.message}")
        return false
    }

    val remoteHash = remoteSha256(transport, remotePath)
    if (remoteHash == null) {
        println("[$label] ERROR: cannot hash remote file $remotePath")
        return false
    }
    if (!remoteHash.equals(localHash, ignoreCase = true)) {
        println("[$label] ERROR: hash mismatch for $fileName")
        println("[$label]   local : $localHash")
        println("[$label]   remote: $remoteHash")
        return false
    }

    println(
        "[$label] upload $fileName ${readableSize(Files.size(local))} via ${used.label} " +
            "sha256 OK in ${elapsed(started)}"
    )
    return true
}

private fun remoteSha256(transport: SshjTransport, remotePath: String): String? {
    val quoted = shellQuote(remotePath)
    val command = buildString {
        append("if command -v sha256sum >/dev/null 2>&1; then sha256sum -- $quoted; ")
        append("elif command -v busybox >/dev/null 2>&1; then busybox sha256sum -- $quoted; ")
        append("elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 $quoted; ")
        append("else echo PATCHER_NO_HASH >&2; exit 127; fi")
    }
    val result = transport.exec(command)
    if (!result.isSuccess) {
        println("[remote] sha256 failed: ${result.stderr.trim()}")
        return null
    }
    val line = result.stdout.lineSequence().map { it.trim() }.firstOrNull { it.isNotEmpty() }
        ?: return null
    val hash = if (line.contains('=')) {
        line.substringAfterLast('=')
    } else {
        line.substringBefore(' ')
    }.trim().lowercase(Locale.ROOT)
    return hash.takeIf { it.matches(Regex("[0-9a-f]{64}")) }
}

private fun requireValue(args: List<String>, index: Int, flag: String): String {
    if (index + 1 >= args.size) {
        throw IllegalArgumentException("$flag needs a value")
    }
    return args[index + 1]
}

private fun userPath(value: String): Path {
    val home = System.getProperty("user.home")
    return when {
        value == "~" -> Path.of(home)
        value.startsWith("~/") || value.startsWith("~\\") -> Path.of(home).resolve(value.substring(2))
        else -> Path.of(value)
    }
}

private fun millis(value: String): Duration {
    val parsed = value.toLongOrNull()
        ?: throw IllegalArgumentException("invalid timeout: $value")
    if (parsed <= 0) throw IllegalArgumentException("timeout must be positive: $value")
    return Duration.ofMillis(parsed)
}

private fun elapsed(startedNanos: Long): String {
    return elapsedSeconds(Duration.ofNanos(System.nanoTime() - startedNanos))
}

private fun elapsedSeconds(duration: Duration): String {
    return String.format(Locale.ROOT, "%.2f s", duration.toMillis() / 1000.0)
}

private fun readableSize(bytes: Long): String {
    return if (bytes >= 1024 * 1024) {
        String.format(Locale.ROOT, "%.1f MiB", bytes / (1024.0 * 1024.0))
    } else {
        String.format(Locale.ROOT, "%.1f KiB", bytes / 1024.0)
    }
}

private fun printUsage() {
    println(
        """
        HU Patcher SSH spike (Stage 1)

        Usage:
          hu-ssh-spike keygen [--out DIR] [--name NAME] [--comment TEXT] [--overwrite]
          hu-ssh-spike run --host HOST --root-key FILE [options]

        keygen:
          --out DIR              output directory (default: current directory)
          --name NAME            key file name (default: id_ed25519)
          --comment TEXT         OpenSSH public key comment (default: hu-patcher)
          --overwrite            replace existing key files

        run:
          --host HOST            head unit address (required)
          --port PORT            SSH port (default: 22)
          --media-password TEXT  media password (default: empty)
          --skip-media           do not test the media account
          --root-key FILE        private key for root (required)
          --root-pub FILE        public key sidecar (default: FILE.pub)
          --upload FILE          file to upload; repeat for several files
          --remote-dir DIR       remote test directory (default: /tmp/hu-patcher-spike)
          --upload-mode MODE     auto, scp or cat (default: auto)
          --connect-timeout-ms N connect timeout (default: 8000)
          --command-timeout-ms N command timeout (default: 120000)
          --upload-timeout-ms N  upload timeout (default: 180000)
          --fingerprint FP       pin host key SHA256 fingerprint
          -h, --help             show this help

        Exit code 0 means both accounts, commands and uploads passed.
        """.trimIndent()
    )
}
