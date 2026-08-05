package com.alnathim.app.probe

import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Minimal native SNMPv1 GET client (UDP) — mirrors snmp_monitor/signal_monitor.py
 * so the phone reads rx/tx/CCQ straight from sector APs/ONTs using the exact
 * OIDs from snmp_monitor/config.py.
 *
 * - SNMPv1 community (mpModel=0), port 161, timeout 3s.
 * - BER-encodes GetRequest PDU, sends over DatagramSocket, parses response.
 * - Never crashes: returns null on any timeout/error.
 */
class SnmpClient(
    private val host: String,
    private val community: String = "public",
    private val port: Int = 161,
    private val timeoutMs: Int = 3000
) {

    /** Read a single OID as a numeric Double (dBm / CCQ), or null on failure. */
    fun getDouble(oid: String): Double? = get(oid)?.toDoubleOrNull()

    /** Read a single OID as a raw string, or null on failure. */
    fun get(oid: String): String? {
        val requestId = (Math.random() * 1_000_000).toInt() and 0x7fffffff
        val request = buildGetRequest(requestId, oid)

        return try {
            DatagramSocket().use { socket ->
                socket.soTimeout = timeoutMs
                val target = InetAddress.getByName(host)
                socket.send(DatagramPacket(request, request.size, target, port))
                val buf = ByteArray(4096)
                val packet = DatagramPacket(buf, buf.size)
                socket.receive(packet)
                parseResponse(packet.data, packet.length, oid)
            }
        } catch (e: Exception) {
            null
        }
    }

    // ── BER encoding (SNMPv1 GetRequest) ──────────────────────

    private fun buildGetRequest(requestId: Int, oid: String): ByteArray {
        val communityBytes = community.toByteArray(Charsets.US_ASCII)
        val oidBytes = encodeOid(oid)
        val varbind = sequence(byteArrayOf(0x30.toByte()) + oidBytes + byteArrayOf(0x05, 0x00))
        val pduContent = intBytes(requestId) + intBytes(0) + intBytes(0) + varbind
        val pdu = sequence(byteArrayOf(0xA0.toByte()) + pduContent)
        val msgContent = byteArrayOf(0x02, 0x01, 0x00) + sequence(communityBytes) + pdu
        return sequence(byteArrayOf(0x30.toByte()) + msgContent)
    }

    private fun intBytes(value: Int): ByteArray {
        var v = value
        val tmp = java.util.ArrayList<Byte>()
        tmp.add((v and 0xff).toByte())
        v = v ushr 8
        while (v != 0 && tmp.size < 4) {
            tmp.add(0, (v and 0xff).toByte())
            v = v ushr 8
        }
        if (tmp[0].toInt() and 0x80 != 0) tmp.add(0, 0.toByte())
        val out = ByteArrayOutputStream()
        out.write(0x02)
        out.write(tmp.size)
        tmp.forEach { out.write(it.toInt()) }
        return out.toByteArray()
    }

    private fun encodeOid(oid: String): ByteArray {
        val parts = oid.trim().trimStart('.').split('.').map { it.toInt() }
        val body = ByteArrayOutputStream()
        body.write(parts[0] * 40 + parts[1])
        for (i in 2 until parts.size) {
            val v = parts[i]
            if (v < 128) {
                body.write(v)
            } else {
                val stack = java.util.ArrayDeque<Int>()
                var n = v
                stack.push(n and 0x7f)
                n = n ushr 7
                while (n > 0) {
                    stack.push((n and 0x7f) or 0x80)
                    n = n ushr 7
                }
                while (!stack.isEmpty()) body.write(stack.pop())
            }
        }
        val bodyBytes = body.toByteArray()
        val out = ByteArrayOutputStream()
        out.write(0x06)
        out.write(bodyBytes.size)
        out.write(bodyBytes)
        return out.toByteArray()
    }

    private fun sequence(content: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(0x30)
        out.write(content.size)
        out.write(content)
        return out.toByteArray()
    }

    // ── BER parsing (GetResponse) ─────────────────────────────

    private fun parseResponse(data: ByteArray, length: Int, oid: String): String? {
        var idx = 0
        if (idx >= length || data[idx].toInt() != 0x30) return null
        idx = skipTlv(data, idx, length) ?: return null
        if (idx >= length || data[idx].toInt() != 0xA1) return null
        val pduEnd = tlvEnd(data, idx, length) ?: return null
        idx = skipTlv(data, idx, pduEnd) ?: return null // request-id
        idx = skipTlv(data, idx, pduEnd) ?: return null // error-status
        idx = skipTlv(data, idx, pduEnd) ?: return null // error-index
        if (idx >= pduEnd || data[idx].toInt() != 0x30) return null
        val vbEnd = tlvEnd(data, idx, pduEnd) ?: return null
        idx = skipTlv(data, idx, vbEnd) ?: return null // OID
        if (idx >= vbEnd) return null
        val type = data[idx].toInt() and 0xff
        val len = data[idx + 1].toInt() and 0xff
        if (idx + 2 + len > vbEnd) return null
        val valueBytes = data.copyOfRange(idx + 2, idx + 2 + len)
        return when (type) {
            0x02 -> parseSint(valueBytes)?.toString()
            0x04 -> String(valueBytes, Charsets.UTF_8)
            0x41 -> parseSint(valueBytes)?.toString()
            0x42 -> parseUint(valueBytes)?.toString()
            0x43 -> parseSint(valueBytes)?.toString()
            0x46 -> parseDec(valueBytes)?.toString()
            0x44 -> valueBytes.joinToString("") { "%02x".format(it) }
            else -> null
        }
    }

    private fun tlvEnd(data: ByteArray, start: Int, limit: Int): Int? {
        if (start + 1 >= limit) return null
        val len = data[start + 1].toInt() and 0xff
        if (len < 128) return start + 2 + len
        val count = len and 0x7f
        if (count == 0 || count > 4 || start + 2 + count > limit) return null
        var n = 0
        for (i in 0 until count) {
            n = (n shl 8) or (data[start + 2 + i].toInt() and 0xff)
        }
        return start + 2 + count + n
    }

    private fun skipTlv(data: ByteArray, start: Int, limit: Int): Int? =
        tlvEnd(data, start, limit)

    private fun parseSint(b: ByteArray): Long? {
        if (b.isEmpty()) return null
        var v = b[0].toLong()
        for (i in 1 until b.size) v = (v shl 8) or (b[i].toLong() and 0xff)
        return v
    }

    private fun parseUint(b: ByteArray): Long? {
        if (b.isEmpty()) return null
        var v = 0L
        for (i in b.indices) v = (v shl 8) or (b[i].toLong() and 0xff)
        return v
    }

    private fun parseDec(b: ByteArray): Double? {
        if (b.isEmpty()) return null
        val mantissa = b[0].toInt() and 0xff
        val expRaw = b.getOrNull(1)?.toInt() ?: 0
        val expNibble = expRaw and 0x0f
        val expSign = if (expRaw and 0x80 != 0) 1 else 0
        var mant = mantissa
        if (mantissa and 0x80 != 0) mant = mantissa - 65536
        val exponent = if (expSign == 1) -expNibble else expNibble
        return mant * Math.pow(10.0, exponent.toDouble())
    }
}