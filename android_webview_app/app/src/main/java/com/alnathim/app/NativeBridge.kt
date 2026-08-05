package com.alnathim.app

import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.alnathim.app.probe.DirectPing
import com.alnathim.app.probe.PortProbeResult
import com.alnathim.app.probe.RouterOsClient
import com.alnathim.app.probe.SnmpClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.TimeUnit
import java.util.regex.Pattern

/**
 * JavaScript bridge exposed to the WebView as `window.AlNathimNative`.
 *
 * Lets the HTML/JS UI (served from Render) trigger the ONLY native part of
 * the app: live device probes — TCP reachability, RouterOS /ppp/active pull
 * and SNMP signal reads — directly from the phone on the ISP LAN.
 *
 * Every method is async: it runs on Dispatchers.IO and posts the result back
 * through `window.onNativeResult(callbackId, json)` on the UI thread.
 */
class NativeBridge(
    private val activity: MainActivity,
    private val webView: WebView
) {

    private val scope = CoroutineScope(Dispatchers.IO)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val hostRe = Pattern.compile("^[A-Za-z0-9.\\-_:]+$")

    // ── Public JS API ────────────────────────────────────────

    /** Full sector probe: TCP ports + RouterOS active PPP + SNMP signal. */
    @JavascriptInterface
    fun probeSector(ip: String?, username: String?, password: String?, community: String?, callbackId: String?) {
        val host = ip?.trim() ?: ""
        if (host.isEmpty()) {
            reply(callbackId ?: "", err("عنوان IP فارغ"))
            return
        }
        scope.launch {
            val json = withContext(Dispatchers.IO) {
                sectorProbe(host, username ?: "", password ?: "", community ?: "")
            }
            reply(callbackId ?: "", json)
        }
    }

    /** Real ICMP ping from the phone (TCP fallback when the ping binary is blocked). */
    @JavascriptInterface
    fun pingHost(ip: String?, callbackId: String?) {
        val host = ip?.trim() ?: ""
        if (host.isEmpty()) {
            reply(callbackId ?: "", err("عنوان IP فارغ"))
            return
        }
        scope.launch {
            val json = withContext(Dispatchers.IO) { ping(host) }
            reply(callbackId ?: "", json)
        }
    }

    /**
     * Standalone SNMP probe from the phone (تبويب SNMP) — reads signal/CCQ for
     * wireless devices or RX/TX for GPON ONTs directly from the device, using
     * the exact OIDs from snmp_monitor/config.py.
     *
     * @param ip the device IP/hostname.
     * @param deviceType "auto" | "wireless" | "optical".
     * @param community the SNMP community string (default "public").
     * @param callbackId the JS callback id.
     */
    @JavascriptInterface
    fun probeSnmp(ip: String?, deviceType: String?, community: String?, callbackId: String?) {
        val host = ip?.trim() ?: ""
        if (host.isEmpty()) {
            reply(callbackId ?: "", err("عنوان IP فارغ"))
            return
        }
        scope.launch {
            val json = withContext(Dispatchers.IO) {
                snmpProbe(host, deviceType ?: "", community ?: "")
            }
            reply(callbackId ?: "", json)
        }
    }

    // ── Standalone SNMP probe (تبويب SNMP) ──────────────────────────────

    private suspend fun snmpProbe(host: String, deviceType: String, community: String): JSONObject {
        val comm = community.ifBlank { "public" }
        val snmp = SnmpClient(host, comm)
        val out = JSONObject().put("host", host).put("device_type", deviceType)

        var signal: Double? = null
        var ccq: Double? = null
        var rxDbm: Double? = null
        var txDbm: Double? = null

        when (deviceType) {
            "optical" -> {
                // GPON ONU — OIDs from snmp_monitor/config.py
                rxDbm = snmp.getDouble("1.3.6.1.4.1.5873.4.1.2.3.1.4")
                txDbm = snmp.getDouble("1.3.6.1.4.1.5873.4.1.2.3.1.5")
            }
            "wireless" -> {
                // Ubiquiti AirOS first, MikroTik fallback
                signal = snmp.getDouble("1.3.6.1.4.1.41112.1.4.5.1.5.1")
                    ?: snmp.getDouble("1.3.6.1.4.1.14988.1.1.2.1.1.1")
                ccq = snmp.getDouble("1.3.6.1.4.1.41112.1.4.7.1.6.1")
            }
            else -> { // auto — try wireless first, then optical
                signal = snmp.getDouble("1.3.6.1.4.1.41112.1.4.5.1.5.1")
                    ?: snmp.getDouble("1.3.6.1.4.1.14988.1.1.2.1.1.1")
                ccq = snmp.getDouble("1.3.6.1.4.1.41112.1.4.7.1.6.1")
                if (signal == null && ccq == null) {
                    rxDbm = snmp.getDouble("1.3.6.1.4.1.5873.4.1.2.3.1.4")
                    txDbm = snmp.getDouble("1.3.6.1.4.1.5873.4.1.2.3.1.5")
                }
            }
        }

        val reachable = signal != null || ccq != null || rxDbm != null || txDbm != null
        out.put("ok", reachable)
        out.put("reachable", reachable)
        out.put("snmp_ok", reachable)
        out.put("signal_dbm", signal ?: JSONObject.NULL)
        out.put("ccq", ccq ?: JSONObject.NULL)
        out.put("rx_dbm", rxDbm ?: JSONObject.NULL)
        out.put("tx_dbm", txDbm ?: JSONObject.NULL)
        if (!reachable) out.put("error", "الجهاز غير متصل / لا توجد قراءة SNMP — تحقق من الـ Community")
        return out
    }

    // ── Sector probe (mirrors android_native NetworkToolsViewModel) ──

    private suspend fun sectorProbe(host: String, username: String, password: String, community: String): JSONObject {
        val out = JSONObject().put("host", host)
        val probes: List<PortProbeResult> = try {
            DirectPing.probePorts(host, scope = scope)
        } catch (e: Exception) {
            return err("فشل الفحص: ${e.message}")
        }
        val open = probes.filter { it.open }
        val portsJson = JSONArray()
        for (p in probes) {
            portsJson.put(
                JSONObject()
                    .put("port", p.port)
                    .put("open", p.open)
                    .put("latency_ms", p.latencyMs)
            )
        }
        out.put("ports", portsJson)
        out.put("reachable", open.isNotEmpty())
        out.put("open_ports", JSONArray(open.map { it.port }))
        out.put("latency_ms", open.minOfOrNull { it.latencyMs } ?: JSONObject.NULL)
        out.put("ok", true)

        // RouterOS: live /ppp/active subscriber count (only when credentials exist).
        if (username.isNotBlank()) {
            try {
                RouterOsClient(host, username, password).use { client ->
                    client.connect()
                    out.put("ppp_active", client.pppActive().size)
                    out.put("interfaces", client.interfaces().size)
                    out.put("routeros_ok", true)
                }
            } catch (e: Exception) {
                out.put("routeros_ok", false)
                out.put("routeros_error", e.message ?: "فشل الاتصال بالراوتر")
            }
        }

        // SNMP: signal / CCQ (Ubiquiti wireless first, then MikroTik fallback).
        val comm = community.ifBlank { "public" }
        val snmp = SnmpClient(host, comm)
        val signal = snmp.getDouble("1.3.6.1.4.1.41112.1.4.5.1.5.1")
            ?: snmp.getDouble("1.3.6.1.4.1.14988.1.1.2.1.1.1")
        val ccq = snmp.getDouble("1.3.6.1.4.1.41112.1.4.7.1.6.1")
        if (signal != null || ccq != null) {
            out.put("signal_dbm", signal ?: JSONObject.NULL)
            out.put("ccq", ccq ?: JSONObject.NULL)
            out.put("snmp_ok", true)
        } else {
            out.put("snmp_ok", false)
        }
        return out
    }

    // ── Ping (ICMP via system binary, TCP fallback) ──────────

    private suspend fun ping(host: String): JSONObject {
        if (!hostRe.matcher(host).matches()) {
            return JSONObject().put("ok", false).put("host", host).put("error", "عنوان غير صالح")
        }
        val out = JSONObject().put("host", host)
        val latencies = mutableListOf<Double>()
        try {
            val start = System.currentTimeMillis()
            // Argument array — no shell, injection-safe. -c 4 fixed like the server.
            val proc = Runtime.getRuntime().exec(arrayOf("ping", "-c", "4", "-W", "2", host))
            val text = proc.inputStream.bufferedReader().use { it.readText() } +
                proc.errorStream.bufferedReader().use { it.readText() }
            proc.waitFor(10, TimeUnit.SECONDS)
            val elapsed = (System.currentTimeMillis() - start) / 1000.0
            val re = Regex("time[=<]\\s*([\\d.]+)\\s*ms", RegexOption.IGNORE_CASE)
            for (m in re.findAll(text)) {
                m.groupValues[1].toDoubleOrNull()?.let { latencies.add(it) }
            }
            val sent = 4
            val received = latencies.size
            out.put("ok", received > 0)
            out.put("sent", sent)
            out.put("received", received)
            out.put("loss_percent", if (sent > 0) (sent - received) * 100.0 / sent else 100.0)
            out.put("latencies_ms", JSONArray(latencies))
            out.put("min_ms", latencies.minOrNull() ?: JSONObject.NULL)
            out.put("avg_ms", if (latencies.isNotEmpty()) latencies.sum() / latencies.size else JSONObject.NULL)
            out.put("max_ms", latencies.maxOrNull() ?: JSONObject.NULL)
            out.put("elapsed_sec", elapsed)
            out.put("is_tcp_probe", false)
            if (received == 0) out.put("error", "لا يوجد استجابة — الجهاز غير متصل")
            return out
        } catch (e: Exception) {
            return tcpProbeFallback(host)
        }
    }

    /** TCP connect probe on 80/443 — mirrors network_tools/ping.py `_tcp_probe`. */
    private fun tcpProbeFallback(host: String): JSONObject {
        val out = JSONObject().put("host", host).put("is_tcp_probe", true)
        val latencies = mutableListOf<Double>()
        for (port in listOf(80, 443)) {
            try {
                val start = System.currentTimeMillis()
                Socket().use { s -> s.connect(InetSocketAddress(host, port), 2000) }
                latencies.add((System.currentTimeMillis() - start).toDouble())
                break
            } catch (e: Exception) {
                // try next port
            }
        }
        if (latencies.isEmpty()) {
            out.put("ok", false).put("error", "لا يوجد استجابة — الجهاز غير متصل")
        } else {
            out.put("ok", true)
                .put("sent", latencies.size)
                .put("received", latencies.size)
                .put("loss_percent", 0.0)
                .put("latencies_ms", JSONArray(latencies))
                .put("min_ms", latencies.minOrNull())
                .put("avg_ms", latencies.sum() / latencies.size)
                .put("max_ms", latencies.maxOrNull())
        }
        return out
    }

    // ── Helpers ──────────────────────────────────────────────

    private fun err(message: String): JSONObject =
        JSONObject().put("ok", false).put("error", message)

    /** Post a JSON result to `window.onNativeResult(callbackId, {...})`. */
    private fun reply(callbackId: String, json: JSONObject) {
        val safeJson = json.toString()
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        val js = "window.onNativeResult('${escapeJs(callbackId)}', $safeJson);"
        mainHandler.post {
            if (!activity.isFinishing && !activity.isDestroyed) {
                webView.evaluateJavascript(js, null)
            }
        }
    }

    /** Escape a string for embedding inside a single-quoted JS literal. */
    private fun escapeJs(s: String): String {
        val sb = StringBuilder()
        for (c in s) {
            when (c) {
                '\\' -> sb.append("\\\\")
                '\'' -> sb.append("\\'")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }
}

