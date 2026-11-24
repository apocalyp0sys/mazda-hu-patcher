package com.hu.patcher.core.ssh

import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.KeyType
import java.security.MessageDigest
import java.security.PublicKey
import java.util.Base64

/**
 * OpenSSH-style SHA256 fingerprints.
 *
 * sshj's [net.schmizz.sshj.common.SecurityUtils.getFingerprint] returns the
 * legacy colon-separated MD5 form. The patcher logs and pins SHA256 instead,
 * which matches `ssh-keygen -lf key.pub`.
 */
object SshFingerprint {
    fun sha256(publicKey: PublicKey): String {
        val keyType = KeyType.fromKey(publicKey)
        require(keyType != KeyType.UNKNOWN) {
            "unsupported public key algorithm: ${publicKey.algorithm}"
        }
        val buffer = Buffer.PlainBuffer()
        keyType.putPubKeyIntoBuffer(publicKey, buffer)
        return "SHA256:" + base64Sha256(buffer.getCompactData())
    }

    fun sha256OfOpenSshPublicKey(publicKeyLine: String): String {
        val body = publicKeyLine
            .lineSequence()
            .map { it.trim() }
            .firstOrNull { it.startsWith("ssh-") || it.startsWith("ecdsa-") }
            ?: throw SshKeyException("not an OpenSSH public key line")
        val parts = body.split(Regex("\\s+"))
        if (parts.size < 2) throw SshKeyException("incomplete OpenSSH public key line")
        val blob = Base64.getDecoder().decode(parts[1])
        return "SHA256:" + base64Sha256(blob)
    }

    fun normalize(fingerprint: String): String {
        return fingerprint.trim()
            .removePrefix("SHA256:")
            .trimEnd('=')
    }

    fun matches(expected: String, actual: String): Boolean {
        return normalize(expected) == normalize(actual)
    }

    private fun base64Sha256(data: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(data)
        return Base64.getEncoder().withoutPadding().encodeToString(digest)
    }
}
