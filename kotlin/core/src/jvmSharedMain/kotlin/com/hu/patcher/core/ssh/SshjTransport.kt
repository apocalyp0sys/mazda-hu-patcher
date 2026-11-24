package com.hu.patcher.core.ssh

import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.userauth.UserAuthException
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.SocketTimeoutException
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/**
 * sshj-backed transport. Uses the built-in legacy SCP file transfer so the
 * head unit does not need an SFTP subsystem.
 */
class SshjTransport(
    override val config: SshConfig,
    override val username: String,
    private val credentials: SshCredentials,
    private val hostKeyPolicy: HostKeyPolicy = HostKeyPolicy.AcceptAndLog,
    private val logger: (String) -> Unit = {},
) : SshTransport {

    private var client: SSHClient? = null

    @Volatile
    private var connected: Boolean = false

    override val isConnected: Boolean
        get() = connected && client?.isConnected == true

    override fun connect() {
        check(client == null) { "transport is already open" }
        val ssh = SSHClient()
        ssh.addHostKeyVerifier(
            LoggingHostKeyVerifier(config.host, config.port, hostKeyPolicy, logger)
        )
        ssh.connectTimeout = config.connectTimeout.toMillis().toInt()
        ssh.timeout = 0

        try {
            logger("connecting to $username@${config.host}:${config.port}")
            ssh.connect(config.host, config.port)
            authenticate(ssh)
        } catch (e: UserAuthException) {
            closeQuietly(ssh)
            throw SshAuthException(
                "authentication failed for $username@${config.host}:${config.port}",
                e,
            )
        } catch (e: SshException) {
            closeQuietly(ssh)
            throw e
        } catch (e: IOException) {
            closeQuietly(ssh)
            if (isTimeout(e)) {
                throw SshTimeoutException(
                    "connection to ${config.host}:${config.port} timed out",
                    e,
                )
            }
            throw SshException(
                "connection to ${config.host}:${config.port} failed: ${e.message}",
                e,
            )
        } catch (e: RuntimeException) {
            closeQuietly(ssh)
            throw SshException(
                "connection to ${config.host}:${config.port} failed: ${e.message}",
                e,
            )
        }

        client = ssh
        connected = true
        logger("connected to $username@${config.host}:${config.port}")
    }

    override fun exec(
        command: String,
        stdin: ByteArray?,
        timeout: Duration,
    ): SshCommandResult {
        val ssh = requireConnected()
        val started = System.nanoTime()
        try {
            return withSocketTimeout(ssh, timeout) {
                val session = ssh.startSession()
                session.use {
                    val channel = it.exec(command)
                    val stdout = ByteArrayOutputStream()
                    val stderr = ByteArrayOutputStream()
                    val readerFailure = AtomicReference<Throwable?>()

                    val stdoutReader = thread(
                        start = true,
                        isDaemon = true,
                        name = "ssh-stdout-${config.host}",
                    ) {
                        runCatching {
                            channel.inputStream.use { input -> input.copyTo(stdout) }
                        }.onFailure { readerFailure.compareAndSet(null, it) }
                    }
                    val stderrReader = thread(
                        start = true,
                        isDaemon = true,
                        name = "ssh-stderr-${config.host}",
                    ) {
                        runCatching {
                            channel.errorStream.use { input -> input.copyTo(stderr) }
                        }.onFailure { readerFailure.compareAndSet(null, it) }
                    }

                    try {
                        channel.outputStream.use { output ->
                            if (stdin != null) output.write(stdin)
                        }
                        awaitExit(channel, timeout)
                        val exitStatus = channel.exitStatus
                            ?: throw SshTimeoutException(
                                "command timed out after ${timeout.toMillis()} ms: ${command.take(120)}"
                            )

                        stdoutReader.join(2000)
                        stderrReader.join(2000)
                        if (stdoutReader.isAlive || stderrReader.isAlive) {
                            throw SshTimeoutException(
                                "output did not finish after ${timeout.toMillis()} ms: " +
                                    command.take(120)
                            )
                        }

                        val failure = readerFailure.get()
                        if (failure != null && failure is SocketTimeoutException) {
                            throw SshTimeoutException(
                                "command timed out after ${timeout.toMillis()} ms: " +
                                    command.take(120),
                                failure,
                            )
                        }
                        if (failure != null) {
                            throw SshException(
                                "cannot read command output: ${failure.message}",
                                failure,
                            )
                        }

                        SshCommandResult(
                            command = command,
                            exitStatus = exitStatus,
                            stdout = stdout.toString(Charsets.UTF_8.name()),
                            stderr = stderr.toString(Charsets.UTF_8.name()),
                            elapsed = Duration.ofNanos(System.nanoTime() - started),
                        )
                    } catch (e: Throwable) {
                        runCatching { channel.close() }
                        throw e
                    }
                }
            }
        } catch (e: SshException) {
            throw e
        } catch (e: IOException) {
            if (isTimeout(e)) {
                throw SshTimeoutException(
                    "command timed out after ${timeout.toMillis()} ms: ${command.take(120)}",
                    e,
                )
            }
            throw SshException("command failed: ${command.take(120)}: ${e.message}", e)
        }
    }

    override fun upload(
        localPath: Path,
        remotePath: String,
        timeout: Duration,
    ) {
        val ssh = requireConnected()
        if (!Files.isRegularFile(localPath)) {
            throw SshException("local file does not exist: $localPath")
        }
        try {
            withSocketTimeout(ssh, timeout) {
                ssh.newSCPFileTransfer().upload(localPath.toString(), remotePath)
            }
        } catch (e: IOException) {
            if (isTimeout(e)) {
                throw SshTimeoutException("SCP upload timed out: $remotePath", e)
            }
            throw SshException("SCP upload failed: $remotePath: ${e.message}", e)
        }
    }

    override fun uploadViaCat(
        localPath: Path,
        remotePath: String,
        timeout: Duration,
    ) {
        if (!Files.isRegularFile(localPath)) {
            throw SshException("local file does not exist: $localPath")
        }
        val data = Files.readAllBytes(localPath)
        val result = exec("cat > ${shellQuote(remotePath)}", data, timeout)
        if (!result.isSuccess) {
            throw SshException(
                "cat upload failed (rc=${result.exitStatus}): ${result.stderr.take(300)}"
            )
        }
    }

    override fun close() {
        val ssh = client
        client = null
        connected = false
        if (ssh != null) {
            closeQuietly(ssh)
            logger("disconnected from ${config.host}:${config.port}")
        }
    }

    private fun authenticate(ssh: SSHClient) {
        when (val value = credentials) {
            is SshCredentials.Password -> {
                ssh.authPassword(username, value.password)
            }

            is SshCredentials.KeyPairCredentials -> {
                ssh.authPublickey(username, ssh.loadKeys(value.keyPair))
            }

            is SshCredentials.KeyFile -> {
                val privateKey = value.privateKey
                val isPkcs8 = runCatching {
                    readUtf8(privateKey).contains("-----BEGIN PRIVATE KEY-----")
                }.getOrDefault(false)
                if (isPkcs8) {
                    if (value.passphrase != null && value.passphrase.isNotEmpty()) {
                        throw SshAuthException("passphrase-protected PKCS#8 keys are not supported")
                    }
                    val keyPair = SshKeyGenerator.loadEd25519(privateKey, value.publicKey)
                    ssh.authPublickey(username, ssh.loadKeys(keyPair))
                } else {
                    val provider = if (value.passphrase != null) {
                        ssh.loadKeys(privateKey.toString(), String(value.passphrase))
                    } else {
                        ssh.loadKeys(privateKey.toString())
                    }
                    ssh.authPublickey(username, provider)
                }
            }
        }
    }

    private fun requireConnected(): SSHClient {
        val ssh = client
        if (!connected || ssh == null || !ssh.isConnected) {
            throw SshException("SSH transport is not connected")
        }
        return ssh
    }

    private fun <T> withSocketTimeout(ssh: SSHClient, timeout: Duration, block: () -> T): T {
        val previous = ssh.timeout
        ssh.timeout = timeout.toMillis().coerceIn(1L, Int.MAX_VALUE.toLong()).toInt()
        return try {
            block()
        } finally {
            ssh.timeout = previous
        }
    }

    private fun awaitExit(channel: net.schmizz.sshj.connection.channel.direct.Session.Command, timeout: Duration) {
        val deadline = System.nanoTime() + timeout.toNanos()
        while (channel.exitStatus == null && System.nanoTime() < deadline) {
            val remainingMs = ((deadline - System.nanoTime()) / 1_000_000L).coerceAtLeast(1L)
            channel.join(minOf(remainingMs, 250L), TimeUnit.MILLISECONDS)
        }
        if (channel.exitStatus == null) {
            throw SshTimeoutException("command timed out after ${timeout.toMillis()} ms")
        }
    }

    private fun isTimeout(error: Throwable): Boolean {
        var current: Throwable? = error
        while (current != null) {
            if (current is SocketTimeoutException) return true
            current = current.cause
        }
        return false
    }

    private fun closeQuietly(ssh: SSHClient) {
        runCatching { ssh.close() }
    }
}
