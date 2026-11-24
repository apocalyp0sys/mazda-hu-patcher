package com.hu.patcher.core.ssh

import net.i2p.crypto.eddsa.EdDSAPrivateKey
import net.i2p.crypto.eddsa.EdDSAPublicKey
import net.i2p.crypto.eddsa.EdDSASecurityProvider
import net.i2p.crypto.eddsa.spec.EdDSAPublicKeySpec
import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.KeyType
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFilePermissions
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.Provider
import java.security.PublicKey
import java.security.Security
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64

data class GeneratedSshKey(
    val privateKey: Path,
    val publicKey: Path,
    val publicKeyLine: String,
    val fingerprint: String,
)

/**
 * Creates and loads the patcher's own SSH key.
 *
 * sshj's Ed25519 implementation uses the `net.i2p.crypto.eddsa` key classes,
 * therefore keys are generated through that provider instead of a generic
 * BouncyCastle `Ed25519` key. The private key is stored as unencrypted PKCS#8
 * PEM, the public key as a standard OpenSSH `ssh-ed25519 ...` line.
 */
object SshKeyGenerator {
    private const val PRIVATE_PEM_HEADER = "-----BEGIN PRIVATE KEY-----"
    private const val PUBLIC_PREFIX = "ssh-ed25519 "

    fun generateEd25519(
        directory: Path,
        name: String = "id_ed25519",
        comment: String = "hu-patcher",
        overwrite: Boolean = false,
    ): GeneratedSshKey {
        val generator = KeyPairGenerator.getInstance("EdDSA", eddsaProvider())
        val keyPair = generator.generateKeyPair()
        return writeKeyPair(directory, name, comment, keyPair, overwrite)
    }

    /**
     * RSA fallback for environments where an Ed25519 key is rejected. Stage 1
     * uses Ed25519; Stage 3 can expose this as an explicit fallback.
     */
    fun generateRsa(
        directory: Path,
        bits: Int = 3072,
        name: String = "id_rsa",
        comment: String = "hu-patcher",
        overwrite: Boolean = false,
    ): GeneratedSshKey {
        require(bits >= 2048) { "RSA key must be at least 2048 bits" }
        val generator = KeyPairGenerator.getInstance("RSA")
        generator.initialize(bits)
        return writeKeyPair(directory, name, comment, generator.generateKeyPair(), overwrite)
    }

    /**
     * Loads our PKCS#8 Ed25519 private key. The public key is derived from the
     * private key, never trusted blindly from a stale `.pub` file.
     */
    fun loadEd25519(privateKeyPath: Path, publicKeyPath: Path? = null): KeyPair {
        val text = readUtf8(privateKeyPath)
        if (!text.contains(PRIVATE_PEM_HEADER)) {
            throw SshKeyException("$privateKeyPath is not an unencrypted PKCS#8 PEM key")
        }
        val privateKey = try {
            EdDSAPrivateKey(PKCS8EncodedKeySpec(decodePem(text, PRIVATE_PEM_HEADER)))
        } catch (e: Exception) {
            throw SshKeyException("cannot read Ed25519 private key $privateKeyPath", e)
        }

        val derivedPublic = EdDSAPublicKey(
            EdDSAPublicKeySpec(privateKey.a, privateKey.params)
        )

        val pubPath = publicKeyPath ?: siblingPublicKey(privateKeyPath)
        if (Files.isRegularFile(pubPath)) {
            val filePublic = parseOpenSshEd25519(readUtf8(pubPath))
            if (!filePublic.abyte.contentEquals(derivedPublic.abyte)) {
                throw SshKeyException(
                    "public key $pubPath does not match private key $privateKeyPath"
                )
            }
        }
        return KeyPair(derivedPublic, privateKey)
    }

    fun publicKeyLine(publicKey: PublicKey, comment: String = "hu-patcher"): String {
        val keyType = KeyType.fromKey(publicKey)
        if (keyType == KeyType.UNKNOWN) {
            throw SshKeyException("unsupported public key algorithm: ${publicKey.algorithm}")
        }
        val buffer = Buffer.PlainBuffer()
        keyType.putPubKeyIntoBuffer(publicKey, buffer)
        val encoded = Base64.getEncoder().encodeToString(buffer.getCompactData())
        val suffix = comment.trim().replace('\n', ' ').replace('\r', ' ')
        return listOf(keyType.toString(), encoded, suffix)
            .filter { it.isNotEmpty() }
            .joinToString(" ")
    }

