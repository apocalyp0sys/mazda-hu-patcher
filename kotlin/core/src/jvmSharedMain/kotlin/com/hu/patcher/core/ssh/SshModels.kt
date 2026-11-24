package com.hu.patcher.core.ssh

import java.io.IOException
import java.nio.file.Path
import java.security.KeyPair
import java.time.Duration

/** Connection parameters shared by Android and desktop. */
data class SshConfig(
    val host: String,
    val port: Int = DEFAULT_PORT,
    val connectTimeout: Duration = Duration.ofSeconds(8),
    val commandTimeout: Duration = Duration.ofSeconds(120),
    val uploadTimeout: Duration = Duration.ofSeconds(180),
) {
    init {
        require(host.isNotBlank()) { "host is blank" }
        require(port in 1..65535) { "invalid port: $port" }
        require(!connectTimeout.isNegative && !connectTimeout.isZero) {
            "connectTimeout must be positive"
        }
        require(!commandTimeout.isNegative && !commandTimeout.isZero) {
            "commandTimeout must be positive"
        }
        require(!uploadTimeout.isNegative && !uploadTimeout.isZero) {
            "uploadTimeout must be positive"
        }
    }

    companion object {
        const val DEFAULT_PORT: Int = 22
    }
}

sealed interface SshCredentials {
    /**
     * Password authentication. Keep the array mutable-free by convention and
     * never include the value in [toString].
     */
    data class Password(val password: CharArray) : SshCredentials {
        override fun toString(): String = "Password(***)"
    }

    /**
     * Key authentication from a private key file. The public key is derived
     * from the private key when possible; [publicKey] is only used to verify a
     * sidecar `.pub` file for supported key formats.
     */
    data class KeyFile(
        val privateKey: Path,
        val publicKey: Path? = null,
        val passphrase: CharArray? = null,
    ) : SshCredentials {
        override fun toString(): String = "KeyFile($privateKey)"
    }

    /** In-memory key pair, used by tests and by callers that keep a key in a vault. */
    data class KeyPairCredentials(val keyPair: KeyPair) : SshCredentials
}

sealed interface HostKeyPolicy {
    /** Accept and log the host key. The Stage 1 spike uses this policy. */
    data object AcceptAndLog : HostKeyPolicy

    /** Only accept a host key whose OpenSSH SHA256 fingerprint matches. */
    data class Pinned(val sha256Fingerprint: String) : HostKeyPolicy
}

data class SshCommandResult(
    val command: String,
    val exitStatus: Int,
    val stdout: String,
    val stderr: String,
    val elapsed: Duration,
) {
    val isSuccess: Boolean get() = exitStatus == 0
}

open class SshException(message: String, cause: Throwable? = null) : IOException(message, cause)

class SshAuthException(message: String, cause: Throwable? = null) : SshException(message, cause)

class SshTimeoutException(message: String, cause: Throwable? = null) : SshException(message, cause)

class SshKeyException(message: String, cause: Throwable? = null) : SshException(message, cause)
