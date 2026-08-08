package com.alnathim.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.view.View
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.WebViewAssetLoader
import java.io.IOException

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var assetLoader: WebViewAssetLoader

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)

        // ── Fullscreen: hide the system bars so there is no letterbox ─────
        enableFullscreen()
        applySafeAreaInsets(webView)

        // ── WebView settings ──────────────────────────────────────────────
        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.loadWithOverviewMode = true
        settings.useWideViewPort = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.textZoom = 100
        settings.setSupportZoom(false)
        settings.builtInZoomControls = false
        // Allow HTTP API calls to the ISP server from a local page
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW

        // ── Local asset loader: serve bundled HTML from assets/ ──────────
        // This is the "Blood" pattern: the app's UI is bundled INSIDE the
        // APK and loads instantly from the device (file://android_asset),
        // while data comes from the server API.
        assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        // ── WebViewClient: route deep links to external apps ─────────────
        webView.webViewClient = object : WebViewClient() {

            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest?
            ): WebResourceResponse? {
                // Serve local assets for our app scheme
                return assetLoader.shouldInterceptRequest(request?.url ?: return null)
            }

            @Suppress("DEPRECATION")
            override fun shouldInterceptRequest(view: WebView?, url: String?): WebResourceResponse? {
                return assetLoader.shouldInterceptRequest(Uri.parse(url))
            }

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

        // ── Native bridge (window.AlNathimNative) — phone-side probes ────
        // Lets the bundled HTML pages trigger native ICMP ping directly
        // from the phone on the ISP LAN.
        webView.addJavascriptInterface(NativeBridge(this, webView), "AlNathimNative")

        // Load the LOCAL bundled index.html (instant, no network wait).
        // The asset loader registers "/" → app/src/main/assets/, so
        // mobile/index.html is assets/mobile/index.html (no extra /assets/).
        webView.loadUrl("https://appassets.androidplatform.net/mobile/index.html")
    }

    /**
     * Enter immersive fullscreen.
     */
    private fun enableFullscreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val controller = WindowCompat.getInsetsController(window, window.decorView)
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = fullscreenUiFlags
        }
    }

    @Suppress("DEPRECATION")
    private val fullscreenUiFlags: Int
        get() =
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = fullscreenUiFlags
        }
    }

    /**
     * Pad the WebView by safe-area insets.
     */
    private fun applySafeAreaInsets(view: View) {
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            val top = maxOf(systemBars.top, cutout.top)
            val bottom = maxOf(systemBars.bottom, cutout.bottom, ime.bottom)
            val left = maxOf(systemBars.left, cutout.left)
            val right = maxOf(systemBars.right, cutout.right)
            v.setPadding(left, top, right, bottom)
            insets
        }
    }

    /**
     * Decide how to handle a navigation request.
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
            // Local asset URLs load inside the WebView
            url.startsWith("https://appassets.androidplatform.net/") -> false
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

    companion object {
        private const val KEY_URL = "saved_url"
    }
}