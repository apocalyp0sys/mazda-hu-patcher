package com.hu.patcher.core.ssh

import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.deleteIfExists
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class SshKeyGeneratorTest {
    @Test
    fun generatedEd25519KeyRoundTrips() {
        withTempDir { dir ->
            val generated = SshKeyGenerator.generateEd25519(
                directory = dir,
                comment = "hu-patcher-test",
            )

            assertTrue(generated.privateKey.toFile().isFile)
            assertTrue(generated.publicKey.toFile().isFile)
            assertTrue(generated.publicKeyLine.startsWith("ssh-ed25519 "))
            assertTrue(generated.publicKeyLine.endsWith(" hu-patcher-test"))
            assertTrue(generated.fingerprint.startsWith("SHA256:"))

            val loaded = SshKeyGenerator.loadEd25519(generated.privateKey)
            assertEquals(
                generated.fingerprint,
                SshFingerprint.sha256(loaded.public),
                "public key must be derived from the private key",
            )
            assertEquals(
                generated.fingerprint,
                SshFingerprint.sha256OfOpenSshPublicKey(generated.publicKeyLine),
            )
        }
    }

    @Test
    fun refusesToOverwriteExistingKey() {
        withTempDir { dir ->
            SshKeyGenerator.generateEd25519(dir)

            assertFailsWith<SshKeyException> {
                SshKeyGenerator.generateEd25519(dir)
            }

            val replaced = SshKeyGenerator.generateEd25519(dir, overwrite = true)
            assertTrue(replaced.privateKey.toFile().isFile)
        }
    }

    @Test
    fun rejectsStalePublicKey() {
        withTempDir { dir ->
            val generated = SshKeyGenerator.generateEd25519(dir)
            val otherDir = Files.createTempDirectory("hu-patcher-other-key")
            try {
                val other = SshKeyGenerator.generateEd25519(otherDir)
                Files.writeString(generated.publicKey, other.publicKeyLine + "\n")

                assertFailsWith<SshKeyException> {
                    SshKeyGenerator.loadEd25519(generated.privateKey)
                }
            } finally {
                deleteTree(otherDir)
            }
        }
    }

    @Test
    fun shellQuoteHandlesSingleQuotes() {
        assertEquals("'plain'", shellQuote("plain"))
        assertEquals("'a'\\''b'", shellQuote("a'b"))
        assertEquals("'/tmp/a b/c'", shellQuote("/tmp/a b/c"))
    }

    private fun withTempDir(block: (Path) -> Unit) {
        val dir = Files.createTempDirectory("hu-patcher-key-test")
        try {
            block(dir)
        } finally {
            deleteTree(dir)
        }
    }

    private fun deleteTree(dir: Path) {
        Files.walk(dir).use { stream ->
            stream.sorted(Comparator.reverseOrder()).forEach { it.deleteIfExists() }
        }
    }
}