    fun publicKeyFingerprint(publicKey: PublicKey): String = SshFingerprint.sha256(publicKey)

    private fun writeKeyPair(
        directory: Path,
        name: String,
        comment: String,
        keyPair: KeyPair,
        overwrite: Boolean,
    ): GeneratedSshKey {
        require(name.isNotBlank() && !name.contains('/') && !name.contains('\\')) {
            "invalid key file name: $name"
        }
        Files.createDirectories(directory)
        val privatePath = directory.resolve(name)
        val publicPath = directory.resolve("$name.pub")
        if (!overwrite) {
            if (Files.exists(privatePath) || Files.exists(publicPath)) {
                throw SshKeyException("key already exists: $privatePath (use --overwrite)")
            }
        }

        writePrivatePem(privatePath, keyPair.private, overwrite)
        val publicLine = publicKeyLine(keyPair.public, comment)
        writeText(publicPath, publicLine + "\n", overwrite)

        return GeneratedSshKey(
            privateKey = privatePath,
            publicKey = publicPath,
            publicKeyLine = publicLine,
            fingerprint = publicKeyFingerprint(keyPair.public),
        )
    }

    private fun writePrivatePem(path: Path, privateKey: PrivateKey, overwrite: Boolean) {
        val encoded = Base64.getEncoder().encodeToString(privateKey.encoded)
        val pem = buildString {
            append(PRIVATE_PEM_HEADER).append('\n')
            encoded.chunked(64).forEach { append(it).append('\n') }
            append("-----END PRIVATE KEY-----").append('\n')
        }
        writeText(path, pem, overwrite)
        try {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
        } catch (_: UnsupportedOperationException) {
            // Windows and some Android filesystems do not expose POSIX modes.
        } catch (_: java.io.IOException) {
            // Best effort: the file is still written.
        }
    }

    private fun writeText(path: Path, text: String, overwrite: Boolean) {
        val options = if (overwrite) {
            arrayOf(StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)
        } else {
            arrayOf(StandardOpenOption.CREATE_NEW)
        }
        Files.write(path, text.toByteArray(Charsets.UTF_8), *options)
    }

    private fun siblingPublicKey(privateKeyPath: Path): Path {
        return privateKeyPath.resolveSibling(privateKeyPath.fileName.toString() + ".pub")
    }

    private fun decodePem(text: String, header: String): ByteArray {
        val start = text.indexOf(header)
        val end = text.indexOf("-----END PRIVATE KEY-----")
        if (start < 0 || end < 0 || end <= start) {
            throw SshKeyException("invalid PEM private key")
        }
        val body = text.substring(start + header.length, end)
            .filterNot { it.isWhitespace() }
        return Base64.getDecoder().decode(body)
    }

    private fun parseOpenSshEd25519(text: String): EdDSAPublicKey {
        val line = text.lineSequence()
            .map { it.trim() }
            .firstOrNull { it.startsWith(PUBLIC_PREFIX) }
            ?: throw SshKeyException("not an ssh-ed25519 public key")
        val parts = line.split(Regex("\\s+"))
        if (parts.size < 2) throw SshKeyException("incomplete ssh-ed25519 public key")
        val blob = Base64.getDecoder().decode(parts[1])
        val buffer = Buffer.PlainBuffer(blob)
        val type = buffer.readString()
        if (type != "ssh-ed25519") {
            throw SshKeyException("unexpected public key type: $type")
        }
        return KeyType.ED25519.readPubKeyFromBuffer(buffer) as EdDSAPublicKey
    }

    private fun eddsaProvider(): Provider {
        val candidate = EdDSASecurityProvider()
        val existing = Security.getProvider(candidate.name)
        if (existing != null) return existing
        Security.addProvider(candidate)
        return Security.getProvider(candidate.name) ?: candidate
    }
}
