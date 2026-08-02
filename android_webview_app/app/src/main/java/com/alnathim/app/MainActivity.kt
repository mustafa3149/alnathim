package com.alnathim.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    /**
     * ⚠️ IMPORTANT — UPDATE THIS URL AFTER RENDER DEPLOYMENT
     * Change this to your real production URL, e.g.:
     *   private val APP_URL = "https://your-app-name.onrender.com"
     */
    private val APP_URL = "https://alnathim.onrender.com/"

    private lateinit var webView: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)

        // ── WebView settings ──────────────────────────────────────────────
        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.loadWithOverviewMode = true
        settings.useWideViewPort = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.textZoom = 100
        // Let the page handle its own zoom (viewport meta enforces user-scalable=no)
        settings.setSupportZoom(false)
        settings.builtInZoomControls = false

        // Copy/paste is handled by the web app's built-in copy buttons (copyText)
        // in the SNMP / customer pages. This avoids the system "Manage apps"
        // context menu that Android shows inside WebView on long-press.

        // ── WebViewClient: route deep links to external apps ──────────────
        webView.webViewClient = object : WebViewClient() {

            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean {
                val url = request?.url ?: return false
                return handleUrl(url.toString())
            }

            @Suppress("DEPRECATION")
            override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                return handleUrl(url ?: return false)
            }
        }

        // ── WebChromeClient: intercept window.open() popups ───────────────
        webView.webChromeClient = object : WebChromeClient() {

            override fun onCreateWindow(
                view: WebView?,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: android.os.Message?
            ): Boolean {
                val newWebView = WebView(this@MainActivity)
                val transport = resultMsg?.obj as? WebView.WebViewTransport
                transport?.webView = newWebView
                newWebView.webViewClient = object : WebViewClient() {

                    override fun shouldOverrideUrlLoading(
                        view: WebView?,
                        request: WebResourceRequest?
                    ): Boolean {
                        val url = request?.url?.toString() ?: return false

                        if (url.startsWith("whatsapp://") ||
                            url.startsWith("https://wa.me/") ||
                            url.startsWith("http://wa.me/") ||
                            url.startsWith("https://api.whatsapp.com/") ||
                            url.startsWith("http://api.whatsapp.com/")
                        ) {
                            return handleUrl(url)
                        }

                        if (url.startsWith("http://") || url.startsWith("https://")) {
                            this@MainActivity.webView.loadUrl(url)
                            return true
                        }

                        return handleUrl(url)
                    }
                }
                resultMsg?.sendToTarget()
                return true
            }

            override fun onJsAlert(
                view: WebView?,
                url: String?,
                message: String?,
                result: JsResult?
            ): Boolean {
                android.app.AlertDialog.Builder(this@MainActivity)
                    .setMessage(message ?: "")
                    .setPositiveButton(getString(android.R.string.ok)) { _, _ -> result?.confirm() }
                    .setCancelable(false)
                    .show()
                return true
            }

            override fun onJsConfirm(
                view: WebView?,
                url: String?,
                message: String?,
                result: JsResult?
            ): Boolean {
                android.app.AlertDialog.Builder(this@MainActivity)
                    .setMessage(message ?: "")
                    .setPositiveButton(getString(android.R.string.ok)) { _, _ -> result?.confirm() }
                    .setNegativeButton(getString(android.R.string.cancel)) { _, _ -> result?.cancel() }
                    .show()
                return true
            }
        }

        // ── Back button: navigate history inside the WebView ─────────────
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    moveTaskToBack(false)
                }
            }
        })

        // Preserve URL across configuration changes (rotation / resize)
        val savedUrl = savedInstanceState?.getString(KEY_URL)
        webView.loadUrl(savedUrl ?: APP_URL)
    }

    private fun getClipboardManager(): ClipboardManager {
        return getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    }

    private fun copySelectedText() {
        // Evaluate JS to copy the selected text into an element we can read
        webView.evaluateJavascript(
            """
            (function(){
                var sel = window.getSelection();
                if(!sel || sel.toString().trim() === '') return '';
                var ta = document.createElement('textarea');
                ta.value = sel.toString();
                ta.style.position = 'fixed';
                ta.style.left = '-9999px';
                ta.style.top = '0';
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                var copied = '';
                try { document.execCommand('copy'); copied = ta.value; } catch(e) {}
                document.body.removeChild(ta);
                return copied;
            })()
            """.trimIndent()
        ) { result ->
            val value = result?.trim()?.trim('"') ?: ""
            if (value.isNotEmpty()) {
                val clip = ClipData.newPlainText("text", value)
                getClipboardManager().setPrimaryClip(clip)
                Toast.makeText(this, "تم النسخ", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "لا يوجد نص محدد", Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** Minimal JSON string escaping helper for pasting into JS. */
    private fun escapeJs(s: String): String {
        val sb = StringBuilder()
        for (c in s) {
            when (c) {
                '\\' -> sb.append("\\\\")
                '"' -> sb.append("\\\"")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }

    /**
     * Decide how to handle a navigation request:
     * - http/https (normal pages)  → load inside the WebView (return false)
     * - WhatsApp links (wa.me, api.whatsapp.com, whatsapp://)
     *   → open the real WhatsApp app / external browser (return true)
     * - intent:// or other custom schemes → launch external app
     */
    private fun handleUrl(url: String): Boolean {

        if (url.startsWith("https://wa.me/") ||
            url.startsWith("http://wa.me/") ||
            url.startsWith("https://api.whatsapp.com/") ||
            url.startsWith("http://api.whatsapp.com/")
        ) {
            launchExternal(url)
            return true
        }

        return when {
            url.startsWith("http://") || url.startsWith("https://") -> false

            url.startsWith("whatsapp://") -> {
                launchExternal(url)
                true
            }

            url.startsWith("intent://") -> {
                try {
                    val intent = Intent.parseUri(url, Intent.URI_INTENT_SCHEME)
                    try {
                        startActivity(intent)
                    } catch (e: ActivityNotFoundException) {
                        val fallback = intent.getStringExtra("browser_fallback_url")
                        if (!fallback.isNullOrBlank()) {
                            webView.loadUrl(fallback)
                        } else {
                            toast(getString(R.string.app_not_found))
                        }
                    }
                } catch (e: Exception) {
                    toast(getString(R.string.app_not_found))
                }
                true
            }

            else -> {
                launchExternal(url)
                true
            }
        }
    }

    /**
     * Launch an external app via ACTION_VIEW.
     */
    private fun launchExternal(url: String) {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            toast(getString(R.string.app_not_found))
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_URL, webView.url)
    }

    companion object {
        private const val KEY_URL = "saved_url"
    }
}