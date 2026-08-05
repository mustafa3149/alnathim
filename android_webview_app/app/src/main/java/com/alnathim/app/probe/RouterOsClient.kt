package com.alnathim.app.probe

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Minimal native MikroTik RouterOS API client — speaks the wire protocol
 * directly over TCP 8728 so the phone can pull subscribers/interface data
 * straight from each sector without the server proxying it.
 *
 * - Sentence framing: `[len]\r\n<word>...<word>`, words are key=value or bare tags.
 * - `/login` with challenge-response (SHA1-based, the classic RouterOS scheme).
 * - Returns parsed rows as Map<String,String> for `/ppp/active/print` and
 *   `/interface/print`.
 */
class RouterOsClient(
    private val host: String,
    private val username: String,
    private val password: String,
    private val port: Int = 8728,
    private val connectTimeoutMs: Int = 3000
) : AutoCloseable {

    private var socket: Socket? = null
    private var input: BufferedInputStream? = null
    private var output: BufferedOutputStream? = null
    private var tagCounter = 0

    /**
     * Open the connection and authenticate. Throws [Exception] with an
     * Arabic-friendly message on any failure (never silent).
     */
    fun connect() {
        val s = Socket()
        s.connect(InetSocketAddress(host, port), connectTimeoutMs)
        s.soTimeout = 5000
        socket = s
        input = BufferedInputStream(s.getInputStream())
        output = BufferedOutputStream(s.getOutputStream())
        login()
    }

    /** Login using the classic `/login` challenge (SHA1 response). */
    private fun login() {
        val loginResp = command(listOf("/login")).firstOrNull()
            ?: throw IllegalStateException("تعذر تسجيل الدخول إلى الراوتر")
        val challenge = loginResp["ret"] ?: throw IllegalStateException("الراوتر لم يرسل تحدياً")
        val combined = "\u0000$password$challenge"
        val sha1 = sha1(combined.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        command(listOf("/login", "=name=$username", "=response=00$sha1"))
    }

    /**
     * Send a command sentence and read until `!done` (or `!trap`/`!fatal`).
     *
     * @param words the command words: e.g. ["/ppp/active/print", "=follow="].
     * @return the row maps (for print commands, one per `!re` sentence).
     */
    fun command(words: List<String>): List<Map<String, String>> {
        val output = output ?: throw IllegalStateException("الاتصال غير مفتوح")
        val input = input ?: throw IllegalStateException("الاتصال غير مفتوح")
        tagCounter++
        val tag = tagCounter.toString()
        writeSentence(output, words + ".tag=$tag")
        output.flush()

        val rows = mutableListOf<Map<String, String>>()
        var done = false
        while (!done) {
            val sentence = readSentence(input) ?: break
            if (sentence.isEmpty()) continue
            when (sentence.first()) {
                "!re" -> {
                    val row = sentence.filter { it.startsWith("=") }
                        .associate { it.substring(1).substringBefore("=") to it.substringAfter("=", "") }
                    rows.add(row)
                }
                "!done" -> done = true
                "!trap", "!fatal" -> {
                    val message = sentence.firstOrNull { it.startsWith("=message=") }
                        ?.substringAfter("=")
                        ?: "خطأ من الراوتر"
                    throw IllegalStateException(message)
                }
            }
        }
        return rows
    }

    /** Pull active PPP sessions — `/ppp/active/print` — one row per session. */
    fun pppActive(): List<Map<String, String>> =
        command(listOf("/ppp/active/print"))

    /** Pull interfaces — `/interface/print` — one row per interface. */
    fun interfaces(): List<Map<String, String>> =
        command(listOf("/interface/print"))

    override fun close() {
        try {
            output?.let {
                writeSentence(it, listOf("/quit"))
                it.flush()
            }
        } catch (ignored: Exception) {
        }
        try {
            socket?.close()
        } catch (ignored: Exception) {
        }
        socket = null
        input = null
        output = null
    }

    // ── Wire framing ──────────────────────────────────────────

    private fun writeSentence(out: BufferedOutputStream, words: List<String>) {
        for (word in words) {
            val bytes = word.toByteArray(Charsets.UTF_8)
            writeLength(out, bytes.size)
            out.write(bytes)
        }
        writeLength(out, 0)
    }

    private fun writeLength(out: BufferedOutputStream, length: Int) {
        when {
            length < 0x80 -> out.write(length)
            length < 0x4000 -> {
                out.write(0x80 or (length ushr 8))
                out.write(length and 0xff)
            }
            length < 0x200000 -> {
                out.write(0xC0 or (length ushr 16))
                out.write((length ushr 8) and 0xff)
                out.write(length and 0xff)
            }
            else -> {
                out.write(0xE0 or (length ushr 24))
                out.write((length ushr 16) and 0xff)
                out.write((length ushr 8) and 0xff)
                out.write(length and 0xff)
            }
        }
    }

    private fun readLength(input: BufferedInputStream): Int {
        val b0 = input.read()
        if (b0 < 0) return -1
        return when {
            b0 < 0x80 -> b0
            b0 < 0xC0 -> ((b0 and 0x3f) shl 8) or input.read()
            b0 < 0xE0 -> {
                val b1 = input.read()
                ((b0 and 0x1f) shl 16) or (b1 shl 8) or input.read()
            }
            else -> {
                val b1 = input.read()
                val b2 = input.read()
                val b3 = input.read()
                ((b0 and 0x1f) shl 24) or (b1 shl 16) or (b2 shl 8) or b3
            }
        }
    }

    private fun readSentence(input: BufferedInputStream): List<String>? {
        val words = mutableListOf<String>()
        while (true) {
            val len = readLength(input)
            if (len < 0) return null
            if (len == 0) break
            val buf = ByteArray(len)
            var read = 0
            while (read < len) {
                val n = input.read(buf, read, len - read)
                if (n < 0) return null
                read += n
            }
            words.add(String(buf, Charsets.UTF_8).trimEnd('\u0000'))
        }
        return words
    }

    private fun sha1(data: ByteArray): ByteArray =
        java.security.MessageDigest.getInstance("SHA-1").digest(data)
}