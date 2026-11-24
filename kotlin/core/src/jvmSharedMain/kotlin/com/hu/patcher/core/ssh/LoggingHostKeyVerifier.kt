package com.hu.patcher.core.ssh

import net.schmizz.sshj.transport.verification.HostKeyVerifier
import java.security.PublicKey

/**
 * Logs the host key and either accepts it (Stage 1) or enforces a pinned
 * SHA256 fingerprint. Pinning is intentionally not persisted yet; Stage 3
 * adds known-host storage and a key-change flow.
 */
class LoggingHostKeyVerifier(
    private val host: String,
    private val port: Int,
    private val policy: HostKeyPolicy,
    private val logger: (String) -> Unit,
) : HostKeyVerifier {

    override fun verify(hostname: String, port: Int, key: PublicKey): Boolean {
        val fingerprint = runCatching { SshFingerprint.sha256(key) }
            .getOrElse { "SHA256-UNKNOWN(${key.algorithm})" }
        logger(
            "host key $hostname:$port ${key.algorithm} $fingerprint " +
                if (policy is HostKeyPolicy.Pinned) "(pinned)" else "(accept-and-log)"
        )
        return when (policy) {
            HostKeyPolicy.AcceptAndLog -> true
            is HostKeyPolicy.Pinned -> {
                val ok = SshFingerprint.matches(policy.sha256Fingerprint, fingerprint)
                if (!ok) {
                    logger(
                        "host key mismatch: expected SHA256:${SshFingerprint.normalize(policy.sha256Fingerprint)}"
                    )
                }
                ok
            }
        }
    }

    override fun findExistingAlgorithms(hostname: String, port: Int): List<String> {
        return emptyList()
    }

    override fun toString(): String = "LoggingHostKeyVerifier($host:$port, $policy)"
}
