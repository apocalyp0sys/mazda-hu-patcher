package com.hu.patcher.core.ssh

import java.security.MessageDigest
import java.nio.file.Files
import java.nio.file.Path

/** UTF-8 helper that works on Android 8 (API 26); Files.readString is API 33. */
internal fun readUtf8(path: Path): String {
    return String(Files.readAllBytes(path), Charsets.UTF_8)
}

object HashUtils {
    fun sha256File(path: Path): String {
        val digest = MessageDigest.getInstance("SHA-256")
        Files.newInputStream(path).use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

/** Quotes a value for POSIX `sh`. Never pass untrusted strings unquoted. */
fun shellQuote(value: String): String {
    require(!value.contains('\u0000')) { "NUL is not allowed in a shell argument" }
    return "'" + value.replace("'", "'\\''") + "'"
}
