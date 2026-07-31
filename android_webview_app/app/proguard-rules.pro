# Add project specific ProGuard rules here.
# keep WebView class by default in release builds
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}