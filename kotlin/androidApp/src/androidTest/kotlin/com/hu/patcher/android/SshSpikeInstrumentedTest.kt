package com.hu.patcher.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.hu.patcher.core.ssh.HashUtils
import com.hu.patcher.core.ssh.HostKeyPolicy
import com.hu.patcher.core.ssh.SshConfig
import com.hu.patcher.core.ssh.SshCredentials
import com.hu.patcher.core.ssh.SshKeyGenerator
import com.hu.patcher.core.ssh.SshjTransport
import com.hu.patcher.core.ssh.shellQuote
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.Base64
import java.util.Locale

/**
 * Stage 1 Android validation.
 *
 * The keygen test always runs. The SSH/SCP test is skipped unless the runner is
 * started with `-e huHost ...` and a base64-encoded root private key:
 *
 * ```
 * adb shell am instrument -w \
 *   -e huHost 192.168.1.100 \
 *   -e huPort 22 \
 *   -e huRootKeyPemBase64 "$(base64 -w0 id_ed25519)" \
 *   -e huTestUpload 1 \
 *   com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
 * ```
 */
@RunWith(AndroidJUnit4::class)
class SshSpikeInstrumentedTest {

    @Test
    fun generatesEd25519KeyOnAndroid() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "ssh-spike/keygen").toPath()
        Files.createDirectories(dir)

        val generated = SshKeyGenerator.generateEd25519(dir, overwrite = true)
        assertTrue(generated.publicKeyLine.startsWith("ssh-ed25519 "))
        assertTrue(generated.fingerprint.startsWith("SHA256:"))

        val loaded = SshKeyGenerator.loadEd25519(generated.privateKey)
        assertEquals(generated.fingerprint, SshKeyGenerator.publicKeyFingerprint(loaded.public))
    }

    @Test
    fun connectsToHuAndUploadsWhenConfigured() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        val host = args.getString("huHost")
        val rootKeyBase64 = args.getString("huRootKeyPemBase64")
        Assume.assumeTrue(
            "set -e huHost and -e huRootKeyPemBase64 to run the real-HU test",
            host != null && rootKeyBase64 != null,
        )

        val context = instrumentation.targetContext
        val port = args.getString("huPort")?.toIntOrNull() ?: 22
        val timeoutMs = args.getString("huCommandTimeoutMs")?.toLongOrNull() ?: 120_000L
        val uploadEnabled = args.getString("huTestUpload") == "1"
        val remoteBase = args.getString("huRemoteDir") ?: "/tmp/hu-patcher-spike/android"

        val keyFile = File(context.filesDir, "ssh-spike/root-key.pem")
        keyFile.parentFile?.mkdirs()
        Files.write(keyFile.toPath(), Base64.getDecoder().decode(rootKeyBase64))

        val config = SshConfig(
            host = host!!,
            port = port,
            connectTimeout = Duration.ofSeconds(8),
            commandTimeout = Duration.ofMillis(timeoutMs),
            uploadTimeout = Duration.ofMillis(timeoutMs * 2),
        )

        SshjTransport(
            config = config,
            username = "root",
            credentials = SshCredentials.KeyFile(keyFile.toPath()),
            hostKeyPolicy = HostKeyPolicy.AcceptAndLog,
            logger = { println("[android] $it") },
        ).use { transport ->
            transport.connect()

            val id = transport.exec("id -u")
            assertEquals(0, id.exitStatus)
            assertEquals("0", id.stdout.trim())

            val boot = transport.exec("cat /proc/sys/kernel/random/boot_id 2>/dev/null")
            assertEquals(0, boot.exitStatus)
            assertTrue("boot_id is empty", boot.stdout.trim().isNotEmpty())

            if (uploadEnabled) {
                val uploadDir = File(context.cacheDir, "ssh-spike/upload").toPath()
                Files.createDirectories(uploadDir)
                val small = createTestFile(uploadDir.resolve("spike-49k.bin"), 49_016)
                val large = createTestFile(uploadDir.resolve("spike-1.2m.bin"), 1_216_784)

                val mkdir = transport.exec("mkdir -p ${shellQuote(remoteBase)}")
                assertEquals(0, mkdir.exitStatus)

                listOf(small, large).forEach { local ->
                    val remote = "$remoteBase/${local.fileName}"
                    transport.upload(local, remote)
                    assertEquals(
                        "sha256 mismatch for ${local.fileName}",
                        HashUtils.sha256File(local),
                        remoteSha256(transport, remote),
                    )
                }
            }
        }
    }

    private fun createTestFile(path: Path, size: Int): Path {
        val chunk = ByteArray(8192) { index -> ((index * 31) and 0xff).toByte() }
        Files.newOutputStream(path).use { output ->
            var remaining = size
            while (remaining > 0) {
                val count = minOf(remaining, chunk.size)
                output.write(chunk, 0, count)
                remaining -= count
            }
        }
        return path
    }

    private fun remoteSha256(transport: SshjTransport, path: String): String? {
        val quoted = shellQuote(path)
        val command = "if command -v sha256sum >/dev/null 2>&1; " +
            "then sha256sum -- $quoted; " +
            "elif command -v busybox >/dev/null 2>&1; then busybox sha256sum -- $quoted; " +
            "elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 $quoted; " +
            "else exit 127; fi"
        val result = transport.exec(command)
        if (result.exitStatus != 0) return null
        val line = result.stdout.lineSequence().map { it.trim() }.firstOrNull { it.isNotEmpty() }
            ?: return null
        val hash = if (line.contains('=')) {
            line.substringAfterLast('=')
        } else {
            line.substringBefore(' ')
        }.trim().lowercase(Locale.ROOT)
        return hash.takeIf { it.matches(Regex("[0-9a-f]{64}")) }
    }
}
