package com.alnathim.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
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
        // Let the page handle its own zoom (viewport meta enforces user-scalable=no)
        settings.setSupportZoom(false)
        settings.builtInZoomControls = false

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
        // The app's reminder page calls window.open('https://wa.me/...') to
        // open WhatsApp. WebView drops these by default — we intercept and
        // send WhatsApp links to the external browser/app instead.
        webView.webChromeClient = object : WebChromeClient() {

            override fun onCreateWindow(
                view: WebView?,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: android.os.Message?
            ): Boolean {
                // We never render popups in a separate window.
                // Instead we create a throwaway transport WebView whose
                // client re-routes each popup:
                //   - WhatsApp / external schemes → launch the real app
                //   - regular http(s) popups (e.g. print pages, receipts)
                //     → load in the MAIN WebView so they are visible.
                val newWebView = WebView(this@MainActivity)
                val transport = resultMsg?.obj as? WebView.WebViewTransport
                transport?.webView = newWebView
                newWebView.webViewClient = object : WebViewClient() {

                    override fun shouldOverrideUrlLoading(
                        view: WebView?,
                        request: WebResourceRequest?
                    ): Boolean {
                        val url = request?.url?.toString() ?: return false

                        // WhatsApp deep links → open the real app
                        if (url.startsWith("whatsapp://") ||
                            url.startsWith("https://wa.me/") ||
                            url.startsWith("http://wa.me/") ||
                            url.startsWith("https://api.whatsapp.com/") ||
                            url.startsWith("http://api.whatsapp.com/")
                        ) {
                            return handleUrl(url)
                        }

                        // Regular http(s) popups (print / receipts) →
                        // show them in the main WebView so they are visible.
                        if (url.startsWith("http://") || url.startsWith("https://")) {
                            this@MainActivity.webView.loadUrl(url)
                            return true
                        }

                        // Any other scheme → external app
                        return handleUrl(url)
                    }
                }
                resultMsg?.sendToTarget()
                return true
            }

            // Allow the app's alert()/confirm() dialogs to work (e.g. confirm delete)
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
                    // Minimize the app (like a real native app home-press)
                    moveTaskToBack(false)
                }
            }
        })

        // Preserve URL across configuration changes (rotation / resize)
        val savedUrl = savedInstanceState?.getString(KEY_URL)
        webView.loadUrl(savedUrl ?: APP_URL)
    }

    /**
     * Decide how to handle a navigation request:
     * - http/https (normal pages)  → load inside the WebView (return false)
     * - WhatsApp links (wa.me, api.whatsapp.com, whatsapp://)
     *   → open the real WhatsApp app / external browser (return true)
     * - intent:// or other custom schemes → launch external app
     */
    private fun handleUrl(url: String): Boolean {

        // WhatsApp web deep links used by the reminders page
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

            // WhatsApp deep links — open the real WhatsApp app
            url.startsWith("whatsapp://") -> {
                launchExternal(url)
                true
            }

            // intent:// scheme (some sites use intent-based deep links)
            url.startsWith("intent://") -> {
                try {
                    val intent = Intent.parseUri(url, Intent.URI_INTENT_SCHEME)
                    try {
                        startActivity(intent)
                    } catch (e: ActivityNotFoundException) {
                        // Fall back: strip the intent fallback URL if present
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

            // Any other non-http scheme (tel:, mailto:, sms:, geo:, etc.)
            else -> {
                launchExternal(url)
                true
            }
        }
    }

    /**
     * Launch an external app via ACTION_VIEW. Safe from crashes when
     * no app can handle the URL (Android 11+ package visibility handled
     * by the <queries> entries in the manifest).
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