package com.hu.patcher.core.ssh

import java.nio.file.Path
import java.time.Duration

/**
 * Small SSH transport surface used by the patcher.
 *
 * It deliberately exposes only what the patcher needs: command execution,
 * legacy SCP upload and a [cat] fallback for units where the remote SCP binary
 * is missing. It never shells out to a local `ssh` or `scp` executable.
 */
interface SshTransport : AutoCloseable {
    val config: SshConfig
    val username: String
    val isConnected: Boolean

    fun connect()

    fun exec(
        command: String,
        stdin: ByteArray? = null,
        timeout: Duration = config.commandTimeout,
    ): SshCommandResult

    /** Legacy SCP upload (remote `scp -t`), equivalent to OpenSSH `scp -O`. */
    fun upload(
        localPath: Path,
        remotePath: String,
        timeout: Duration = config.uploadTimeout,
    )

    /** Fallback upload via `cat > remote`; use only when SCP is unavailable. */
    fun uploadViaCat(
        localPath: Path,
        remotePath: String,
        timeout: Duration = config.uploadTimeout,
    )
}
