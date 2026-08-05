package com.alnathim.app.probe

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.withContext
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Result of a direct TCP port probe to a sector device.
 *
 * @param host the probed IP/hostname.
 * @param port the probed port.
 * @param open true when the TCP connect succeeded within [timeoutMs].
 * @param latencyMs measured connect latency, or -1 on failure.
 */
data class PortProbeResult(
    val host: String,
    val port: Int,
    val open: Boolean,
    val latencyMs: Long = -1L
)

/**
 * Native sector ping — the phone itself checks the MikroTik sector by
 * opening parallel TCP connects on the classic RouterOS ports:
 * 8728 (API), 22 (SSH), 8291 (WinBox).
 *
 * Runs entirely on Dispatchers.IO and never blocks the UI thread. A device
 * is considered reachable when ANY of the ports accepts a connection.
 */
object DirectPing {

    /** Default MikroTik ports probed in parallel. */
    val MIKROTIK_PORTS = listOf(8728, 22, 8291)

    /**
     * Parallel port probe — returns one [PortProbeResult] per port.
     *
     * @param host the sector IP/hostname.
     * @param ports the ports to test (defaults to the MikroTik set).
     * @param timeoutMs per-connect timeout in ms (default 1200 — fast).
     * @param scope the coroutine scope (caller controls cancellation).
     */
    suspend fun probePorts(
        host: String,
        ports: List<Int> = MIKROTIK_PORTS,
        timeoutMs: Int = 1200,
        scope: CoroutineScope
    ): List<PortProbeResult> = withContext(Dispatchers.IO) {
        ports.map { port ->
            scope.async {
                val start = System.currentTimeMillis()
                val open = try {
                    Socket().use { socket ->
                        socket.connect(InetSocketAddress(host, port), timeoutMs)
                    }
                    true
                } catch (e: Exception) {
                    false
                }
                PortProbeResult(
                    host = host,
                    port = port,
                    open = open,
                    latencyMs = if (open) System.currentTimeMillis() - start else -1L
                )
            }
        }.awaitAll()
    }

    /**
     * Overall reachability — true when at least one probed port is open.
     * Uses [probePorts] then short-circuits on the first open port.
     */
    suspend fun isReachable(
        host: String,
        ports: List<Int> = MIKROTIK_PORTS,
        timeoutMs: Int = 1200,
        scope: CoroutineScope
    ): Boolean = probePorts(host, ports, timeoutMs, scope).any { it.open }

    /**
     * Best-latency probe summary — the port with the smallest latency,
     * or null when nothing is reachable.
     */
    suspend fun bestProbe(
        host: String,
        ports: List<Int> = MIKROTIK_PORTS,
        timeoutMs: Int = 1200,
        scope: CoroutineScope
    ): PortProbeResult? = probePorts(host, ports, timeoutMs, scope)
        .filter { it.open }
        .minByOrNull { it.latencyMs }
}
